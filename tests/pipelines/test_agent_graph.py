"""Pruebas deterministas del grafo LangGraph del agente (sin Qdrant ni LLM)."""

import pytest
from langgraph.graph import END, START, StateGraph

import services.agent.graph as agent_graph
from services.agent.graph import (
    INVALID_QUESTION_ANSWER,
    AgentGraphStructureError,
    AgentRunError,
    AgentState,
    KnowledgeAgent,
    build_agent_graph,
    compile_agent_graph,
    sqlite_checkpointer,
)
from services.agent.tracing import TraceStore, executed_nodes, node_output
from mock_data.pipelines.rag import NO_CONTEXT_ANSWER

SEUR_CHUNK = {
    "company": "trackflow", "source_document": "carrier-coverage", "section": "Cobertura de Transportistas > España (Zaragoza)",
    "language": "es", "chunk_index": 1, "text": "- SEUR: mejor cobertura en zonas rurales de Aragón.", "score": 0.65,
}


class Spy:
    """retrieve()/generate_answer() simulados que registran sus llamadas."""

    def __init__(self, chunks=None, answer="SEUR tiene la mejor cobertura en zonas rurales de Aragón."):
        self.chunks = [SEUR_CHUNK] if chunks is None else chunks
        self.answer = answer
        self.retrieve_calls, self.generate_calls = [], []

    def retrieve(self, question, *, k, min_score):
        self.retrieve_calls.append((question, k, min_score))
        return list(self.chunks)

    def generate(self, question, context):
        self.generate_calls.append((question, context))
        return self.answer


def make_agent(spy, tmp_path=None):
    return KnowledgeAgent(
        retrieve_fn=spy.retrieve, generate_fn=spy.generate,
        checkpointer=sqlite_checkpointer(tmp_path / "cp.sqlite" if tmp_path else ":memory:"),
        traces=TraceStore(tmp_path / "traces.sqlite" if tmp_path else ":memory:"),
    )


# --------------------------------------------------------------------------
# Enrutamiento y contrato de nodos
# --------------------------------------------------------------------------
def test_valid_question_retrieves_then_generates_with_retrieved_context():
    spy = Spy()
    agent = make_agent(spy)

    result = agent.run("  ¿Qué transportista   cubre mejor Aragón rural? ")

    assert result.answer == spy.answer and result.final_node == "generate"
    assert spy.retrieve_calls == [("¿Qué transportista cubre mejor Aragón rural?", 5, 0.45)]
    # generate recibe exactamente el contexto que produjo retrieve (sin volver a recuperar)
    assert spy.generate_calls == [("¿Qué transportista cubre mejor Aragón rural?", [SEUR_CHUNK])]
    assert len(spy.retrieve_calls) == 1


def test_empty_question_routes_to_invalid_question_without_retrieving():
    spy = Spy()
    result = make_agent(spy).run("   ")

    assert result.final_node == "invalid_question"
    assert result.error == "empty_question"
    assert result.answer == INVALID_QUESTION_ANSWER
    assert spy.retrieve_calls == [] and spy.generate_calls == []


def test_too_long_question_is_rejected_before_retrieval():
    spy = Spy()
    result = make_agent(spy).run("¿SLA? " * 300)

    assert (result.final_node, result.error) == ("invalid_question", "question_too_long")
    assert spy.retrieve_calls == []


def test_no_context_above_threshold_answers_honestly_without_generation():
    spy = Spy(chunks=[])
    result = make_agent(spy).run("¿Cuál es la capital de Mongolia?")  # fuera de dominio, pero no es una tarea personal

    assert result.final_node == "no_context"
    assert result.answer == NO_CONTEXT_ANSWER
    assert spy.generate_calls == []  # nunca se fuerza la generación sobre contexto vacío


def test_graph_uses_rag_functions_not_monolithic_query():
    assert agent_graph.retrieve.__module__ == "mock_data.pipelines.rag"
    assert agent_graph.generate_answer.__module__ == "mock_data.pipelines.rag"
    assert not hasattr(agent_graph, "query"), "el grafo no debe usar query() (retrieve + generación en bloque)"


# --------------------------------------------------------------------------
# Compilación: errores estructurales claros
# --------------------------------------------------------------------------
def test_production_graph_compiles():
    compiled = compile_agent_graph(build_agent_graph())
    assert set(compiled.get_graph().nodes) >= {"receive_question", "retrieve", "generate", "no_context", "invalid_question"}


def _graph_with_orphan():
    builder = build_agent_graph()
    builder.add_node("orphan", lambda state: {})
    builder.add_edge("orphan", END)
    return builder


def _graph_with_node_without_exit():
    builder = StateGraph(AgentState)
    builder.add_node("receive_question", agent_graph.receive_question)
    builder.add_edge(START, "receive_question")
    return builder


def test_compile_rejects_orphan_node():
    with pytest.raises(AgentGraphStructureError, match="no declara|sin conexión"):
        compile_agent_graph(_graph_with_orphan())


def test_compile_rejects_node_without_exit():
    with pytest.raises(AgentGraphStructureError, match="sin arista de salida"):
        compile_agent_graph(_graph_with_node_without_exit())


def test_compile_rejects_writes_outside_typed_state(monkeypatch):
    monkeypatch.setitem(agent_graph.NODE_WRITES, "generate", frozenset({"answer", "chat_history"}))
    with pytest.raises(AgentGraphStructureError, match="chat_history"):
        compile_agent_graph(build_agent_graph())


