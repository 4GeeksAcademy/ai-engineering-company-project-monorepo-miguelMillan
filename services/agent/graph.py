"""Agente de conocimiento de TrackFlow como grafo de LangGraph (Parte 1).

Reutiliza sin duplicar el RAG de ``mock_data/pipelines/rag.py`` (el
``data/pipelines/`` del enunciado):

* el nodo ``retrieve`` llama a ``retrieve()``;
* el nodo ``generate`` llama a ``generate_answer(question, context)`` con el
  contexto que ``retrieve`` ya produjo. ``query()`` (retrieve + generación en
  bloque) NO se usa: el flujo queda explícito y la recuperación nunca se repite.

Flujo::

    START → receive_question ─┬─ pregunta inválida ──→ invalid_question → END
                              └─ pregunta válida ────→ retrieve ─┬─ sin contexto ≥ min_score → no_context → END
                                                                 └─ con contexto ─────────────→ generate  → END

Estado mínimo (``AgentState``): ``question``, ``context``, ``answer`` y
``error``. No viaja el historial de la conversación: el asistente comercial
responde preguntas independientes sobre los acuerdos estándar y ningún nodo
lo necesita para decidir el siguiente paso.

El grafo se compila (con validación estructural propia, ver
``validate_structure``) antes de cualquier ejecución. Cada superpaso queda
guardado por el checkpointer (``thread_id`` = ``run_id``), y cada nodo y cada
decisión de arista quedan en un trace persistente (``services/agent/tracing.py``).
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass
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
from services.agent.tracing import TraceStore, _now  # noqa: E402

QUESTION_MIN_LENGTH = 3
QUESTION_MAX_LENGTH = 1000
INVALID_QUESTION_ANSWER = (
    "La pregunta está vacía o no es válida. Escribe una pregunta sobre SLA de entrega, política de "
    "devoluciones, cobertura de transportistas o tarifas de almacenamiento de TrackFlow."
)


# --------------------------------------------------------------------------
# Estado
# --------------------------------------------------------------------------
class RetrievedChunk(TypedDict):
    """Payload que devuelve ``retrieve()`` (campos del CONTEXT + ``score``)."""

    company: str
    source_document: str
    section: str
    language: str
    chunk_index: int
    text: str
    score: float


class AgentState(TypedDict):
    question: str
    context: NotRequired[list[RetrievedChunk]]
    answer: NotRequired[str]
    error: NotRequired[Literal["empty_question", "question_too_long"]]


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


def make_retrieve_node(retrieve_fn: Callable[..., list[dict[str, Any]]]):
    def retrieve_node(state: AgentState) -> dict[str, Any]:
        """Recupera chunks de ``trackflow_knowledge`` con el umbral calibrado."""
        top_k = int(os.environ.get("RAG_TOP_K", DEFAULT_TOP_K))
        min_score = float(os.environ.get("RAG_MIN_SCORE", DEFAULT_MIN_SCORE))
        return {"context": retrieve_fn(state["question"], k=top_k, min_score=min_score)}

    return retrieve_node


def make_generate_node(generate_fn: Callable[[str, list[dict[str, Any]]], str]):
    def generate_node(state: AgentState) -> dict[str, Any]:
        """Genera la respuesta SOLO a partir del contexto ya recuperado."""
        return {"answer": generate_fn(state["question"], state["context"])}

    return generate_node


def no_context(state: AgentState) -> dict[str, Any]:
    """Respuesta honesta cuando ningún chunk supera ``min_score``: no se fuerza la generación."""
    return {"answer": NO_CONTEXT_ANSWER}


def invalid_question(state: AgentState) -> dict[str, Any]:
    return {"answer": INVALID_QUESTION_ANSWER}


# Claves del estado que cada nodo puede escribir (se valida al compilar y al ejecutar).
NODE_WRITES: dict[str, frozenset[str]] = {
    "receive_question": frozenset({"question", "error"}),
    "retrieve": frozenset({"context"}),
    "generate": frozenset({"answer"}),
    "no_context": frozenset({"answer"}),
    "invalid_question": frozenset({"answer"}),
}


# --------------------------------------------------------------------------
# Aristas condicionales
# --------------------------------------------------------------------------
def route_after_receive(state: AgentState) -> Literal["invalid_question", "retrieve"]:
    return "invalid_question" if state.get("error") else "retrieve"


def route_after_retrieve(state: AgentState) -> Literal["generate", "no_context"]:
    return "generate" if state.get("context") else "no_context"


ROUTE_REASONS = {
    ("route_after_receive", "invalid_question"): "pregunta vacía o demasiado larga",
    ("route_after_receive", "retrieve"): "pregunta válida",
    ("route_after_retrieve", "generate"): "hay contexto ≥ min_score",
    ("route_after_retrieve", "no_context"): "ningún chunk supera min_score",
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

    def wrapper(state: AgentState, config) -> dict[str, Any]:
        run_id = config["configurable"]["thread_id"]
        started_at, started = _now(), time.perf_counter()
        try:
            update = fn(state)
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
    traces: TraceStore | None = None,
) -> StateGraph:
    """Define nodos y aristas (sin compilar). ``retrieve_fn``/``generate_fn`` solo se inyectan en tests."""
    nodes = {
        "receive_question": receive_question,
        "retrieve": make_retrieve_node(retrieve_fn),
        "generate": make_generate_node(generate_fn),
        "no_context": no_context,
        "invalid_question": invalid_question,
    }
    builder = StateGraph(AgentState)
    for name, fn in nodes.items():
        builder.add_node(name, _traced_node(name, fn, traces))
    builder.add_edge(START, "receive_question")
    builder.add_conditional_edges(
        "receive_question", _traced_route(route_after_receive, traces),
        {"invalid_question": "invalid_question", "retrieve": "retrieve"},
    )
    builder.add_conditional_edges(
        "retrieve", _traced_route(route_after_retrieve, traces),
        {"generate": "generate", "no_context": "no_context"},
    )
    builder.add_edge("generate", END)
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


@dataclass
class AgentResult:
    run_id: str
    answer: str
    final_node: str
    error: str | None
    context_count: int


class KnowledgeAgent:
    """Grafo compilado + checkpointer + trace store, listo para servir peticiones."""

    def __init__(self, *, data_dir: Path | None = None, retrieve_fn=retrieve, generate_fn=generate_answer,
                 checkpointer=None, traces: TraceStore | None = None):
        data_dir = data_dir or default_data_dir()
        self.traces = traces or TraceStore(data_dir / "traces.sqlite")
        self.checkpointer = checkpointer or sqlite_checkpointer(data_dir / "checkpoints.sqlite")
        self.graph = compile_agent_graph(
            build_agent_graph(retrieve_fn=retrieve_fn, generate_fn=generate_fn, traces=self.traces),
            checkpointer=self.checkpointer,
        )

    def run(self, question: str, *, run_id: str | None = None) -> AgentResult:
        run_id = run_id or str(uuid.uuid4())
        config = {"configurable": {"thread_id": run_id}}
        self.traces.start_run(run_id, question)
        try:
            state = self.graph.invoke({"question": question}, config, durability="sync")
        except Exception as error:
            trace = self.traces.get_trace(run_id) or {"steps": []}
            failed = next((s["name"] for s in reversed(trace["steps"]) if s["status"] == "error"), None)
            self.traces.finish_run(run_id, status="failed", final_node=failed, error_type=type(error).__name__)
            raise AgentRunError(run_id, failed, error) from error
        final_node = next(s["name"] for s in reversed(self.traces.get_trace(run_id)["steps"]) if s["kind"] == "node")
        self.traces.finish_run(run_id, status="completed", final_node=final_node)
        return AgentResult(
            run_id=run_id,
            answer=state["answer"],
            final_node=final_node,
            error=state.get("error"),
            context_count=len(state.get("context") or []),
        )

    def checkpoints(self, run_id: str) -> list[dict[str, Any]]:
        """Historial de checkpoints de una corrida (del más antiguo al más reciente)."""
        history = list(self.graph.get_state_history({"configurable": {"thread_id": run_id}}))
        return [
            {"step": snap.metadata.get("step"), "next": list(snap.next), "values": snap.values}
            for snap in reversed(history)
        ]
