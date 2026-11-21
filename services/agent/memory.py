"""Memoria persistente del agente de soporte de TrackFlow (Hito 8, Parte 1).

Qué se recuerda: SOLO lo que el CONTEXT del hito declara memorizable.

* ``regla_carrier``: reglas de asignación de carrier corregidas (un carrier
  cambió su cobertura en una zona o dejó de operar una ruta). Se consolidan por
  **carrier + país**: una sola entrada por cada uno de los 8 pares
  carrier-país de TrackFlow, con una regla por zona.
* ``contexto_incidente_recurrente``: la causa conocida de un patrón de
  incidencias que se repite (p. ej. una huelga que retrasa una ruta esta
  semana), para no re-escalar la misma alerta. Caduca sola (TTL).
* ``preferencia_reporte_b2b``: cómo quiere un cliente B2B recurrente (una
  marca) su reporte mensual: formato y métricas destacadas.

Qué NUNCA se recuerda (lo rechaza ``check_proposal`` en código, aunque el
modelo lo proponga):

* direcciones exactas o ubicaciones de clientes, **tanto B2C (destinatarios
  finales) como B2B (marcas)**, y rutas internas del almacén (seguridad física);
* datos de una incidencia puntual de un solo paquete sin patrón repetible;
* información de contratos comerciales activos o en negociación (es del CRM
  comercial, no del agente de soporte).

Backend: Redis, en un namespace propio (``trackflow:agent_memory:*``). NO usa
la colección RAG ``trackflow_knowledge`` de Qdrant: el RAG sigue siendo de
solo lectura y sus evals de retrieval no se contaminan con hechos episódicos.

Interfaz explícita (``MemoryStore``): ``recall`` lee solo las entradas
relevantes para la pregunta; ``save_pending`` guarda una PROPUESTA, que no es
memoria y que ``recall`` nunca lee; ``confirm`` es la única escritura en
memoria y requiere la aprobación de un usuario.
"""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MemoryType = Literal["regla_carrier", "contexto_incidente_recurrente", "preferencia_reporte_b2b"]
Country = Literal["ES", "US"]

# Los 8 carriers de TrackFlow por país (docs/company-knowledge-base/trackflow-carrier-coverage.es.md):
# EE. UU.: UPS, FedEx y DHL. España: MRW, SEUR, DHL y dos transportistas locales del área metropolitana de Zaragoza.
CARRIERS_BY_COUNTRY: dict[str, tuple[str, ...]] = {
    "US": ("UPS", "FEDEX", "DHL"),
    "ES": ("SEUR", "MRW", "DHL", "LOCAL_ZARAGOZA_1", "LOCAL_ZARAGOZA_2"),
}
CARRIER_ALIASES = {
    "ups": "UPS", "fedex": "FEDEX", "fed ex": "FEDEX", "dhl": "DHL", "seur": "SEUR", "mrw": "MRW",
    "transportista local": "LOCAL_ZARAGOZA", "carrier local": "LOCAL_ZARAGOZA", "local": "LOCAL_ZARAGOZA",
}
COUNTRY_HINTS = {
    "ES": ("zaragoza", "espana", "aragon", "cataluna", "madrid", "barcelona", "huesca", "teruel"),
    "US": ("los angeles", "estados unidos", "california", "eeuu", "ee. uu.", "usa", "nevada", "arizona"),
}
DEFAULT_INCIDENT_TTL_DAYS = 7
MAX_INCIDENT_TTL_DAYS = 30
MAX_RECALLED = 5


def normalize(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (text or "").lower()) if unicodedata.category(c) != "Mn")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalize(text)).strip("-")[:60] or "sin-nombre"


# ============================================================================
# 1. Auto-evaluación: criterio explícito (puerta determinista)
# ============================================================================
# Solo se pide una propuesta al modelo si la interacción trae información NUEVA o
# CORREGIDA sobre una de las tres categorías memorizables. La mayoría de las
# interacciones (consultas puntuales, cierres, tareas de un solo uso) no abren la
# puerta: "nada que recordar".
CORRECTION_SIGNALS = re.compile(
    r"\b(en realidad|ya no|dejo de|dejaron de|ha dejado de|ha cambiado|cambio su|cambiaron|desde el mes pasado|"
    r"desde la semana pasada|desde hace|a partir de|ahora hay que|ahora se usa|hay que usar|ya no cubre|no es correcto|"
    r"esta mal|te equivocas|corrige|correccion)\b")
RECURRENCE_SIGNALS = re.compile(
    r"\b(ya van (dos|tres|cuatro|cinco|varios|\d+)|otra vez|de nuevo|se repite|recurrente|todos los dias|esta semana|"
    r"estos dias|huelga|no es un problema (nuestro|de trackflow)|no por un problema nuestro|no es culpa nuestra)\b")
PREFERENCE_SIGNALS = re.compile(
    r"\b(siempre quiere|siempre pide|siempre prefiere|prefiere|le gusta recibir|quiere su reporte|quiere el reporte)\b")
DOMAIN_ANCHORS = re.compile(
    r"\b(ups|fedex|dhl|seur|mrw|carrier|carriers|transportista|transportistas|cobertura|ruta|rutas|zona|retrasos?|"
    r"incidencias?|tickets?|reporte|informe|cliente|clientes|marca)\b")
