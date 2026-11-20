"""Contratos de entrada/salida de las tools.

Los nombres de campo, los IDs y los valores de dominio son EXACTAMENTE los de
las APIs ya existentes (``services/api/models.py`` y ``services/api/schemas.py``):

* Incidents Manager: ``id`` (UUID), ``title``, ``description``, ``category``,
  ``origin``, ``branch``, ``status`` (``open`` | ``in_progress`` | ``resolved`` |
  ``discarded``), ``created_at``, ``updated_at``.
* Inventario: ``id`` (entero, ``sku.id``), ``sku``, ``name``, ``client_name``,
  ``client_id``, ``category`` (``fashion`` | ``electronics`` | ``cosmetics``),
  ``warehouse`` (``LA`` | ``ZGZ``), ``current_stock``, ``minimum_stock``.

``tests/mcp/test_contracts_match_backend.py`` compara estos contratos con los
modelos del backend para que no puedan divergir en silencio.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

IncidentStatus = Literal["open", "in_progress", "resolved", "discarded"]
IncidentCategory = Literal[
    "lost_parcel", "delivery_failure", "inventory_discrepancy", "carrier_issue", "returns_issue",
    "warehouse_incident", "system_failure", "client_complaint", "other",
]
IncidentOrigin = Literal["customer", "branch", "internal"]
IncidentBranch = Literal["central", "la_warehouse", "la_office", "zaragoza_warehouse", "zaragoza_office"]
Warehouse = Literal["LA", "ZGZ"]
ProductCategory = Literal["fashion", "electronics", "cosmetics"]

# Ciclo de vida del Incidents Manager (mismo grafo que TRANSITIONS en
# services/api/routes/incidents.py). Solo se usa para documentar y para
# explicar un rechazo; quien decide es siempre el endpoint del backend.
STATUS_TRANSITIONS: dict[str, list[str]] = {
    "open": ["in_progress", "discarded"],
    "in_progress": ["resolved", "discarded"],
    "resolved": [],
    "discarded": [],
}


class IncidentSummary(BaseModel):
    """Ticket tal como lo lista ``GET /api/incidents``."""

    id: UUID
    title: str
    category: IncidentCategory
    status: IncidentStatus
    origin: IncidentOrigin
    branch: IncidentBranch


class IncidentDetail(IncidentSummary):
    """Ticket completo, como ``GET /api/incidents/{id}``."""

    description: str
    created_at: datetime
    updated_at: datetime


class IncidentList(BaseModel):
    total: int = Field(description="Tickets que cumplen los filtros en el Incidents Manager")
    returned: int = Field(description="Tickets incluidos en esta respuesta (máximo `limit`)")
    filters: dict[str, str] = Field(description="Filtros aplicados (status, category, origin, branch)")
    incidents: list[IncidentSummary]


class IncidentCreated(BaseModel):
    """Respuesta de ``POST /api/incidents``: todo ticket nuevo nace en ``open``."""

    id: UUID
    status: IncidentStatus
    created_at: datetime
    updated_at: datetime


class IncidentStatusChanged(BaseModel):
    """Respuesta de ``PATCH /api/incidents/{id}/status`` más el estado anterior."""

    id: UUID
    previous_status: IncidentStatus
    status: IncidentStatus
    updated_at: datetime


class Product(BaseModel):
    """Producto (SKU) del inventario con su stock calculado por el backend."""

    id: int
    sku: str
    name: str
    client_name: str
    client_id: str
    category: ProductCategory
    warehouse: Warehouse
    current_stock: int
    minimum_stock: int | None
    below_minimum: bool = Field(description="current_stock < minimum_stock (false si no hay mínimo configurado)")


class InventoryQueryResult(BaseModel):
    operation: Literal["list", "get"]
    total: int
    products: list[Product]
