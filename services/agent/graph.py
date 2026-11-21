"""Agente de soporte de TrackFlow como grafo de LangGraph (Partes 1, 2 y migración a MCP).

Parte 1: el RAG de ``mock_data/pipelines/rag.py`` (el ``data/pipelines/`` del
enunciado) vive en nodos con una sola responsabilidad. El nodo ``retrieve``
llama a ``retrieve()`` y el nodo ``generate`` llama a
``generate_answer(question, context)`` con el contexto ya recuperado; ``query()``
no se usa.

Parte 2: tools de solo lectura sobre los servicios reales. El nodo
``plan_sources`` decide a partir de la pregunta si hace falta el RAG, alguna
tool o ambos (``services/agent/routing.py``), y las aristas condicionales
recorren las fuentes planificadas en orden: incidencias → inventario → RAG.

Migración a MCP: los nodos de tools son ahora CLIENTES del servidor MCP de
TrackFlow (``mcps/trackflow_mcp``). ``mcp_incidents`` y ``mcp_inventory``
invocan las tools ``incidents_get_ticket`` / ``incidents_list_tickets`` e
``inventory_query_products``, cargadas con ``langchain-mcp-adapters``
(``services/agent/mcp_client.py``) y autenticadas con OAuth 2.1. Sustituyen a
``lookup_incidents`` / ``lookup_inventory``, que llamaban por HTTP directo a
``services/api``; esa implementación se eliminó. El enrutamiento no cambia.

Flujo::

    START → receive_question ─┬─ inválida → invalid_question → END
                              └─ válida   → plan_sources ─┬─→ mcp_incidents ─┬─→ mcp_inventory ─┬─→ retrieve
                                                          ├─→ mcp_inventory  ├─→ retrieve       └─→ compose_answer
                                                          └─→ retrieve       └─→ compose_answer
    retrieve ─┬─ hay contexto ≥ min_score ─────────→ generate ─┬─ hubo tools → compose_answer → END
              ├─ sin contexto, pero hubo tools ────→ compose_answer         └─ solo RAG ──────────→ END
              └─ sin contexto y sin tools ─────────→ no_context → END

Estado mínimo (``AgentState``): pregunta, plan de fuentes, resultados de cada
fuente y respuesta. No viaja el historial de conversación ni credenciales: el
access token OAuth del agente vive solo en el cliente MCP (no queda en los
checkpoints ni en los traces).
"""

from __future__ import annotations

import inspect
import logging
import os
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, NotRequired, TypedDict, get_type_hints

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langgraph.graph import END, START, StateGraph  # noqa: E402

from mock_data.pipelines.rag import (  # noqa: E402
    DEFAULT_MIN_SCORE,
    DEFAULT_TOP_K,
    NO_CONTEXT_ANSWER,
    generate_answer,
    retrieve,
)
from services.agent import answers  # noqa: E402
from services.agent.routing import SourcePlan, plan_sources as plan_question_sources  # noqa: E402
from services.agent.tools import (  # noqa: E402
    IncidentQuery,
    IncidentToolResult,
    InventoryQuery,
    InventoryToolResult,
    McpCaller,
    ReadOnlyViolation,
    default_mcp,
    lookup_incidents,
    lookup_inventory,
)
from services.agent.memory import (  # noqa: E402
    MemoryEntry,
    MemoryStore,
    MemoryWriteRejected,
    PendingAlreadyOpen,
    check_proposal,
    classify_pending_reply,
    enrich_proposal,
    evaluate_memory_gate,
    parse_proposal,
)
from services.agent.guardrails import (  # noqa: E402
    SAFE_FALLBACK,
    SessionContext,
    check_input,
    check_output,
    sanitize_external,
)
from services.agent.memory_llm import generate_with_memory, reflect_memory  # noqa: E402
from services.agent.prompts import CX_SYSTEM_PROMPT, generate_cx_answer  # noqa: E402
from services.agent.tracing import TraceStore, _now  # noqa: E402

logger = logging.getLogger("trackflow.agent")

QUESTION_MIN_LENGTH = 3
QUESTION_MAX_LENGTH = 1000
INVALID_QUESTION_ANSWER = (
    "La pregunta está vacía o no es válida. Escribe una pregunta sobre SLA de entrega, política de "
    "devoluciones, cobertura de transportistas, tarifas de almacenamiento, el estado de un ticket o el stock "
    "de un producto de TrackFlow."
)
SOURCE_BY_NODE = {"mcp_incidents": "incidents", "mcp_inventory": "inventory", "retrieve": "rag"}


# --------------------------------------------------------------------------
# Estado
# --------------------------------------------------------------------------
class RetrievedChunk(TypedDict):
    """Payload que devuelve ``retrieve()`` (campos del CONTEXT del Hito 7 + ``score``)."""

    company: str
    source_document: str
    section: str
    language: str
    chunk_index: int
    text: str
    score: float