CLOSING_ONLY = re.compile(r"^(ok|vale|perfecto|genial|gracias|muchas gracias|listo|entendido|de acuerdo)\b.{0,40}$")


class MemoryGate(BaseModel):
    open: bool
    signals: list[str] = Field(default_factory=list)
    reason: str
    candidate_type: str | None = Field(default=None, description="Tipo memorizable que sugieren las señales")
    hints: dict[str, Any] = Field(default_factory=dict, description="Carriers y países mencionados (pistas, no decisiones)")


def evaluate_memory_gate(question: str) -> MemoryGate:
    """¿Esta interacción puede contener algo nuevo o corregido que valga la pena recordar?"""
    text = normalize(question).strip(" ¿?¡!.")
    if CLOSING_ONLY.match(text):
        return MemoryGate(open=False, reason="cierre de conversación: nada que recordar")
    signals = [name for name, pattern in (("correccion", CORRECTION_SIGNALS), ("recurrencia", RECURRENCE_SIGNALS),
                                          ("preferencia", PREFERENCE_SIGNALS)) if pattern.search(text)]
    if not signals:
        return MemoryGate(open=False, reason="sin información nueva ni corregida (consulta o tarea puntual): nada que recordar")
    if not DOMAIN_ANCHORS.search(text):
        return MemoryGate(open=False, signals=signals,
                          reason="hay una señal, pero no sobre carriers, incidentes recurrentes ni reportes B2B")
    candidate = None
    if "preferencia" in signals and re.search(r"\b(reporte|informe)\b", text):
        candidate = "preferencia_reporte_b2b"
    elif "recurrencia" in signals and re.search(r"\b(retrasos?|incidencias?|incidentes?|tickets?|huelga|alerta)\b", text):
        candidate = "contexto_incidente_recurrente"
    elif mentioned_carriers(question) or re.search(r"\b(carrier|transportista|cobertura|cubre|ruta)\b", text):
        candidate = "regla_carrier"
    hints = {"carriers_mencionados": sorted(mentioned_carriers(question)), "paises_mencionados": sorted(mentioned_countries(question))}
    return MemoryGate(open=True, signals=signals, candidate_type=candidate, hints=hints,
                      reason=f"señales de {', '.join(signals)} sobre una categoría memorizable")


