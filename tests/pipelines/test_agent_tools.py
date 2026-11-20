"""Pruebas deterministas de las tools del agente (cliente MCP), del enrutamiento y del grafo con tools.

Desde la migración, las tools del agente invocan el servidor MCP de TrackFlow
(``services/agent/mcp_client.py`` + ``langchain-mcp-adapters``). Aquí se prueban
con un doble del cliente MCP (``FakeMcp``), que registra qué tool MCP y con
qué argumentos se invoca, y con servidores HTTP reales lentos o caídos para el
timeout y la indisponibilidad. La pila completa (agente → adapters → servidor
MCP con OAuth) está en ``tests/mcp_server/test_agent_via_mcp.py``, y la evidencia
contra los servicios reales está en los evals.
"""

import json
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest

import services.agent.mcp_client as mcp_client
from services.agent.graph import KnowledgeAgent, sqlite_checkpointer, sources_used
from services.agent.mcp_client import McpCallResult, TrackflowMcpTools
from services.agent.routing import plan_sources
from services.agent.tools import (
    IncidentQuery,
    IncidentToolResult,
    InventoryQuery,
    ReadOnlyViolation,
    lookup_incidents,
    lookup_inventory,
)
from services.agent.tracing import TraceStore, executed_nodes, node_output

ROOT = Path(__file__).resolve().parents[2]
TICKET_ID = "8a4f3f2e-6c1d-5b9a-9e2f-0123456789ab"
INCIDENT = {
    "id": TICKET_ID, "title": "Paquete no entregado en Zaragoza", "category": "lost_parcel", "status": "in_progress",
    "origin": "customer", "branch": "zaragoza_office", "description": "texto libre",
    "created_at": "2026-11-02T00:00:00+00:00", "updated_at": "2026-11-10T09:30:00+00:00",
}
PRODUCTS = [
    {"id": 3, "name": "Auriculares inalámbricos Pro", "sku": "TEC-EAR-001", "client_name": "SoundCo", "client_id": "c-1",
     "category": "electronics", "warehouse": "LA", "current_stock": 74, "minimum_stock": 20, "below_minimum": False},
    {"id": 4, "name": "Sérum facial hidratante 30ml", "sku": "CSM-SRM-030", "client_name": "GlowLab", "client_id": "c-2",
     "category": "cosmetics", "warehouse": "ZGZ", "current_stock": 9, "minimum_stock": 15, "below_minimum": True},
]


class FakeMcp:
    """Doble del cliente MCP: respuestas por nombre de tool y registro de cada llamada."""

    def __init__(self, **responses):
        self.responses = responses
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        response = self.responses[name]
        return response(arguments) if callable(response) else response


def ok(data):
    return McpCallResult(ok=True, data=data)


def mcp_error(code):
    return McpCallResult(ok=False, error_code=code, error_message="x")


def products_mcp():
    return FakeMcp(inventory_query_products=lambda args: ok(
        {"operation": "list", "total": 2, "products": [p for p in PRODUCTS if not args.get("sku") or p["sku"] == args["sku"]]}))


# --------------------------------------------------------------------------
# Tool de incidencias → tools MCP del Incidents Manager
# --------------------------------------------------------------------------
def test_incident_detail_uses_mcp_get_ticket_tool():
    mcp = FakeMcp(incidents_get_ticket=ok(INCIDENT))
    result = lookup_incidents(IncidentQuery(ticket_id=TICKET_ID), mcp=mcp)

    assert result.ok and result.mode == "detail" and result.mcp_tool == "incidents_get_ticket"
    assert result.incidents[0].status == "in_progress" and result.incidents[0].branch == "zaragoza_office"
    assert mcp.calls == [("incidents_get_ticket", {"incident_id": TICKET_ID})]
    assert "description" not in result.model_dump()["incidents"][0]  # contrato tipado: solo los campos declarados


def test_incident_search_passes_filters_to_mcp_list_tool():
    rows = [INCIDENT, {**INCIDENT, "id": str(uuid4()), "status": "open"}]
    mcp = FakeMcp(incidents_list_tickets=ok({"total": 7, "returned": 2, "filters": {}, "incidents": rows}))
    result = lookup_incidents(IncidentQuery(status="open", category="returns_issue"), mcp=mcp)

    assert result.ok and result.mode == "list" and result.total == 7 and len(result.incidents) == 2
    assert mcp.calls == [("incidents_list_tickets", {"status": "open", "category": "returns_issue", "limit": 50})]


