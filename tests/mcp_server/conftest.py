"""Fixtures del servidor MCP: proveedor OIDC local, backend registrado y servidor MCP real en uvicorn.

* ``oidc``: un proveedor OAuth 2.1/OIDC mínimo en un hilo HTTP. Publica
  discovery y JWKS (claves RSA reales) y emite tokens ``client_credentials``.
  mcpauth descubre su configuración y valida las firmas exactamente igual que
  con Keycloak.
* ``backend``: ``httpx.MockTransport`` que implementa las rutas del Incidents
  Manager y del inventario usadas por el servidor (con el mismo ciclo de vida de
  estados) y registra cada petición para comprobar ruta y método. La
  integración contra el API real está en
  ``services/api/tests/test_mcp_server_integration.py``.
* ``mcp_server``: ``build_app`` servido por uvicorn en un puerto libre (HTTP
  real; el transporte Streamable HTTP necesita el lifespan).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

import httpx
import pytest
from mcpauth.config import AuthServerType
from mcpauth.utils import fetch_server_config

from mcps.trackflow_mcp.backend import TrackflowBackend
from mcps.trackflow_mcp.dev.local_oidc import CLIENT_SCOPES, CLIENT_SECRET, free_port, serve, start_local_oidc  # noqa: F401
from mcps.trackflow_mcp.config import Settings
from mcps.trackflow_mcp.server import build_app

TICKET_ID = "d32a9b66-b81b-5dce-b90f-c4e8bfc27161"

@pytest.fixture(scope="session")
def oidc():
    provider, server = start_local_oidc()
    yield provider
    server.shutdown()


class FakeIncidentsAndInventory:
    """Rutas de services/api usadas por el servidor MCP, con registro de cada petición."""

    TRANSITIONS = {"open": {"in_progress", "discarded"}, "in_progress": {"resolved", "discarded"},
                   "resolved": set(), "discarded": set()}

    def __init__(self):
        self.requests: list[tuple[str, str, object]] = []
        self.logins = 0
        self.valid_tokens: set[str] = set()
        self.incidents = {TICKET_ID: {
            "id": TICKET_ID, "title": "Return requested within 14-day window, awaiting approval",
            "description": "Return requested within 14-day window, awaiting approval", "category": "returns_issue",
            "status": "open", "origin": "customer", "branch": "zaragoza_office",
            "created_at": "2024-01-08T00:00:00Z", "updated_at": "2024-01-08T00:00:00Z"}}
        self.products = [
            {"id": 1, "name": "Zapatilla blanca clásica - Talla 42", "sku": "CLT-SNK-W-42-Z", "client_name": "PureStep Footwear",
             "client_id": "purestep_footwear", "category": "fashion", "warehouse": "ZGZ", "current_stock": 52, "minimum_stock": None},
            {"id": 2, "name": "Sérum facial hidratante 30ml", "sku": "CSM-SRM-030", "client_name": "GlowLab",
             "client_id": "glowlab", "category": "cosmetics", "warehouse": "LA", "current_stock": 9, "minimum_stock": 15},
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, body))
        path, method = request.url.path, request.method
        if path == "/auth/login":
            self.logins += 1
            token = f"backend-jwt-{self.logins}"
            self.valid_tokens.add(token)
            return httpx.Response(200, json={"access_token": token, "token_type": "bearer", "expires_in": 1800})
        if request.headers.get("Authorization", "").removeprefix("Bearer ") not in self.valid_tokens:
            return httpx.Response(401, json={"detail": "Credenciales de autenticación no válidas."})
        if path == "/api/incidents" and method == "GET":
            rows = [i for i in self.incidents.values() if all(i[k] == v for k, v in request.url.params.items())]
            return httpx.Response(200, json=[{k: i[k] for k in ("id", "title", "category", "status", "origin", "branch")} for i in rows])
        if path == "/api/incidents" and method == "POST":
            new_id = str(uuid4())
            self.incidents[new_id] = {**body, "id": new_id, "status": "open", "created_at": "2026-11-20T10:00:00Z",
                                      "updated_at": "2026-11-20T10:00:00Z"}
            return httpx.Response(201, json={k: self.incidents[new_id][k] for k in ("id", "status", "created_at", "updated_at")})
        if path.startswith("/api/incidents/"):
            parts = path.split("/")
            incident = self.incidents.get(parts[3])
            if incident is None:
                return httpx.Response(404, json={"detail": "Incidencia no encontrada."})
            if method == "GET" and len(parts) == 4:
                return httpx.Response(200, json=incident)
            if method == "PATCH" and parts[4:] == ["status"]:
                if body["status"] not in self.TRANSITIONS[incident["status"]]:
                    return httpx.Response(400, json={"detail": {"field": "status", "message": "transición no permitida"}})
                incident.update(status=body["status"], updated_at="2026-11-20T11:00:00Z")
                return httpx.Response(200, json={k: incident[k] for k in ("id", "status", "updated_at")})
        if path == "/inventory/products" and method == "GET":
            return httpx.Response(200, json=self.products)
        if path.startswith("/inventory/products/") and method == "GET":
            product = next((p for p in self.products if str(p["id"]) == path.rsplit("/", 1)[1]), None)
            return httpx.Response(200, json=product) if product else httpx.Response(404, json={"detail": "SKU no encontrado."})
        return httpx.Response(405, json={"detail": "método no soportado por el doble"})

    def writes_to(self, prefix: str) -> list[tuple[str, str, object]]:
        return [r for r in self.requests if r[1].startswith(prefix) and r[0] != "GET"]


@pytest.fixture
def backend():
    return FakeIncidentsAndInventory()


@dataclass
class RunningServer:
    url: str
    resource: str
    settings: Settings
    audit_path: str

    def audit(self) -> list[dict]:
        with open(self.audit_path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]


@pytest.fixture
def mcp_server(oidc, backend, tmp_path):
    port = free_port()
    resource = f"http://127.0.0.1:{port}/mcp"
    oidc.resource = resource
    settings = Settings(port=port, resource_url=resource, oidc_issuer=oidc.issuer, api_base_url="http://backend.test",
                        backend_email="mcp-service@example.com", backend_password="contraseña-de-prueba",
                        backend_timeout_seconds=2, audit_log_path=str(tmp_path / "audit.log"))
    app = build_app(settings, backend=TrackflowBackend(settings, transport=httpx.MockTransport(backend.handler)),
                    auth_server=fetch_server_config(oidc.issuer, AuthServerType.OIDC))
    server, thread = serve(app, port)
    yield RunningServer(url=f"http://127.0.0.1:{port}/mcp", resource=resource, settings=settings,
                        audit_path=settings.audit_log_path)
    server.should_exit = True
    thread.join(5)


def rpc(url: str, method: str, params: dict | None = None, token: str | None = None) -> httpx.Response:
    """Petición JSON-RPC cruda al endpoint MCP (modo stateless + JSON)."""
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
               "MCP-Protocol-Version": "2025-06-18"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.post(url, headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, timeout=10)


def call_tool(url: str, token: str, name: str, arguments: dict) -> dict:
    response = rpc(url, "tools/call", {"name": name, "arguments": arguments}, token)
    assert response.status_code == 200, response.text
    return response.json()["result"]


def tool_error(result: dict) -> dict:
    assert result["isError"] is True, result
    return json.loads(result["content"][0]["text"])["error"]
