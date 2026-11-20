"""Pruebas deterministas de las tools (Parte 2), del enrutamiento y del grafo con tools.

Las tools se prueban contra ``httpx.MockTransport`` (y un servidor HTTP local
lento para el timeout real); la evidencia contra los servicios reales está en
los evals (``test_agent_evals.py``), grabados con el API en marcha.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx
import pytest

import services.agent.tools as tools
from services.agent.graph import KnowledgeAgent, sqlite_checkpointer, sources_used
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

TICKET_ID = "8a4f3f2e-6c1d-5b9a-9e2f-0123456789ab"
INCIDENT = {
    "id": TICKET_ID, "title": "Paquete no entregado en Zaragoza", "category": "lost_parcel", "status": "in_progress",
    "origin": "customer", "branch": "zaragoza_office", "description": "texto libre",
    "created_at": "2026-11-02T00:00:00+00:00", "updated_at": "2026-11-10T09:30:00+00:00",
}
PRODUCTS = [
    {"id": 3, "name": "Auriculares inalámbricos Pro", "sku": "TEC-EAR-001", "client_name": "SoundCo", "client_id": "c-1",
     "category": "electronics", "warehouse": "LA", "current_stock": 74, "minimum_stock": 20},
    {"id": 4, "name": "Sérum facial hidratante 30ml", "sku": "CSM-SRM-030", "client_name": "GlowLab", "client_id": "c-2",
     "category": "cosmetics", "warehouse": "ZGZ", "current_stock": 9, "minimum_stock": 15},
]


def transport(handler):
    calls = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    return httpx.MockTransport(wrapped), calls


# --------------------------------------------------------------------------
# Tool de incidencias
# --------------------------------------------------------------------------
def test_incident_detail_is_read_with_get_and_caller_token():
    mock, calls = transport(lambda r: httpx.Response(200, json=INCIDENT))
    result = lookup_incidents(IncidentQuery(ticket_id=TICKET_ID), auth_token="jwt-del-usuario", transport=mock)

    assert result.ok and result.mode == "detail"
    assert result.incidents[0].status == "in_progress" and result.incidents[0].branch == "zaragoza_office"
    assert [(c.method, c.url.path) for c in calls] == [("GET", f"/api/incidents/{TICKET_ID}")]
    assert calls[0].headers["Authorization"] == "Bearer jwt-del-usuario"
    assert "description" not in result.model_dump()["incidents"][0]  # contrato tipado: solo los campos declarados


def test_incident_search_passes_filters_as_query_params(monkeypatch):
    monkeypatch.setenv("AGENT_API_TOKEN", "token-de-servicio-desde-entorno")
    mock, calls = transport(lambda r: httpx.Response(200, json=[INCIDENT, {**INCIDENT, "id": str(uuid4()), "status": "open"}]))
    result = lookup_incidents(IncidentQuery(status="open", category="returns_issue"), transport=mock)

    assert result.ok and result.mode == "list" and result.total == 2
    assert dict(calls[0].url.params) == {"status": "open", "category": "returns_issue"}
    assert calls[0].headers["Authorization"] == "Bearer token-de-servicio-desde-entorno"


@pytest.mark.parametrize("status,error", [(404, "not_found"), (401, "unauthorized"), (500, "unavailable")])
def test_incident_http_errors_become_typed_errors(status, error):
    mock, _ = transport(lambda r: httpx.Response(status, json={"detail": "x"}))
    result = lookup_incidents(IncidentQuery(ticket_id=TICKET_ID), transport=mock)
    assert (result.ok, result.error_type, result.incidents) == (False, error, [])


def test_incident_connection_error_is_unavailable_not_exception():
    def refuse(request):
        raise httpx.ConnectError("connection refused")

    mock, _ = transport(refuse)
    assert lookup_incidents(IncidentQuery(ticket_id=TICKET_ID), transport=mock).error_type == "unavailable"


def test_non_uuid_ticket_reference_is_rejected_without_calling_the_service():
    mock, calls = transport(lambda r: httpx.Response(200, json=INCIDENT))
    result = lookup_incidents(IncidentQuery(raw_ticket_reference="482"), transport=mock)
    assert (result.ok, result.error_type, calls) == (False, "invalid_query", [])


def test_timeout_is_explicit_numeric_and_does_not_hang(monkeypatch):
    assert tools.TOOL_TIMEOUT_SECONDS == 4.0

    class Slow(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(1.5)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("AGENT_API_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}")
        monkeypatch.setattr(tools, "TOOL_TIMEOUT_SECONDS", 0.3)
        started = time.perf_counter()
        result = lookup_incidents(IncidentQuery(ticket_id=TICKET_ID))
        elapsed = time.perf_counter() - started
    finally:
        server.shutdown()
    assert result.error_type == "timeout"
    assert elapsed < 1.0  # cortó en el timeout, no esperó la respuesta lenta


def test_tools_are_read_only_writes_are_blocked_before_sending():
    mock, calls = transport(lambda r: httpx.Response(200, json={}))
    with tools._client("jwt", mock) as client:
        for method in ("POST", "PATCH", "PUT", "DELETE"):
            with pytest.raises(ReadOnlyViolation):
                client.request(method, f"/api/incidents/{TICKET_ID}/status", json={"status": "resolved"})
    assert calls == []  # ninguna escritura llegó al servicio


def test_no_hardcoded_token_without_env_or_caller(monkeypatch):
    monkeypatch.delenv("AGENT_API_TOKEN", raising=False)
    mock, calls = transport(lambda r: httpx.Response(401, json={"detail": "x"}))
    assert lookup_incidents(IncidentQuery(ticket_id=TICKET_ID), transport=mock).error_type == "unauthorized"
    assert "Authorization" not in calls[0].headers


# --------------------------------------------------------------------------
# Tool de inventario
# --------------------------------------------------------------------------
def test_inventory_by_sku_reads_get_inventory_products():
    mock, calls = transport(lambda r: httpx.Response(200, json=PRODUCTS))
    result = lookup_inventory(InventoryQuery(sku="tec-ear-001"), auth_token="jwt", transport=mock)

    assert result.ok and [(i.sku, i.current_stock) for i in result.items] == [("TEC-EAR-001", 74)]
    assert [(c.method, c.url.path) for c in calls] == [("GET", "/inventory/products")]


def test_inventory_by_name_ignores_accents_and_respects_warehouse():
    mock, _ = transport(lambda r: httpx.Response(200, json=PRODUCTS))
    assert lookup_inventory(InventoryQuery(name_terms=["serum", "hidratante"]), transport=mock).items[0].sku == "CSM-SRM-030"
    assert lookup_inventory(InventoryQuery(name_terms=["serum"], warehouse="LA"), transport=mock).error_type == "not_found"


def test_inventory_failure_is_typed():
    mock, _ = transport(lambda r: httpx.Response(503, json={}))
    assert lookup_inventory(InventoryQuery(sku="TEC-EAR-001"), transport=mock).error_type == "unavailable"


# --------------------------------------------------------------------------
# Enrutamiento automático (sin que el usuario indique la fuente)
# --------------------------------------------------------------------------
def test_every_rag_eval_question_still_routes_only_to_rag():
    from pathlib import Path
    queries = json.loads((Path(__file__).resolve().parents[2] / "mock_data/eval/test-queries.json").read_text(encoding="utf-8"))
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
        self.tokens = []

    def incidents(self, query, *, auth_token=None):
        self.calls.append("incidents")
        self.tokens.append(auth_token)
        return self.incident_result or IncidentToolResult(ok=True, mode="detail", query=query, incidents=[INCIDENT], total=1)

    def inventory(self, query, *, auth_token=None):
        self.calls.append("inventory")
        mock, _ = transport(lambda r: httpx.Response(200, json=PRODUCTS))
        return lookup_inventory(query, transport=mock)

    def retrieve(self, question, *, k, min_score):
        self.calls.append("rag")
        return list(self.chunks)

    def generate(self, question, context):
        self.calls.append("generate")
        return "La ventana de devolución estándar es de 30 días desde la entrega."


def agent_for(sources, tmp_path=None):
    return KnowledgeAgent(retrieve_fn=sources.retrieve, generate_fn=sources.generate, incidents_fn=sources.incidents,
                          inventory_fn=sources.inventory,
                          checkpointer=sqlite_checkpointer(tmp_path / "cp.sqlite" if tmp_path else ":memory:"),
                          traces=TraceStore(":memory:"))


def test_ticket_question_uses_only_the_incidents_tool():
    src = Sources()
    agent = agent_for(src)
    result = agent.run(f"¿En qué estado está el ticket {TICKET_ID}?", auth_token="jwt")

    assert src.calls == ["incidents"] and result.sources == ["incidents"]
    assert executed_nodes(agent.traces.get_trace(result.run_id)) == ["receive_question", "plan_sources", "lookup_incidents", "compose_answer"]
    assert "**En curso**" in result.answer and TICKET_ID in result.answer
    assert src.tokens == ["jwt"]


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
    def boom(query, *, auth_token=None):
        raise ValueError("respuesta corrupta")

    src = Sources()
    agent = KnowledgeAgent(retrieve_fn=src.retrieve, generate_fn=src.generate, incidents_fn=boom, inventory_fn=src.inventory,
                           checkpointer=sqlite_checkpointer(":memory:"), traces=TraceStore(":memory:"))
    result = agent.run(f"¿En qué estado está el ticket {TICKET_ID}?")
    assert "No pude confirmar" in result.answer
    assert node_output(agent.traces.get_trace(result.run_id), "lookup_incidents")["incident_result"]["error_type"] == "unavailable"


def test_inventory_question_answers_with_live_stock():
    src = Sources()
    result = agent_for(src).run("¿Cuántas unidades quedan de CSM-SRM-030?")
    assert src.calls == ["inventory"] and result.sources == ["inventory"]
    assert "9 unidades en stock" in result.answer and "POR DEBAJO DEL MÍNIMO" in result.answer


def test_caller_token_is_not_persisted_in_checkpoints_or_trace(tmp_path):
    src = Sources()
    agent = agent_for(src, tmp_path)
    result = agent.run(f"¿En qué estado está el ticket {TICKET_ID}?", auth_token="jwt-secreto-del-usuario")

    assert src.tokens == ["jwt-secreto-del-usuario"]
    assert "jwt-secreto-del-usuario" not in (tmp_path / "cp.sqlite").read_bytes().decode("latin-1")
    assert "jwt-secreto-del-usuario" not in json.dumps(agent.traces.get_trace(result.run_id))
    assert agent._credentials == {}  # se descarta al terminar la corrida


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
                           checkpointer=sqlite_checkpointer(":memory:"), traces=TraceStore(":memory:"))
    agent.run(f"¿En qué estado está el ticket {TICKET_ID} y cuál es la ventana de devolución estándar?")
    assert seen == [("retrieve", "¿Cuál es la ventana de devolución estándar?"), ("generate", "¿Cuál es la ventana de devolución estándar?")]