class AgentState(TypedDict):
    question: str
    session_id: NotRequired[str]                         # conversación (para la propuesta pendiente)
    session: NotRequired[dict[str, Any]]                 # SessionContext: usuario autenticado y SUS pedidos
    guard: NotRequired[dict[str, Any]]                   # decisión de la guarda de entrada
    output_check: NotRequired[dict[str, Any]]            # validación de la salida
    memory_resolution: NotRequired[dict[str, Any]]       # cómo respondió el usuario a la propuesta pendiente
    error: NotRequired[Literal["empty_question", "question_too_long"]]
    plan: NotRequired[SourcePlan]
    incident_result: NotRequired[dict[str, Any]]   # IncidentToolResult serializado
    inventory_result: NotRequired[dict[str, Any]]  # InventoryToolResult serializado
    context: NotRequired[list[RetrievedChunk]]
    answer: NotRequired[str]
    # Memoria (Hito 8): lo recordado, la decisión de auto-evaluación y la propuesta.
    memory_gate: NotRequired[dict[str, Any]]            # MemoryGate: ¿hay algo nuevo o corregido que valga recordar?
    memories: NotRequired[list[dict[str, Any]]]          # MemoryEntry relevantes (lectura explícita)
    memory_evaluated_by: NotRequired[Literal["reflect"]]
    memory_proposal_raw: NotRequired[Any]                # campo propuesta_memoria tal como lo devolvió el modelo
    memory_proposal: NotRequired[dict[str, Any] | None]  # propuesta pendiente (id, tipo, hecho…) o None
    memory_rejection: NotRequired[dict[str, Any]]        # propuesta descartada por los guardrails (códigos)


def _has_tool_results(state: AgentState) -> bool:
    return "incident_result" in state or "inventory_result" in state


def _planned(state: AgentState) -> list[str]:
    return state.get("plan", {}).get("sources", [])


# --------------------------------------------------------------------------
# Guardrails (Hito 8, Parte 2): entrada, aislamiento del contenido externo y salida
# --------------------------------------------------------------------------
GuardLog = Callable[..., None]


def make_guard_log(traces: TraceStore | None) -> GuardLog:
    def guard_log(state: AgentState, config, *, guard: str, action: str, failure_type: str | None,
                  code: str | None, detail: str = "") -> None:
        if traces is not None:
            traces.record_guardrail(run_id=config["configurable"]["thread_id"], session_id=state.get("session_id"),
                                    guard=guard, action=action, failure_type=failure_type, code=code, detail=detail)

    return guard_log


def make_input_guard_node(guard_log: GuardLog, clock: Callable[[], Any] | None = None):
    def input_guard(state: AgentState, config) -> dict[str, Any]:
        """Primera capa: seguridad (cambio de instrucciones, datos sensibles, autorización por sesión), contenido
        (uso personal, mezcla de políticas por país, small talk) y estructura, ANTES de tocar el modelo o las tools."""
        kwargs = {"now": clock} if clock else {}
        decision = check_input(state["question"], SessionContext.from_state(state.get("session")), **kwargs)
        update: dict[str, Any] = {"guard": decision.to_state()}
        if decision.rewritten_question:
            update["question"] = decision.rewritten_question
        if decision.action != "allow":
            guard_log(state, config, guard=decision.guard, action=decision.action, failure_type=decision.failure_type,
                      code=decision.code, detail=decision.reason)
        return update

    return input_guard


def guard_response(state: AgentState) -> dict[str, Any]:
    """Respuesta determinista de un guardrail (rechazo o redirección): el modelo no interviene."""
    return {"answer": state["guard"]["response"]}


def make_output_guard_node(guard_log: GuardLog):
    def output_guard(state: AgentState, config) -> dict[str, Any]:
        """Última capa: valida formato, filtraciones de instrucciones internas y datos sensibles antes de responder."""
        answer = state.get("answer") or ""
        notice = (state.get("guard") or {}).get("notice")
        if notice:
            answer = f"{notice}\n\n{answer}"
        check = check_output(answer, SessionContext.from_state(state.get("session")), CX_SYSTEM_PROMPT)
        if not check.ok:
            guard_log(state, config, guard="output_validation", action="replace", failure_type=check.failure_type,
                      code=",".join(check.codes), detail=check.detail)
            answer = SAFE_FALLBACK
        return {"answer": answer, "output_check": check.to_state()}

    return output_guard


def _sanitize_fields(items: list[dict[str, Any]], fields: tuple[str, ...]) -> tuple[list[dict[str, Any]], list[str]]:
    findings: list[str] = []
    for item in items:
        for name in fields:
            if isinstance(item.get(name), str):
                item[name], found = sanitize_external(item[name])
                findings += found
    return items, findings


# --------------------------------------------------------------------------
# Nodos (una sola responsabilidad cada uno)
# --------------------------------------------------------------------------
def receive_question(state: AgentState) -> dict[str, Any]:
    """Normaliza la pregunta y marca si no es válida (no decide el destino)."""
    question = " ".join((state.get("question") or "").split())
    if len(question) < QUESTION_MIN_LENGTH:
        return {"question": question, "error": "empty_question"}
    if len(question) > QUESTION_MAX_LENGTH:
        return {"question": question[:QUESTION_MAX_LENGTH], "error": "question_too_long"}
    return {"question": question}


def plan_sources(state: AgentState) -> dict[str, Any]:
    """Decide qué fuentes necesita la pregunta (RAG, tools o ambas) y con qué parámetros."""
    return {"plan": plan_question_sources(state["question"])}


