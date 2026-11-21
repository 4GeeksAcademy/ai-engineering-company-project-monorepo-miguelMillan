"""Servidor MCP de TrackFlow contra el Incidents Manager y el inventario REALES (esta app FastAPI).

Pila: proveedor OIDC local → servidor MCP (``mcps/trackflow_mcp``, mcpauth) →
API de TrackFlow (``create_app`` con TinyDB e inventario SQL temporales), todo
por HTTP real. Comprueba que los cambios de estado pasan por el ciclo de vida
del Incidents Manager, que el inventario no se puede modificar desde MCP y que
el agente LangGraph consulta tickets como cliente MCP.
"""

import json

import httpx
import pytest

import routes.agent  # noqa: F401  (añade la raíz del repo a sys.path, como en producción)
from mcpauth.config import AuthServerType
from mcpauth.utils import fetch_server_config
from mcps.trackflow_mcp.config import Settings
from mcps.trackflow_mcp.dev.local_oidc import CLIENT_SECRET, free_port, serve, start_local_oidc
from mcps.trackflow_mcp.server import build_app
from services.agent.graph import KnowledgeAgent, sqlite_checkpointer
from services.agent.mcp_client import OAuthClientCredentials, TrackflowMcpTools
from services.agent.tracing import TraceStore, executed_nodes

TEST_SECRET = "test-secret-key-with-at-least-32-bytes"  # mismo valor que tests/conftest.py del API

SERVICE_EMAIL, SERVICE_PASSWORD = "mcp-service@example.com", "contraseña-de-servicio-mcp"


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'inventory.db'}")
    from main import create_app

    api_port = free_port()
    api_server, api_thread = serve(create_app(tmp_path / "incidents.json"), api_port)
    api = f"http://127.0.0.1:{api_port}"
    assert httpx.post(f"{api}/users", json={"email": SERVICE_EMAIL, "password": SERVICE_PASSWORD}).status_code == 201
    user_token = httpx.post(f"{api}/auth/login", json={"email": SERVICE_EMAIL, "password": SERVICE_PASSWORD}).json()["access_token"]
    headers = {"Authorization": f"Bearer {user_token}"}
    ticket = httpx.post(f"{api}/api/incidents", headers=headers, json={
        "title": "Paquete extraviado en Zaragoza", "description": "El transportista no lo localiza.",
        "category": "lost_parcel", "origin": "customer", "branch": "zaragoza_office"}).json()
    product = httpx.post(f"{api}/inventory/products", headers=headers, json={
        "name": "Sérum facial hidratante 30ml", "sku": "CSM-SRM-030", "client_name": "GlowLab",
        "category": "cosmetics", "warehouse": "LA", "minimum_stock": 15}).json()
    assert httpx.post(f"{api}/inventory/orders/inbound", headers=headers, json={
        "sku_id": product["id"], "quantity": 9, "reference": "ALB-001", "warehouse": "LA"}).status_code == 201

    provider, idp = start_local_oidc()
    mcp_port = free_port()
    settings = Settings(port=mcp_port, resource_url=f"http://127.0.0.1:{mcp_port}/mcp", oidc_issuer=provider.issuer,
                        api_base_url=api, backend_email=SERVICE_EMAIL, backend_password=SERVICE_PASSWORD,
                        audit_log_path=str(tmp_path / "mcp-audit.log"))
    provider.resource = settings.resource_url
    mcp_server, mcp_thread = serve(build_app(settings, auth_server=fetch_server_config(provider.issuer, AuthServerType.OIDC)), mcp_port)
    yield {"api": api, "headers": headers, "ticket": ticket, "product": product, "oidc": provider, "mcp_url": settings.resource_url}
    for server, thread in ((mcp_server, mcp_thread), (api_server, api_thread)):
        server.should_exit = True
        thread.join(5)
    idp.shutdown()


def call(stack, client, name, arguments):
    response = httpx.post(stack["mcp_url"], timeout=15, headers={
        "Authorization": f"Bearer {stack['oidc'].token(client)}", "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                   "params": {"name": name, "arguments": arguments}})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    return (json.loads(result["content"][0]["text"])["error"], None) if result["isError"] else (None, result["structuredContent"])


def test_ticket_lifecycle_through_mcp_changes_the_real_incidents_manager(stack):
    error, created = call(stack, "trackflow-ops-console", "incidents_create_ticket", {
        "title": "Palé dañado en recepción", "description": "Llegó con el film roto.", "category": "warehouse_incident",
        "origin": "internal", "branch": "zaragoza_warehouse"})
    assert error is None and created["status"] == "open"
    for status in ("in_progress", "resolved"):
        error, changed = call(stack, "trackflow-ops-console", "incidents_update_ticket_status",
                              {"incident_id": created["id"], "status": status})
        assert error is None and changed["status"] == status
    error, _ = call(stack, "trackflow-ops-console", "incidents_update_ticket_status", {"incident_id": created["id"], "status": "open"})
    assert error["code"] == "INVALID_STATUS_TRANSITION" and error["details"]["allowed"] == []

    stored = httpx.get(f"{stack['api']}/api/incidents/{created['id']}", headers=stack["headers"]).json()
    assert stored["status"] == "resolved" and stored["title"] == "Palé dañado en recepción"


def test_inventory_is_read_through_mcp_and_cannot_be_modified(stack):
    error, result = call(stack, "trackflow-inventory-viewer", "inventory_query_products", {"sku": "CSM-SRM-030"})
    assert error is None
    assert [(p["current_stock"], p["minimum_stock"], p["below_minimum"]) for p in result["products"]] == [(9, 15, True)]

    error, _ = call(stack, "trackflow-ops-console", "inventory_query_products",
                    {"operation": "update", "product_id": stack["product"]["id"], "current_stock": 500})
    assert error["code"] == "INVENTORY_READ_ONLY"
    product = httpx.get(f"{stack['api']}/inventory/products/{stack['product']['id']}", headers=stack["headers"]).json()
    assert product["current_stock"] == 9
    assert len(httpx.get(f"{stack['api']}/inventory/orders", headers=stack["headers"]).json()) == 1


def test_agent_reads_real_ticket_status_as_mcp_client(stack, tmp_path):
    mcp = TrackflowMcpTools(stack["mcp_url"], auth=OAuthClientCredentials(stack["oidc"].issuer, "trackflow-agent", CLIENT_SECRET))
    agent = KnowledgeAgent(retrieve_fn=lambda *a, **k: [], generate_fn=lambda q, c: "", mcp=mcp,
                           checkpointer=sqlite_checkpointer(tmp_path / "cp.sqlite"), traces=TraceStore(tmp_path / "traces.sqlite"))
    ticket_id = stack["ticket"]["id"]
    result = agent.run(f"¿En qué estado está el ticket {ticket_id}?")

    assert result.sources == ["incidents"]
    assert executed_nodes(agent.traces.get_trace(result.run_id)) == ["resolve_pending_memory", "receive_question", "recall_memory", "plan_sources", "mcp_incidents", "compose_answer", "propose_memory"]
    assert "**Abierta**" in result.answer and "Paquete extraviado en Zaragoza" in result.answer
