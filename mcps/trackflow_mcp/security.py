"""Autorización por tool (mínimo privilegio), guardia de solo lectura del inventario y auditoría.

Capas, de fuera hacia dentro, para cada petición a ``/mcp``:

1. ``AuthFailureAuditMiddleware`` (ASGI): registra en el log de auditoría cada
   401/403 que devuelva la capa de autenticación (sin el token).
2. **MCP Auth** (``mcpauth``) ``bearer_auth_middleware("jwt", resource=…,
   audience=…, required_scopes=["mcp:access"])``: valida firma (JWKS del
   proveedor), emisor, audiencia y el scope base. Sin token válido no se llega
   a MCP: no se puede ni listar tools.
3. ``AuthInfoToRequestState`` (ASGI): copia el ``AuthInfo`` que dejó mcpauth en
   el estado del request HTTP. Las tools lo leen de ahí (``get_http_request``) y
   no de la ContextVar, porque el request es el de CADA mensaje MCP.
4. ``ToolAccessMiddleware`` (FastMCP): en ``tools/list`` muestra solo las tools
   que el token puede invocar; en ``tools/call`` rechaza escrituras de
   inventario (``INVENTORY_READ_ONLY``) y scopes insuficientes
   (``INSUFFICIENT_SCOPE``), y deja una línea de auditoría por invocación
   (tool, cliente, resultado, duración).
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools import Tool
from mcpauth import MCPAuth
from mcpauth.types import AuthInfo
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from pydantic import ValidationError

from mcps.trackflow_mcp.errors import ToolErrorCode, TrackflowToolError

BASE_SCOPE = "mcp:access"
INVENTORY_TOOL = "inventory_query_products"
TOOL_SCOPES: dict[str, str] = {
    "incidents_get_ticket": "incidents:read",
    "incidents_list_tickets": "incidents:read",
    "incidents_create_ticket": "incidents:write",
    "incidents_update_ticket_status": "incidents:write",
    INVENTORY_TOOL: "inventory:read",
}
INVENTORY_READ_OPERATIONS = {"list", "get"}
# Campos que solo tendrían sentido para modificar inventario. Si llegan en los
# argumentos de la tool de inventario, es un intento de escritura y se rechaza
# explícitamente (antes incluso de validar el esquema).
INVENTORY_WRITE_FIELDS = {
    "current_stock", "stock", "quantity", "minimum_stock", "physical_stock", "delta", "adjustment",
    "new_stock", "set_stock", "exit_type", "tracking_number", "reference", "name", "client_name",
}

AUDIT_LOGGER = "trackflow.mcp.audit"
audit_log = logging.getLogger(AUDIT_LOGGER)


def configure_audit_log(path: str | None) -> None:
    audit_log.setLevel(logging.INFO)
    audit_log.propagate = False
    if not audit_log.handlers:
        audit_log.addHandler(logging.StreamHandler(sys.stdout))
    if path and not any(isinstance(h, logging.FileHandler) and h.baseFilename == path for h in audit_log.handlers):
        audit_log.addHandler(logging.FileHandler(path, encoding="utf-8"))


def audit(event: str, **fields: Any) -> None:
    record = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "event": event, **fields}
    audit_log.info(json.dumps(record, ensure_ascii=False, default=str))


# ---------------------------------------------------------------------------- ASGI
class AuthInfoToRequestState:
    """Copia el ``AuthInfo`` validado por mcpauth al ``request.state`` del mensaje."""

    def __init__(self, app, mcp_auth: MCPAuth):
        self.app = app
        self.mcp_auth = mcp_auth

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope.setdefault("state", {})["mcp_auth_info"] = self.mcp_auth.auth_info
        await self.app(scope, receive, send)


class AuthFailureAuditMiddleware:
    """Registra los rechazos de autenticación/autorización de la capa HTTP (401/403)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start" and message["status"] in (401, 403):
                headers = {k.decode().lower(): v.decode() for k, v in message.get("headers", [])}
                challenge = headers.get("www-authenticate", "")
                error = challenge.split('error="', 1)[1].split('"', 1)[0] if 'error="' in challenge else None
                audit("auth_rejected", status=message["status"], error=error, path=scope.get("path"),
                      client_ip=(scope.get("client") or [None])[0])
            await send(message)

        await self.app(scope, receive, send_wrapper)