def make_incidents_node(lookup_fn: Callable[..., IncidentToolResult], mcp: Callable[[], McpCaller],
                        guard_log: GuardLog | None = None):
    def mcp_incidents_node(state: AgentState, config) -> dict[str, Any]:
        """Cliente MCP: consulta el Incidents Manager con las tools del servidor MCP. Nunca rompe la corrida."""
        query = IncidentQuery.model_validate(state["plan"].get("incident_query", {}))
        try:
            result = lookup_fn(query, mcp=mcp())
        except ReadOnlyViolation:
            raise
        except Exception:
            logger.exception("mcp_incidents falló de forma inesperada")
            result = IncidentToolResult(ok=False, mode="detail" if query.ticket_id else "list", query=query,
                                        error_type="unavailable")
        data = result.model_dump(mode="json")
        # El texto de los tickets lo escriben clientes y operadores: es DATO, nunca instrucción.
        data["incidents"], findings = _sanitize_fields(data.get("incidents") or [], ("title",))
        if findings and guard_log:
            guard_log(state, config, guard="external_content", action="sanitize", failure_type="seguridad",
                      code="INSTRUCCION_EN_TOOL", detail=f"mcp:incidents {sorted(set(findings))}")
        return {"incident_result": data}

    return mcp_incidents_node


def make_inventory_node(lookup_fn: Callable[..., InventoryToolResult], mcp: Callable[[], McpCaller],
                        guard_log: GuardLog | None = None):
    def mcp_inventory_node(state: AgentState, config) -> dict[str, Any]:
        """Cliente MCP: consulta el stock con la tool de inventario (solo lectura) del servidor MCP."""
        query = InventoryQuery.model_validate(state["plan"].get("inventory_query", {}))
        try:
            result = lookup_fn(query, mcp=mcp())
        except ReadOnlyViolation:
            raise
        except Exception:
            logger.exception("mcp_inventory falló de forma inesperada")
            result = InventoryToolResult(ok=False, query=query, error_type="unavailable")
        data = result.model_dump(mode="json")
        data["items"], findings = _sanitize_fields(data.get("items") or [], ("name", "client_name"))
        if findings and guard_log:
            guard_log(state, config, guard="external_content", action="sanitize", failure_type="seguridad",
                      code="INSTRUCCION_EN_TOOL", detail=f"mcp:inventory {sorted(set(findings))}")
        return {"inventory_result": data}

    return mcp_inventory_node


def make_retrieve_node(retrieve_fn: Callable[..., list[dict[str, Any]]], guard_log: GuardLog | None = None):
    def retrieve_node(state: AgentState, config) -> dict[str, Any]:
        """Recupera chunks de ``trackflow_knowledge`` con el umbral calibrado y los sanea: son DATOS, no instrucciones."""
        top_k = int(os.environ.get("RAG_TOP_K", DEFAULT_TOP_K))
        min_score = float(os.environ.get("RAG_MIN_SCORE", DEFAULT_MIN_SCORE))
        # En preguntas mixtas (tool + RAG) se busca solo con la parte de política.
        question = state.get("plan", {}).get("rag_question") or state["question"]
        chunks, findings = _sanitize_fields([dict(c) for c in retrieve_fn(question, k=top_k, min_score=min_score)], ("text",))
        if findings and guard_log:
            guard_log(state, config, guard="external_content", action="sanitize", failure_type="seguridad",
                      code="INSTRUCCION_EN_DOCUMENTO", detail=f"rag {sorted(set(findings))}")
        return {"context": chunks}

    return retrieve_node


RESOLUTION_ACKS = {
    "aprobada": "✅ Memoria actualizada: «{hecho}». Lo tendré en cuenta en las próximas conversaciones.",
    "editada_y_aprobada": "✅ Memoria actualizada con tu corrección: «{hecho}».",
    "rechazada": "De acuerdo, no lo guardo: la memoria queda sin cambios.",
    "rechazada_por_guardrails": "No puedo guardar eso en memoria ({codes}); la memoria queda sin cambios.",
    "descartada_sin_respuesta_clara": "(Descarté la propuesta de memoria anterior porque no respondiste sí o no; no se guardó nada.)",
}


def make_resolve_node(memory_store: Callable[[], MemoryStore | None]):
    def resolve_pending_memory(state: AgentState, config) -> dict[str, Any]:
        """Si la conversación tiene una propuesta pendiente, el mensaje se evalúa PRIMERO contra ella.

        Clasificación explícita (``classify_pending_reply``): aprobar, rechazar, editar u otro_tema. Lo que no es
        un sí o un no claro descarta la propuesta: nunca se aprueba por silencio ni por ambigüedad. El resto
        del mensaje continúa por el grafo con normalidad.
        """
        store, session = memory_store(), state.get("session_id")
        if store is None or not session:
            return {}
        try:
            pending = store.session_pending(session)
        except Exception:  # noqa: BLE001
            logger.exception("no se pudo leer la propuesta pendiente")
            return {}
        if pending is None:
            return {}
        intent = classify_pending_reply(state["question"])
        user = config["configurable"].get("user") or f"sesion:{session}"
        hecho, codes = pending.proposal.hecho, ""
        try:
            if intent.label == "aprobar":
                store.confirm(pending.id, approved_by=user, reply=state["question"])
                outcome = "aprobada"
            elif intent.label == "editar":
                store.edit_and_confirm(pending.id, intent.edited_fact or hecho, approved_by=user, reply=state["question"])
                outcome, hecho = "editada_y_aprobada", intent.edited_fact or hecho
            elif intent.label == "rechazar":
                store.reject(pending.id, rejected_by=user, reply=state["question"])
                outcome = "rechazada"
            else:
                store.discard(pending.id, reason=intent.rationale, reply=state["question"])
                outcome = "descartada_sin_respuesta_clara"
        except MemoryWriteRejected as error:
            outcome, codes = "rechazada_por_guardrails", ", ".join(sorted({v.code for v in error.violations}))
        resolution = {"proposal_id": pending.id, "label": intent.label, "outcome": outcome, "rationale": intent.rationale,
                      "ack": RESOLUTION_ACKS[outcome].format(hecho=hecho, codes=codes), "remainder": intent.remainder}
        return {"memory_resolution": resolution, "question": intent.remainder or state["question"]}

    return resolve_pending_memory


