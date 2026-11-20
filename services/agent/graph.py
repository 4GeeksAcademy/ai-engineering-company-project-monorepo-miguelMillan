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
    error: NotRequired[Literal["empty_question", "question_too_long"]]
    plan: NotRequired[SourcePlan]
    incident_result: NotRequired[dict[str, Any]]   # IncidentToolResult serializado
    inventory_result: NotRequired[dict[str, Any]]  # InventoryToolResult serializado
    context: NotRequired[list[RetrievedChunk]]
    answer: NotRequired[str]


def _has_tool_results(state: AgentState) -> bool:
    return "incident_result" in state or "inventory_result" in state


def _planned(state: AgentState) -> list[str]:
    return state.get("plan", {}).get("sources", [])


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


def make_incidents_node(lookup_fn: Callable[..., IncidentToolResult], mcp: Callable[[], McpCaller]):
    def mcp_incidents_node(state: AgentState) -> dict[str, Any]:
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
        return {"incident_result": result.model_dump(mode="json")}

    return mcp_incidents_node


def make_inventory_node(lookup_fn: Callable[..., InventoryToolResult], mcp: Callable[[], McpCaller]):
    def mcp_inventory_node(state: AgentState) -> dict[str, Any]:
        """Cliente MCP: consulta el stock con la tool de inventario (solo lectura) del servidor MCP."""
        query = InventoryQuery.model_validate(state["plan"].get("inventory_query", {}))
        try:
            result = lookup_fn(query, mcp=mcp())
        except ReadOnlyViolation:
            raise
        except Exception:
            logger.exception("mcp_inventory falló de forma inesperada")
            result = InventoryToolResult(ok=False, query=query, error_type="unavailable")
        return {"inventory_result": result.model_dump(mode="json")}

    return mcp_inventory_node


def make_retrieve_node(retrieve_fn: Callable[..., list[dict[str, Any]]]):
    def retrieve_node(state: AgentState) -> dict[str, Any]:
        """Recupera chunks de ``trackflow_knowledge`` con el umbral calibrado."""
        top_k = int(os.environ.get("RAG_TOP_K", DEFAULT_TOP_K))
        min_score = float(os.environ.get("RAG_MIN_SCORE", DEFAULT_MIN_SCORE))
        # En preguntas mixtas (tool + RAG) se busca solo con la parte de política.
        question = state.get("plan", {}).get("rag_question") or state["question"]
        return {"context": retrieve_fn(question, k=top_k, min_score=min_score)}

    return retrieve_node


def make_generate_node(generate_fn: Callable[[str, list[dict[str, Any]]], str]):
    def generate_node(state: AgentState) -> dict[str, Any]:
        """Genera la respuesta SOLO a partir del contexto ya recuperado."""
        question = state.get("plan", {}).get("rag_question") or state["question"]
        return {"answer": generate_fn(question, state["context"])}

    return generate_node


def compose_answer(state: AgentState) -> dict[str, Any]:
    """Une, en orden de ejecución, los resultados de las tools (texto determinista) y la parte del RAG."""
    return {"answer": answers.compose(
        state.get("incident_result"), state.get("inventory_result"),
        rag_answer=state.get("answer"), rag_planned="rag" in _planned(state), no_context_answer=NO_CONTEXT_ANSWER,
    )}


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
    "generate": frozenset({"answer"}),
    "compose_answer": frozenset({"answer"}),
    "no_context": frozenset({"answer"}),
    "invalid_question": frozenset({"answer"}),
}


# --------------------------------------------------------------------------
# Aristas condicionales
# --------------------------------------------------------------------------
def route_after_receive(state: AgentState) -> Literal["invalid_question", "plan_sources"]:
    return "invalid_question" if state.get("error") else "plan_sources"


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


def route_after_retrieve(state: AgentState) -> Literal["generate", "compose_answer", "no_context"]:
    if state.get("context"):
        return "generate"
    return "compose_answer" if _has_tool_results(state) else "no_context"


def route_after_generate(state: AgentState) -> Literal["compose_answer", "__end__"]:
    return "compose_answer" if _has_tool_results(state) else END