@pytest.mark.parametrize("code,error", [
    ("NOT_FOUND", "not_found"), ("INSUFFICIENT_SCOPE", "unauthorized"), ("VALIDATION_ERROR", "invalid_query"),
    ("BACKEND_TIMEOUT", "timeout"), ("BACKEND_UNAVAILABLE", "unavailable"), ("timeout", "timeout"),
    ("unavailable", "unavailable"), ("unauthorized", "unauthorized"), ("ALGO_NUEVO", "bad_response"),
])
def test_mcp_error_codes_become_typed_agent_errors(code, error):
    result = lookup_incidents(IncidentQuery(ticket_id=TICKET_ID), mcp=FakeMcp(incidents_get_ticket=mcp_error(code)))
    assert (result.ok, result.error_type, result.mcp_error_code, result.incidents) == (False, error, code, [])


def test_non_uuid_ticket_reference_is_rejected_without_calling_the_mcp_server():
    mcp = FakeMcp()
    result = lookup_incidents(IncidentQuery(raw_ticket_reference="482"), mcp=mcp)
    assert (result.ok, result.error_type, mcp.calls) == (False, "invalid_query", [])


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_mcp_timeout_is_explicit_numeric_and_does_not_hang():
    assert mcp_client.TOOL_TIMEOUT_SECONDS == 8.0

    class Slow(BaseHTTPRequestHandler):
        def do_POST(self):
            time.sleep(1.5)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = TrackflowMcpTools(f"http://127.0.0.1:{server.server_address[1]}/mcp", timeout=0.3)
        started = time.perf_counter()
        result = lookup_incidents(IncidentQuery(ticket_id=TICKET_ID), mcp=client)
        elapsed = time.perf_counter() - started
    finally:
        server.shutdown()
    assert result.error_type == "timeout"
    assert elapsed < 1.4  # cortó en el timeout, no esperó la respuesta lenta


def test_mcp_server_down_is_unavailable_not_exception():
    client = TrackflowMcpTools(f"http://127.0.0.1:{_closed_port()}/mcp", timeout=3)
    assert lookup_incidents(IncidentQuery(ticket_id=TICKET_ID), mcp=client).error_type == "unavailable"


@pytest.mark.parametrize("tool", ["incidents_create_ticket", "incidents_update_ticket_status", "inventory_update_stock"])
def test_agent_mcp_client_only_invokes_read_tools(tool):
    client = TrackflowMcpTools(f"http://127.0.0.1:{_closed_port()}/mcp", timeout=1)
    with pytest.raises(ReadOnlyViolation):
        client.call(tool, {"incident_id": TICKET_ID, "status": "resolved"})


