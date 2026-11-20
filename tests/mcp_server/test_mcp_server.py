"""Servidor MCP de TrackFlow: OAuth vía MCP Auth, mínimo privilegio, discovery, solo lectura de inventario,
ciclo de vida de tickets, códigos de error y auditoría."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from mcps.trackflow_mcp.backend import InventoryWriteBlocked, TrackflowBackend
from mcps.trackflow_mcp.errors import TOOL_ERROR_DESCRIPTIONS, ToolErrorCode
from mcps.trackflow_mcp.server import create_mcp
from tests.mcp_server.conftest import TICKET_ID, call_tool, rpc, tool_error

ALL_TOOLS = {"incidents_get_ticket", "incidents_list_tickets", "incidents_create_ticket",
             "incidents_update_ticket_status", "inventory_query_products"}


def www_authenticate(response: httpx.Response) -> str:
    return response.headers.get("www-authenticate", "")


# --------------------------------------------------------------------------
# OAuth (MCP Auth): Protected Resource Metadata y bearer JWT
# --------------------------------------------------------------------------
def test_protected_resource_metadata_is_published(mcp_server, oidc):
    response = httpx.get(mcp_server.url.replace("/mcp", "/.well-known/oauth-protected-resource/mcp"))
    assert response.status_code == 200
    metadata = response.json()
    assert metadata["resource"] == mcp_server.resource
    assert metadata["authorization_servers"] == [oidc.issuer]
    assert metadata["scopes_supported"] == ["mcp:access", "incidents:read", "incidents:write", "inventory:read"]
    assert metadata["bearer_methods_supported"] == ["header"]


def test_without_token_nothing_can_be_listed_or_called(mcp_server, backend):
    for method, params in (("tools/list", {}), ("tools/call", {"name": "incidents_get_ticket", "arguments": {"incident_id": TICKET_ID}})):
        response = rpc(mcp_server.url, method, params)
        assert response.status_code == 401
        assert response.json()["error"] == "missing_auth_header"
        assert 'resource_metadata="' + mcp_server.url.replace("/mcp", "/.well-known/oauth-protected-resource/mcp") in www_authenticate(response)
    assert backend.requests == []


@pytest.mark.parametrize("case,expected", [
    ("firma_de_otra_clave", "invalid_token"),
    ("expirado", "invalid_token"),
    ("otra_audiencia", "invalid_audience"),
    ("otro_emisor", "invalid_issuer"),
    ("basura", "invalid_token"),
])
def test_invalid_tokens_are_rejected_with_specific_codes(mcp_server, oidc, case, expected):
    tokens = {
        "firma_de_otra_clave": oidc.token(key=rsa.generate_private_key(public_exponent=65537, key_size=2048)),
        "expirado": oidc.token(exp_in=-3600),
        "otra_audiencia": oidc.token(aud="https://otro-servicio.example.com"),
        "otro_emisor": oidc.token(iss="https://idp-desconocido.example.com"),
        "basura": "no-es-un-jwt",
    }
    response = rpc(mcp_server.url, "tools/list", token=tokens[case])
    assert response.status_code == 401
    assert response.json()["error"] == expected
    assert f'error="{expected}"' in www_authenticate(response)


def test_missing_base_scope_is_403_from_mcp_auth_required_scopes(mcp_server, oidc):
    response = rpc(mcp_server.url, "tools/list", token=oidc.token("sin-acceso-mcp"))
    assert response.status_code == 403
    assert response.json()["error"] == "missing_required_scopes"


def test_fastmcp_builtin_auth_is_not_used():
    server = create_mcp(TrackflowBackend.__new__(TrackflowBackend))
    assert server.auth is None
    tools = asyncio.run(server.list_tools(run_middleware=False))
    assert {tool.name for tool in tools} == ALL_TOOLS
    assert all(getattr(tool, "auth", None) is None for tool in tools)


# --------------------------------------------------------------------------
# Discovery: nombre, descripción, esquemas de entrada/salida y anotaciones
# --------------------------------------------------------------------------
def test_discovery_documents_every_tool_for_an_agent_without_human_context(mcp_server, oidc):
    init = rpc(mcp_server.url, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                              "clientInfo": {"name": "pytest", "version": "1"}}, oidc.token())
    instructions = init.json()["result"]["instructions"]
    for code in ToolErrorCode:
        assert code.value in instructions
    assert "INVENTORY_READ_ONLY" in instructions and "open → in_progress | discarded" in instructions

    tools = {t["name"]: t for t in rpc(mcp_server.url, "tools/list", token=oidc.token()).json()["result"]["tools"]}
    assert set(tools) == ALL_TOOLS
    for name, tool in tools.items():
        assert len(tool["description"]) > 80 and "scope" in tool["description"], name
        assert tool["inputSchema"]["type"] == "object" and tool["outputSchema"]["type"] == "object", name
        assert all(prop.get("description") for prop in tool["inputSchema"]["properties"].values()), name
    for name in ("incidents_get_ticket", "incidents_list_tickets", "inventory_query_products"):
        assert tools[name]["annotations"]["readOnlyHint"] is True
    for name in ("incidents_create_ticket", "incidents_update_ticket_status"):
        assert tools[name]["annotations"]["readOnlyHint"] is False
    # Valores de dominio idénticos a los del Incidents Manager y el inventario
    status_enum = tools["incidents_update_ticket_status"]["inputSchema"]["properties"]["status"]["enum"]
    assert status_enum == ["open", "in_progress", "resolved", "discarded"]
    assert tools["incidents_create_ticket"]["inputSchema"]["required"] == ["title", "description", "category", "origin", "branch"]
    output = tools["inventory_query_products"]["outputSchema"]
    product = output.get("$defs", {}).get("Product") or output["properties"]["products"]["items"]
    assert set(product["properties"]) >= {
        "id", "sku", "name", "client_name", "client_id", "category", "warehouse", "current_stock", "minimum_stock"}


@pytest.mark.parametrize("client,visible", [
    ("trackflow-agent", {"incidents_get_ticket", "incidents_list_tickets", "inventory_query_products"}),
    ("trackflow-ops-console", ALL_TOOLS),
    ("trackflow-inventory-viewer", {"inventory_query_products"}),
])
def test_tools_list_only_shows_what_the_token_may_invoke(mcp_server, oidc, client, visible):
    tools = rpc(mcp_server.url, "tools/list", token=oidc.token(client)).json()["result"]["tools"]
    assert {t["name"] for t in tools} == visible


# --------------------------------------------------------------------------
# Mínimo privilegio por tool
# --------------------------------------------------------------------------
def test_read_only_client_cannot_create_or_update_tickets(mcp_server, oidc, backend):
    token = oidc.token("trackflow-agent")
    create = tool_error(call_tool(mcp_server.url, token, "incidents_create_ticket", {
        "title": "x", "description": "y", "category": "other", "origin": "internal", "branch": "central"}))
    update = tool_error(call_tool(mcp_server.url, token, "incidents_update_ticket_status", {"incident_id": TICKET_ID, "status": "discarded"}))
    for error, tool in ((create, "incidents_create_ticket"), (update, "incidents_update_ticket_status")):
        assert error["code"] == "INSUFFICIENT_SCOPE"
        assert error["details"] == {"tool": tool, "required_scope": "incidents:write",
                                    "granted_scopes": ["incidents:read", "inventory:read", "mcp:access"]}
    assert backend.writes_to("/api/incidents") == []


def test_inventory_viewer_cannot_read_tickets(mcp_server, oidc, backend):
    error = tool_error(call_tool(mcp_server.url, oidc.token("trackflow-inventory-viewer"), "incidents_get_ticket", {"incident_id": TICKET_ID}))
    assert error["code"] == "INSUFFICIENT_SCOPE" and error["details"]["required_scope"] == "incidents:read"
    assert not any(path.startswith("/api/incidents") for _, path, _ in backend.requests)


# --------------------------------------------------------------------------
# Incidents Manager: consultar, crear y cambiar estado (endpoint de ciclo de vida)
# --------------------------------------------------------------------------
def test_full_ticket_flow_create_update_status_and_query(mcp_server, oidc, backend):
    token = oidc.token("trackflow-ops-console")
    created = call_tool(mcp_server.url, token, "incidents_create_ticket", {
        "title": "Palé dañado en recepción", "description": "Llegó con el film roto.", "category": "warehouse_incident",
        "origin": "internal", "branch": "zaragoza_warehouse"})["structuredContent"]
    assert created["status"] == "open"
    changed = call_tool(mcp_server.url, token, "incidents_update_ticket_status",
                        {"incident_id": created["id"], "status": "in_progress"})["structuredContent"]
    assert (changed["previous_status"], changed["status"]) == ("open", "in_progress")
    detail = call_tool(mcp_server.url, token, "incidents_get_ticket", {"incident_id": created["id"]})["structuredContent"]
    assert detail["status"] == "in_progress" and detail["branch"] == "zaragoza_warehouse"

    writes = backend.writes_to("/api/incidents")
    assert writes == [
        ("POST", "/api/incidents", {"title": "Palé dañado en recepción", "description": "Llegó con el film roto.",
                                    "category": "warehouse_incident", "origin": "internal", "branch": "zaragoza_warehouse"}),
        ("PATCH", f"/api/incidents/{created['id']}/status", {"status": "in_progress"}),
    ]
    # Nunca un PATCH genérico sobre el recurso de incidencia
    assert not any(m == "PATCH" and not p.endswith("/status") for m, p, _ in backend.requests)


def test_invalid_transition_is_explained_with_the_allowed_states(mcp_server, oidc):
    token = oidc.token("trackflow-ops-console")
    error = tool_error(call_tool(mcp_server.url, token, "incidents_update_ticket_status", {"incident_id": TICKET_ID, "status": "resolved"}))
    assert error["code"] == "INVALID_STATUS_TRANSITION"
    assert error["details"]["current_status"] == "open" and error["details"]["allowed"] == ["in_progress", "discarded"]


def test_list_tickets_filters_with_domain_values(mcp_server, oidc, backend):
    result = call_tool(mcp_server.url, oidc.token("trackflow-agent"), "incidents_list_tickets",
                       {"status": "open", "category": "returns_issue"})["structuredContent"]
    assert result["total"] == 1 and result["incidents"][0]["id"] == TICKET_ID
    assert ("GET", "/api/incidents", None) in backend.requests


@pytest.mark.parametrize("arguments,code,field", [
    ({"incident_id": "482"}, "VALIDATION_ERROR", "incident_id"),
    ({"incident_id": "00000000-0000-4000-8000-000000000000"}, "NOT_FOUND", None),
])
def test_validation_and_not_found_have_their_own_codes(mcp_server, oidc, arguments, code, field):
    error = tool_error(call_tool(mcp_server.url, oidc.token(), "incidents_get_ticket", arguments))
    assert error["code"] == code
    if field:
        assert error["details"]["errors"][0]["field"] == field


def test_invalid_domain_value_is_a_validation_error(mcp_server, oidc):
    error = tool_error(call_tool(mcp_server.url, oidc.token(), "incidents_list_tickets", {"status": "closed"}))
    assert error["code"] == "VALIDATION_ERROR" and error["details"]["errors"][0]["field"] == "status"


# --------------------------------------------------------------------------
# Inventario: solo lectura por diseño; los intentos de escritura se rechazan explícitamente
# --------------------------------------------------------------------------
def test_inventory_read_list_and_get(mcp_server, oidc):
    token = oidc.token("trackflow-inventory-viewer")
    listed = call_tool(mcp_server.url, token, "inventory_query_products", {"below_minimum_only": True})["structuredContent"]
    assert [(p["sku"], p["current_stock"], p["below_minimum"]) for p in listed["products"]] == [("CSM-SRM-030", 9, True)]
    got = call_tool(mcp_server.url, token, "inventory_query_products", {"operation": "get", "product_id": 1})["structuredContent"]
    assert got["products"][0]["sku"] == "CLT-SNK-W-42-Z" and got["products"][0]["warehouse"] == "ZGZ"


@pytest.mark.parametrize("name,arguments", [
    ("inventory_query_products", {"operation": "update", "product_id": 1, "current_stock": 999}),
    ("inventory_query_products", {"operation": "set_stock", "product_id": 1}),
    ("inventory_query_products", {"operation": "delete", "product_id": 1}),
    ("inventory_query_products", {"operation": "get", "product_id": 1, "minimum_stock": 3}),
    ("inventory_query_products", {"quantity": -5, "sku": "CLT-SNK-W-42-Z"}),
    ("inventory_update_stock", {"product_id": 1, "current_stock": 0}),
])
def test_inventory_write_attempts_are_explicitly_rejected(mcp_server, oidc, backend, name, arguments):
    # Incluso con el token con más permisos: no existe scope de escritura de inventario.
    error = tool_error(call_tool(mcp_server.url, oidc.token("trackflow-ops-console"), name, arguments))
    assert error["code"] == "INVENTORY_READ_ONLY"
    assert error["details"]["allowed_operations"] == ["get", "list"] and error["details"]["reason"]
    assert backend.writes_to("/inventory") == []


def test_inventory_backend_client_blocks_writes_before_sending(backend):
    from mcps.trackflow_mcp.config import Settings

    client = TrackflowBackend(Settings(backend_email="a@example.com", backend_password="x"), transport=httpx.MockTransport(backend.handler))
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(InventoryWriteBlocked):
            asyncio.run(client.request(method, "/inventory/products/1/stock", json={"quantity": 1}))
    assert backend.writes_to("/inventory") == []


# --------------------------------------------------------------------------
# Fallos del backend: códigos propios, sin inventar resultados
# --------------------------------------------------------------------------
def test_backend_login_is_renewed_once_when_the_backend_rejects_the_token(mcp_server, oidc, backend):
    token = oidc.token()
    call_tool(mcp_server.url, token, "incidents_get_ticket", {"incident_id": TICKET_ID})
    backend.valid_tokens.clear()  # p. ej. el JWT de la cuenta de servicio caducó
    result = call_tool(mcp_server.url, token, "incidents_get_ticket", {"incident_id": TICKET_ID})
    assert result["isError"] is False and backend.logins == 2


@pytest.mark.parametrize("failure,code", [("down", "BACKEND_UNAVAILABLE"), ("slow", "BACKEND_TIMEOUT"), ("500", "BACKEND_UNAVAILABLE")])
def test_backend_failures_have_specific_codes(oidc, tmp_path, failure, code):
    from mcpauth.config import AuthServerType
    from mcpauth.utils import fetch_server_config

    from mcps.trackflow_mcp.config import Settings
    from mcps.trackflow_mcp.server import build_app
    from tests.mcp_server.conftest import free_port, serve

    def handler(request):
        if failure == "down":
            raise httpx.ConnectError("connection refused")
        if failure == "slow":
            raise httpx.ReadTimeout("timeout")
        if request.url.path == "/auth/login":
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(500, json={})

    port = free_port()
    settings = Settings(port=port, resource_url=f"http://127.0.0.1:{port}/mcp", oidc_issuer=oidc.issuer,
                        backend_email="a@example.com", backend_password="x", audit_log_path=str(tmp_path / "a.log"))
    oidc.resource = settings.resource_url
    app = build_app(settings, backend=TrackflowBackend(settings, transport=httpx.MockTransport(handler)),
                    auth_server=fetch_server_config(oidc.issuer, AuthServerType.OIDC))
    server, thread = serve(app, port)
    try:
        error = tool_error(call_tool(settings.resource_url, oidc.token(), "incidents_get_ticket", {"incident_id": TICKET_ID}))
    finally:
        server.should_exit = True
        thread.join(5)
    assert error["code"] == code


def test_every_error_code_is_documented():
    assert set(TOOL_ERROR_DESCRIPTIONS) == set(ToolErrorCode)


# --------------------------------------------------------------------------
# Trazabilidad: una línea de auditoría por invocación (tool, cliente, resultado)
# --------------------------------------------------------------------------
def test_every_invocation_and_auth_rejection_is_audited_without_tokens(mcp_server, oidc):
    ops, viewer = oidc.token("trackflow-ops-console"), oidc.token("trackflow-inventory-viewer")
    call_tool(mcp_server.url, ops, "incidents_get_ticket", {"incident_id": TICKET_ID})
    call_tool(mcp_server.url, ops, "inventory_query_products", {"operation": "update", "product_id": 1})
    call_tool(mcp_server.url, viewer, "incidents_get_ticket", {"incident_id": TICKET_ID})
    rpc(mcp_server.url, "tools/list")

    records = mcp_server.audit()
    calls = [(r["tool"], r["client_id"], r["outcome"], r.get("error_code")) for r in records if r["event"] == "tool_call"]
    assert calls == [
        ("incidents_get_ticket", "trackflow-ops-console", "ok", None),
        ("inventory_query_products", "trackflow-ops-console", "error", "INVENTORY_READ_ONLY"),
        ("incidents_get_ticket", "trackflow-inventory-viewer", "error", "INSUFFICIENT_SCOPE"),
    ]
    assert all(r["duration_ms"] >= 0 and r["subject"] for r in records if r["event"] == "tool_call")
    assert {"event": "auth_rejected", "status": 401, "error": "missing_auth_header"}.items() <= records[-1].items()
    raw = json.dumps(records)
    assert ops not in raw and viewer not in raw


# --------------------------------------------------------------------------
# Clientes MCP web (p. ej. MCP Playground): CORS solo para orígenes configurados
# --------------------------------------------------------------------------
def test_cors_preflight_only_for_configured_origins_and_auth_still_required(oidc, backend):
    from mcpauth.config import AuthServerType
    from mcpauth.utils import fetch_server_config
    from starlette.testclient import TestClient

    from mcps.trackflow_mcp.config import Settings
    from mcps.trackflow_mcp.server import build_app

    settings = Settings(resource_url="https://trackflow-8090.app.github.dev/mcp", oidc_issuer=oidc.issuer,
                        allowed_hosts=["trackflow-8090.app.github.dev"], cors_origins=["https://mcpplaygroundonline.com"])
    app = build_app(settings, backend=TrackflowBackend(settings, transport=httpx.MockTransport(backend.handler)),
                    auth_server=fetch_server_config(oidc.issuer, AuthServerType.OIDC))
    client = TestClient(app, base_url="https://trackflow-8090.app.github.dev")
    preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}
    allowed = client.options("/mcp", headers={"Origin": "https://mcpplaygroundonline.com", **preflight})
    assert allowed.status_code == 200 and allowed.headers["access-control-allow-origin"] == "https://mcpplaygroundonline.com"
    other = client.options("/mcp", headers={"Origin": "https://evil.example.com", **preflight})
    assert "access-control-allow-origin" not in other.headers
    unauthenticated = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                  headers={"Origin": "https://mcpplaygroundonline.com"})
    assert unauthenticated.status_code == 401
    assert "www-authenticate" in unauthenticated.headers["access-control-expose-headers"].lower()
