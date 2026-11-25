"""Tools del agente: consultas EN VIVO y de SOLO LECTURA a TrackFlow, a través del servidor MCP.

Desde la migración al servidor MCP (``mcps/trackflow_mcp``), el agente ya no
llama a ``services/api`` por HTTP: este módulo traduce la consulta planificada
por el enrutador a una llamada a una tool MCP, mediante el cliente
``services/agent/mcp_client.py`` (``langchain-mcp-adapters``), y su resultado a
los contratos que consumen la redacción (``answers.py``) y los evals.

* ``lookup_incidents``: tool MCP ``incidents_get_ticket`` (por id) o
  ``incidents_list_tickets`` (con filtros).
* ``lookup_inventory``: tool MCP ``inventory_query_products`` (``operation='list'``).

No queda ningún otro camino del agente hacia el Incidents Manager. La
implementación anterior (cliente ``httpx`` directo a ``/api/incidents`` y
``/inventory/products`` con el JWT del usuario) se eliminó; está en el
historial de git (PR #38).

Solo lectura: el cliente MCP solo permite las tools de ``AGENT_ALLOWED_TOOLS`` y
el token OAuth del agente no tiene ``incidents:write``.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from services.agent.mcp_client import McpCallResult, ReadOnlyViolation, TrackflowMcpTools  # noqa: F401 (reexport)

MAX_LISTED_INCIDENTS = 50

IncidentStatus = Literal["open", "in_progress", "resolved", "discarded"]
IncidentCategory = Literal[
    "lost_parcel", "delivery_failure", "inventory_discrepancy", "carrier_issue", "returns_issue",
    "warehouse_incident", "system_failure", "client_complaint", "other",
]
IncidentOrigin = Literal["customer", "branch", "internal"]
IncidentBranch = Literal["central", "la_warehouse", "la_office", "zaragoza_warehouse", "zaragoza_office"]
ToolErrorType = Literal["timeout", "unavailable", "not_found", "unauthorized", "invalid_query", "bad_response", "forbidden"]

STATUS_LABELS = {"open": "Abierta", "in_progress": "En curso", "resolved": "Resuelta", "discarded": "Descartada"}
CATEGORY_LABELS = {
    "lost_parcel": "Paquete extraviado", "delivery_failure": "Fallo de entrega",
    "inventory_discrepancy": "Discrepancia de inventario", "carrier_issue": "Problema de transportista",
    "returns_issue": "Problema de devolución", "warehouse_incident": "Incidente en almacén",
    "system_failure": "Fallo de sistema", "client_complaint": "Queja de cliente", "other": "Otra incidencia",
}
ORIGIN_LABELS = {"customer": "Cliente", "branch": "Sede", "internal": "Interno"}
BRANCH_LABELS = {
    "central": "Central", "la_warehouse": "Los Ángeles — Almacén", "la_office": "Los Ángeles — Oficina",
    "zaragoza_warehouse": "Zaragoza — Almacén", "zaragoza_office": "Zaragoza — Oficina",
}


class McpCaller(Protocol):
    def call(self, name: str, arguments: dict) -> McpCallResult: ...


# --------------------------------------------------------------------------
# Contratos tipados
# --------------------------------------------------------------------------
class IncidentQuery(BaseModel):
    """Entrada de ``lookup_incidents``: un ticket concreto O filtros de búsqueda."""

    model_config = ConfigDict(extra="forbid")

    ticket_id: UUID | None = None
    raw_ticket_reference: str | None = Field(default=None, description="Identificador citado que no es un UUID válido")
    status: IncidentStatus | None = None
    category: IncidentCategory | None = None
    origin: IncidentOrigin | None = None
    branch: IncidentBranch | None = None


class IncidentRecord(BaseModel):
    """Mismos campos que expone el gestor de incidencias (detalle sin la descripción libre)."""

    model_config = ConfigDict(extra="ignore")

    id: UUID
    title: str
    status: IncidentStatus
    category: IncidentCategory
    origin: IncidentOrigin
    branch: IncidentBranch
    created_at: datetime | None = None
    updated_at: datetime | None = None


class IncidentToolResult(BaseModel):
    ok: bool
    mode: Literal["detail", "list"]
    query: IncidentQuery
    incidents: list[IncidentRecord] = Field(default_factory=list)
    total: int = 0
    error_type: ToolErrorType | None = None
    mcp_tool: str | None = Field(default=None, description="Tool MCP invocada (None si no se llamó al servidor)")
    mcp_error_code: str | None = Field(default=None, description="Código de error devuelto por el servidor MCP")


class InventoryQuery(BaseModel):
    """Entrada de ``lookup_inventory``: código SKU exacto o términos del nombre del producto."""

    model_config = ConfigDict(extra="forbid")

    sku: str | None = None
    name_terms: list[str] = Field(default_factory=list)
    warehouse: Literal["LA", "ZGZ"] | None = None


class InventoryItem(BaseModel):
    """Campos de ``SKUResponse`` del gestor de inventario relevantes para stock."""

    model_config = ConfigDict(extra="ignore")

    sku: str
    name: str
    client_name: str
    client_id: str | None = None
    warehouse: Literal["LA", "ZGZ"]
    current_stock: int
    minimum_stock: int | None = None


class InventoryToolResult(BaseModel):
    ok: bool
    query: InventoryQuery
    items: list[InventoryItem] = Field(default_factory=list)
    error_type: ToolErrorType | None = None
    mcp_tool: str | None = None
    mcp_error_code: str | None = None


# --------------------------------------------------------------------------
# Tools (vía servidor MCP)
# --------------------------------------------------------------------------
# Códigos del servidor MCP (mcps/trackflow_mcp/errors.py) y del transporte → error del agente.
MCP_ERROR_MAP: dict[str, ToolErrorType] = {
    "NOT_FOUND": "not_found",
    "VALIDATION_ERROR": "invalid_query",
    "INSUFFICIENT_SCOPE": "unauthorized",
    "TOOL_NOT_AVAILABLE": "unauthorized",
    "BACKEND_AUTH_FAILED": "unavailable",
    "BACKEND_TIMEOUT": "timeout",
    "BACKEND_UNAVAILABLE": "unavailable",
    "timeout": "timeout",
    "unavailable": "unavailable",
    "unauthorized": "unauthorized",
}


def _error_type(result: McpCallResult) -> ToolErrorType:
    return MCP_ERROR_MAP.get(result.error_code or "", "bad_response")


_default_mcp: TrackflowMcpTools | None = None


def default_mcp() -> TrackflowMcpTools:
    """Cliente MCP del proceso (configurado por entorno: MCP_SERVER_URL, OIDC_ISSUER, AGENT_OAUTH_*)."""
    global _default_mcp
    if _default_mcp is None:
        _default_mcp = TrackflowMcpTools()
    return _default_mcp


def lookup_incidents(query: IncidentQuery, *, mcp: McpCaller | None = None) -> IncidentToolResult:
    """Consulta un ticket por id o lista tickets filtrados, con las tools MCP del Incidents Manager."""
    mode: Literal["detail", "list"] = "detail" if (query.ticket_id or query.raw_ticket_reference) else "list"
    if query.raw_ticket_reference and not query.ticket_id:
        return IncidentToolResult(ok=False, mode=mode, query=query, error_type="invalid_query")
    mcp = mcp or default_mcp()
    if mode == "detail":
        tool, arguments = "incidents_get_ticket", {"incident_id": str(query.ticket_id)}
    else:
        tool = "incidents_list_tickets"
        arguments = {**{k: v for k, v in query.model_dump(include={"status", "category", "origin", "branch"}).items() if v},
                     "limit": MAX_LISTED_INCIDENTS}
    result = mcp.call(tool, arguments)
    if not result.ok:
        return IncidentToolResult(ok=False, mode=mode, query=query, error_type=_error_type(result),
                                  mcp_tool=tool, mcp_error_code=result.error_code)
    try:
        if mode == "detail":
            records, total = [IncidentRecord.model_validate(result.data)], 1
        else:
            records = [IncidentRecord.model_validate(row) for row in result.data["incidents"]]
            total = int(result.data["total"])
    except (KeyError, TypeError, ValueError):
        return IncidentToolResult(ok=False, mode=mode, query=query, error_type="bad_response", mcp_tool=tool)
    return IncidentToolResult(ok=True, mode=mode, query=query, incidents=records, total=total, mcp_tool=tool)


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def _select_products(products: list[InventoryItem], query: InventoryQuery) -> list[InventoryItem]:
    """SKU exacto, o los productos cuyo nombre contiene más términos buscados (sin tildes)."""
    candidates = [p for p in products if not query.warehouse or p.warehouse == query.warehouse]
    if query.sku:
        return [p for p in candidates if p.sku.lower() == query.sku.lower()]
    terms = [_plain(t) for t in query.name_terms]
    scored = [(sum(t in _plain(p.name) for t in terms), p) for p in candidates]
    best = max((score for score, _ in scored), default=0)
    return [p for score, p in scored if best and score == best]


def lookup_inventory(query: InventoryQuery, *, mcp: McpCaller | None = None,
                     client_ids: list[str] | None = None) -> InventoryToolResult:
    """Consulta el stock actual de un producto (por SKU o por nombre) con la tool MCP de inventario.

    ``client_ids`` limita el resultado a esas marcas (``None`` = sin límite, solo personal). Lo ajeno se descarta antes
    de seleccionar y se responde igual que un producto inexistente."""
    if not query.sku and not query.name_terms:
        return InventoryToolResult(ok=False, query=query, error_type="invalid_query")
    tool = "inventory_query_products"
    arguments = {"operation": "list", **({"sku": query.sku} if query.sku else {}),
                 **({"warehouse": query.warehouse} if query.warehouse else {})}
    result = (mcp or default_mcp()).call(tool, arguments)
    if not result.ok:
        return InventoryToolResult(ok=False, query=query, error_type=_error_type(result), mcp_tool=tool,
                                   mcp_error_code=result.error_code)
    try:
        products = [InventoryItem.model_validate(p) for p in result.data["products"]]
    except (KeyError, TypeError, ValueError):
        return InventoryToolResult(ok=False, query=query, error_type="bad_response", mcp_tool=tool)
    if client_ids is not None:
        products = [p for p in products if p.client_id in client_ids]
    items = _select_products(products, query)
    if not items:
        return InventoryToolResult(ok=False, query=query, error_type="not_found", mcp_tool=tool)
    return InventoryToolResult(ok=True, query=query, items=items, mcp_tool=tool)


SKU_PATTERN = re.compile(r"\b[A-Z]{3}(?:-[A-Z0-9]{1,5}){1,4}\b")