def memory_acknowledge(state: AgentState) -> dict[str, Any]:
    """El mensaje solo respondía a la propuesta ("sí", "no, gracias"): se confirma el resultado y termina."""
    return {"answer": state["memory_resolution"]["ack"]}


def make_recall_node(memory_store: Callable[[], MemoryStore | None]):
    def recall_memory(state: AgentState) -> dict[str, Any]:
        """Lectura explícita de memoria (solo lo relevante) + criterio de auto-evaluación para esta interacción."""
        gate = evaluate_memory_gate(state["question"])
        store, memories = memory_store(), []
        if store is not None:
            try:
                memories = [m.model_dump() for m in store.recall(state["question"])]
            except Exception:  # noqa: BLE001 — sin memoria se responde igual que antes
                logger.exception("recall de memoria no disponible")
        return {"memory_gate": gate.model_dump(), "memories": memories}

    return recall_memory


def make_generate_node(generate_fn: Callable[[str, list[dict[str, Any]]], str], memory_generate_fn: Callable[..., str]):
    def generate_node(state: AgentState) -> dict[str, Any]:
        """Genera la respuesta SOLO a partir del contexto recuperado y, si la hay, de la memoria aprobada relevante."""
        question = state.get("plan", {}).get("rag_question") or state["question"]
        memories = [MemoryEntry.model_validate(m) for m in state.get("memories") or []]
        if not memories:
            return {"answer": generate_fn(question, state.get("context") or [])}
        return {"answer": memory_generate_fn(question, state.get("context") or [], memories)}

    return generate_node


def statement_ack(state: AgentState) -> str:
    """Respuesta determinista a un mensaje que aporta un dato (puerta de memoria abierta, sin tools).

    El agente NO da por cierta una corrección que nadie ha aprobado (defensa contra el envenenamiento): acusa
    recibo, muestra lo que dice hoy la base de conocimiento y, si procede, propone recordarlo.
    """
    lines = ["Tomo nota de lo que indicas. Todavía no está verificado ni guardado en memoria."]
    for chunk in (state.get("context") or [])[:2]:
        lines.append(f"Lo que dice hoy la base de conocimiento: «{' '.join(chunk['text'].split())[:280]}» "
                     f"({chunk['source_document']} — {chunk['section']}).")
    for memory in state.get("memories") or []:
        lines += [f"Memoria aprobada relacionada: {line}" for line in MemoryEntry.model_validate(memory).lines()]
    return "\n".join(lines)


def compose_answer(state: AgentState) -> dict[str, Any]:
    """Une, en orden de ejecución, los resultados de las tools (texto determinista), la parte del RAG y,
    si la hay, la memoria aprobada relevante (para no re-escalar un incidente ya explicado)."""
    rag_answer = state.get("answer")
    if state.get("memory_gate", {}).get("open") and state.get("context"):
        rag_answer = "\n".join(line for line in statement_ack({"context": state["context"]}).splitlines()[1:])
    answer = answers.compose(
        state.get("incident_result"), state.get("inventory_result"),
        rag_answer=rag_answer, rag_planned="rag" in _planned(state), no_context_answer=NO_CONTEXT_ANSWER,
    )
    if state.get("memories") and not state.get("context"):
        lines = [line for m in state["memories"] for line in MemoryEntry.model_validate(m).lines()]
        answer += "\n\nContexto recordado (memoria aprobada por el equipo):\n" + "\n".join(f"- {line}" for line in lines)
    return {"answer": answer}


MEMORY_TYPE_LABELS = {
    "regla_carrier": "regla de carrier",
    "contexto_incidente_recurrente": "contexto de incidente recurrente",
    "preferencia_reporte_b2b": "preferencia de reporte B2B",
}