ROUTE_REASONS = {
    ("route_after_receive", "invalid_question"): "pregunta vacía o demasiado larga",
    ("route_after_receive", "plan_sources"): "pregunta válida",
    ("route_after_plan", "mcp_incidents"): "el plan incluye el gestor de incidencias (vía servidor MCP)",
    ("route_after_plan", "mcp_inventory"): "el plan incluye el gestor de inventario (vía servidor MCP)",
    ("route_after_plan", "retrieve"): "el plan solo necesita la base de conocimiento",
    ("route_after_incidents", "mcp_inventory"): "falta consultar inventario",
    ("route_after_incidents", "retrieve"): "falta consultar la base de conocimiento",
    ("route_after_incidents", "compose_answer"): "no hay más fuentes planificadas",
    ("route_after_inventory", "retrieve"): "falta consultar la base de conocimiento",
    ("route_after_inventory", "compose_answer"): "no hay más fuentes planificadas",
    ("route_after_retrieve", "generate"): "hay contexto ≥ min_score",
    ("route_after_retrieve", "compose_answer"): "sin contexto ≥ min_score; se responde con las tools",
    ("route_after_retrieve", "no_context"): "ningún chunk supera min_score",
    ("route_after_generate", "compose_answer"): "hay resultados de tools que combinar",
    ("route_after_generate", END): "solo RAG: la respuesta generada es la final",
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
    generate_fn: Callable[[str, list[dict[str, Any]]], str] = generate_answer,
    incidents_fn: Callable[..., IncidentToolResult] = lookup_incidents,
    inventory_fn: Callable[..., InventoryToolResult] = lookup_inventory,
    mcp: Callable[[], McpCaller] = default_mcp,
    traces: TraceStore | None = None,
) -> StateGraph:
    """Define nodos y aristas (sin compilar). Las funciones solo se inyectan en tests."""
    nodes = {
        "receive_question": receive_question,
        "plan_sources": plan_sources,
        "mcp_incidents": make_incidents_node(incidents_fn, mcp),
        "mcp_inventory": make_inventory_node(inventory_fn, mcp),
        "retrieve": make_retrieve_node(retrieve_fn),
        "generate": make_generate_node(generate_fn),
        "compose_answer": compose_answer,
        "no_context": no_context,
        "invalid_question": invalid_question,
    }
    builder = StateGraph(AgentState)
    for name, fn in nodes.items():
        builder.add_node(name, _traced_node(name, fn, traces))

    def conditional(source: str, route: Callable, destinations: list[str]) -> None:
        builder.add_conditional_edges(source, _traced_route(route, traces), {d: d for d in destinations})

    builder.add_edge(START, "receive_question")
    conditional("receive_question", route_after_receive, ["invalid_question", "plan_sources"])
    conditional("plan_sources", route_after_plan, ["mcp_incidents", "mcp_inventory", "retrieve"])
    conditional("mcp_incidents", route_after_incidents, ["mcp_inventory", "retrieve", "compose_answer"])
    conditional("mcp_inventory", route_after_inventory, ["retrieve", "compose_answer"])
    conditional("retrieve", route_after_retrieve, ["generate", "compose_answer", "no_context"])
    conditional("generate", route_after_generate, ["compose_answer", END])
    builder.add_edge("compose_answer", END)
    builder.add_edge("no_context", END)
    builder.add_edge("invalid_question", END)
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


class KnowledgeAgent:
    """Grafo compilado + checkpointer + trace store, listo para servir peticiones."""

    def __init__(self, *, data_dir: Path | None = None, retrieve_fn=retrieve, generate_fn=generate_answer,
                 incidents_fn=lookup_incidents, inventory_fn=lookup_inventory, mcp: McpCaller | None = None,
                 checkpointer=None, traces: TraceStore | None = None):
        data_dir = data_dir or default_data_dir()
        self.traces = traces or TraceStore(data_dir / "traces.sqlite")
        self.checkpointer = checkpointer or sqlite_checkpointer(data_dir / "checkpoints.sqlite")
        # El cliente MCP se crea al primer uso: las preguntas solo-RAG no necesitan servidor MCP.
        mcp_provider = (lambda: mcp) if mcp is not None else default_mcp
        self.graph = compile_agent_graph(
            build_agent_graph(retrieve_fn=retrieve_fn, generate_fn=generate_fn, incidents_fn=incidents_fn,
                              inventory_fn=inventory_fn, mcp=mcp_provider, traces=self.traces),
            checkpointer=self.checkpointer,
        )

    def run(self, question: str, *, run_id: str | None = None) -> AgentResult:
        """Ejecuta una corrida. Las tools usan la identidad OAuth del agente ante el servidor MCP."""
        run_id = run_id or str(uuid.uuid4())
        config = {"configurable": {"thread_id": run_id}}
        self.traces.start_run(run_id, question)
        try:
            state = self.graph.invoke({"question": question}, config, durability="sync")
        except Exception as error:
            trace = self.traces.get_trace(run_id) or {"steps": []}
            failed = next((s["name"] for s in reversed(trace["steps"]) if s["status"] == "error"), None)
            self.traces.finish_run(run_id, status="failed", final_node=failed, error_type=type(error).__name__,
                                   sources=sources_used(trace))
            raise AgentRunError(run_id, failed, error) from error
        trace = self.traces.get_trace(run_id)
        final_node = next(s["name"] for s in reversed(trace["steps"]) if s["kind"] == "node")
        sources = sources_used(trace)
        self.traces.finish_run(run_id, status="completed", final_node=final_node, sources=sources)
        return AgentResult(run_id=run_id, answer=state["answer"], final_node=final_node, error=state.get("error"),
                           context_count=len(state.get("context") or []), sources=sources)

    def checkpoints(self, run_id: str) -> list[dict[str, Any]]:
        """Historial de checkpoints de una corrida (del más antiguo al más reciente)."""
        history = list(self.graph.get_state_history({"configurable": {"thread_id": run_id}}))
        return [
            {"step": snap.metadata.get("step"), "next": list(snap.next), "values": snap.values}
            for snap in reversed(history)
        ]
