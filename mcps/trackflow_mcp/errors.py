"""Catálogo de códigos de error del servidor MCP de TrackFlow.

Hay dos capas, y cada fallo tiene un código propio (nunca un "error" genérico):

1. **Transporte HTTP** (antes de llegar a MCP): las genera MCP Auth (mcpauth)
   en el bearer middleware, con status HTTP y cabecera ``WWW-Authenticate``
   (RFC 6750) que apunta a la Protected Resource Metadata (RFC 9728):

   ======  ==============================  ========================================
   HTTP    ``error``                       cuándo
   ======  ==============================  ========================================
   401     ``missing_auth_header``         sin cabecera ``Authorization``
   401     ``invalid_auth_header_format``  no es ``Bearer <token>``
   401     ``invalid_token``               firma, formato o expiración inválidos
   401     ``invalid_issuer``              emisor distinto del configurado
   401     ``invalid_audience``            ``aud`` no es este recurso MCP
   403     ``missing_required_scopes``     falta ``mcp:access``
   ======  ==============================  ========================================

2. **Tool** (dentro de MCP): resultado ``isError: true`` cuyo texto es un JSON
   ``{"error": {"code", "message", "details"}}`` con uno de estos códigos:
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from fastmcp.exceptions import ToolError


class ToolErrorCode(StrEnum):
    INSUFFICIENT_SCOPE = "INSUFFICIENT_SCOPE"
    INVENTORY_READ_ONLY = "INVENTORY_READ_ONLY"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    INVALID_STATUS_TRANSITION = "INVALID_STATUS_TRANSITION"
    BACKEND_TIMEOUT = "BACKEND_TIMEOUT"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    BACKEND_AUTH_FAILED = "BACKEND_AUTH_FAILED"
    BACKEND_ERROR = "BACKEND_ERROR"


# Descripción de cada código: se publica también en el README y en las
# instrucciones del servidor (discovery), para que un cliente sepa reaccionar.
TOOL_ERROR_DESCRIPTIONS: dict[ToolErrorCode, str] = {
    ToolErrorCode.INSUFFICIENT_SCOPE: "El access token no tiene el scope que exige la tool (details.required_scope). No reintentar sin un token con ese scope.",
    ToolErrorCode.INVENTORY_READ_ONLY: "Intento de modificar el inventario. El servidor lo rechaza siempre: el inventario solo se consulta por MCP.",
    ToolErrorCode.VALIDATION_ERROR: "Argumentos inválidos (details.errors indica campo y motivo). Corregir y reintentar.",
    ToolErrorCode.NOT_FOUND: "El ticket o producto no existe en el servicio de TrackFlow.",
    ToolErrorCode.INVALID_STATUS_TRANSITION: "El Incidents Manager no permite ese cambio de estado (details.allowed lista los posibles).",
    ToolErrorCode.BACKEND_TIMEOUT: "El servicio de TrackFlow no respondió a tiempo. Reintentar más tarde; no se asumió ningún resultado.",
    ToolErrorCode.BACKEND_UNAVAILABLE: "El servicio de TrackFlow no está disponible. Reintentar más tarde.",
    ToolErrorCode.BACKEND_AUTH_FAILED: "El servidor MCP no pudo autenticarse contra el backend (configuración de la cuenta de servicio).",
    ToolErrorCode.BACKEND_ERROR: "Respuesta inesperada del backend.",
}


class TrackflowToolError(ToolError):
    """ToolError con código estable y detalles estructurados (serializados como JSON)."""

    def __init__(self, code: ToolErrorCode, message: str, details: dict[str, Any] | None = None):
        self.code = code
        self.details = details or {}
        payload = {"error": {"code": code.value, "message": message, "details": self.details}}
        super().__init__(json.dumps(payload, ensure_ascii=False, default=str))


def error_catalog_markdown() -> str:
    return "\n".join(f"- {code.value}: {text}" for code, text in TOOL_ERROR_DESCRIPTIONS.items())