def make_propose_node(memory_store: Callable[[], MemoryStore | None], reflect_fn: Callable[..., Any]):
    def propose_memory(state: AgentState, config) -> dict[str, Any]:
        """Auto-evaluación: valida la propuesta del modelo y, si procede, la PROPONE al usuario al final de la respuesta.

        Nunca escribe en memoria: guarda una propuesta pendiente que solo un usuario puede confirmar.
        """
        gate = state.get("memory_gate") or {}
        resolution = state.get("memory_resolution")
        ack = resolution["ack"] + "\n\n" if resolution else ""
        update: dict[str, Any] = {"memory_proposal": None}
        base = state.get("answer")
        if gate.get("open") and base is None:
            base = statement_ack(state)  # el mensaje aporta un dato: acuse determinista, sin dar nada por cierto
        base = base or ""
        if ack or base != state.get("answer"):
            update["answer"] = ack + base
        if not gate.get("open"):
            return update
        # Auto-evaluación: la ÚNICA llamada al modelo de esta interacción, con salida estructurada propuesta_memoria.
        try:
            raw = reflect_fn(state["question"], base, [MemoryEntry.model_validate(m) for m in state.get("memories") or []],
                             gate=gate)
        except Exception as error:  # noqa: BLE001 — la auto-evaluación nunca rompe la respuesta
            logger.warning("auto-evaluación de memoria no disponible: %r", error)
            return update | {"memory_evaluated_by": "reflect",
                             "memory_rejection": {"codes": ["AUTOEVALUACION_NO_DISPONIBLE"], "detail": type(error).__name__}}
        update |= {"memory_evaluated_by": "reflect", "memory_proposal_raw": raw}
        raw, filled = enrich_proposal(raw, state["question"], gate)
        if filled:  # queda en el trace qué claves completó el código (el modelo decidió y redactó el hecho)
            update["memory_proposal_raw"] = {**raw, "_campos_completados_por_el_sistema": filled}
        proposal, parse_error = parse_proposal(raw)
        if proposal is None:
            if parse_error:
                update["memory_rejection"] = {"codes": ["PROPUESTA_MAL_FORMADA"], "detail": parse_error}
            return update  # el modelo decidió que no hay nada que recordar
        violations = check_proposal(proposal, state["question"])
        if violations:
            return update | {"memory_rejection": {"codes": sorted({v.code for v in violations}),
                                                  "violations": [v.model_dump() for v in violations],
                                                  "proposal": proposal.model_dump()}}
        store = memory_store()
        if store is None:
            return update | {"memory_rejection": {"codes": ["MEMORIA_NO_DISPONIBLE"], "proposal": proposal.model_dump()}}
        try:
            pending = store.save_pending(proposal, state["question"], config["configurable"]["thread_id"],
                                         session_id=state.get("session_id"))
        except PendingAlreadyOpen:
            # Solo una propuesta pendiente a la vez: no se lanza otra hasta cerrar la primera.
            return update | {"memory_rejection": {"codes": ["PROPUESTA_PENDIENTE_ABIERTA"], "proposal": proposal.model_dump()}}
        except Exception as error:  # noqa: BLE001
            logger.warning("no se pudo guardar la propuesta de memoria: %r", error)
            return update | {"memory_rejection": {"codes": ["MEMORIA_NO_DISPONIBLE"], "proposal": proposal.model_dump()}}
        question = (f"\n\n📌 Propuesta de memoria ({MEMORY_TYPE_LABELS[proposal.tipo]}): «{proposal.hecho}». "
                    f"Motivo: {proposal.motivo}. ¿Quieres que lo recuerde para las próximas conversaciones? "
                    f"(propuesta {pending.id}; no se guarda nada hasta que la confirmes)")
        return update | {"answer": ack + base + question,
                         "memory_proposal": {"id": pending.id, **proposal.model_dump()}}

    return propose_memory


def no_context(state: AgentState) -> dict[str, Any]:
    """Respuesta honesta cuando ningún chunk supera ``min_score``: no se fuerza la generación."""
    return {"answer": NO_CONTEXT_ANSWER}


def invalid_question(state: AgentState) -> dict[str, Any]:
    return {"answer": INVALID_QUESTION_ANSWER}


# Claves del estado que cada nodo puede escribir (se valida al compilar y al ejecutar).
NODE_WRITES: dict[str, frozenset[str]] = {
    "receive_question": frozenset({"question", "error"}),
    "plan_sources": frozenset({"plan"}),
    "mcp_incidents": frozenset({"incident_result"}),
    "mcp_inventory": frozenset({"inventory_result"}),
    "retrieve": frozenset({"context"}),
    "resolve_pending_memory": frozenset({"memory_resolution", "question"}),
    "memory_acknowledge": frozenset({"answer"}),
    "input_guard": frozenset({"guard", "question"}),
    "guard_response": frozenset({"answer"}),
    "output_guard": frozenset({"answer", "output_check"}),
    "recall_memory": frozenset({"memory_gate", "memories"}),
    "generate": frozenset({"answer"}),
    "propose_memory": frozenset({"answer", "memory_evaluated_by", "memory_proposal_raw", "memory_proposal", "memory_rejection"}),
    "compose_answer": frozenset({"answer"}),
    "no_context": frozenset({"answer"}),
    "invalid_question": frozenset({"answer"}),
}


# --------------------------------------------------------------------------
# Aristas condicionales
# --------------------------------------------------------------------------
def route_after_input_guard(state: AgentState) -> Literal["guard_response", "resolve_pending_memory"]:
    return "guard_response" if (state.get("guard") or {}).get("response") else "resolve_pending_memory"


def route_after_resolve(state: AgentState) -> Literal["memory_acknowledge", "receive_question"]:
    resolution = state.get("memory_resolution")
    return "memory_acknowledge" if resolution and not resolution["remainder"].strip() else "receive_question"


def route_after_receive(state: AgentState) -> Literal["invalid_question", "recall_memory"]:
    return "invalid_question" if state.get("error") else "recall_memory"


def route_after_plan(state: AgentState) -> Literal["mcp_incidents", "mcp_inventory", "retrieve"]:
    planned = _planned(state)
    if "incidents" in planned:
        return "mcp_incidents"
    if "inventory" in planned:
        return "mcp_inventory"
    return "retrieve"


def route_after_incidents(state: AgentState) -> Literal["mcp_inventory", "retrieve", "compose_answer"]:
    planned = _planned(state)
    if "inventory" in planned:
        return "mcp_inventory"
    return "retrieve" if "rag" in planned else "compose_answer"


def route_after_inventory(state: AgentState) -> Literal["retrieve", "compose_answer"]:
    return "retrieve" if "rag" in _planned(state) else "compose_answer"


def route_after_retrieve(state: AgentState) -> Literal["generate", "compose_answer", "no_context", "propose_memory"]:
    gate_open = bool(state.get("memory_gate", {}).get("open"))
    if gate_open:
        # El mensaje aporta un dato: no se redacta texto libre (no se da por cierto) y la única llamada al
        # modelo es la auto-evaluación de propose_memory.
        return "compose_answer" if _has_tool_results(state) else "propose_memory"
    if state.get("context") or (state.get("memories") and not _has_tool_results(state)):
        return "generate"
    return "compose_answer" if _has_tool_results(state) else "no_context"