# ============================================================================
# 2. Propuesta de memoria (salida estructurada del modelo) y guardrails
# ============================================================================
class MemoryProposal(BaseModel):
    """Campo ``propuesta_memoria`` de la salida del modelo."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    tipo: MemoryType
    accion: Literal["agregar", "corregir"] = "agregar"
    hecho: str = Field(min_length=8, max_length=400, description="Lo que se recordaría, en una frase")
    motivo: str = Field(min_length=4, max_length=300, description="Por qué vale la pena recordarlo")
    carrier: str | None = None
    pais: Country | None = None
    zona: str | None = Field(default=None, max_length=80)
    ruta: str | None = Field(default=None, max_length=80)
    cliente_b2b: str | None = Field(default=None, max_length=80)
    tickets_relacionados: int | None = Field(default=None, ge=0)
    vigencia_dias: int | None = Field(default=None, ge=1, le=MAX_INCIDENT_TTL_DAYS)


class Violation(BaseModel):
    code: str
    detail: str


PROHIBITED = [
    # Ubicación exacta de clientes B2C o B2B: direcciones, números de portal, códigos postales, coordenadas.
    ("DIRECCION_EXACTA_CLIENTE", re.compile(
        r"\b(calle|c/|avenida|avda|av\.|paseo|plaza|pza|camino|carretera|ctra|poligono|pol\. ind|nave|street|st\.|avenue|ave\.|"
        r"blvd|boulevard|road|rd\.|drive|dr\.|suite|apt|apartment|unit)\s+[\w\s\.'-]{1,40}?\d+")),
    ("DIRECCION_EXACTA_CLIENTE", re.compile(r"\b(n\.?o|num(ero)?|portal|piso|puerta|planta|escalera|bajo|esc\.)\s*\d+\b")),
    ("DIRECCION_EXACTA_CLIENTE", re.compile(r"\b(cp|c\.p\.|codigo postal|zip)\s*:?\s*\d{5}\b|\b(50|22|44|08|28)\d{3}\b|\b9\d{4}(-\d{4})?\b")),
    ("DIRECCION_EXACTA_CLIENTE", re.compile(r"-?\d{1,3}\.\d{4,},\s*-?\d{1,3}\.\d{4,}")),
    ("UBICACION_CLIENTE", re.compile(
        r"\b(vive en|domicilio|direccion (de entrega|del cliente|de la marca|de su almacen|de su oficina)|su almacen esta en|"
        r"sus oficinas estan en|recoger en su casa|entregar en su casa)\b")),
    ("DATO_PERSONAL_CONTACTO", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\+?\d[\d\s-]{8,}\d")),
    # Rutas internas del almacén (seguridad física)
    ("RUTA_INTERNA_ALMACEN", re.compile(
        r"\b(pasillo|estanteria|estante|rack|muelle|anden|ubicacion interna|ruta interna|recorrido interno|zona de picking|"
        r"ruta de picking|camara de seguridad|codigo de acceso|puerta de servicio|aisle|bay|dock door|loading dock|shelf)\b")),
    # Contratos comerciales activos o en negociación (CRM comercial)
    ("CONTRATO_EN_NEGOCIACION", re.compile(
        r"\b(contrato|contratos|negociacion|negociando|negociamos|en negociacion|propuesta comercial|oferta comercial|"
        r"renovacion del contrato|tarifa (pactada|negociada|especial)|descuento (pactado|negociado)|clausula|firma del contrato|"
        r"rfp|licitacion)\b")),
]
TRACKING_OR_SINGLE_PACKAGE = re.compile(
    r"\b(tracking|n(umero)? de seguimiento|el paquete|ese paquete|este paquete|un paquete|el envio [a-z]{0,3}\d+|pedido \d+)\b|"
    r"\b[a-z]{1,4}\d{3,}\b|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
MIN_TICKETS_FOR_PATTERN = 2


def canonical_carrier(name: str | None, country: str | None) -> str | None:
    if not name:
        return None
    key = normalize(name).strip()
    canon = CARRIER_ALIASES.get(key) or next((alias_value for alias, alias_value in CARRIER_ALIASES.items() if alias in key), None)
    canon = canon or key.upper().replace(" ", "_")
    if canon == "LOCAL_ZARAGOZA":
        return "LOCAL_ZARAGOZA" if country in (None, "ES") else None
    valid = {c for carriers in CARRIERS_BY_COUNTRY.values() for c in carriers}
    return canon if canon in valid else None


def check_proposal(proposal: MemoryProposal, question: str = "") -> list[Violation]:
    """Guardrails en código: lo prohibido por el CONTEXT nunca llega a memoria, lo diga quien lo diga."""
    texts = " | ".join(filter(None, [proposal.hecho, proposal.motivo, proposal.zona, proposal.ruta, proposal.cliente_b2b]))
    normalized = normalize(texts)
    violations = []
    for code, pattern in PROHIBITED:
        match = pattern.search(normalized)
        if match and code not in {v.code for v in violations}:
            violations.append(Violation(code=code, detail=f"contiene «{match.group(0).strip()}»"))

    if proposal.tipo == "regla_carrier":
        if not proposal.pais:
            violations.append(Violation(code="CAMPO_OBLIGATORIO", detail="una regla de carrier necesita el país (ES o US)"))
        carrier = canonical_carrier(proposal.carrier, proposal.pais)
        if carrier is None:
            violations.append(Violation(code="CARRIER_DESCONOCIDO", detail=f"«{proposal.carrier}» no es uno de los carriers de TrackFlow"))
        elif proposal.pais and not any(carrier.startswith(c) or c.startswith(carrier) for c in CARRIERS_BY_COUNTRY[proposal.pais]):
            violations.append(Violation(code="CARRIER_DESCONOCIDO", detail=f"{carrier} no opera en {proposal.pais}"))
    elif proposal.tipo == "contexto_incidente_recurrente":
        if not proposal.pais or not (proposal.ruta or proposal.zona):
            violations.append(Violation(code="CAMPO_OBLIGATORIO", detail="un contexto de incidente necesita país y ruta o zona"))
        recurrence = (proposal.tickets_relacionados or 0) >= MIN_TICKETS_FOR_PATTERN or RECURRENCE_SIGNALS.search(normalize(question))
        if not recurrence or TRACKING_OR_SINGLE_PACKAGE.search(normalized):
            violations.append(Violation(code="INCIDENCIA_PUNTUAL_SIN_PATRON",
                                        detail="no hay patrón repetible (un solo paquete, un tracking o un ticket aislado)"))
    elif proposal.tipo == "preferencia_reporte_b2b":
        if not proposal.cliente_b2b:
            violations.append(Violation(code="CAMPO_OBLIGATORIO", detail="una preferencia de reporte necesita el cliente B2B"))
        if not re.search(r"\b(reporte|informe|metrica|metricas|desglose|formato|kpi|orden|primero|graficos?|tabla|pdf|excel)\b", normalized):
            violations.append(Violation(code="FUERA_DE_CATEGORIA", detail="no describe una preferencia sobre el reporte mensual"))
    return violations


# ============================================================================
# 3. Entradas de memoria (consolidadas) y propuestas pendientes
# ============================================================================
class MemoryEntry(BaseModel):
    key: str
    tipo: MemoryType
    titulo: str
    hechos: dict[str, dict[str, Any]] = Field(default_factory=dict, description="Un hecho por zona / ruta / aspecto")
    actualizado_en: str
    expira_en: str | None = None
    ultimo_uso: str | None = None

    def lines(self) -> list[str]:
        return [f"{self.titulo} — {h['hecho']} (aprobado por {h.get('aprobado_por', '?')} el {h.get('aprobado_en', '?')[:10]})"
                for h in self.hechos.values()]


class PendingProposal(BaseModel):
    id: str
    proposal: MemoryProposal
    question: str
    run_id: str | None = None
    session_id: str | None = None
    created_at: str
    status: Literal["pending", "confirmed", "rejected", "discarded"] = "pending"


def memory_key(proposal: MemoryProposal) -> tuple[str, str, str]:
    """(clave de la entrada consolidada, título, sub-clave del hecho dentro de la entrada)."""
    if proposal.tipo == "regla_carrier":
        carrier = canonical_carrier(proposal.carrier, proposal.pais)
        return f"carrier:{carrier}:{proposal.pais}", f"Carrier {carrier} ({proposal.pais})", slug(proposal.zona or "general")
    if proposal.tipo == "contexto_incidente_recurrente":
        route = slug(proposal.ruta or proposal.zona or "general")
        return f"incident:{proposal.pais}:{route}", f"Incidentes recurrentes {proposal.pais} · {proposal.ruta or proposal.zona}", "causa"
    client = slug(proposal.cliente_b2b or "")
    return f"b2b_report:{client}", f"Reporte mensual de {proposal.cliente_b2b}", slug(proposal.zona or "preferencia")


class MemoryStore(Protocol):
    def recall(self, question: str, limit: int = MAX_RECALLED) -> list[MemoryEntry]: ...
    def save_pending(self, proposal: MemoryProposal, question: str, run_id: str | None,
                     session_id: str | None = None) -> PendingProposal: ...
    def get_pending(self, proposal_id: str) -> PendingProposal | None: ...
    def session_pending(self, session_id: str | None) -> PendingProposal | None: ...
    def confirm(self, proposal_id: str, approved_by: str, reply: str | None = None) -> MemoryEntry: ...
    def edit_and_confirm(self, proposal_id: str, new_fact: str, approved_by: str, reply: str | None = None) -> MemoryEntry: ...
    def reject(self, proposal_id: str, rejected_by: str, reply: str | None = None) -> PendingProposal: ...
    def discard(self, proposal_id: str, reason: str, reply: str | None = None) -> PendingProposal: ...
    def entries(self) -> list[MemoryEntry]: ...
    def audit_log(self) -> list[dict[str, Any]]: ...


class MemoryWriteRejected(ValueError):
    def __init__(self, violations: list[Violation]):
        super().__init__("; ".join(f"{v.code}: {v.detail}" for v in violations))
        self.violations = violations


class PendingAlreadyOpen(RuntimeError):
    """Ya hay una propuesta sin resolver en esta conversación: no se lanza una segunda."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def mentioned_carriers(text: str) -> set[str]:
    norm = normalize(text)
    return {value for alias, value in CARRIER_ALIASES.items() if alias != "local" and re.search(rf"\b{re.escape(alias)}\b", norm)}