def test_no_hardcoded_credentials_oauth_comes_only_from_env(monkeypatch):
    monkeypatch.delenv("AGENT_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("AGENT_OAUTH_CLIENT_SECRET", raising=False)
    assert mcp_client.auth_from_env() is None
    monkeypatch.setenv("AGENT_OAUTH_CLIENT_ID", "trackflow-agent")
    monkeypatch.setenv("AGENT_OAUTH_CLIENT_SECRET", "secreto-de-entorno")
    auth = mcp_client.auth_from_env()
    assert auth.client_id == "trackflow-agent" and auth.issuer.endswith("/realms/trackflow")


def test_agent_has_a_single_path_to_the_incidents_manager():
    """Ningún módulo del agente llama ya por HTTP a /api/incidents ni a /inventory: solo al servidor MCP."""
    direct_call = re.compile(r"""\.(get|post|patch|put|delete|request)\(\s*(["'](GET|POST|PATCH|PUT|DELETE)["'],\s*)?f?["']/(api|inventory)""")
    for path in (ROOT / "services" / "agent").glob("*.py"):
        code = path.read_text(encoding="utf-8")
        assert not direct_call.search(code), path
        assert "AGENT_API_TOKEN" not in code and "AGENT_API_BASE_URL" not in code, path
    assert "import httpx" not in (ROOT / "services/agent/tools.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Tool de inventario → tool MCP de inventario (solo lectura)
# --------------------------------------------------------------------------
def test_inventory_by_sku_uses_mcp_read_only_tool():
    mcp = products_mcp()
    result = lookup_inventory(InventoryQuery(sku="TEC-EAR-001"), mcp=mcp)

    assert result.ok and [(i.sku, i.current_stock) for i in result.items] == [("TEC-EAR-001", 74)]
    assert mcp.calls == [("inventory_query_products", {"operation": "list", "sku": "TEC-EAR-001"})]


def test_inventory_by_name_ignores_accents_and_respects_warehouse():
    assert lookup_inventory(InventoryQuery(name_terms=["serum", "hidratante"]), mcp=products_mcp()).items[0].sku == "CSM-SRM-030"
    assert lookup_inventory(InventoryQuery(name_terms=["serum"], warehouse="LA"), mcp=products_mcp()).error_type == "not_found"


def test_inventory_failure_is_typed():
    mcp = FakeMcp(inventory_query_products=mcp_error("BACKEND_UNAVAILABLE"))
    assert lookup_inventory(InventoryQuery(sku="TEC-EAR-001"), mcp=mcp).error_type == "unavailable"


# --------------------------------------------------------------------------
# Enrutamiento automático (sin que el usuario indique la fuente)
# --------------------------------------------------------------------------
def test_every_rag_eval_question_still_routes_only_to_rag():
    queries = json.loads((ROOT / "mock_data/eval/test-queries.json").read_text(encoding="utf-8"))
    for case in queries["in_scope"] + queries["out_of_scope"]:
        assert plan_sources(case["question"])["sources"] == ["rag"], case["id"]


@pytest.mark.parametrize("question,sources", [
    (f"¿En qué estado está el ticket {TICKET_ID}?", ["incidents"]),
    ("¿Cuántas incidencias de devolución están abiertas?", ["incidents"]),
    ("¿Tenemos stock de auriculares inalámbricos?", ["inventory"]),
    ("¿Cuántas unidades quedan de TEC-EAR-001?", ["inventory"]),
    (f"¿En qué estado está el ticket {TICKET_ID} y cuál es la ventana de devolución estándar?", ["incidents", "rag"]),
    ("¿Qué se cobra por el stock que lleva más de seis meses sin venderse?", ["rag"]),
    ("¿Podemos garantizar los plazos de entrega durante Black Friday?", ["rag"]),
])
def test_router_decides_sources_from_the_question(question, sources):
    assert plan_sources(question)["sources"] == sources


def test_router_extracts_typed_tool_parameters():
    assert plan_sources(f"estado del ticket {TICKET_ID.upper()}")["incident_query"] == {"ticket_id": TICKET_ID}
    assert plan_sources("¿en qué estado está la incidencia 482?")["incident_query"] == {"raw_ticket_reference": "482"}
    assert plan_sources("¿Cuántas incidencias de devolución están abiertas?")["incident_query"] == {"status": "open", "category": "returns_issue"}
    assert plan_sources("¿Hay stock de TEC-EAR-001 en Los Ángeles?")["inventory_query"] == {"sku": "TEC-EAR-001", "warehouse": "LA"}


# --------------------------------------------------------------------------
# Grafo con tools: caminos, orden, fallback y credenciales
# --------------------------------------------------------------------------
class Sources:
    def __init__(self, incident_result=None, chunks=None):
        self.incident_result = incident_result
        self.chunks = chunks or []
        self.calls = []

    def incidents(self, query, *, mcp=None):
        self.calls.append("incidents")
        return self.incident_result or IncidentToolResult(ok=True, mode="detail", query=query, incidents=[INCIDENT], total=1)

    def inventory(self, query, *, mcp=None):
        self.calls.append("inventory")
        return lookup_inventory(query, mcp=products_mcp())

    def retrieve(self, question, *, k, min_score):
        self.calls.append("rag")
        return list(self.chunks)

    def generate(self, question, context):
        self.calls.append("generate")
        return "La ventana de devolución estándar es de 30 días desde la entrega."


def agent_for(sources, tmp_path=None):
    return KnowledgeAgent(retrieve_fn=sources.retrieve, generate_fn=sources.generate, incidents_fn=sources.incidents,
                          inventory_fn=sources.inventory,
                          mcp=FakeMcp(), checkpointer=sqlite_checkpointer(tmp_path / "cp.sqlite" if tmp_path else ":memory:"),
                          traces=TraceStore(":memory:"))


def test_ticket_question_uses_only_the_incidents_tool():
    src = Sources()
    agent = agent_for(src)
    result = agent.run(f"¿En qué estado está el ticket {TICKET_ID}?")

    assert src.calls == ["incidents"] and result.sources == ["incidents"]
    assert executed_nodes(agent.traces.get_trace(result.run_id)) == ["receive_question", "plan_sources", "mcp_incidents", "compose_answer"]
    assert "**En curso**" in result.answer and TICKET_ID in result.answer


def test_policy_question_uses_only_rag():
    chunk = {"company": "trackflow", "source_document": "returns-policy", "section": "s", "language": "es", "chunk_index": 1,
             "text": "Ventana: 30 días.", "score": 0.6}
    src = Sources(chunks=[chunk])
    result = agent_for(src).run("¿Cuál es la ventana de devolución estándar?")
    assert src.calls == ["rag", "generate"] and result.sources == ["rag"] and result.final_node == "generate"


def test_mixed_question_uses_tool_then_rag_and_combines_in_that_order():
    chunk = {"company": "trackflow", "source_document": "returns-policy", "section": "s", "language": "es", "chunk_index": 1,
             "text": "Ventana: 30 días.", "score": 0.6}
    src = Sources(chunks=[chunk])
    agent = agent_for(src)
    result = agent.run(f"¿En qué estado está el ticket {TICKET_ID} y cuál es la ventana de devolución estándar?")

    assert src.calls == ["incidents", "rag", "generate"]
    assert sources_used(agent.traces.get_trace(result.run_id)) == ["incidents", "rag"]
    assert result.final_node == "compose_answer"
    assert result.answer.index("En curso") < result.answer.index("30 días")


def test_tool_failure_falls_back_honestly_and_never_invents_a_status():
    down = IncidentToolResult(ok=False, mode="detail", query=IncidentQuery(ticket_id=TICKET_ID), error_type="timeout")
    result = agent_for(Sources(incident_result=down)).run(f"¿En qué estado está el ticket {TICKET_ID}?")

    assert result.final_node == "compose_answer"
    assert "No pude confirmar el estado de ese ticket ahora mismo" in result.answer
    for label in ("Abierta", "En curso", "Resuelta", "Descartada"):
        assert label not in result.answer


def test_unexpected_tool_exception_does_not_break_the_run():
    def boom(query, *, mcp=None):
        raise ValueError("respuesta corrupta")

    src = Sources()
    agent = KnowledgeAgent(retrieve_fn=src.retrieve, generate_fn=src.generate, incidents_fn=boom, inventory_fn=src.inventory,
                           mcp=FakeMcp(), checkpointer=sqlite_checkpointer(":memory:"), traces=TraceStore(":memory:"))
    result = agent.run(f"¿En qué estado está el ticket {TICKET_ID}?")
    assert "No pude confirmar" in result.answer
    assert node_output(agent.traces.get_trace(result.run_id), "mcp_incidents")["incident_result"]["error_type"] == "unavailable"


def test_inventory_question_answers_with_live_stock():
    src = Sources()
    result = agent_for(src).run("¿Cuántas unidades quedan de CSM-SRM-030?")
    assert src.calls == ["inventory"] and result.sources == ["inventory"]
    assert "9 unidades en stock" in result.answer and "POR DEBAJO DEL MÍNIMO" in result.answer


def test_tool_nodes_call_the_real_lookup_through_the_injected_mcp_client(tmp_path):
    """Sin dobles de lookup: el nodo mcp_incidents usa lookup_incidents → cliente MCP inyectado."""
    mcp = FakeMcp(incidents_get_ticket=ok(INCIDENT))
    agent = KnowledgeAgent(retrieve_fn=lambda *a, **k: [], generate_fn=lambda q, c: "", mcp=mcp,
                           checkpointer=sqlite_checkpointer(tmp_path / "cp.sqlite"), traces=TraceStore(":memory:"))
    result = agent.run(f"¿En qué estado está el ticket {TICKET_ID}?")

    assert mcp.calls == [("incidents_get_ticket", {"incident_id": TICKET_ID})]
    output = node_output(agent.traces.get_trace(result.run_id), "mcp_incidents")["incident_result"]
    assert output["mcp_tool"] == "incidents_get_ticket" and output["ok"] is True
    assert "**En curso**" in result.answer


def test_mixed_question_sends_only_the_policy_clause_to_rag_preserving_accents():
    plan = plan_sources(f"¿En qué estado está el ticket {TICKET_ID} y cuál es la ventana de devolución estándar?")
    # Con el UUID dentro el mejor chunk puntuaba 0.419 (< 0.45); el modelo distingue tildes, así que se conservan.
    assert plan["rag_question"] == "¿Cuál es la ventana de devolución estándar?"
    assert "rag_question" not in plan_sources("¿Cuál es la ventana de devolución estándar?")


def test_rag_and_generation_use_the_policy_subquestion_in_mixed_runs():
    seen = []
    src = Sources(chunks=[{"company": "trackflow", "source_document": "returns-policy", "section": "s", "language": "es",
                           "chunk_index": 1, "text": "Ventana: 30 días.", "score": 0.57}])
    agent = KnowledgeAgent(retrieve_fn=lambda q, *, k, min_score: seen.append(("retrieve", q)) or src.chunks,
                           generate_fn=lambda q, c: seen.append(("generate", q)) or "30 días.",
                           incidents_fn=src.incidents, inventory_fn=src.inventory,
                           mcp=FakeMcp(), checkpointer=sqlite_checkpointer(":memory:"), traces=TraceStore(":memory:"))
    agent.run(f"¿En qué estado está el ticket {TICKET_ID} y cuál es la ventana de devolución estándar?")
    assert seen == [("retrieve", "¿Cuál es la ventana de devolución estándar?"), ("generate", "¿Cuál es la ventana de devolución estándar?")]
