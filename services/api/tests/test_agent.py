"""POST /agent/query: solo invoca el grafo compilado y traduce su resultado a HTTP."""

import pytest

import routes.agent  # noqa: F401  (añade la raíz del repo a sys.path, como en producción)
from services.agent.graph import KnowledgeAgent, sqlite_checkpointer
from services.agent.tracing import TraceStore, executed_nodes

CHUNK = {"company": "trackflow", "source_document": "returns-policy", "section": "Política de Devoluciones > Ventana de devolución estándar",
         "language": "es", "chunk_index": 1, "text": "Ventana de devolución estándar: 30 días desde la entrega.", "score": 0.57}


@pytest.fixture
def agent(monkeypatch):
    def build(*, chunks=(CHUNK,), generate=lambda q, c: "La ventana estándar es de 30 días desde la entrega.", retrieve=None):
        instance = KnowledgeAgent(retrieve_fn=retrieve or (lambda q, *, k, min_score: list(chunks)), generate_fn=generate,
                                  checkpointer=sqlite_checkpointer(":memory:"), traces=TraceStore(":memory:"))
        monkeypatch.setattr("routes.agent.get_agent", lambda: instance)
        return instance
    return build


def test_agent_query_returns_graph_answer_and_run_id(authenticated_client, agent):
    instance = agent()
    response = authenticated_client.post("/agent/query", json={"question": "¿Cuál es la ventana de devolución estándar?"})

    assert response.status_code == 200
    assert response.json() == {"answer": "La ventana estándar es de 30 días desde la entrega."}
    trace = instance.traces.get_trace(response.headers["X-Agent-Run-Id"])
    assert executed_nodes(trace) == ["receive_question", "plan_sources", "retrieve", "generate"]


def test_agent_query_never_exposes_chunks_scores_or_trace(authenticated_client, agent):
    agent()
    body = authenticated_client.post("/agent/query", json={"question": "¿Ventana de devolución?"}).json()
    assert set(body) == {"answer"}


def test_empty_question_is_routed_by_graph_to_clear_422(authenticated_client, agent):
    instance = agent()
    response = authenticated_client.post("/agent/query", json={"question": "  "})

    assert response.status_code == 422
    assert "pregunta está vacía" in response.json()["detail"]
    trace = instance.traces.get_trace(response.headers["X-Agent-Run-Id"])
    assert executed_nodes(trace) == ["receive_question", "invalid_question"]


def test_no_context_returns_honest_answer(authenticated_client, agent):
    agent(chunks=())
    response = authenticated_client.post("/agent/query", json={"question": "¿Qué receta lleva una paella?"})

    assert response.status_code == 200
    assert "No tengo información suficiente" in response.json()["answer"]


def test_node_failure_returns_clear_message_without_stack_trace(authenticated_client, agent):
    def missing_llm(question, context):
        raise RuntimeError("Configura LLM_API_URL, LLM_API_KEY, GENERATION_MODEL")

    instance = agent(generate=missing_llm)
    response = authenticated_client.post("/agent/query", json={"question": "¿Ventana de devolución?"})

    assert response.status_code == 503
    assert response.json() == {"detail": "El agente de conocimiento no está disponible en este momento."}
    assert "Traceback" not in response.text and "LLM_API_KEY" not in response.text
    trace = instance.traces.get_trace(response.headers["X-Agent-Run-Id"])
    assert (trace["status"], trace["final_node"]) == ("failed", "generate")


def test_unexpected_node_error_maps_to_502(authenticated_client, agent):
    def qdrant_down(question, *, k, min_score):
        raise ConnectionError("qdrant:6333 refused")

    instance = agent(retrieve=qdrant_down)
    response = authenticated_client.post("/agent/query", json={"question": "¿Ventana de devolución?"})

    assert response.status_code == 502
    assert "qdrant" not in response.text
    assert instance.traces.get_trace(response.headers["X-Agent-Run-Id"])["final_node"] == "retrieve"


def test_agent_query_requires_authentication(api_client, agent):
    agent()
    assert api_client.post("/agent/query", json={"question": "¿Ventana?"}).status_code == 401


def test_ticket_question_forwards_caller_jwt_to_incidents_tool(authenticated_client, monkeypatch):
    from services.agent.tools import IncidentToolResult

    seen = {}

    def incidents(query, *, auth_token=None):
        seen["token"] = auth_token
        return IncidentToolResult(ok=False, mode="detail", query=query, error_type="not_found")

    instance = KnowledgeAgent(retrieve_fn=lambda q, *, k, min_score: [], generate_fn=lambda q, c: "x",
                              incidents_fn=incidents, checkpointer=sqlite_checkpointer(":memory:"), traces=TraceStore(":memory:"))
    monkeypatch.setattr("routes.agent.get_agent", lambda: instance)
    ticket = "8a4f3f2e-6c1d-5b9a-9e2f-0123456789ab"
    response = authenticated_client.post("/agent/query", json={"question": f"¿En qué estado está el ticket {ticket}?"})

    assert response.status_code == 200
    assert seen["token"] == authenticated_client.headers["Authorization"].split(" ", 1)[1]
    assert response.json() == {"answer": f"No encontré ningún ticket con el identificador {ticket} en el gestor de incidencias."}