def route_after_generate(state: AgentState) -> Literal["compose_answer", "propose_memory"]:
    return "compose_answer" if _has_tool_results(state) else "propose_memory"


ROUTE_REASONS = {
    ("route_after_input_guard", "guard_response"): "un guardrail bloquea o redirige la petición (respuesta determinista)",
    ("route_after_input_guard", "resolve_pending_memory"): "petición dentro del dominio (o reescrita por la guarda)",
    ("route_after_resolve", "memory_acknowledge"): "el mensaje solo respondía a la propuesta de memoria pendiente",
    ("route_after_resolve", "receive_question"): "no hay propuesta pendiente, o el mensaje sigue con otra pregunta",
    ("route_after_receive", "invalid_question"): "pregunta vacía o demasiado larga",
    ("route_after_receive", "recall_memory"): "pregunta válida",
    ("route_after_plan", "mcp_incidents"): "el plan incluye el gestor de incidencias (vía servidor MCP)",
    ("route_after_plan", "mcp_inventory"): "el plan incluye el gestor de inventario (vía servidor MCP)",
    ("route_after_plan", "retrieve"): "el plan solo necesita la base de conocimiento",
    ("route_after_incidents", "mcp_inventory"): "falta consultar inventario",
    ("route_after_incidents", "retrieve"): "falta consultar la base de conocimiento",
    ("route_after_incidents", "compose_answer"): "no hay más fuentes planificadas",
    ("route_after_inventory", "retrieve"): "falta consultar la base de conocimiento",
    ("route_after_inventory", "compose_answer"): "no hay más fuentes planificadas",
    ("route_after_retrieve", "generate"): "hay contexto ≥ min_score o memoria aprobada relevante",
    ("route_after_retrieve", "propose_memory"): "el mensaje aporta un dato memorizable: auto-evaluación y acuse sin darlo por cierto",
    ("route_after_retrieve", "compose_answer"): "sin contexto ≥ min_score; se responde con las tools",
    ("route_after_retrieve", "no_context"): "ningún chunk supera min_score",
    ("route_after_generate", "compose_answer"): "hay resultados de tools que combinar",
    ("route_after_generate", "propose_memory"): "solo RAG: la respuesta generada es la final",
}


# --------------------------------------------------------------------------
# Construcción, validación y compilación
# --------------------------------------------------------------------------
class AgentGraphStructureError(ValueError):
    """Error estructural del grafo detectado al compilar (nunca en producción)."""


class AgentRunError(RuntimeError):
    """Fallo de un nodo durante una corrida; lleva el ``run_id`` y el nodo que falló."""

    def __init__(self, run_id: str, node: str | None, cause: BaseException):
        super().__init__(f"El nodo '{node}' falló en la corrida {run_id}: {type(cause).__name__}")
        self.run_id = run_id
        self.node = node
        self.cause = cause


def _traced_node(name: str, fn: Callable, traces: TraceStore | None):
    allowed = NODE_WRITES[name]
    takes_config = len(inspect.signature(fn).parameters) >= 2

    def wrapper(state: AgentState, config) -> dict[str, Any]:
        run_id = config["configurable"]["thread_id"]
        started_at, started = _now(), time.perf_counter()
        try:
            update = fn(state, config) if takes_config else fn(state)
            unexpected = set(update) - allowed
            if unexpected:
                raise AgentGraphStructureError(f"El nodo '{name}' escribió claves no declaradas: {sorted(unexpected)}")
        except Exception as error:
            if traces:
                traces.record(run_id, kind="node", name=name, started_at=started_at,
                              duration_ms=(time.perf_counter() - started) * 1000, status="error",
                              output={"error_type": type(error).__name__, "error": str(error)[:500]})
            raise
        if traces:
            traces.record(run_id, kind="node", name=name, started_at=started_at,
                          duration_ms=(time.perf_counter() - started) * 1000, status="ok", output=update)
        return update

    wrapper.__name__ = f"traced_{name}"
    return wrapper


def _traced_route(fn: Callable, traces: TraceStore | None):
    def wrapper(state: AgentState, config) -> str:
        started_at, started = _now(), time.perf_counter()
        destination = fn(state)
        if traces:
            traces.record(config["configurable"]["thread_id"], kind="route", name=fn.__name__,
                          started_at=started_at, duration_ms=(time.perf_counter() - started) * 1000, status="ok",
                          output={"next": destination, "reason": ROUTE_REASONS.get((fn.__name__, destination), "")})
        return destination

    wrapper.__name__ = fn.__name__
    return wrapper