# ---------------------------------------------------------------------------- helpers
def current_auth() -> AuthInfo | None:
    try:
        return getattr(get_http_request().state, "mcp_auth_info", None)
    except RuntimeError:
        return None


def caller(auth: AuthInfo | None) -> dict[str, Any]:
    if auth is None:
        return {"client_id": None, "subject": None, "scopes": []}
    return {"client_id": auth.client_id, "subject": auth.subject, "scopes": sorted(auth.scopes)}


def inventory_write_attempt(tool_name: str, arguments: dict[str, Any]) -> str | None:
    """Motivo del rechazo si la llamada intenta modificar inventario; ``None`` si es una lectura."""
    if tool_name.startswith("inventory_") and tool_name != INVENTORY_TOOL:
        return f"La tool '{tool_name}' no existe: el inventario no expone ninguna operación de escritura."
    if tool_name != INVENTORY_TOOL:
        return None
    operation = str(arguments.get("operation", "list")).strip().lower()
    if operation not in INVENTORY_READ_OPERATIONS:
        return f"La operación '{operation}' no está permitida: solo se admiten {sorted(INVENTORY_READ_OPERATIONS)}."
    write_fields = sorted(INVENTORY_WRITE_FIELDS & set(arguments))
    if write_fields:
        return f"Los campos {write_fields} modificarían el inventario y no se aceptan."
    return None


def _field_errors(error: Exception) -> list[dict[str, str]]:
    cause = error if isinstance(error, ValidationError) else error.__cause__
    if isinstance(cause, ValidationError):
        return [{"field": ".".join(str(p) for p in e["loc"] if p not in ("call",)) or "arguments", "message": e["msg"]}
                for e in cause.errors(include_url=False)]
    return [{"field": "arguments", "message": str(error)}]


# ---------------------------------------------------------------------------- FastMCP
class ToolAccessMiddleware(Middleware):
    async def on_list_tools(self, context: MiddlewareContext, call_next) -> Sequence[Tool]:
        tools = await call_next(context)
        auth = current_auth()
        scopes = set(auth.scopes) if auth else set()
        visible = [tool for tool in tools if TOOL_SCOPES.get(tool.name) in scopes]
        audit("tools_list", **caller(auth), visible_tools=[t.name for t in visible])
        return visible

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        name = context.message.name
        arguments = dict(context.message.arguments or {})
        auth = current_auth()
        started = time.perf_counter()
        safe_args = {k: v for k, v in arguments.items() if k not in {"description"}}
        try:
            reason = inventory_write_attempt(name, arguments)
            if reason:
                raise TrackflowToolError(
                    ToolErrorCode.INVENTORY_READ_ONLY,
                    "El inventario es de solo lectura en este servidor MCP; la modificación se ha rechazado. "
                    "Los movimientos de stock se registran en TrackFlow como entradas o salidas de inventario.",
                    {"tool": name, "reason": reason, "allowed_operations": sorted(INVENTORY_READ_OPERATIONS)},
                )
            required = TOOL_SCOPES.get(name)
            if required and (auth is None or required not in auth.scopes):
                raise TrackflowToolError(
                    ToolErrorCode.INSUFFICIENT_SCOPE,
                    f"El access token no incluye el scope '{required}' que exige la tool '{name}'.",
                    {"tool": name, "required_scope": required, "granted_scopes": sorted(auth.scopes) if auth else []},
                )
            try:
                result = await call_next(context)
            except (ValidationError, FastMCPValidationError) as error:
                raise TrackflowToolError(
                    ToolErrorCode.VALIDATION_ERROR, f"Argumentos inválidos para '{name}'.", {"errors": _field_errors(error)},
                ) from error
        except TrackflowToolError as error:
            audit("tool_call", tool=name, **caller(auth), arguments=safe_args, outcome="error",
                  error_code=error.code.value, duration_ms=round((time.perf_counter() - started) * 1000, 1))
            raise
        except Exception as error:
            audit("tool_call", tool=name, **caller(auth), arguments=safe_args, outcome="error",
                  error_code=type(error).__name__, duration_ms=round((time.perf_counter() - started) * 1000, 1))
            raise
        audit("tool_call", tool=name, **caller(auth), arguments=safe_args, outcome="ok",
              duration_ms=round((time.perf_counter() - started) * 1000, 1))
        return result
