"""Enrutamiento del agente: decide a partir de la pregunta si hace falta el RAG,
una tool, o ambos, sin que el usuario indique la fuente.

Es un clasificador determinista por reglas (no un LLM): cada decisión queda en
el trace con su motivo, se puede testear exhaustivamente y no depende de la
calidad de tool-calling del modelo de generación.

Fuentes, en orden de ejecución: ``incidents`` → ``inventory`` → ``rag``.

* ``incidents``: la pregunta cita un ticket (UUID) o habla de tickets /
  incidencias / incidentes. Se extraen filtros (estado, categoría, origen, sede).
* ``inventory``: la pregunta cita un código SKU o pregunta por stock /
  existencias / unidades **de** un producto ("¿hay stock de…?", "unidades de…").
  "El stock que lleva seis meses sin venderse" NO activa la tool: es la política
  de inventario de larga duración (RAG).
* ``rag``: si no hay ninguna tool, o si además aparecen términos de política
  (ventana, SLA, tarifa, descuento, cobertura, Black Friday…).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal, NotRequired, TypedDict

from services.agent.tools import SKU_PATTERN

Source = Literal["incidents", "inventory", "rag"]


class SourcePlan(TypedDict):
    sources: list[Source]
    reasons: list[str]
    incident_query: NotRequired[dict[str, Any]]
    inventory_query: NotRequired[dict[str, Any]]
    rag_question: NotRequired[str]


def normalize(text: str) -> str:
    """Minúsculas y sin tildes, para que las reglas no dependan de la ortografía."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


UUID_PATTERN = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
INCIDENT_WORDS = re.compile(r"\b(ticket|tickets|incidencia|incidencias|incidente|incidentes)\b")
RAW_TICKET_REF = re.compile(r"\b(?:ticket|incidencia|incidente)\s*(?:n(?:umero|o|º)?\.?\s*)?#?\s*([0-9]{1,10})\b")
INVENTORY_PATTERNS = [
    re.compile(r"\b(stock|existencias|unidades)\s+(disponibles?\s+)?(de|del)\s+\w"),
    re.compile(r"\b(hay|tenemos|queda|quedan|cuanto|cuanta|cuantos|cuantas)\s+(stock|existencias|unidades)\b"),
]
POLICY_WORDS = re.compile(
    r"\b(politica|ventana|sla|plazo|plazos|tarifa|tarifas|precio|cuesta|cuestan|cobra|cobertura|cubre|descuento|"
    r"descuentos|compensacion|garantiza\w*|black friday|rebajas|navidad|express|internacional\w*|almacenamiento|"
    r"dias habiles|reacondicion\w*|procedimiento|proceso)\b"
)

STATUS_WORDS = [
    (re.compile(r"\b(abiert[oa]s?|pendientes?)\b"), "open"),
    (re.compile(r"\b(en curso|en progreso|en proceso)\b"), "in_progress"),
    (re.compile(r"\b(resuelt[oa]s?|cerrad[oa]s?)\b"), "resolved"),
    (re.compile(r"\b(descartad[oa]s?)\b"), "discarded"),
]
CATEGORY_WORDS = [
    (re.compile(r"\b(extraviad[oa]s?|perdid[oa]s?)\b"), "lost_parcel"),
    (re.compile(r"\b(fallo de entrega|entregas? fallidas?)\b"), "delivery_failure"),
    (re.compile(r"\bdiscrepancias?\b"), "inventory_discrepancy"),
    (re.compile(r"\b(de transportistas?|del transportista)\b"), "carrier_issue"),
    (re.compile(r"\bde devoluci(?:on|ones)\b"), "returns_issue"),
    (re.compile(r"\b(quejas?)\b"), "client_complaint"),
    (re.compile(r"\bde sistema\b"), "system_failure"),
]
ORIGIN_WORDS = [
    (re.compile(r"\b(de|del) clientes?\b"), "customer"),
    (re.compile(r"\binternas?\b"), "internal"),
]
BRANCH_WORDS = [
    (re.compile(r"\balmacen de los angeles\b"), "la_warehouse"),
    (re.compile(r"\boficina de los angeles\b"), "la_office"),
    (re.compile(r"\balmacen de zaragoza\b"), "zaragoza_warehouse"),
    (re.compile(r"\boficina de zaragoza\b"), "zaragoza_office"),
]
STOPWORDS = set(
    "que cual cuales como cuanto cuanta cuantos cuantas hay tenemos queda quedan stock existencias unidades disponible "
    "disponibles de del la el los las un una unos unas en para por con sin y o hoy ahora producto productos articulo "
    "articulos almacen almacenes los angeles zaragoza todavia aun".split()
)