def test_compile_rejects_edge_to_unknown_node():
    builder = build_agent_graph()
    builder.add_edge("generate", "missing_node")
    with pytest.raises(AgentGraphStructureError, match="missing_node"):
        compile_agent_graph(builder)


def test_node_writing_undeclared_key_fails_at_runtime(monkeypatch):
    """LangGraph ignoraría en silencio una clave fuera del estado; el arnés la convierte en error."""
    def leaky_generate_node(generate_fn, memory_generate_fn):
        return lambda state: {"answer": "x", "chat_history": ["turno previo"]}

    monkeypatch.setattr(agent_graph, "make_generate_node", leaky_generate_node)
    with pytest.raises(AgentRunError) as error:
        make_agent(Spy()).run("¿Qué transportista cubre mejor Aragón rural?")

    assert error.value.node == "generate"
    assert isinstance(error.value.cause, AgentGraphStructureError)
    assert "chat_history" in str(error.value.cause)


# --------------------------------------------------------------------------
# Checkpointing y trace persistente
# --------------------------------------------------------------------------
def test_each_transition_is_checkpointed_and_inspectable(tmp_path):
    agent = make_agent(Spy(), tmp_path)
    result = agent.run("¿Qué transportista cubre mejor Aragón rural?")

    checkpoints = agent.checkpoints(result.run_id)
    assert [c["next"] for c in checkpoints] == [
        ["__start__"], ["input_guard"], ["resolve_pending_memory"], ["receive_question"], ["recall_memory"], ["plan_sources"],
        ["retrieve"], ["generate"], ["propose_memory"], ["output_guard"], [],
    ]
    assert checkpoints[2]["values"]["guard"]["action"] == "allow"  # guarda de entrada: dentro del dominio
    assert checkpoints[4]["values"]["question"] == "¿Qué transportista cubre mejor Aragón rural?"
    assert checkpoints[5]["values"]["memory_gate"]["open"] is False  # consulta sin nada nuevo que recordar
    assert checkpoints[6]["values"]["plan"]["sources"] == ["rag"]
    assert checkpoints[7]["values"]["context"] == [SEUR_CHUNK]  # estado tras retrieve, antes de generate
    assert checkpoints[-1]["values"]["answer"].startswith("SEUR")

    # Persistente: otro proceso (nuevo checkpointer sobre el mismo archivo) puede inspeccionar la corrida.
    reopened = KnowledgeAgent(retrieve_fn=Spy().retrieve, generate_fn=Spy().generate,
                              checkpointer=sqlite_checkpointer(tmp_path / "cp.sqlite"),
                              traces=TraceStore(tmp_path / "traces.sqlite"))
    assert reopened.checkpoints(result.run_id)[-1]["values"]["answer"] == result.answer


def test_trace_records_nodes_in_order_with_outputs_and_routes(tmp_path):
    agent = make_agent(Spy(), tmp_path)
    result = agent.run("¿Qué transportista cubre mejor Aragón rural?")

    trace = TraceStore(tmp_path / "traces.sqlite").get_trace(result.run_id)  # consultable después de la corrida
    assert trace["status"] == "completed" and trace["final_node"] == "generate"
    assert executed_nodes(trace) == ["input_guard", "resolve_pending_memory", "receive_question", "recall_memory", "plan_sources", "retrieve", "generate", "propose_memory", "output_guard"]
    assert trace["sources_used"] == ["rag"]
    assert [(s["kind"], s["name"]) for s in trace["steps"]] == [
        ("node", "input_guard"), ("route", "route_after_input_guard"),
        ("node", "resolve_pending_memory"), ("route", "route_after_resolve"),
        ("node", "receive_question"), ("route", "route_after_receive"), ("node", "recall_memory"),
        ("node", "plan_sources"), ("route", "route_after_plan"),
        ("node", "retrieve"), ("route", "route_after_retrieve"),
        ("node", "generate"), ("route", "route_after_generate"), ("node", "propose_memory"), ("node", "output_guard"),
    ]
    assert node_output(trace, "retrieve")["context"][0]["source_document"] == "carrier-coverage"
    assert node_output(trace, "generate")["answer"] == result.answer
    route = next(s for s in trace["steps"] if s["name"] == "route_after_retrieve")
    assert route["output"] == {"next": "generate", "reason": "hay contexto ≥ min_score o memoria aprobada relevante"}


def test_node_failure_is_traced_and_raised_with_run_id(tmp_path):
    def broken_generate(question, context):
        raise RuntimeError("Configura LLM_API_URL, LLM_API_KEY, GENERATION_MODEL")

    agent = KnowledgeAgent(retrieve_fn=Spy().retrieve, generate_fn=broken_generate,
                           checkpointer=sqlite_checkpointer(":memory:"), traces=TraceStore(tmp_path / "t.sqlite"))
    with pytest.raises(AgentRunError) as error:
        agent.run("¿Qué transportista cubre mejor Aragón rural?")

    assert error.value.node == "generate"
    trace = agent.traces.get_trace(error.value.run_id)
    assert (trace["status"], trace["final_node"], trace["error_type"]) == ("failed", "generate", "RuntimeError")
    failed = trace["steps"][-1]
    assert (failed["name"], failed["status"], failed["output"]["error_type"]) == ("generate", "error", "RuntimeError")