def mentioned_countries(text: str) -> set[str]:
    norm = normalize(text)
    return {country for country, hints in COUNTRY_HINTS.items()
            if any(re.search(rf"(?<![a-z]){re.escape(h)}(?![a-z])", norm) for h in hints)}


# ============================================================================
# 4. Confirmación del usuario: clasificación explícita de la respuesta a la propuesta pendiente
# ============================================================================
ReplyLabel = Literal["aprobar", "rechazar", "editar", "otro_tema"]

_APPROVE = (r"si|sip|claro|vale|ok|okay|de acuerdo|adelante|correcto|exacto|confirmo|confirmado|apruebo|aprobado|"
            r"guardalo|recuerdalo|memorizalo|anotalo|hazlo|perfecto,? guardalo|si,? por favor|si,? guardalo|si,? recuerdalo")
_REJECT = (r"no|nop|mejor no|no,? gracias|no lo guardes|no lo recuerdes|no hace falta|no es necesario|descartala|descartalo|"
           r"rechazo|rechazada|olvidalo|ni se te ocurra|no,? eso no")
_EDIT = (r"si,? pero|casi|no exactamente|corrigelo|corrige|cambialo|guardalo como|mejor (pon|guarda|anota)|"
         r"recuerdalo (asi|como)|en vez de eso")
# La etiqueta solo se asigna si la respuesta abre el mensaje y termina en un separador claro (o en el final).
_END = r"(?=\s*(?:[,.;:!—-]|\by\b|$))"
APPROVE_RE = re.compile(rf"^\s*(?:{_APPROVE}){_END}")
REJECT_RE = re.compile(rf"^\s*(?:{_REJECT}){_END}")
EDIT_RE = re.compile(rf"^\s*(?:{_EDIT})\b\s*[,:]?\s*")


class PendingReplyIntent(BaseModel):
    """Etiqueta estructurada de cómo responde el usuario a la propuesta pendiente."""

    label: ReplyLabel
    remainder: str = Field(default="", description="El resto del mensaje, que se responde con normalidad")
    edited_fact: str | None = None
    rationale: str


_FILLERS = re.compile(
    r"^\s*[,.;:!—-]*\s*(?:(?:guardalo|recuerdalo|anotalo|adelante|por favor|gracias|perfecto|vale|ok|claro|"
    r"no lo guardes|no hace falta|descartala|descartalo|olvidalo|y|otra cosa|ademas|por cierto|tambien)\b\s*[,.;:!—-]*\s*)+")


def _rest_after(match_end: int, original: str) -> str:
    rest = original[match_end:]
    stripped = _FILLERS.match(normalize(rest))
    return rest[stripped.end():].strip() if stripped else rest.strip(" ,.;:!")


