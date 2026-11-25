"""Agente LangGraph → langchain-mcp-adapters → servidor MCP (OAuth vía MCP Auth) → Incidents Manager / inventario.

Pila completa sin dobles en el camino MCP: el agente obtiene un access token
``client_credentials`` del proveedor OIDC, el cliente de
``langchain-mcp-adapters`` descubre las tools por Streamable HTTP y el
servidor MCP valida el token con mcpauth antes de llamar al backend. Se
comprueba que el enrutamiento RAG / tools es el mismo que antes de la migración
y que el Incidents Manager solo recibe peticiones del servidor MCP.
"""

from __future__ import annotations

import json

import pytest

from services.agent.guardrails import SessionContext
from services.agent.graph import KnowledgeAgent, sqlite_checkpointer
from services.agent.mcp_client import OAuthClientCredentials, TrackflowMcpTools
from services.agent.tracing import TraceStore, executed_nodes, node_output
from tests.mcp_server.conftest import CLIENT_SECRET, TICKET_ID, free_port

CHUNK = {"company": "trackflow", "source_document": "returns-policy", "section": "Ventana", "language": "es",
         "chunk_index": 1, "text": "La ventana de devolución estándar es de 30 días.", "score": 0.57}


class StaffAgent(KnowledgeAgent):
    """Estas pruebas no miran la autorización (tests/security): sin sesión explícita, pregunta el personal."""

    def _start(self, question, run_id, session_id, user, session):
        return super()._start(question, run_id, session_id, user, session or SessionContext(user=user, staff=True))



@pytest.fixture
def agent_stack(mcp_server, oidc, tmp_path):
    auth = OAuthClientCredentials(oidc.issuer, "trackflow-agent", CLIENT_SECRET)
    mcp = TrackflowMcpTools(mcp_server.url, auth=auth, timeout=5)
    rag_calls = []
    agent = StaffAgent(
        retrieve_fn=lambda q, *, k, min_score: rag_calls.append(q) or [CHUNK],
        generate_fn=lambda q, c: "La ventana de devolución estándar es de 30 días desde la entrega.",
        mcp=mcp, checkpointer=sqlite_checkpointer(tmp_path / "checkpoints.sqlite"), traces=TraceStore(tmp_path / "traces.sqlite"),
    )
    return agent, mcp, rag_calls


def test_agent_discovers_only_its_read_tools_through_the_mcp_server(agent_stack):
    _, mcp, _ = agent_stack
    assert mcp.tool_names() == ["incidents_get_ticket", "incidents_list_tickets", "inventory_query_products"]


def test_ticket_question_goes_through_the_mcp_client_node(agent_stack, mcp_server, backend):
    agent, _, rag_calls = agent_stack
    result = agent.run(f"¿En qué estado está el ticket {TICKET_ID}?")
    trace = agent.traces.get_trace(result.run_id)

    assert result.sources == ["incidents"] and rag_calls == []
    assert executed_nodes(trace) == ["input_guard", "resolve_pending_memory", "receive_question", "recall_memory", "plan_sources", "mcp_incidents", "compose_answer", "propose_memory", "output_guard"]
    assert node_output(trace, "mcp_incidents")["incident_result"]["mcp_tool"] == "incidents_get_ticket"
    assert "**Abierta**" in result.answer
    # El backend solo vio al servidor MCP (cuenta de servicio), y el servidor MCP vio al agente por OAuth
    assert ("GET", f"/api/incidents/{TICKET_ID}", None) in backend.requests
    audit = [r for r in mcp_server.audit() if r["event"] == "tool_call"]
    assert [(r["tool"], r["client_id"], r["outcome"]) for r in audit] == [("incidents_get_ticket", "trackflow-agent", "ok")]


def test_policy_question_still_uses_only_rag_and_never_touches_mcp(agent_stack, mcp_server, backend):
    agent, _, rag_calls = agent_stack
    result = agent.run("¿Cuál es la ventana de devolución estándar?")
    assert result.sources == ["rag"] and result.final_node == "generate" and rag_calls
    assert backend.requests == [] and not [r for r in mcp_server.audit() if r["event"] == "tool_call"]


def test_inventory_and_mixed_questions_keep_the_same_routing(agent_stack):
    agent, _, rag_calls = agent_stack
    stock = agent.run("¿Cuántas unidades quedan de CSM-SRM-030?")
    assert stock.sources == ["inventory"] and "9 unidades en stock" in stock.answer and "POR DEBAJO DEL MÍNIMO" in stock.answer

    mixed = agent.run(f"¿En qué estado está el ticket {TICKET_ID} y cuál es la ventana de devolución estándar?")
    assert mixed.sources == ["incidents", "rag"] and mixed.final_node == "compose_answer"
    assert rag_calls == ["¿Cuál es la ventana de devolución estándar?"]
    assert mixed.answer.index("Abierta") < mixed.answer.index("30 días")


def test_agent_never_writes_tickets_even_if_it_tried(agent_stack, backend):
    from services.agent.tools import ReadOnlyViolation

    _, mcp, _ = agent_stack
    with pytest.raises(ReadOnlyViolation):
        mcp.call("incidents_update_ticket_status", {"incident_id": TICKET_ID, "status": "discarded"})
    assert backend.writes_to("/api/incidents") == []


def test_mcp_server_down_falls_back_honestly(oidc, tmp_path):
    mcp = TrackflowMcpTools(f"http://127.0.0.1:{free_port()}/mcp",
                            auth=OAuthClientCredentials(oidc.issuer, "trackflow-agent", CLIENT_SECRET), timeout=3)
    agent = StaffAgent(retrieve_fn=lambda *a, **k: [], generate_fn=lambda q, c: "", mcp=mcp,
                           checkpointer=sqlite_checkpointer(":memory:"), traces=TraceStore(":memory:"))
    result = agent.run(f"¿En qué estado está el ticket {TICKET_ID}?")
    assert "No pude confirmar el estado de ese ticket ahora mismo" in result.answer
    assert node_output(agent.traces.get_trace(result.run_id), "mcp_incidents")["incident_result"]["error_type"] == "unavailable"


def test_invalid_agent_credentials_are_reported_as_unauthorized(mcp_server, oidc):
    mcp = TrackflowMcpTools(mcp_server.url, auth=OAuthClientCredentials(oidc.issuer, "trackflow-agent", "secreto-incorrecto"), timeout=3)
    result = mcp.call("incidents_get_ticket", {"incident_id": TICKET_ID})
    assert (result.ok, result.error_code) == (False, "unauthorized")


def test_access_token_is_never_persisted_in_checkpoints_or_traces(agent_stack, oidc, tmp_path):
    agent, _, _ = agent_stack
    issued_before = len(oidc.issued)
    result = agent.run(f"¿En qué estado está el ticket {TICKET_ID}?")
    tokens = oidc.issued[issued_before:] or oidc.issued[-1:]
    stored = (tmp_path / "checkpoints.sqlite").read_bytes().decode("latin-1") + json.dumps(agent.traces.get_trace(result.run_id))
    assert tokens and all(token not in stored for token in tokens)
