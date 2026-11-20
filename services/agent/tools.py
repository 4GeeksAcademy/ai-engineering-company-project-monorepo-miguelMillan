"""Tools del agente: consultas EN VIVO y de SOLO LECTURA a los servicios de TrackFlow.

Cada tool hace una sola cosa:

* ``lookup_incidents``: estado de tickets en el gestor de incidencias
  (``GET /api/incidents/{id}`` o ``GET /api/incidents?status=…``).
* ``lookup_inventory``: stock por producto en el gestor de inventario
  (``GET /inventory/products``).

Transporte: HTTP al servicio real (``services/api``), nunca datos simulados.
Las incidencias viven en la TinyDB del servicio y el inventario en Postgres,
detrás de sus rutas; llamarlas por HTTP reutiliza su validación, su caché y su
autenticación sin duplicar lógica en el agente.

Autenticación: ambos routers exigen un JWT de usuario. Se usa, por orden:
(1) el token de quien llamó a ``POST /agent/query`` (el agente consulta con
los permisos del propio account manager) y (2) ``AGENT_API_TOKEN`` del entorno
para scripts y evaluación. Nunca se hardcodea un token.

Solo lectura: el cliente HTTP rechaza en un hook cualquier método distinto de
GET antes de enviarlo; ninguna tool puede crear, actualizar ni borrar tickets.

Timeout: ``TOOL_TIMEOUT_SECONDS`` = 4 s por llamada (``GET /inventory/products``
tarda 0,6–1,2 s medido contra Supabase). Si se agota, la tool devuelve
``error_type="timeout"`` y el grafo responde con honestidad; nunca se cuelga.
"""

from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime
from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

TOOL_TIMEOUT_SECONDS = 4.0
DEFAULT_API_BASE_URL = "http://127.0.0.1:8081"
MAX_LISTED_INCIDENTS = 50

IncidentStatus = Literal["open", "in_progress", "resolved", "discarded"]
IncidentCategory = Literal[
    "lost_parcel", "delivery_failure", "inventory_discrepancy", "carrier_issue", "returns_issue",
    "warehouse_incident", "system_failure", "client_complaint", "other",
]
IncidentOrigin = Literal["customer", "branch", "internal"]
IncidentBranch = Literal["central", "la_warehouse", "la_office", "zaragoza_warehouse", "zaragoza_office"]
ToolErrorType = Literal["timeout", "unavailable", "not_found", "unauthorized", "invalid_query", "bad_response"]

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
    warehouse: Literal["LA", "ZGZ"]
    current_stock: int
    minimum_stock: int | None = None


class InventoryToolResult(BaseModel):
    ok: bool
    query: InventoryQuery
    items: list[InventoryItem] = Field(default_factory=list)
    error_type: ToolErrorType | None = None


# --------------------------------------------------------------------------
# Cliente HTTP de solo lectura
# --------------------------------------------------------------------------
class ReadOnlyViolation(RuntimeError):
    pass


def _enforce_get(request: httpx.Request) -> None:
    if request.method != "GET":
        raise ReadOnlyViolation(f"Las tools del agente son de solo lectura; se bloqueó {request.method} {request.url.path}")


def api_base_url() -> str:
    return (os.environ.get("AGENT_API_BASE_URL") or DEFAULT_API_BASE_URL).rstrip("/")


def _client(auth_token: str | None, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    token = auth_token or os.environ.get("AGENT_API_TOKEN") or ""
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(
        base_url=api_base_url(), headers=headers, transport=transport,
        timeout=httpx.Timeout(TOOL_TIMEOUT_SECONDS), event_hooks={"request": [_enforce_get]},
    )


def _error_type(error: Exception | None = None, response: httpx.Response | None = None) -> ToolErrorType:
    if isinstance(error, httpx.TimeoutException):
        return "timeout"
    if isinstance(error, httpx.TransportError):
        return "unavailable"
    if response is not None:
        if response.status_code == 404:
            return "not_found"
        if response.status_code in (401, 403):
            return "unauthorized"
        if response.status_code >= 500:
            return "unavailable"
    return "bad_response"


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------
def lookup_incidents(query: IncidentQuery, *, auth_token: str | None = None,
                     transport: httpx.BaseTransport | None = None) -> IncidentToolResult:
    """Consulta un ticket por ID o lista tickets filtrados. Solo GET."""
    mode: Literal["detail", "list"] = "detail" if (query.ticket_id or query.raw_ticket_reference) else "list"
    if query.raw_ticket_reference and not query.ticket_id:
        return IncidentToolResult(ok=False, mode=mode, query=query, error_type="invalid_query")
    try:
        with _client(auth_token, transport) as client:
            if mode == "detail":
                response = client.get(f"/api/incidents/{query.ticket_id}")
            else:
                params = {k: v for k, v in query.model_dump(include={"status", "category", "origin", "branch"}).items() if v}
                response = client.get("/api/incidents", params=params)
    except ReadOnlyViolation:
        raise
    except httpx.HTTPError as error:
        return IncidentToolResult(ok=False, mode=mode, query=query, error_type=_error_type(error))
    if response.status_code != 200:
        return IncidentToolResult(ok=False, mode=mode, query=query, error_type=_error_type(response=response))
    try:
        payload = response.json()
        records = [IncidentRecord.model_validate(payload)] if mode == "detail" else [IncidentRecord.model_validate(i) for i in payload]
    except (ValueError, ValidationError):
        return IncidentToolResult(ok=False, mode=mode, query=query, error_type="bad_response")
    records.sort(key=lambda r: (r.updated_at or r.created_at or datetime.min).isoformat(), reverse=True)
    return IncidentToolResult(ok=True, mode=mode, query=query, incidents=records[:MAX_LISTED_INCIDENTS], total=len(records))


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


def lookup_inventory(query: InventoryQuery, *, auth_token: str | None = None,
                     transport: httpx.BaseTransport | None = None) -> InventoryToolResult:
    """Consulta el stock actual de un producto (por SKU o por nombre). Solo GET."""
    if not query.sku and not query.name_terms:
        return InventoryToolResult(ok=False, query=query, error_type="invalid_query")
    try:
        with _client(auth_token, transport) as client:
            response = client.get("/inventory/products")
    except ReadOnlyViolation:
        raise
    except httpx.HTTPError as error:
        return InventoryToolResult(ok=False, query=query, error_type=_error_type(error))
    if response.status_code != 200:
        return InventoryToolResult(ok=False, query=query, error_type=_error_type(response=response))
    try:
        products = [InventoryItem.model_validate(p) for p in response.json()]
    except (ValueError, ValidationError):
        return InventoryToolResult(ok=False, query=query, error_type="bad_response")
    items = _select_products(products, query)
    if not items:
        return InventoryToolResult(ok=False, query=query, error_type="not_found")
    return InventoryToolResult(ok=True, query=query, items=items)


SKU_PATTERN = re.compile(r"\b[A-Z]{3}(?:-[A-Z0-9]{1,5}){1,4}\b")
