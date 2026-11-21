"""Guardrails del agente de primera línea de CX de TrackFlow (Hito 8, Parte 2).

El agente protegido es el MISMO que ya existe: grafo LangGraph con RAG, tools vía MCP y memoria. Atiende a
clientes B2B (marcas) y B2C (destinatarios finales) de Estados Unidos (Los Ángeles) y España (Zaragoza), en el
área de Valentina Cruz (CX Manager).

Capas (todas deterministas, sin depender del LLM):

* ``check_input``: clasifica el mensaje ANTES de que llegue al modelo o a las tools.
  - seguridad: cambio de instrucciones o rol, extracción del system prompt, inyección de delimitadores,
    petición de datos que nunca se revelan, pedido/tracking que no pertenece a la sesión autenticada;
  - contenido: uso como chatbot personal (rechazo y redirección), mezcla de políticas entre países
    (se impone la del país real del pedido), small talk y logística general (respuesta breve y redirección);
  - estructural: caracteres de control o mensajes desmesurados.
* ``sanitize_external``: el texto de tools (MCP) y de documentos recuperados (RAG) es DATO, nunca
  instrucción. Se neutralizan instrucciones incrustadas y delimitadores antes de que lleguen al modelo.
* ``check_output``: valida la respuesta antes de devolverla (formato, sin instrucciones internas filtradas,
  sin datos sensibles del CONTEXT ni pedidos ajenos a la sesión).

Cada bloqueo o redirección se registra con su tipo de fallo (``estructural`` | ``contenido`` | ``seguridad``).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo

logger = logging.getLogger("trackflow.agent.guardrails")

FailureType = Literal["estructural", "contenido", "seguridad"]
Action = Literal["allow", "block", "redirect"]

PURPOSE = ("Soy el asistente de soporte de TrackFlow: puedo ayudarte con el estado de tus envíos, las políticas de "
           "devolución y los SLA de Estados Unidos y España, y con incidencias (paquete perdido, entrega fallida o "
           "dirección incorrecta).")
REDIRECT = "¿Hay algún envío, devolución o incidencia con el que te pueda ayudar?"

# Marcador secreto del system prompt: si aparece en una salida, el prompt se ha filtrado.
PROMPT_CANARY = "TF-CX-CANARY-7Q2X"
INTERNAL_MARKERS = ("<<DATOS_EXTERNOS", "<<FIN_DATOS_EXTERNOS", "<<MENSAJE_DEL_USUARIO", "<<FIN_MENSAJE_DEL_USUARIO",
                    "<<SESION", "<<FIN_SESION", "<<MEMORIA_APROBADA", "<<FIN_MEMORIA_APROBADA", PROMPT_CANARY)
MAX_INPUT_CHARS = 4000
MAX_OUTPUT_CHARS = 6000


def normalize(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (text or "").lower()) if unicodedata.category(c) != "Mn")


# ============================================================================
# Sesión autenticada
# ============================================================================
@dataclass
class SessionContext:
    """Quién pregunta. Lo construye el API a partir del usuario autenticado; el agente nunca lo amplía."""

    user: str | None = None
    customer_type: Literal["b2b", "b2c", "desconocido"] = "desconocido"
    # Pedidos/trackings de ESTA sesión → datos del envío (almacén, país, fecha, producto).
    authorized_orders: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_state(cls, data: dict[str, Any] | None) -> "SessionContext":
        data = data or {}
        return cls(user=data.get("user"), customer_type=data.get("customer_type", "desconocido"),
                   authorized_orders={k.upper(): v for k, v in (data.get("authorized_orders") or {}).items()})

    def to_state(self) -> dict[str, Any]:
        return {"user": self.user, "customer_type": self.customer_type, "authorized_orders": self.authorized_orders}


@dataclass
class GuardDecision:
    action: Action
    guard: str                       # nombre del guardrail que decidió
    failure_type: FailureType | None = None
    code: str | None = None
    reason: str = ""
    response: str | None = None      # respuesta determinista (bloqueo / redirección)
    rewritten_question: str | None = None
    notice: str | None = None        # aviso que se antepone a la respuesta final
    country_lock: str | None = None

    def to_state(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


# ============================================================================
# Patrones (es / en). Se aplican sobre el texto normalizado (minúsculas, sin tildes).
# ============================================================================
_INSTR = r"(instrucciones|reglas|indicaciones|directrices|normas|instructions|rules|guidelines|prompt|prompts)"
INSTRUCTION_OVERRIDE = [
    re.compile(rf"\b(ignora|ignore|olvida|olvidate de|forget|descarta|omite|disregard|salta(te)?|skip|anula|override)\b"
               rf"[^.?!]{{0,40}}\b{_INSTR}\b"),
    re.compile(r"\b(ahora eres|a partir de ahora eres|desde ahora eres|you are now|from now on you are|act as|actua como|"
               r"haz de cuenta que eres|finge (ser|que eres)|pretend (to be|you are)|roleplay as|juega a ser|"
               r"comportate como|behave as)\b"),
    re.compile(r"\b(sin (reglas|restricciones|filtros|limites|censura)|with no (rules|restrictions|filters)|no rules|"
               r"without (rules|restrictions|filters)|unrestricted|modo desarrollador|developer mode|jailbreak|"
               r"\bdan\b|do anything now)\b"),
    re.compile(r"\b(olvida(te)?( de)?( que trabajas (para|en))? (trackflow|la empresa|tu empresa|esta empresa)|"
               r"forget (that you work for |about )?(trackflow|the company|your company)|"
               r"ya no trabajas (para|en) (trackflow|la empresa|tu empresa|nadie)|deja de (ser|trabajar para) (el )?(asistente|agente|trackflow|la empresa)|"
               r"no eres (de|el asistente de) trackflow|you no longer work for)\b"),
    re.compile(r"\b(tus nuevas instrucciones|nuevas reglas|nueva regla|new instructions|your new rules|"
               r"cambia tus (instrucciones|reglas)|change your (instructions|rules)|update your (instructions|system prompt))\b"),
]
PROMPT_EXTRACTION = re.compile(
    r"\b(muestra|muestrame|revela|revelame|dime|imprime|repite|copia|ensena|show|print|reveal|repeat|output|tell me)\b"
    r"[^.?!]{0,30}\b(system prompt|prompt del sistema|mensaje del sistema|instrucciones (del sistema|internas|iniciales|ocultas)|"
    r"tus instrucciones|your (instructions|system prompt|initial prompt|hidden prompt)|las reglas que te dieron)\b")
DELIMITER_INJECTION = re.compile(
    r"(<\s*/?\s*(system|sistema|instructions|instrucciones|assistant)\s*>|\[\s*/?\s*(system|inst|sistema)\s*\]|"
    r"#{2,}\s*(system|sistema)\b|<<\s*/?\s*(fin_)?(datos_externos|mensaje_del_usuario|sesion|memoria_aprobada)|"
    r"\b(begin|end) (system|instructions)\b|<\|im_start\|>|<\|im_end\|>)")
SENSITIVE_REQUEST = [
    ("TARIFA_CARRIER", re.compile(
        r"\b(tarifa|tarifas|precio|precios|descuento|descuentos|cuanto (le )?(paga|pagamos|pagais|cobra)|rates?|discounts?)\b"
        r"[^.?!]{0,60}\b(ups|fedex|dhl|mrw|seur|carrier|carriers|transportista|transportistas)\b|"
        r"\b(ups|fedex|dhl|mrw|seur)\b[^.?!]{0,40}\b(tarifa|tarifas|precio|descuento|negociad\w*|pactad\w*|rates?)\b")),
    ("TERMINOS_COMERCIALES_B2B", re.compile(
        r"\b(terminos comerciales|condiciones comerciales|contrato (de|con) (la marca|el cliente|otra marca|[a-z]+lab|[a-z]+ footwear)|"
        r"cuanto paga (la marca|el cliente|[a-z]+lab)|descuento (de|que tiene) (la marca|el cliente|otra marca)|"
        r"tarifa (negociada|pactada|especial) (de|con|del) (la marca|el cliente|cliente))\b")),
    ("UBICACION_ALMACEN", re.compile(
        r"\b(direccion (exacta )?del almacen|donde esta (exactamente )?el almacen|ubicacion (exacta )?del almacen|"
        r"coordenadas del almacen|plano del almacen|rutas? internas?|pasillos? del almacen|codigo de acceso|"
        r"warehouse (address|location)|where is the warehouse|internal routes?)\b")),
]
PERSONAL_USE = re.compile(
    r"\b(poema|poemas|poesia|cancion|canciones|letra de (una )?cancion|soneto|cuento|novela|ensayo|ensayos|"
    r"redaccion (escolar|para clase)|tarea|tareas|deberes|examen|universidad|asignatura|trabajo de clase|"
    r"tesis|tfg|tfm|codigo|programa en|script|python|javascript|java\b|sql\b|html|receta|recetas|chiste|chistes|"
    r"horoscopo|carta de amor|consejo (amoroso|sentimental|personal|financiero|medico|legal)|mi novi[oa]|mi pareja|"
    r"dieta|rutina de ejercicio|plan de viaje|vacaciones|essay|homework|poem|love letter|write (me )?code)\b")
PERSONAL_VERBS = re.compile(r"\b(escribe|escribeme|redacta|haz|hazme|ayudame( a| con)?|dame|genera|crea|resuelve|write|help me|make me|give me)\b")
GREETING = re.compile(r"^(hola|buenas|buenos dias|buenas tardes|buenas noches|hey|hi|hello|que tal|como estas|"
                      r"como va|saludos|gracias|muchas gracias|thanks|thank you|adios|hasta luego|chao|bye)\b")
TIME_QUESTION = re.compile(r"\b(que hora es|hora es en|hora en|what time is it|time in)\b")
WEATHER_QUESTION = re.compile(r"\b(que tiempo hace|clima en|va a llover|temperatura en|weather)\b")
CITY_ZONES = {
    "tokio": "Asia/Tokyo", "tokyo": "Asia/Tokyo", "los angeles": "America/Los_Angeles", "zaragoza": "Europe/Madrid",
    "madrid": "Europe/Madrid", "barcelona": "Europe/Madrid", "nueva york": "America/New_York", "new york": "America/New_York",
    "londres": "Europe/London", "london": "Europe/London", "paris": "Europe/Paris", "berlin": "Europe/Berlin",
    "mexico": "America/Mexico_City", "ciudad de mexico": "America/Mexico_City", "buenos aires": "America/Argentina/Buenos_Aires",
    "bogota": "America/Bogota", "lima": "America/Lima", "sidney": "Australia/Sydney", "sydney": "Australia/Sydney",
}
LOGISTICS_GLOSSARY = {
    "logistica inversa": ("la logística inversa es el flujo de vuelta de un producto desde el cliente final hasta el "
                          "almacén (devoluciones, reparaciones, reciclaje).",
                          "En TrackFlow se aplica en el proceso de devoluciones: la marca configura las reglas, se genera la "
                          "etiqueta y el producto se inspecciona y clasifica al llegar al almacén."),
    "ultima milla": ("la última milla es el tramo final del envío, desde el centro de distribución hasta el destinatario.",
                     "TrackFlow gestiona la última milla en Los Ángeles y en Zaragoza con carriers distintos en cada país."),
    "cross docking": ("el cross-docking consiste en pasar la mercancía de un transporte de entrada a uno de salida casi "
                      "sin almacenarla.", "Si te interesa cómo lo aplicaría TrackFlow a tus envíos, puedo orientarte."),
    "fulfillment": ("el fulfillment es el conjunto de almacenaje, preparación y envío de pedidos en nombre de una marca.",
                    "Es el servicio que TrackFlow presta a sus marcas clientes en Los Ángeles y Zaragoza."),
    "3pl": ("un 3PL (third-party logistics) es un operador logístico externo que gestiona almacén y envíos de otra empresa.",
            "TrackFlow es un 3PL con operación en Estados Unidos y España."),
    "picking": ("el picking es la preparación de pedidos: recoger del almacén los productos de cada pedido.",
                "Si tienes una incidencia con un pedido preparado por TrackFlow, puedo ayudarte a gestionarla."),
    "sla": ("un SLA es el nivel de servicio comprometido, por ejemplo el plazo de entrega.",
            "TrackFlow tiene SLA de entrega distintos según el país; puedo explicarte el del tuyo."),
}
GENERAL_LOGISTICS_Q = re.compile(r"\b(que es|que son|que significa|en que consiste|explicame|definicion de|what is|what are)\b")
TRACKFLOW_SPECIFIC = re.compile(r"\b(trackflow|vuestr[oa]s?|tu politica|su politica|mi pedido|mi envio|mi paquete|nuestro)\b")
ORDER_REFERENCE = re.compile(
    r"(?:\b(?:pedido|pedidos|orden|order|tracking|trackings|envio|envios|paquete|seguimiento|numero de seguimiento|shipment)"
    r"\s*(?:n(?:umero|o|º|°)?\.?\s*)?(?:de\s+)?(?:#\s*)?|#\s*)([a-z0-9][a-z0-9-]{3,29})\b")
UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
NOT_AN_ORDER = {"anterior", "perdido", "perdida", "roto", "rota", "dañado", "danado", "extraviado", "entrega", "devolucion",
                "estado", "llego", "llega", "esta", "tiene", "para", "desde", "hasta", "este", "ese", "aqui", "cliente"}
COUNTRIES = {
    "ES": re.compile(r"\b(espana|espanola|espanol|zaragoza|madrid|barcelona|aragon|spain)\b"),
    "US": re.compile(r"\b(estados unidos|ee\.? ?uu\.?|eeuu|usa|los angeles|california|united states|us)\b"),
}
COUNTRY_NAMES = {"ES": "España (Zaragoza)", "US": "Estados Unidos (Los Ángeles)"}
POLICY_WORDS = re.compile(r"\b(politica|politicas|ventana|devolucion|devoluciones|sla|plazo|plazos|reembolso|policy|return)\b")
APPLY_OTHER_POLICY = re.compile(
    r"\b(aplica(me|r|le)?|usa(me|r)?|utiliza|quiero|dame|apply|use)\b[^.?!]{0,40}\b(la )?(politica|politicas|ventana|sla|reglas|policy)\b"
    r"[^.?!]{0,30}\b(de|del|of)\s+(?P<country>espana|zaragoza|estados unidos|ee\.? ?uu\.?|eeuu|usa|los angeles|spain|the us)\b")
ORDER_LOCATION = re.compile(
    r"\b(pedido|envio|paquete|compra|order)\b[^.?!]{0,25}\b(en|de|a|desde|in|from)\s+(?P<place>los angeles|zaragoza|espana|"
    r"estados unidos|ee\.? ?uu\.?|eeuu|usa|madrid|barcelona|california)\b")


def _country_of(text: str) -> str | None:
    found = [code for code, pattern in COUNTRIES.items() if pattern.search(text)]
    return found[0] if len(found) == 1 else None


def order_references(message: str) -> list[str]:
    """Números de pedido/tracking citados (no los UUID de tickets internos)."""
    norm = normalize(message)
    refs = []
    for match in ORDER_REFERENCE.finditer(norm):
        ref = match.group(1)
        if ref in NOT_AN_ORDER or UUID_RE.search(ref) or not re.search(r"\d", ref):
            continue
        refs.append(ref.upper().lstrip("#"))
    return list(dict.fromkeys(refs))


# ============================================================================
# Guarda de entrada
# ============================================================================
def check_input(message: str, session: SessionContext | None = None,
                now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> GuardDecision:
    session = session or SessionContext()
    raw = message or ""
    norm = normalize(raw)

    # --- estructural
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", raw):
        return GuardDecision("block", "input_structure", "estructural", "CARACTERES_DE_CONTROL",
                             "el mensaje contiene caracteres de control", f"No he podido procesar ese mensaje. {REDIRECT}")
    if len(raw) > MAX_INPUT_CHARS:
        return GuardDecision("block", "input_structure", "estructural", "MENSAJE_DEMASIADO_LARGO",
                             f"más de {MAX_INPUT_CHARS} caracteres", f"El mensaje es demasiado largo. {REDIRECT}")

    # --- seguridad: cambio de instrucciones / rol / extracción del prompt / delimitadores
    if DELIMITER_INJECTION.search(norm):
        return _instruction_change("INYECCION_DE_DELIMITADORES", "intenta abrir o cerrar bloques del sistema")
    if PROMPT_EXTRACTION.search(norm):
        return _instruction_change("EXTRACCION_DE_PROMPT", "pide revelar las instrucciones del sistema")
    for index, pattern in enumerate(INSTRUCTION_OVERRIDE):
        if pattern.search(norm):
            code = ["IGNORAR_INSTRUCCIONES", "CAMBIO_DE_ROL", "MODO_SIN_REGLAS", "OLVIDAR_TRACKFLOW", "NUEVAS_INSTRUCCIONES"][index]
            personal = " También es una tarea personal fuera de mi propósito." if PERSONAL_USE.search(norm) else ""
            return _instruction_change(code, "intenta cambiar las instrucciones o el rol del agente", extra=personal)

    # --- seguridad: datos que nunca se revelan
    for code, pattern in SENSITIVE_REQUEST:
        if pattern.search(norm):
            texts = {
                "TARIFA_CARRIER": "las tarifas o condiciones negociadas con los carriers (UPS, FedEx, DHL, MRW, SEUR)",
                "TERMINOS_COMERCIALES_B2B": "los términos comerciales entre TrackFlow y sus clientes",
                "UBICACION_ALMACEN": "la ubicación exacta ni las rutas internas de los almacenes",
            }
            return GuardDecision("block", "sensitive_data_request", "seguridad", code, f"pide {texts[code]}",
                                 f"No puedo compartir {texts[code]}: es información confidencial. {REDIRECT}")

    # --- seguridad: autorización por sesión (pedidos / trackings)
    refs = order_references(raw)
    unauthorized = [ref for ref in refs if ref not in session.authorized_orders]
    if unauthorized:
        return GuardDecision(
            "block", "order_authorization", "seguridad", "PEDIDO_NO_AUTORIZADO",
            f"pedido(s) {', '.join(unauthorized)} no asociados a la sesión autenticada",
            "No puedo darte información de ese pedido: no está asociado a la cuenta con la que has iniciado sesión. "
            "Por seguridad solo puedo consultar los envíos de tu propia cuenta. Si es tuyo, accede con la cuenta que "
            "realizó el pedido o contacta con el remitente.")
    if refs:
        return GuardDecision("redirect", "order_tracking", None, "TRACKING_AUTORIZADO", "pedido de la sesión",
                             response=_tracking_answer(refs, session))

    # --- contenido: uso como chatbot personal
    if PERSONAL_USE.search(norm) and (PERSONAL_VERBS.search(norm) or len(norm.split()) <= 8):
        return GuardDecision("block", "personal_use", "contenido", "USO_PERSONAL",
                             "tarea sin relación con envíos, devoluciones o incidencias",
                             f"No puedo ayudarte con eso: no está relacionado con envíos, devoluciones ni incidencias. "
                             f"{PURPOSE} {REDIRECT}")

    # --- contenido: mezcla de políticas entre países
    policy = _policy_country_lock(norm, session)
    if policy:
        return policy

    # --- contenido: small talk y logística general (respuesta breve + redirección obligatoria)
    small = _small_talk(norm, now)
    if small:
        return small
    if GENERAL_LOGISTICS_Q.search(norm) and not TRACKFLOW_SPECIFIC.search(norm):
        term = next((t for t in LOGISTICS_GLOSSARY if t in norm.replace("-", " ")), None)
        if term:
            definition, application = LOGISTICS_GLOSSARY[term]
            return GuardDecision("redirect", "general_logistics", "contenido", "LOGISTICA_GENERAL",
                                 f"pregunta general de logística: {term}",
                                 f"En pocas palabras, {definition} {application} {REDIRECT}")
    return GuardDecision("allow", "input_guard", reason="consulta dentro del dominio de soporte")


def _instruction_change(code: str, reason: str, extra: str = "") -> GuardDecision:
    return GuardDecision(
        "block", "instruction_change", "seguridad", code, reason,
        "No puedo cambiar mis instrucciones, mi rol ni dejar de trabajar para TrackFlow, y no comparto mis "
        f"instrucciones internas.{extra} {PURPOSE} {REDIRECT}")


def _tracking_answer(refs: list[str], session: SessionContext) -> str:
    lines = []
    for ref in refs:
        order = session.authorized_orders[ref]
        place = COUNTRY_NAMES.get(order.get("country", ""), order.get("warehouse", "almacén de TrackFlow"))
        when = order.get("dispatched_at", "")
        lines.append(f"Tu envío {ref}" + (f" ({order['product']})" if order.get("product") else "") +
                     f" salió del almacén de {place}" + (f" el {when[:10]}" if when else "") +
                     ". Para la posición en ruta, consulta el seguimiento del carrier con ese mismo número.")
    return "\n".join(lines) + f"\nSi hay un problema con la entrega, puedo ayudarte a abrir una incidencia."


def _policy_country_lock(norm: str, session: SessionContext) -> GuardDecision | None:
    wanted = APPLY_OTHER_POLICY.search(norm)
    if not wanted or not POLICY_WORDS.search(norm):
        return None
    requested = _country_of(wanted.group("country"))
    location = ORDER_LOCATION.search(norm)
    actual = _country_of(location.group("place")) if location else None
    if actual is None and len({o.get("country") for o in session.authorized_orders.values()} - {None}) == 1:
        actual = next(o["country"] for o in session.authorized_orders.values() if o.get("country"))
    if not requested or not actual or requested == actual:
        return None
    return GuardDecision(
        "redirect", "policy_country", "contenido", "MEZCLA_DE_POLITICAS",
        f"pide aplicar la política de {requested} a un pedido de {actual}",
        # Formulación medida contra el RAG real: recupera ventana, costes y proceso estándar (returns-policy#1, #2, #0)
        # y no el fragmento de devoluciones internacionales, que la versión corta atraía primero.
        rewritten_question=("¿Cuál es la política de devoluciones estándar (ventana de devolución, costes y proceso) para un "
                            f"pedido entregado en {COUNTRY_NAMES[actual]}?"),
        notice=(f"Tu pedido es de {COUNTRY_NAMES[actual]}, así que se rige por la política de ese país. No puedo aplicarle "
                f"la de {COUNTRY_NAMES[requested]}, aunque te convenga más: cada pedido se gestiona con la política del "
                "país donde se entrega."),
        country_lock=actual)


def _small_talk(norm: str, now: Callable[[], datetime]) -> GuardDecision | None:
    text = norm.strip(" ¿?¡!.")
    if TIME_QUESTION.search(text):
        city = next((c for c in sorted(CITY_ZONES, key=len, reverse=True) if c in text), None)
        if city:
            local = now().astimezone(ZoneInfo(CITY_ZONES[city]))
            answer = f"En {city.title()} son ahora las {local:%H:%M} ({local:%d/%m/%Y})."
        else:
            answer = f"Ahora son las {now():%H:%M} UTC."
        return GuardDecision("redirect", "small_talk", "contenido", "PREGUNTA_CASUAL", "pregunta casual: hora",
                             f"{answer} {REDIRECT}")
    if WEATHER_QUESTION.search(text):
        return GuardDecision("redirect", "small_talk", "contenido", "PREGUNTA_CASUAL", "pregunta casual: tiempo",
                             f"No tengo acceso a información meteorológica, aunque sí sé que el tiempo puede afectar a "
                             f"los plazos de entrega. {REDIRECT}")
    if GREETING.match(text) and len(text.split()) <= 6:
        return GuardDecision("redirect", "small_talk", "contenido", "SALUDO", "saludo o cortesía",
                             f"¡Hola! {PURPOSE} {REDIRECT}" if not text.startswith(("gracias", "muchas gracias", "thank", "adios", "hasta", "chao", "bye"))
                             else f"¡A ti! {REDIRECT}")
    return None


# ============================================================================
# Aislamiento del contenido externo (tools MCP y documentos RAG)
# ============================================================================
EMBEDDED_INSTRUCTION = re.compile(
    r"((ignora|ignore|olvida|forget|disregard)[^.\n]{0,60}(instrucciones|instructions|reglas|rules|prompt)|"
    r"(you are now|ahora eres|act as|actua como)[^.\n]{0,60}|"
    r"(system prompt|prompt del sistema)[^.\n]{0,40}|"
    r"(responde|answer|reply)[^.\n]{0,20}(siempre|always|solo|only)[^.\n]{0,60}|"
    r"<\s*/?\s*(system|sistema|instructions|assistant)\s*>|\[\s*/?\s*(system|inst)\s*\]|<\|im_(start|end)\|>)",
    re.IGNORECASE)


def sanitize_external(text: str) -> tuple[str, list[str]]:
    """Neutraliza instrucciones y delimitadores incrustados en texto de una tool o de un documento RAG."""
    findings: list[str] = []
    clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text or "")
    for marker in ("<<", ">>"):
        if marker in clean:
            findings.append("delimitador_interno")
            clean = clean.replace(marker, "«" if marker == "<<" else "»")
    if PROMPT_CANARY in clean:
        findings.append("canary")
        clean = clean.replace(PROMPT_CANARY, "[eliminado]")

    def _neutralize(match: re.Match) -> str:
        findings.append("instruccion_incrustada")
        return "[instrucción eliminada: contenido externo]"

    clean = EMBEDDED_INSTRUCTION.sub(_neutralize, clean)
    return clean, findings


def wrap_external(source: str, text: str) -> str:
    """Bloque de DATOS no confiables para el prompt (el system prompt explica que nunca son instrucciones)."""
    clean, _ = sanitize_external(text)
    return f"<<DATOS_EXTERNOS fuente=\"{source}\">>\n{clean}\n<<FIN_DATOS_EXTERNOS>>"


def wrap_user_message(text: str) -> str:
    """El mensaje del usuario como petición, sin poder cerrar el bloque ni abrir otros."""
    safe = (text or "").replace("<<", "«").replace(">>", "»")
    return f"<<MENSAJE_DEL_USUARIO>>\n{safe}\n<<FIN_MENSAJE_DEL_USUARIO>>"


# ============================================================================
# Validación de la salida
# ============================================================================
SENSITIVE_OUTPUT = [
    # Solo DATOS concretos: una cifra junto a un carrier, una ubicación con número… Mencionar el concepto en una
    # negativa («no puedo compartir las tarifas negociadas con UPS») no es una filtración.
    ("TARIFA_CARRIER", re.compile(
        r"\b(ups|fedex|dhl|mrw|seur)\b[^.\n]{0,60}\d+[.,]?\d*\s?(%|€|\$|usd|eur|euros|dolares|centimos)|"
        r"\d+[.,]?\d*\s?(%|€|\$|usd|eur|euros|dolares)[^.\n]{0,60}\b(ups|fedex|dhl|mrw|seur)\b")),
    ("UBICACION_ALMACEN", re.compile(
        r"\b(pasillo|estanteria|rack|muelle|anden|nave|puerta)\s*(n[ºo°.]*\s*)?\d+|"
        r"\b(codigo de acceso|clave de acceso)\s*:?\s*\w*\d|"
        r"\b(calle|avenida|avda|poligono|street|ave\.|blvd|boulevard)\b[^.\n]{0,40}\d+")),
    ("TERMINOS_COMERCIALES_B2B", re.compile(
        r"\b(contrato|tarifa negociada|descuento negociado|condiciones comerciales|paga)\b[^.\n]{0,40}\b(glowlab|purestep|soundwave)\b"
        r"[^.\n]{0,40}\d+[.,]?\d*\s?(%|€|\$|usd|eur|euros)")),
]


@dataclass
class OutputCheck:
    ok: bool
    failure_type: FailureType | None = None
    codes: list[str] = field(default_factory=list)
    detail: str = ""

    def to_state(self) -> dict[str, Any]:
        return {"ok": self.ok, "failure_type": self.failure_type, "codes": self.codes, "detail": self.detail}


SAFE_FALLBACK = ("No puedo darte esa respuesta tal como se generó porque incluía información que no debo compartir. "
                 f"{REDIRECT}")


def check_output(answer: str, session: SessionContext | None = None, system_prompt: str = "") -> OutputCheck:
    session = session or SessionContext()
    text = answer or ""
    norm = normalize(text)
    if not text.strip():
        return OutputCheck(False, "estructural", ["RESPUESTA_VACIA"], "respuesta vacía")
    if len(text) > MAX_OUTPUT_CHARS:
        return OutputCheck(False, "estructural", ["RESPUESTA_DEMASIADO_LARGA"], f"{len(text)} caracteres")
    leaked = [m for m in INTERNAL_MARKERS if m.lower() in text.lower()]
    if leaked or (system_prompt and _overlaps_prompt(norm, system_prompt)):
        return OutputCheck(False, "seguridad", ["FILTRACION_DE_INSTRUCCIONES"], f"marcadores internos: {leaked or 'texto del system prompt'}")
    codes = [code for code, pattern in SENSITIVE_OUTPUT if pattern.search(norm)]
    foreign = [ref for ref in order_references(text) if ref not in session.authorized_orders]
    if foreign:
        codes.append("PEDIDO_DE_OTRA_SESION")
    if codes:
        return OutputCheck(False, "seguridad", codes, f"datos sensibles: {codes}")
    if text.count("```") % 2 == 1:
        return OutputCheck(False, "estructural", ["FORMATO_INVALIDO"], "bloque de código sin cerrar")
    return OutputCheck(True)


def _overlaps_prompt(norm_answer: str, system_prompt: str) -> bool:
    """¿La respuesta reproduce frases del system prompt? (3 o más líneas distintivas de ≥ 40 caracteres)."""
    lines = [normalize(line).strip(" -•*") for line in system_prompt.splitlines()]
    distinctive = [line for line in lines if len(line) >= 40]
    return sum(1 for line in distinctive if line[:60] in norm_answer) >= 3


# ============================================================================
# Observabilidad
# ============================================================================
def log_event(event: dict[str, Any]) -> None:
    logger.info(json.dumps({"event": "guardrail", **event}, ensure_ascii=False, default=str))
