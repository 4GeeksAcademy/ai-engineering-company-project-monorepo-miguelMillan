"""Servidor MCP de TrackFlow: Incidents Manager (lectura/escritura) e inventario (solo lectura).

Transporte: **Streamable HTTP** en modo *stateless*. El servidor lo consumen
varios clientes remotos (el agente LangGraph, otros equipos, partners), así que
no puede ser stdio: stdio solo sirve a un proceso local hijo y no tiene una
capa HTTP donde aplicar OAuth. Con HTTP, cada petición lleva su access token y
se valida en cada mensaje. Sin estado de sesión, cualquier réplica puede
atender cualquier petición.

Autenticación: OAuth 2.1 vía **MCP Auth** (``mcpauth``) en modo resource server:

* ``GET /.well-known/oauth-protected-resource/mcp``: Protected Resource
  Metadata (RFC 9728) con el authorization server, los scopes y la documentación.
* ``/mcp`` exige ``Authorization: Bearer <JWT>`` firmado por el proveedor OIDC
  configurado (``OIDC_ISSUER``), con ``aud`` = este recurso y el scope
  ``mcp:access``. Si falta, devuelve 401 o 403 con ``WWW-Authenticate``.

No se usa la capa de auth integrada de FastMCP (ni ``auth=`` en el servidor ni
en las tools).

Arranque::

    uv run python -m mcps.trackflow_mcp.server
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import uvicorn
from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from mcpauth import MCPAuth
from mcpauth.config import AuthServerConfig, AuthServerType
from mcpauth.types import ResourceServerConfig, ResourceServerMetadata
from mcpauth.utils import fetch_server_config
from pydantic import Field
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount

from mcps.trackflow_mcp.backend import TrackflowBackend
from mcps.trackflow_mcp.config import Settings, load_settings
from mcps.trackflow_mcp.contracts import (
    STATUS_TRANSITIONS,
    IncidentBranch,
    IncidentCategory,
    IncidentCreated,
    IncidentDetail,
    IncidentList,
    IncidentOrigin,
    IncidentStatus,
    IncidentStatusChanged,
    IncidentSummary,
    InventoryQueryResult,
    Product,
    ProductCategory,
    Warehouse,
)
from mcps.trackflow_mcp.errors import ToolErrorCode, TrackflowToolError, error_catalog_markdown
from mcps.trackflow_mcp.security import (
    BASE_SCOPE,
    INVENTORY_READ_OPERATIONS,
    TOOL_SCOPES,
    AuthFailureAuditMiddleware,
    AuthInfoToRequestState,
    ToolAccessMiddleware,
    configure_audit_log,
)

SERVER_NAME = "trackflow-mcp"
SCOPES_SUPPORTED = [BASE_SCOPE, "incidents:read", "incidents:write", "inventory:read"]

INSTRUCTIONS = f"""\
Servidor MCP de TrackFlow (operador logístico 3PL: almacenes de Los Ángeles y Zaragoza).
Expone el Incidents Manager (tickets de soporte) y el inventario de la compañía.

Autorización: OAuth 2.1 (bearer JWT). Todas las peticiones requieren el scope '{BASE_SCOPE}'.
Cada tool exige además su propio scope y tools/list solo muestra las que tu token puede invocar:
- incidents_get_ticket, incidents_list_tickets → incidents:read
- incidents_create_ticket, incidents_update_ticket_status → incidents:write
- inventory_query_products → inventory:read (SOLO LECTURA: cualquier intento de modificar
  stock, mínimos o productos se rechaza con INVENTORY_READ_ONLY; no existe scope de escritura de inventario).

Ciclo de vida de un ticket (lo aplica el Incidents Manager): open → in_progress | discarded;
in_progress → resolved | discarded; resolved y discarded son finales. Los tickets nuevos nacen en 'open'.