def _first(rules, text: str):
    return next((value for pattern, value in rules if pattern.search(text)), None)


def _incident_query(question: str, text: str) -> dict[str, Any]:
    uuid_match = UUID_PATTERN.search(question)
    if uuid_match:
        return {"ticket_id": uuid_match.group(0).lower()}
    raw = RAW_TICKET_REF.search(text)
    if raw:
        return {"raw_ticket_reference": raw.group(1)}
    filters = {
        "status": _first(STATUS_WORDS, text),
        "category": _first(CATEGORY_WORDS, text),
        "origin": _first(ORIGIN_WORDS, text),
        "branch": _first(BRANCH_WORDS, text),
    }
    return {k: v for k, v in filters.items() if v}


def _inventory_query(question: str, text: str) -> dict[str, Any]:
    query: dict[str, Any] = {}
    sku = SKU_PATTERN.search(question.upper())
    if sku:
        query["sku"] = sku.group(0)
    else:
        query["name_terms"] = [w for w in re.findall(r"[a-z0-9]+", text) if len(w) >= 3 and w not in STOPWORDS]
    if "los angeles" in text:
        query["warehouse"] = "LA"
    elif "zaragoza" in text:
        query["warehouse"] = "ZGZ"
    return query


CLAUSE_SPLIT = re.compile(
    r"\s+y\s+(?=¿?(?:cu[aá]l|cu[aá]les|qu[eé]|c[oó]mo|cu[aá]nt[oa]s?|cu[aá]ndo|d[oó]nde|qui[eé]n|podemos|puedo|se)\b)|[;?]\s*",
    re.IGNORECASE,
)


def rag_subquestion(question: str) -> str:
    """Parte de política de una pregunta mixta, para que el RAG no busque con el UUID del ticket.

    "¿En qué estado está el ticket <uuid> y cuál es la ventana de devolución estándar?"
    → "¿Cuál es la ventana de devolución estándar?". Se conserva el texto original
    (mayúsculas y tildes): el modelo de embeddings es sensible a ellas y el RAG
    nunca las elimina. Con el UUID dentro, el mejor chunk puntuaba 0.419
    (< min_score 0.45); con la sub-pregunta, 0.57.
    """
    clauses = [c.strip(" ¿?,.") for c in CLAUSE_SPLIT.split(question) if c and c.strip(" ¿?,.")]
    policy = [c for c in clauses
              if POLICY_WORDS.search(normalize(c)) and not UUID_PATTERN.search(c) and not INCIDENT_WORDS.search(normalize(c))]
    if not policy:
        return UUID_PATTERN.sub("", question).strip()
    text = " y ".join(policy)
    return "¿" + text[0].upper() + text[1:] + "?"


def plan_sources(question: str) -> SourcePlan:
    text = normalize(question)
    sources: list[Source] = []
    reasons: list[str] = []
    plan: SourcePlan = {"sources": sources, "reasons": reasons}

    if UUID_PATTERN.search(question) or INCIDENT_WORDS.search(text):
        sources.append("incidents")
        reasons.append("cita un ticket o habla de incidencias → gestor de incidencias (dato operativo en vivo)")
        plan["incident_query"] = _incident_query(question, text)
    if SKU_PATTERN.search(question.upper()) or any(p.search(text) for p in INVENTORY_PATTERNS):
        sources.append("inventory")
        reasons.append("pregunta por stock de un producto → gestor de inventario (dato operativo en vivo)")
        plan["inventory_query"] = _inventory_query(question, text)
    if not sources:
        sources.append("rag")
        reasons.append("pregunta de políticas o procedimientos → base de conocimiento (RAG)")
    elif POLICY_WORDS.search(text):
        sources.append("rag")
        reasons.append("además menciona una política (SLA, devoluciones, tarifas, cobertura…) → también RAG")
        plan["rag_question"] = rag_subquestion(question)
    return plan