def build_agent_graph(
    *,
    retrieve_fn: Callable[..., list[dict[str, Any]]] = retrieve,
    generate_fn: Callable[[str, list[dict[str, Any]]], str] = generate_cx_answer,
    incidents_fn: Callable[..., IncidentToolResult] = lookup_incidents,
    inventory_fn: Callable[..., InventoryToolResult] = lookup_inventory,
    mcp: Callable[[], McpCaller] = default_mcp,
    memory_store: Callable[[], MemoryStore | None] = lambda: None,
    memory_generate_fn: Callable[..., str] = generate_with_memory,
    reflect_fn: Callable[..., Any] = reflect_memory,
    traces: TraceStore | None = None,
    clock: Callable[[], Any] | None = None,
) -> StateGraph:
    """Define nodos y aristas (sin compilar). Las funciones solo se inyectan en tests."""
    guard_log = make_guard_log(traces)
    nodes = {
        "input_guard": make_input_guard_node(guard_log, clock),
        "guard_response": guard_response,
        "output_guard": make_output_guard_node(guard_log),
        "resolve_pending_memory": make_resolve_node(memory_store),
        "memory_acknowledge": memory_acknowledge,
        "receive_question": receive_question,
        "recall_memory": make_recall_node(memory_store),
        "plan_sources": plan_sources,
        "mcp_incidents": make_incidents_node(incidents_fn, mcp, guard_log),
        "mcp_inventory": make_inventory_node(inventory_fn, mcp, guard_log),
        "retrieve": make_retrieve_node(retrieve_fn, guard_log),
        "generate": make_generate_node(generate_fn, memory_generate_fn),
        "compose_answer": compose_answer,
        "propose_memory": make_propose_node(memory_store, reflect_fn),
        "no_context": no_context,
        "invalid_question": invalid_question,
    }
    builder = StateGraph(AgentState)
    for name, fn in nodes.items():
        builder.add_node(name, _traced_node(name, fn, traces))

    def conditional(source: str, route: Callable, destinations: list[str]) -> None:
        builder.add_conditional_edges(source, _traced_route(route, traces), {d: d for d in destinations})

    builder.add_edge(START, "input_guard")
    conditional("input_guard", route_after_input_guard, ["guard_response", "resolve_pending_memory"])
    builder.add_edge("guard_response", "output_guard")
    conditional("resolve_pending_memory", route_after_resolve, ["memory_acknowledge", "receive_question"])
    builder.add_edge("memory_acknowledge", "output_guard")
    conditional("receive_question", route_after_receive, ["invalid_question", "recall_memory"])
    builder.add_edge("recall_memory", "plan_sources")
    conditional("plan_sources", route_after_plan, ["mcp_incidents", "mcp_inventory", "retrieve"])
    conditional("mcp_incidents", route_after_incidents, ["mcp_inventory", "retrieve", "compose_answer"])
    conditional("mcp_inventory", route_after_inventory, ["retrieve", "compose_answer"])
    conditional("retrieve", route_after_retrieve, ["generate", "compose_answer", "no_context", "propose_memory"])
    conditional("generate", route_after_generate, ["compose_answer", "propose_memory"])
    builder.add_edge("compose_answer", "propose_memory")
    builder.add_edge("no_context", "propose_memory")
    builder.add_edge("propose_memory", "output_guard")
    builder.add_edge("invalid_question", "output_guard")
    builder.add_edge("output_guard", END)
    return builder


def validate_structure(builder: StateGraph, node_writes: dict[str, frozenset[str]] = NODE_WRITES) -> None:
    """Comprobaciones que ``StateGraph.compile()`` NO hace por sí solo.

    LangGraph compila sin error un nodo huérfano, un nodo sin salida y un nodo
    que escribe claves inexistentes en el estado (las ignora en silencio). Aquí
    se rechazan los tres casos con un mensaje claro.
    """
    state_keys = set(get_type_hints(builder.state_schema, include_extras=True))
    if not state_keys:
        raise AgentGraphStructureError("El estado del grafo no declara ninguna clave tipada.")
    nodes = set(builder.nodes)
    for name in nodes:
        if name not in node_writes:
            raise AgentGraphStructureError(f"El nodo '{name}' no declara qué claves del estado escribe.")
        unknown = node_writes[name] - state_keys
        if unknown:
            raise AgentGraphStructureError(f"El nodo '{name}' escribe claves que no existen en el estado: {sorted(unknown)}")

    successors: dict[str, set[str]] = {name: set() for name in nodes | {START}}
    for source, target in builder.edges:
        successors.setdefault(source, set()).add(target)
    for source, branches in builder.branches.items():
        for branch in branches.values():
            if not branch.ends:
                raise AgentGraphStructureError(f"La arista condicional desde '{source}' no declara sus destinos.")
            successors.setdefault(source, set()).update(branch.ends.values())

    without_exit = sorted(name for name in nodes if not successors.get(name))
    if without_exit:
        raise AgentGraphStructureError(f"Nodos sin arista de salida: {without_exit}")
    reachable, pending = set(), [START]
    while pending:
        for target in successors.get(pending.pop(), set()):
            if target not in reachable:
                reachable.add(target)
                pending.append(target)
    orphans = sorted(nodes - reachable)
    if orphans:
        raise AgentGraphStructureError(f"Nodos sin conexión desde START: {orphans}")
    if END not in reachable:
        raise AgentGraphStructureError("Ningún camino del grafo llega a END.")


def compile_agent_graph(builder: StateGraph | None = None, *, checkpointer=None):
    """Valida y compila el grafo. Falla con ``AgentGraphStructureError`` si está mal construido."""
    builder = builder or build_agent_graph()
    validate_structure(builder)
    try:
        return builder.compile(checkpointer=checkpointer)
    except ValueError as error:  # validaciones propias de LangGraph (aristas a nodos inexistentes…)
        raise AgentGraphStructureError(f"El grafo no compila: {error}") from error


# --------------------------------------------------------------------------
# Ejecución
# --------------------------------------------------------------------------
def default_data_dir() -> Path:
    return Path(os.environ.get("AGENT_DATA_DIR") or ROOT / ".agent-runs")


def sqlite_checkpointer(path: Path | str):
    from langgraph.checkpoint.sqlite import SqliteSaver

    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(str(path), check_same_thread=False))