def classify_pending_reply(message: str) -> PendingReplyIntent:
    """Clasificador explícito (reglas) de la respuesta a una propuesta pendiente.

    * ``aprobar`` / ``rechazar``: el mensaje EMPIEZA por una respuesta clara ("sí, guárdalo", "no, gracias")
      seguida de un separador. "No sé dónde está el paquete" NO es un rechazo: no hay separador tras "no".
    * ``editar``: "sí, pero …", "guárdalo como: …" → el texto que sigue sustituye al hecho propuesto.
    * ``otro_tema``: cualquier otra cosa, incluida la ambigüedad → la propuesta se DESCARTA. Nunca se asume
      aprobación por silencio, por ambigüedad ni por un "sí" en mitad del mensaje.
    """
    original = (message or "").strip()
    # Se normaliza conservando longitudes (solo minúsculas y sin tildes) para poder cortar el original.
    norm = "".join(c for c in unicodedata.normalize("NFC", original.lower()))
    norm = "".join(normalize(c) or c for c in norm)
    norm = norm.lstrip("¡¿")
    offset = len(original) - len(original.lstrip("¡¿ "))
    edit = EDIT_RE.match(norm)
    if edit:
        fact = original[offset + edit.end():].strip(" .")
        fact, _, remainder = fact.partition("?") if "?" in fact and len(fact.split("?")[0]) > 8 else (fact, "", "")
        if len(fact) >= 8:
            return PendingReplyIntent(label="editar", edited_fact=fact.strip(" .,"), remainder=remainder.strip(),
                                      rationale="respuesta de edición explícita al inicio del mensaje")
    approve = APPROVE_RE.match(norm)
    if approve:
        return PendingReplyIntent(label="aprobar", remainder=_rest_after(offset + approve.end(), original),
                                  rationale=f"aprobación explícita al inicio: «{original[:approve.end() + offset].strip()}»")
    reject = REJECT_RE.match(norm)
    if reject:
        return PendingReplyIntent(label="rechazar", remainder=_rest_after(offset + reject.end(), original),
                                  rationale=f"rechazo explícito al inicio: «{original[:reject.end() + offset].strip()}»")
    return PendingReplyIntent(label="otro_tema", remainder=original,
                              rationale="el mensaje no responde claramente sí, no ni edita la propuesta: se descarta por defecto")


# ============================================================================
# 5. Consolidación y limpieza
# ============================================================================
# Política (ver docs/agent/agent-memory.md):
# * regla_carrier: una entrada por carrier + país (máx. 8 pares); máx. 12 zonas por entrada. Cada regla
#   caduca a los 180 días de su última confirmación: las coberturas cambian y una corrección vieja que
#   nadie reconfirma es más peligrosa que no tenerla. Al superar el tope se descarta la zona menos usada.
# * contexto_incidente_recurrente: TTL de 7 días (máx. 30) en Redis. Es una causa coyuntural.
# * preferencia_reporte_b2b: una entrada por cliente, máx. 6 aspectos; caduca a los 365 días sin uso.
#   Máx. 60 clientes: al superarlo se descarta el menos usado (LRU).
# * Deduplicación: el mismo hecho (normalizado) no crea otro registro; suma una confirmación.
FACT_TTL_DAYS = {"regla_carrier": 180, "preferencia_reporte_b2b": 365}
MAX_FACTS_PER_ENTRY = {"regla_carrier": 12, "contexto_incidente_recurrente": 3, "preferencia_reporte_b2b": 6}
MAX_B2B_CLIENTS = 60


def _same_fact(a: str, b: str) -> bool:
    return re.sub(r"[^a-z0-9]+", " ", normalize(a)).strip() == re.sub(r"[^a-z0-9]+", " ", normalize(b)).strip()


