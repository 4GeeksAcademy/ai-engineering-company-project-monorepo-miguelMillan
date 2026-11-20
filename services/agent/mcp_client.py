"""Cliente MCP del agente: carga las tools del servidor MCP de TrackFlow con ``langchain-mcp-adapters``.

Es el ÚNICO camino del agente hacia el Incidents Manager y el inventario. Desde
la migración, el agente no llama a ``services/api`` por HTTP directo.

* Conexión: Streamable HTTP a ``MCP_SERVER_URL`` (por defecto
  ``http://127.0.0.1:8090/mcp``) con ``MultiServerMCPClient``.
* Autenticación: OAuth 2.1 ``client_credentials`` como el cliente
  confidencial ``trackflow-agent`` (``AGENT_OAUTH_CLIENT_ID`` /
  ``AGENT_OAUTH_CLIENT_SECRET``) contra ``OIDC_ISSUER``. El proveedor solo le
  concede ``mcp:access incidents:read inventory:read``, así que, aunque hubiera
  un fallo en el agente, el servidor MCP rechazaría cualquier escritura de
  tickets (``INSUFFICIENT_SCOPE``) o de inventario (``INVENTORY_READ_ONLY``).
* Mínimo privilegio también en el cliente: solo se invocan las tools de
  ``AGENT_ALLOWED_TOOLS``; pedir otra lanza ``ReadOnlyViolation`` sin salir del
  proceso.
* Timeout: ``TOOL_TIMEOUT_SECONDS`` por llamada (descubrimiento + invocación).
  Si se agota, el resultado es ``error_type="timeout"`` y el grafo responde con
  honestidad.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx

TOOL_TIMEOUT_SECONDS = 8.0
DEFAULT_MCP_SERVER_URL = "http://127.0.0.1:8090/mcp"
DEFAULT_OIDC_ISSUER = "http://localhost:8180/realms/trackflow"
AGENT_ALLOWED_TOOLS = frozenset({"incidents_get_ticket", "incidents_list_tickets", "inventory_query_products"})


class ReadOnlyViolation(RuntimeError):
    """El agente intentó invocar una tool que no es de solo lectura."""


@dataclass
class McpCallResult:
    ok: bool
    data: dict[str, Any] | None = None
    error_code: str | None = None   # código del servidor MCP (NOT_FOUND…) o del transporte (timeout…)
    error_message: str | None = None


class OAuthClientCredentials(httpx.Auth):
    """``httpx.Auth`` que obtiene y renueva un access token OAuth 2.1 (``client_credentials``).

    El token se pide al endpoint de token publicado en el discovery OIDC del
    issuer, se reutiliza hasta 30 s antes de expirar y se renueva una vez si el
    servidor MCP responde 401. Nunca se escribe en disco, ni en el estado ni en
    los traces del grafo.
    """

    requires_response_body = True

    def __init__(self, issuer: str, client_id: str, client_secret: str, scope: str | None = None):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self._secret = client_secret
        self.scope = scope
        self._token_url: str | None = None
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def _token_request(self) -> httpx.Request:
        data = {"grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self._secret}
        if self.scope:
            data["scope"] = self.scope
        return httpx.Request("POST", self._token_url, data=data)

    def _store(self, response: httpx.Response) -> None:
        response.raise_for_status()
        body = response.json()
        with self._lock:
            self._token = body["access_token"]
            self._expires_at = time.monotonic() + float(body.get("expires_in", 60)) - 30

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        if self._token_url is None:
            discovery = yield httpx.Request("GET", f"{self.issuer}/.well-known/openid-configuration")
            discovery.raise_for_status()
            self._token_url = discovery.json()["token_endpoint"]
        if self._token is None or time.monotonic() >= self._expires_at:
            self._store((yield self._token_request()))
        request.headers["Authorization"] = f"Bearer {self._token}"
        response = yield request
        if response.status_code == 401:
            self._store((yield self._token_request()))
            request.headers["Authorization"] = f"Bearer {self._token}"
            yield request


def auth_from_env() -> OAuthClientCredentials | None:
    client_id, secret = os.environ.get("AGENT_OAUTH_CLIENT_ID"), os.environ.get("AGENT_OAUTH_CLIENT_SECRET")
    if not client_id or not secret:
        return None
    return OAuthClientCredentials(os.environ.get("OIDC_ISSUER", DEFAULT_OIDC_ISSUER), client_id, secret,
                                  scope=os.environ.get("AGENT_OAUTH_SCOPE"))


class TrackflowMcpTools:
    """Tools del servidor MCP cargadas como LangChain tools y ejecutables desde nodos síncronos del grafo.

    Los nodos de LangGraph del agente son síncronos y las tools MCP son
    asíncronas: las llamadas se ejecutan en un event loop propio, en un hilo
    dedicado, con un timeout total por llamada.
    """

    def __init__(self, url: str | None = None, *, auth: httpx.Auth | None = None,
                 timeout: float = TOOL_TIMEOUT_SECONDS, httpx_client_factory=None):
        from langchain_mcp_adapters.client import MultiServerMCPClient

        self.url = url or os.environ.get("MCP_SERVER_URL") or DEFAULT_MCP_SERVER_URL
        self.timeout = timeout
        connection: dict[str, Any] = {"transport": "streamable_http", "url": self.url,
                                      "timeout": timedelta(seconds=timeout)}
        auth = auth if auth is not None else auth_from_env()
        if auth is not None:
            connection["auth"] = auth
        if httpx_client_factory is not None:
            connection["httpx_client_factory"] = httpx_client_factory
        self._client = MultiServerMCPClient({"trackflow": connection}, handle_tool_errors=True)
        self._tools: dict[str, Any] | None = None
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, name="trackflow-mcp-client", daemon=True).start()

    # ---------------------------------------------------------------- async
    async def _load_tools(self) -> dict[str, Any]:
        if self._tools is None:
            self._tools = {tool.name: tool for tool in await self._client.get_tools(server_name="trackflow")}
        return self._tools

    async def _call(self, name: str, arguments: dict[str, Any]) -> McpCallResult:
        tools = await self._load_tools()
        if name not in tools:
            self._tools = None  # el token o el servidor cambiaron: se redescubre en la próxima llamada
            return McpCallResult(ok=False, error_code="TOOL_NOT_AVAILABLE",
                                 error_message=f"El servidor MCP no ofrece la tool '{name}' a este cliente.")
        message = await tools[name].ainvoke({"type": "tool_call", "id": f"agent-{name}", "name": name, "args": arguments})
        if getattr(message, "status", "success") == "error":
            return McpCallResult(ok=False, **_parse_tool_error(message.content))
        artifact = getattr(message, "artifact", None) or {}
        data = artifact.get("structured_content") if isinstance(artifact, dict) else None
        if data is None:
            data = json.loads(_text(message.content))
        return McpCallResult(ok=True, data=data)

    # ---------------------------------------------------------------- sync API
    def call(self, name: str, arguments: dict[str, Any]) -> McpCallResult:
        """Invoca una tool de lectura del servidor MCP. Nunca lanza por fallos del servidor o la red."""
        if name not in AGENT_ALLOWED_TOOLS:
            raise ReadOnlyViolation(f"El agente solo puede usar tools de lectura; se bloqueó '{name}'.")
        future = asyncio.run_coroutine_threadsafe(asyncio.wait_for(self._call(name, arguments), self.timeout), self._loop)
        try:
            return future.result(self.timeout + 1)
        except (TimeoutError, asyncio.TimeoutError):
            future.cancel()
            return McpCallResult(ok=False, error_code="timeout", error_message=f"Sin respuesta en {self.timeout:g} s")
        except Exception as error:  # noqa: BLE001 — transporte, 401/403 del servidor, discovery…
            self._tools = None
            return McpCallResult(ok=False, error_code=_transport_error_code(error), error_message=type(error).__name__)

    def tool_names(self) -> list[str]:
        future = asyncio.run_coroutine_threadsafe(asyncio.wait_for(self._load_tools(), self.timeout), self._loop)
        return sorted(future.result(self.timeout + 1))


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content or [])


def _parse_tool_error(content: Any) -> dict[str, str | None]:
    text = _text(content)
    try:
        error = json.loads(text[text.index("{"):])["error"]
        return {"error_code": error["code"], "error_message": error["message"]}
    except (ValueError, KeyError, TypeError):
        return {"error_code": "MCP_TOOL_ERROR", "error_message": text[:300]}


def _transport_error_code(error: BaseException) -> str:
    """Busca la causa real dentro de los ExceptionGroup de anyio."""
    pending = [error]
    while pending:
        current = pending.pop()
        if isinstance(current, httpx.TimeoutException):
            return "timeout"
        if isinstance(current, httpx.HTTPStatusError) and current.response.status_code in (401, 403):
            return "unauthorized"
        if isinstance(current, (httpx.TransportError, ConnectionError)):
            return "unavailable"
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
    return "unavailable"
