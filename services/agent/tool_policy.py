"""Validación de las llamadas a tools ANTES de ejecutarlas.

Hoy los argumentos los construye código determinista (``routing.py``), no el modelo. Esta capa no depende de eso: si un día
los propone un modelo, o si un texto manipulado llega hasta el plan, la llamada se rechaza aquí. Lista cerrada de tools,
esquema estricto por tool y ningún argumento de más.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger("trackflow.agent.tool_policy")


class ToolCallRejected(ValueError):
    def __init__(self, tool: str, reasons: list[str]):
        super().__init__(f"{tool}: {'; '.join(reasons)}")
        self.tool, self.reasons = tool, reasons


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetTicket(_Args):
    incident_id: UUID


class ListTickets(_Args):
    status: Literal["open", "in_progress", "resolved", "discarded"] | None = None
    category: Literal["lost_parcel", "delivery_failure", "inventory_discrepancy", "carrier_issue", "returns_issue",
                      "warehouse_incident", "system_failure", "client_complaint", "other"] | None = None
    origin: Literal["customer", "branch", "internal"] | None = None
    branch: Literal["central", "la_warehouse", "la_office", "zaragoza_warehouse", "zaragoza_office"] | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=50)]


class QueryProducts(_Args):
    operation: Literal["list"]
    sku: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{1,39}$")] | None = None
    warehouse: Literal["LA", "ZGZ"] | None = None


TOOL_SCHEMAS: dict[str, type[_Args]] = {
    "incidents_get_ticket": GetTicket,
    "incidents_list_tickets": ListTickets,
    "inventory_query_products": QueryProducts,
}


def validate_tool_call(tool: str, arguments: Any) -> dict[str, Any]:
    schema = TOOL_SCHEMAS.get(tool)
    if schema is None:
        raise ToolCallRejected(tool, ["la tool no está en la lista permitida"])
    if not isinstance(arguments, dict):
        raise ToolCallRejected(tool, ["los argumentos no son un objeto"])
    try:
        return schema.model_validate(arguments).model_dump(mode="json", exclude_none=True)
    except ValidationError as error:
        reasons = [f"{'.'.join(map(str, e['loc'])) or 'args'}: {e['type']}" for e in error.errors()]
        logger.warning("llamada a tool rechazada: %s %s", tool, reasons)  # sin los valores: pueden ser datos personales
        raise ToolCallRejected(tool, reasons) from error