Errores de tool: resultado con isError=true y texto JSON {{"error": {{"code", "message", "details"}}}}. Códigos:
{error_catalog_markdown()}
"""


def _product(raw: dict) -> Product:
    minimum = raw.get("minimum_stock")
    return Product(**{k: raw[k] for k in Product.model_fields if k in raw},
                   below_minimum=minimum is not None and raw["current_stock"] < minimum)


def create_mcp(backend: TrackflowBackend) -> FastMCP:
    mcp = FastMCP(name=SERVER_NAME, instructions=INSTRUCTIONS, version="1.0.0", middleware=[ToolAccessMiddleware()])

    # ------------------------------------------------------------------ incidencias
    @mcp.tool(
        name="incidents_get_ticket",
        title="Consultar un ticket",
        description=(
            "Devuelve un ticket del Incidents Manager por su id (UUID): estado actual (status), título, "
            "descripción, categoría, origen, sede y fechas. Usa esta tool para conocer el estado de un ticket. "
            f"Requiere el scope {TOOL_SCOPES['incidents_get_ticket']}. Errores: NOT_FOUND si el id no existe, "
            "VALIDATION_ERROR si no es un UUID."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
        tags={"incidents", "read"},
    )
    async def incidents_get_ticket(
        incident_id: Annotated[UUID, Field(description="Id del ticket (UUID), p. ej. d32a9b66-b81b-5dce-b90f-c4e8bfc27161")],
    ) -> IncidentDetail:
        return IncidentDetail.model_validate(await backend.get_incident(str(incident_id)))

    @mcp.tool(
        name="incidents_list_tickets",
        title="Buscar tickets",
        description=(
            "Lista tickets del Incidents Manager filtrando por estado, categoría, origen y/o sede (todos opcionales; "
            "se combinan con AND). Devuelve el total y hasta `limit` tickets. "
            f"Requiere el scope {TOOL_SCOPES['incidents_list_tickets']}."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
        tags={"incidents", "read"},
    )
    async def incidents_list_tickets(
        status: Annotated[IncidentStatus | None, Field(description="Estado del ticket")] = None,
        category: Annotated[IncidentCategory | None, Field(description="Categoría de la incidencia")] = None,
        origin: Annotated[IncidentOrigin | None, Field(description="Quién la originó")] = None,
        branch: Annotated[IncidentBranch | None, Field(description="Sede afectada")] = None,
        limit: Annotated[int, Field(ge=1, le=100, description="Máximo de tickets devueltos")] = 20,
    ) -> IncidentList:
        filters = {k: v for k, v in {"status": status, "category": category, "origin": origin, "branch": branch}.items() if v}
        rows = await backend.list_incidents(filters)
        incidents = [IncidentSummary.model_validate(row) for row in rows]
        return IncidentList(total=len(incidents), returned=min(limit, len(incidents)), filters=filters, incidents=incidents[:limit])

    @mcp.tool(
        name="incidents_create_ticket",
        title="Crear un ticket",
        description=(
            "Crea un ticket en el Incidents Manager (POST /api/incidents). El ticket nace en estado 'open' y "
            "devuelve su id. No es idempotente: cada llamada crea un ticket nuevo. "
            f"Requiere el scope {TOOL_SCOPES['incidents_create_ticket']}."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
        tags={"incidents", "write"},
    )
    async def incidents_create_ticket(
        title: Annotated[str, Field(min_length=1, max_length=200, description="Resumen corto de la incidencia")],
        description: Annotated[str, Field(min_length=1, max_length=4000, description="Detalle de lo ocurrido")],
        category: Annotated[IncidentCategory, Field(description="Categoría de la incidencia")],
        origin: Annotated[IncidentOrigin, Field(description="customer (cliente), branch (sede) o internal")],
        branch: Annotated[IncidentBranch, Field(description="Sede afectada")],
    ) -> IncidentCreated:
        if not title.strip() or not description.strip():
            raise TrackflowToolError(ToolErrorCode.VALIDATION_ERROR, "title y description no pueden estar vacíos.",
                                     {"errors": [{"field": "title/description", "message": "solo espacios"}]})
        created = await backend.create_incident(
            {"title": title, "description": description, "category": category, "origin": origin, "branch": branch})
        return IncidentCreated.model_validate(created)

    @mcp.tool(
        name="incidents_update_ticket_status",
        title="Cambiar el estado de un ticket",
        description=(
            "Cambia el estado de un ticket mediante el endpoint de ciclo de vida del Incidents Manager "
            "(PATCH /api/incidents/{id}/status). Transiciones válidas: open → in_progress | discarded; "
            "in_progress → resolved | discarded; resolved y discarded son finales. Una transición no permitida "
            "devuelve INVALID_STATUS_TRANSITION con los estados posibles. Cerrar un ticket (resolved, discarded) no se ejecuta: "
            "devuelve CONFIRMATION_REQUIRED hasta que una persona lo confirme. "
            f"Requiere el scope {TOOL_SCOPES['incidents_update_ticket_status']}."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
        tags={"incidents", "write"},
    )
    async def incidents_update_ticket_status(
        incident_id: Annotated[UUID, Field(description="Id del ticket (UUID)")],
        status: Annotated[IncidentStatus, Field(description="Nuevo estado")],
    ) -> IncidentStatusChanged:
        current = await backend.get_incident(str(incident_id))
        response = await backend.update_incident_status(str(incident_id), status)
        if response.status_code == 400:
            raise TrackflowToolError(
                ToolErrorCode.INVALID_STATUS_TRANSITION,
                f"El Incidents Manager no permite pasar de '{current['status']}' a '{status}'.",
                {"current_status": current["status"], "requested_status": status,
                 "allowed": STATUS_TRANSITIONS[current["status"]], "backend_detail": response.json().get("detail")},
            )
        if response.status_code == 202:  # cerrar es definitivo: lo confirma una persona en el backoffice
            raise TrackflowToolError(ToolErrorCode.CONFIRMATION_REQUIRED, "Pendiente de confirmación humana; el ticket no ha cambiado.",
                                     {"action_id": response.json()["action_id"], "current_status": current["status"],
                                      "requested_status": status})
        body = TrackflowBackend._json(response, not_found=f"No existe ningún ticket con id {incident_id}.")
        return IncidentStatusChanged(previous_status=current["status"], **body)

    # ------------------------------------------------------------------ inventario
    @mcp.tool(
        name="inventory_query_products",
        title="Consultar inventario (solo lectura)",
        description=(
            "Consulta productos (SKU) del inventario y su stock actual. SOLO LECTURA. operation='list' (por defecto) "
            "lista productos con filtros opcionales sku, warehouse, category y below_minimum_only; operation='get' "
            "devuelve un producto por product_id. Cualquier otra operación (update, set_stock, adjust, delete, "
            "create…) o argumentos de escritura (current_stock, quantity, minimum_stock…) se rechazan con "
            f"INVENTORY_READ_ONLY. Requiere el scope {TOOL_SCOPES['inventory_query_products']}."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
        tags={"inventory", "read"},
    )
    async def inventory_query_products(
        operation: Annotated[str, Field(description="'list' o 'get'. Solo operaciones de lectura.",
                                        examples=["list", "get"])] = "list",
        product_id: Annotated[int | None, Field(gt=0, description="Id del producto (obligatorio con operation='get')")] = None,
        sku: Annotated[str | None, Field(description="Código SKU exacto, p. ej. FAS-LA-001")] = None,
        warehouse: Annotated[Warehouse | None, Field(description="Almacén: LA (Los Ángeles) o ZGZ (Zaragoza)")] = None,
        category: Annotated[ProductCategory | None, Field(description="Categoría del producto")] = None,
        below_minimum_only: Annotated[bool, Field(description="Solo productos por debajo de su stock mínimo")] = False,
    ) -> InventoryQueryResult:
        operation = operation.strip().lower()
        if operation not in INVENTORY_READ_OPERATIONS:  # segunda barrera; el middleware ya lo rechaza
            raise TrackflowToolError(ToolErrorCode.INVENTORY_READ_ONLY, "El inventario es de solo lectura.",
                                     {"operation": operation})
        if operation == "get":
            if product_id is None:
                raise TrackflowToolError(ToolErrorCode.VALIDATION_ERROR, "operation='get' requiere product_id.",
                                         {"errors": [{"field": "product_id", "message": "obligatorio con operation='get'"}]})
            products = [_product(await backend.get_product(product_id))]
        else:
            products = [_product(p) for p in await backend.list_products()]
            products = [p for p in products
                        if (not sku or p.sku.lower() == sku.strip().lower())
                        and (not warehouse or p.warehouse == warehouse)
                        and (not category or p.category == category)
                        and (not below_minimum_only or p.below_minimum)]
        return InventoryQueryResult(operation=operation, total=len(products), products=products)

    return mcp


def build_app(settings: Settings | None = None, *, backend: TrackflowBackend | None = None,
              auth_server: AuthServerConfig | None = None) -> Starlette:
    """App ASGI completa: Protected Resource Metadata + endpoint MCP protegido por MCP Auth."""
    settings = settings or load_settings()
    configure_audit_log(settings.audit_log_path)
    backend = backend or TrackflowBackend(settings)
    auth_server = auth_server or fetch_server_config(settings.oidc_issuer, AuthServerType.OIDC)

    mcp_auth = MCPAuth(protected_resources=ResourceServerConfig(metadata=ResourceServerMetadata(
        resource=settings.resource_url,
        authorization_servers=[auth_server],
        scopes_supported=SCOPES_SUPPORTED,
        bearer_methods_supported=["header"],
        resource_name="TrackFlow MCP: Incidents Manager e inventario",
        resource_documentation="https://github.com/4GeeksAcademy/ai-engineering-company-project-monorepo-miguelMillan/tree/main/mcps/trackflow_mcp",
    )))
    bearer_auth = mcp_auth.bearer_auth_middleware(
        "jwt", resource=settings.resource_url, audience=settings.resource_url, required_scopes=[BASE_SCOPE],
    )
    mcp_app = create_mcp(backend).http_app(
        path=settings.mcp_path, stateless_http=True, json_response=True,
        middleware=[Middleware(AuthFailureAuditMiddleware), Middleware(bearer_auth),
                    Middleware(AuthInfoToRequestState, mcp_auth=mcp_auth)],
        allowed_hosts=settings.allowed_hosts or None, allowed_origins=settings.cors_origins or None,
    )
    # CORS solo para los orígenes configurados (clientes MCP web). El preflight OPTIONS se resuelve
    # aquí, antes de la autenticación; WWW-Authenticate se expone para que el cliente descubra la PRM.
    cors = [Middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "Accept", "Mcp-Session-Id", "Mcp-Protocol-Version", "Last-Event-ID"],
                       expose_headers=["WWW-Authenticate", "Mcp-Session-Id"])] if settings.cors_origins else []
    return Starlette(routes=[*mcp_auth.resource_metadata_router().routes, Mount("/", app=mcp_app)],
                     middleware=cors, lifespan=mcp_app.lifespan)


def main() -> None:
    settings = load_settings()
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port, proxy_headers=True)


if __name__ == "__main__":
    main()