MEMORY_NODES = frozenset({"resolve_pending_memory", "recall_memory", "propose_memory"})


ANSWER_NODES = frozenset({"generate", "compose_answer", "no_context", "invalid_question", "memory_acknowledge",
                          "guard_response"})


def answering_node(trace: dict[str, Any]) -> str:
    """Nodo que produjo la respuesta. Si fue un acuse de un dato memorizable, es ``propose_memory``."""
    nodes = [s["name"] for s in trace["steps"] if s["kind"] == "node" and s["name"] != "output_guard"]
    return next((n for n in reversed(nodes) if n in ANSWER_NODES), nodes[-1])


def default_memory_store() -> MemoryStore | None:
    """Redis de ``AGENT_MEMORY_REDIS_URL``; ``None`` (sin memoria) si ``AGENT_MEMORY_ENABLED=0``."""
    if os.environ.get("AGENT_MEMORY_ENABLED", "1") == "0":
        return None
    from services.agent.memory import RedisMemoryStore

    return RedisMemoryStore.from_env()


def sources_used(trace: dict[str, Any]) -> list[str]:
    """Fuentes consultadas en una corrida, en orden: 'incidents', 'inventory', 'rag'."""
    return [SOURCE_BY_NODE[s["name"]] for s in trace["steps"] if s["kind"] == "node" and s["name"] in SOURCE_BY_NODE]


@dataclass
class AgentResult:
    run_id: str
    answer: str
    final_node: str
    error: str | None
    context_count: int
    sources: list[str] = field(default_factory=list)
    memory_proposal: dict[str, Any] | None = None
    memory_resolution: dict[str, Any] | None = None
    guard: dict[str, Any] | None = None
    output_check: dict[str, Any] | None = None


class KnowledgeAgent:
    """Grafo compilado + checkpointer + trace store, listo para servir peticiones."""

    def __init__(self, *, data_dir: Path | None = None, retrieve_fn=retrieve, generate_fn=generate_cx_answer,
                 incidents_fn=lookup_incidents, inventory_fn=lookup_inventory, mcp: McpCaller | None = None,
                 memory_store: MemoryStore | None = None, memory_generate_fn=generate_with_memory,
                 reflect_fn=reflect_memory, checkpointer=None, traces: TraceStore | None = None, clock=None):
        data_dir = data_dir or default_data_dir()
        self.traces = traces or TraceStore(data_dir / "traces.sqlite")
        self.checkpointer = checkpointer or sqlite_checkpointer(data_dir / "checkpoints.sqlite")
        # El cliente MCP se crea al primer uso: las preguntas solo-RAG no necesitan servidor MCP.
        mcp_provider = (lambda: mcp) if mcp is not None else default_mcp
        # Memoria: store explícito (Redis por defecto, AGENT_MEMORY_REDIS_URL); nunca la colección RAG.
        self.memory_store = memory_store if memory_store is not None else default_memory_store()
        self.graph = compile_agent_graph(
            build_agent_graph(retrieve_fn=retrieve_fn, generate_fn=generate_fn, incidents_fn=incidents_fn,
                              inventory_fn=inventory_fn, mcp=mcp_provider, memory_store=lambda: self.memory_store,
                              memory_generate_fn=memory_generate_fn, reflect_fn=reflect_fn, traces=self.traces,
                              clock=clock),
            checkpointer=self.checkpointer,
        )

    def run(self, question: str, *, run_id: str | None = None, session_id: str | None = None,
            user: str | None = None, session: SessionContext | None = None) -> AgentResult:
        """Ejecuta una corrida. ``session_id`` identifica la conversación (propuesta de memoria pendiente) y
        ``user`` queda en el registro de auditoría de memoria. ``session`` es el contexto autenticado (qué
        pedidos puede consultar); sin él, ningún pedido concreto está autorizado. Las tools usan la identidad
        OAuth del agente."""
        run_id = run_id or str(uuid.uuid4())
        config = {"configurable": {"thread_id": run_id, "user": user}}
        self.traces.start_run(run_id, question)
        initial: dict[str, Any] = {"question": question, "session": (session or SessionContext(user=user)).to_state()}
        if session_id:
            initial["session_id"] = session_id
        try:
            state = self.graph.invoke(initial, config, durability="sync")
        except Exception as error:
            trace = self.traces.get_trace(run_id) or {"steps": []}
            failed = next((s["name"] for s in reversed(trace["steps"]) if s["status"] == "error"), None)
            self.traces.finish_run(run_id, status="failed", final_node=failed, error_type=type(error).__name__,
                                   sources=sources_used(trace))
            raise AgentRunError(run_id, failed, error) from error
        trace = self.traces.get_trace(run_id)
        final_node = answering_node(trace)
        sources = sources_used(trace)
        self.traces.finish_run(run_id, status="completed", final_node=final_node, sources=sources)
        return AgentResult(run_id=run_id, answer=state["answer"], final_node=final_node, error=state.get("error"),
                           context_count=len(state.get("context") or []), sources=sources,
                           memory_proposal=state.get("memory_proposal"), memory_resolution=state.get("memory_resolution"),
                           guard=state.get("guard"), output_check=state.get("output_check"))

    def checkpoints(self, run_id: str) -> list[dict[str, Any]]:
        """Historial de checkpoints de una corrida (del más antiguo al más reciente)."""
        history = list(self.graph.get_state_history({"configurable": {"thread_id": run_id}}))
        return [
            {"step": snap.metadata.get("step"), "next": list(snap.next), "values": snap.values}
            for snap in reversed(history)
        ]