class RedisMemoryStore:
    """``MemoryStore`` sobre Redis. Claves (prefijo ``trackflow:agent_memory:``):

    * ``entry:carrier:<CARRIER>:<PAIS>``: JSON ``MemoryEntry`` consolidada por carrier + país;
    * ``entry:incident:<PAIS>:<ruta>``: JSON con TTL (``vigencia_dias``, por defecto 7);
    * ``entry:b2b_report:<cliente>``: JSON;
    * ``pending:<id>``: propuesta pendiente (TTL de 7 días). NO es memoria: ``recall`` no la lee;
    * ``session:<id>:pending``: la ÚNICA propuesta pendiente de una conversación;
    * ``audit``: registro de cada decisión (propuesta, resultado, mensajes, usuario, marca de tiempo).
    """

    PREFIX = "trackflow:agent_memory:"
    # Una propuesta sin respuesta caduca a las 24 h: se registra como "caducada_sin_respuesta" (nunca aprobada).
    PENDING_TTL = timedelta(hours=24)
    SESSION_POINTER_TTL = timedelta(days=7)

    def __init__(self, client):
        self.r = client

    @classmethod
    def from_env(cls) -> "RedisMemoryStore":
        import os

        import redis

        url = os.environ.get("AGENT_MEMORY_REDIS_URL", "redis://127.0.0.1:6379/2")
        return cls(redis.Redis.from_url(url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2))

    def _k(self, suffix: str) -> str:
        return f"{self.PREFIX}{suffix}"

    # ---------------------------------------------------------------- lectura
    def _load(self, key: str) -> MemoryEntry | None:
        raw = self.r.get(self._k(f"entry:{key}"))
        return MemoryEntry.model_validate_json(raw) if raw else None

    def _save(self, entry: MemoryEntry) -> None:
        ttl = self.r.ttl(self._k(f"entry:{entry.key}"))
        self.r.set(self._k(f"entry:{entry.key}"), entry.model_dump_json(), ex=ttl if ttl and ttl > 0 else None)

    def entries(self) -> list[MemoryEntry]:
        keys = sorted(self.r.scan_iter(match=self._k("entry:*")))
        return [MemoryEntry.model_validate_json(raw) for raw in (self.r.get(k) for k in keys) if raw]

    def recall(self, question: str, limit: int = MAX_RECALLED) -> list[MemoryEntry]:
        """Solo las entradas relevantes para la pregunta (por carrier, país, ruta y cliente mencionados), con un tope."""
        self.cleanup()
        norm = normalize(question)
        carriers, countries = mentioned_carriers(question), mentioned_countries(question)
        wanted: list[str] = []
        for carrier in carriers:
            for country in (countries or {c for c, cs in CARRIERS_BY_COUNTRY.items() if any(x.startswith(carrier) for x in cs)}):
                wanted.append(f"carrier:{carrier}:{country}")
        if not carriers and countries and re.search(r"\b(carrier|carriers|transportista|transportistas|cobertura|envio|enviar|pueblo|rural)\b", norm):
            wanted += [f"carrier:{c}:{country}" for country in countries for c in CARRIERS_BY_COUNTRY[country] + ("LOCAL_ZARAGOZA",)]
        if re.search(r"\b(retrasos?|incidencias?|incidentes?|tickets?|alerta|escalar|escalo)\b", norm):
            for country in countries or {"ES", "US"}:
                wanted += [k.removeprefix(self._k("entry:")) for k in self.r.scan_iter(match=self._k(f"entry:incident:{country}:*"))]
        if re.search(r"\b(reporte|informe)\b", norm):
            for k in self.r.scan_iter(match=self._k("entry:b2b_report:*")):
                key = k.removeprefix(self._k("entry:"))
                client_words = key.split(":", 1)[1].split("-")
                if any(w and len(w) > 3 and w in norm for w in client_words):
                    wanted.append(key)
        found, now = [], _now().isoformat(timespec="seconds")
        for key in dict.fromkeys(wanted):
            entry = self._load(key)
            if entry:
                found.append(entry)
                self.r.zadd(self._k("usage"), {key: _now().timestamp()})  # para la limpieza LRU
        for entry in found[:limit]:
            entry.ultimo_uso = now
        return found[:limit]

    # ---------------------------------------------------------------- propuestas (no son memoria)
    def save_pending(self, proposal: MemoryProposal, question: str, run_id: str | None,
                     session_id: str | None = None) -> PendingProposal:
        if session_id and self.session_pending(session_id):
            raise PendingAlreadyOpen(session_id)
        pending = PendingProposal(id=f"mem-{uuid.uuid4().hex[:10]}", proposal=proposal, question=question, run_id=run_id,
                                  session_id=session_id, created_at=_now().isoformat(timespec="seconds"))
        ttl = int(self.PENDING_TTL.total_seconds())
        self.r.set(self._k(f"pending:{pending.id}"), pending.model_dump_json(), ex=ttl)
        if session_id:
            # El puntero de la sesión vive más que la propuesta: si caduca sin respuesta, se audita al volver.
            pointer = {"id": pending.id, "tipo": proposal.tipo, "hecho": proposal.hecho, "mensaje_origen": question,
                       "run_id": run_id, "created_at": pending.created_at}
            self.r.set(self._k(f"session:{session_id}:pending"), json.dumps(pointer, ensure_ascii=False),
                       ex=int(self.SESSION_POINTER_TTL.total_seconds()))
        self._audit("propuesta", pending, user=None)
        return pending

    def get_pending(self, proposal_id: str) -> PendingProposal | None:
        raw = self.r.get(self._k(f"pending:{proposal_id}"))
        return PendingProposal.model_validate_json(raw) if raw else None

    def session_pending(self, session_id: str | None) -> PendingProposal | None:
        if not session_id:
            return None
        raw = self.r.get(self._k(f"session:{session_id}:pending"))
        if not raw:
            return None
        pointer = json.loads(raw)
        pending = self.get_pending(pointer["id"])
        if pending is None:
            # Caducó sin respuesta: se descarta y queda registrado. El silencio nunca es aprobación.
            self.r.delete(self._k(f"session:{session_id}:pending"))
            self.r.rpush(self._k("audit"), json.dumps({
                "ts": _now().isoformat(timespec="seconds"), "outcome": "caducada_sin_respuesta", "proposal_id": pointer["id"],
                "session_id": session_id, "user": None, "tipo": pointer["tipo"], "propuesta": {"hecho": pointer["hecho"]},
                "mensaje_origen": pointer["mensaje_origen"], "run_id": pointer.get("run_id"),
                "propuesta_creada_en": pointer["created_at"]}, ensure_ascii=False))
        return pending

    def _close(self, pending: PendingProposal, status: str) -> None:
        self.r.delete(self._k(f"pending:{pending.id}"))
        raw = self.r.get(self._k(f"session:{pending.session_id}:pending")) if pending.session_id else None
        if raw and json.loads(raw)["id"] == pending.id:
            self.r.delete(self._k(f"session:{pending.session_id}:pending"))
        pending.status = status

    def reject(self, proposal_id: str, rejected_by: str, reply: str | None = None) -> PendingProposal:
        pending = self._require_pending(proposal_id)
        self._close(pending, "rejected")
        self._audit("rechazada", pending, rejected_by, reply=reply)
        return pending

    def discard(self, proposal_id: str, reason: str, reply: str | None = None) -> PendingProposal:
        """Descarta sin escribir: el usuario cambió de tema o respondió de forma ambigua."""
        pending = self._require_pending(proposal_id)
        self._close(pending, "discarded")
        self._audit("descartada_sin_respuesta_clara", pending, None, reply=reply, reason=reason)
        return pending

    # ---------------------------------------------------------------- escritura (única vía: aprobación del usuario)
    def confirm(self, proposal_id: str, approved_by: str, reply: str | None = None) -> MemoryEntry:
        """Escribe en memoria una propuesta que un usuario aprobó. Vuelve a pasar los guardrails."""
        return self._write(self._require_pending(proposal_id), approved_by, reply, outcome="aprobada")

    def edit_and_confirm(self, proposal_id: str, new_fact: str, approved_by: str, reply: str | None = None) -> MemoryEntry:
        """El usuario corrige el texto propuesto y aprueba esa versión (que también pasa los guardrails)."""
        pending = self._require_pending(proposal_id)
        edited = pending.model_copy(update={"proposal": pending.proposal.model_copy(update={"hecho": new_fact, "accion": "corregir"})})
        return self._write(edited, approved_by, reply, outcome="editada_y_aprobada", original=pending.proposal.hecho)

    def _write(self, pending: PendingProposal, approved_by: str, reply: str | None, *, outcome: str, **extra) -> MemoryEntry:
        violations = check_proposal(pending.proposal, pending.question)
        if violations:
            self._close(pending, "rejected")
            self._audit("rechazada_por_guardrails", pending, approved_by, reply=reply,
                        violations=[v.model_dump() for v in violations])
            raise MemoryWriteRejected(violations)
        proposal = pending.proposal
        key, title, fact_key = memory_key(proposal)
        now = _now()
        entry = self._load(key) or MemoryEntry(key=key, tipo=proposal.tipo, titulo=title, actualizado_en=now.isoformat(timespec="seconds"))
        previous = entry.hechos.get(fact_key)
        duplicate = bool(previous and _same_fact(previous["hecho"], proposal.hecho))
        entry.hechos[fact_key] = {
            "hecho": proposal.hecho, "motivo": proposal.motivo, "zona": proposal.zona, "ruta": proposal.ruta,
            "accion": proposal.accion, "aprobado_por": approved_by, "aprobado_en": now.isoformat(timespec="microseconds"),
            "confirmaciones": (previous or {}).get("confirmaciones", 0) + 1 if duplicate else 1,
            "propuesta_id": pending.id, "run_id": pending.run_id,
        }
        entry.actualizado_en = now.isoformat(timespec="seconds")
        evicted = self._cap_facts(entry)
        ttl = None
        if proposal.tipo == "contexto_incidente_recurrente":
            days = min(proposal.vigencia_dias or DEFAULT_INCIDENT_TTL_DAYS, MAX_INCIDENT_TTL_DAYS)
            ttl = int(timedelta(days=days).total_seconds())
            entry.expira_en = (now + timedelta(days=days)).isoformat(timespec="seconds")
        self.r.set(self._k(f"entry:{key}"), entry.model_dump_json(), ex=ttl)
        self.r.zadd(self._k("usage"), {key: now.timestamp()})
        self._close(pending, "confirmed")
        self._audit(outcome, pending, approved_by, reply=reply, key=key, deduplicada=duplicate,
                    zonas_descartadas_por_tope=evicted, **extra)
        self.cleanup()
        return entry

    # ---------------------------------------------------------------- consolidación y limpieza
    def _cap_facts(self, entry: MemoryEntry) -> list[str]:
        limit = MAX_FACTS_PER_ENTRY[entry.tipo]
        evicted = []
        while len(entry.hechos) > limit:
            oldest = min(entry.hechos, key=lambda k: entry.hechos[k]["aprobado_en"])
            evicted.append(oldest)
            del entry.hechos[oldest]
        return evicted

    def cleanup(self) -> dict[str, int]:
        """Caduca hechos viejos y limita los clientes B2B (LRU). Idempotente; se ejecuta en cada recall y escritura."""
        removed = {"hechos_caducados": 0, "entradas_vacias": 0, "clientes_lru": 0}
        now = _now()
        for entry in self.entries():
            ttl_days = FACT_TTL_DAYS.get(entry.tipo)
            if not ttl_days:
                continue
            last_use = self.r.zscore(self._k("usage"), entry.key) or 0
            for fact_key, fact in list(entry.hechos.items()):
                approved = datetime.fromisoformat(fact["aprobado_en"])
                reference = max(approved.timestamp(), last_use if entry.tipo == "preferencia_reporte_b2b" else 0)
                if now.timestamp() - reference > ttl_days * 86400:
                    del entry.hechos[fact_key]
                    removed["hechos_caducados"] += 1
            if not entry.hechos:
                self.r.delete(self._k(f"entry:{entry.key}"))
                self.r.zrem(self._k("usage"), entry.key)
                removed["entradas_vacias"] += 1
            elif removed["hechos_caducados"]:
                self._save(entry)
        clients = [k.removeprefix(self._k("entry:")) for k in self.r.scan_iter(match=self._k("entry:b2b_report:*"))]
        if len(clients) > MAX_B2B_CLIENTS:
            by_use = sorted(clients, key=lambda key: self.r.zscore(self._k("usage"), key) or 0)
            for key in by_use[:len(clients) - MAX_B2B_CLIENTS]:
                self.r.delete(self._k(f"entry:{key}"))
                self.r.zrem(self._k("usage"), key)
                removed["clientes_lru"] += 1
        return removed

    # ---------------------------------------------------------------- auditoría
    def _require_pending(self, proposal_id: str) -> PendingProposal:
        pending = self.get_pending(proposal_id)
        if pending is None:
            raise KeyError(proposal_id)
        return pending

    def _audit(self, outcome: str, pending: PendingProposal, user: str | None, **extra) -> None:
        record = {"ts": _now().isoformat(timespec="seconds"), "outcome": outcome, "proposal_id": pending.id,
                  "session_id": pending.session_id, "user": user, "tipo": pending.proposal.tipo,
                  "propuesta": pending.proposal.model_dump(exclude_none=True), "mensaje_origen": pending.question,
                  "run_id": pending.run_id, **{k: v for k, v in extra.items() if v not in (None, [], False)}}
        self.r.rpush(self._k("audit"), json.dumps(record, ensure_ascii=False))

    def audit_log(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.r.lrange(self._k("audit"), 0, -1)]


NUMBER_WORDS = {"dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10}


def enrich_proposal(raw: Any, question: str, gate: dict[str, Any] | None) -> tuple[Any, list[str]]:
    """Completa campos ESTRUCTURALES que el modelo omitió, solo a partir de pistas literales del mensaje.

    El modelo decide si hay algo que recordar y redacta el hecho; el código solo rellena claves de
    consolidación (país, carrier, nº de tickets, zona o cliente) cuando el mensaje las dice sin ambigüedad.
    Devuelve la propuesta y la lista de campos completados (queda en el trace).
    """
    if not isinstance(raw, dict) or not raw.get("tipo"):
        return raw, []
    data, filled = dict(raw), []
    hints = (gate or {}).get("hints") or {}
    norm = normalize(question)

    def fill(field: str, value: Any) -> None:
        if value not in (None, "", []) and not data.get(field):
            data[field] = value
            filled.append(field)

    countries = hints.get("paises_mencionados") or sorted(mentioned_countries(question))
    if len(countries) == 1:
        if data.get("pais") and data["pais"] != countries[0]:
            # El mensaje nombra un único país y el modelo puso otro: la clave de consolidación sigue al mensaje.
            data["pais"] = countries[0]
            filled.append("pais(corregido)")
        fill("pais", countries[0])
    if data["tipo"] == "regla_carrier":
        named = [c for c in (hints.get("carriers_mencionados") or sorted(mentioned_carriers(question))) if c != "LOCAL_ZARAGOZA"]
        if len(named) == 1:
            fill("carrier", named[0])
        zone = re.search(r"\b(zona rural de [a-z ]+?|zona de [a-z ]+?|ruta [a-z ]+? a [a-z ]+?)(?:,|\.|;| hay | desde | y |$)", norm)
        fill("zona", zone.group(1).strip() if zone else None)
    elif data["tipo"] == "contexto_incidente_recurrente":
        count = re.search(r"\bya van (\d+|" + "|".join(NUMBER_WORDS) + r") (tickets?|incidencias?|casos?)", norm)
        if count:
            fill("tickets_relacionados", int(count.group(1)) if count.group(1).isdigit() else NUMBER_WORDS[count.group(1)])
        route = re.search(r"\bruta de ([a-z ]+?) a ([a-z ]+?)(?:,|\.|;| esta | son |$)", norm)
        city = next((c for c in ("los angeles", "zaragoza") if c in norm), None)
        fill("ruta", f"{route.group(1).strip()} - {route.group(2).strip()}" if route else (city.title() if city else None))
    elif data["tipo"] == "preferencia_reporte_b2b":
        client = re.search(r"\b(?:el|la) (cliente de [a-z]+|marca de [a-z]+|cliente [A-Za-z][\w-]+)", norm)
        fill("cliente_b2b", client.group(1) if client else None)
    return data, filled


def parse_proposal(raw: Any) -> tuple[MemoryProposal | None, str | None]:
    """Valida el campo ``propuesta_memoria`` del modelo. ``None`` → nada que recordar."""
    if raw in (None, "", {}, [], "null", "ninguna"):
        return None, None
    try:
        return MemoryProposal.model_validate(raw), None
    except ValidationError as error:
        return None, "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors())[:300]
