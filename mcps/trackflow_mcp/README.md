# `trackflow_mcp`: servidor MCP del Incidents Manager y del inventario

Servidor **Model Context Protocol** que expone a cualquier cliente MCP autorizado:
- el **Incidents Manager** de TrackFlow: consultar, crear y cambiar el estado de tickets;
- el **inventario**, siempre en **solo lectura**.

No reimplementa nada: delega en las rutas de `services/api`, que conservan su validación, su caché y sus reglas de negocio.

```
cliente MCP (agente LangGraph, MCP Playground, otro equipo…)
   │  Authorization: Bearer <JWT del proveedor OIDC>      Streamable HTTP (stateless)
   ▼
/mcp ── MCP Auth (mcpauth): firma, emisor, audiencia y scope mcp:access
   │     401 / 403 + WWW-Authenticate → /.well-known/oauth-protected-resource/mcp
   ▼
ToolAccessMiddleware: scope por tool · inventario de solo lectura · auditoría
   ▼
tools (FastMCP) ──HTTP──► services/api  (cuenta de servicio del servidor MCP)
   GET   /api/incidents, /api/incidents/{id}
   POST  /api/incidents
   PATCH /api/incidents/{id}/status      ← endpoint de ciclo de vida, nunca un PATCH genérico
   GET   /inventory/products, /inventory/products/{id}   ← el cliente bloquea cualquier no-GET
```

## Decisiones de diseño

| Decisión | Elección | Por qué |
|---|---|---|
| Transporte | **Streamable HTTP**, *stateless*, respuestas JSON | Lo consumen varios clientes remotos: el agente, otros equipos y partners. stdio solo sirve a un proceso hijo local y no tiene capa HTTP donde exigir un bearer token. Con HTTP, cada petición lleva su token y se valida en cada mensaje. Sin sesión, cualquier réplica atiende cualquier petición. |
| Autenticación | OAuth 2.1 con **MCP Auth** (`mcpauth` 0.2) en modo *resource server* | Publica la Protected Resource Metadata (RFC 9728) y valida JWT contra el JWKS de un proveedor OIDC cualquiera (`OIDC_ISSUER`). No se usa la auth integrada de FastMCP: ni `auth=` en el servidor ni en las tools. |
| Identidad hacia el backend | Cuenta de servicio (`MCP_BACKEND_EMAIL`/`MCP_BACKEND_PASSWORD`) | El access token OAuth del cliente tiene como audiencia el servidor MCP, no el API, así que **nunca se reenvía**. El JWT del backend se guarda en memoria y se renueva si caduca. |
| Mínimo privilegio | Un scope por capacidad y por tool | `mcpauth` exige `mcp:access` con `required_scopes` para cualquier petición. Cada tool exige además su scope, y `tools/list` solo muestra las tools que el token puede invocar. |
| Inventario de solo lectura | Por diseño, en 4 capas | (1) No existe el scope `inventory:write`. (2) La única tool de inventario es de lectura (`readOnlyHint`). (3) Cualquier intento de escritura se **rechaza explícitamente** con `INVENTORY_READ_ONLY`: una operación distinta de `list`/`get`, campos como `current_stock`, `quantity` o `minimum_stock`, o una tool `inventory_*` inexistente como `inventory_update_stock`. (4) El cliente HTTP del backend bloquea cualquier método distinto de GET hacia `/inventory`. |
| Discovery | Instrucciones del servidor y, por tool, descripción, `inputSchema`, `outputSchema` y anotaciones | Un agente sin contexto humano sabe qué puede hacer, con qué scope, qué devuelve, qué transiciones de estado son válidas y qué significa cada error. |

## Tools

Los nombres de campo, los IDs y los valores de dominio son los de las APIs existentes: `models.py` y `schemas.py` de `services/api`. Lo garantiza [`tests/mcp_server/test_contracts_match_backend.py`](../../tests/mcp_server/test_contracts_match_backend.py).

| Tool | Scope | Backend | Entrada | Salida | Anotaciones |
|---|---|---|---|---|---|
| `incidents_get_ticket` | `incidents:read` | `GET /api/incidents/{id}` | `incident_id` (UUID) | `IncidentDetail`: `id`, `title`, `description`, `category`, `status`, `origin`, `branch`, `created_at`, `updated_at` | readOnly, idempotent |
| `incidents_list_tickets` | `incidents:read` | `GET /api/incidents?status&category&origin&branch` | filtros opcionales y `limit` (1–100, por defecto 20) | `IncidentList`: `total`, `returned`, `filters`, `incidents[]` | readOnly, idempotent |
| `incidents_create_ticket` | `incidents:write` | `POST /api/incidents` | `title`, `description`, `category`, `origin`, `branch` | `IncidentCreated`: `id`, `status` (`open`), `created_at`, `updated_at` | no idempotente |
| `incidents_update_ticket_status` | `incidents:write` | `PATCH /api/incidents/{id}/status` | `incident_id`, `status` | `IncidentStatusChanged`: `id`, `previous_status`, `status`, `updated_at` | no idempotente |
| `inventory_query_products` | `inventory:read` | `GET /inventory/products[/{id}]` | `operation` (`list` o `get`), `product_id`, `sku`, `warehouse` (`LA`/`ZGZ`), `category`, `below_minimum_only` | `InventoryQueryResult`: `operation`, `total` y `products[]` (`id`, `sku`, `name`, `client_name`, `client_id`, `category`, `warehouse`, `current_stock`, `minimum_stock`, `below_minimum`) | readOnly, idempotent |

Valores de dominio:
- `status`: `open` | `in_progress` | `resolved` | `discarded`. Transiciones: `open → in_progress | discarded`; `in_progress → resolved | discarded`; `resolved` y `discarded` son finales.
- `category` (incidencias): `lost_parcel`, `delivery_failure`, `inventory_discrepancy`, `carrier_issue`, `returns_issue`, `warehouse_incident`, `system_failure`, `client_complaint` u `other`.
- `origin`: `customer` | `branch` | `internal`.
- `branch`: `central`, `la_warehouse`, `la_office`, `zaragoza_warehouse` o `zaragoza_office`.
- `category` (producto): `fashion` | `electronics` | `cosmetics`.

## Scopes y clientes

| Scope | Permite |
|---|---|
| `mcp:access` | Conectarse al servidor (lo exige `mcpauth` en cada petición) |
| `incidents:read` | `incidents_get_ticket`, `incidents_list_tickets` |
| `incidents:write` | `incidents_create_ticket`, `incidents_update_ticket_status` |
| `inventory:read` | `inventory_query_products` |

Clientes OAuth de desarrollo (`client_credentials`, creados por [`dev/keycloak_setup.py`](dev/keycloak_setup.py)):

| Cliente | Scopes | Uso |
|---|---|---|
| `trackflow-agent` | `mcp:access incidents:read inventory:read` | El agente LangGraph: solo lectura |
| `trackflow-ops-console` | `mcp:access incidents:read incidents:write inventory:read` | Operación y pruebas en MCP Playground |
| `trackflow-inventory-viewer` | `mcp:access inventory:read` | Demostrar el mínimo privilegio |

## Códigos de error

**Capa HTTP (MCP Auth).** Responde antes de llegar a MCP con `WWW-Authenticate: Bearer error="…", resource_metadata="…/.well-known/oauth-protected-resource/mcp"`.

| HTTP | `error` | Cuándo |
|---|---|---|
| 401 | `missing_auth_header` | Sin cabecera `Authorization` |
| 401 | `invalid_auth_header_format` | La cabecera no es `Bearer <token>` |
| 401 | `invalid_token` | Firma inválida, token mal formado o expirado |
| 401 | `invalid_issuer` | El emisor no es `OIDC_ISSUER` |
| 401 | `invalid_audience` | `aud` no es este recurso (`MCP_RESOURCE_URL`) |
| 403 | `missing_required_scopes` | Falta `mcp:access` |

**Capa de tool.** El resultado lleva `isError: true` y su texto es JSON:

```json
{"error": {"code": "INVENTORY_READ_ONLY", "message": "…", "details": {"tool": "inventory_query_products", "reason": "…", "allowed_operations": ["get", "list"]}}}
```

| `code` | Cuándo | Qué hacer |
|---|---|---|
| `INSUFFICIENT_SCOPE` | Al token le falta el scope de la tool (`details.required_scope`, `granted_scopes`) | Pedir un token con ese scope; no reintentar |
| `INVENTORY_READ_ONLY` | Cualquier intento de modificar el inventario | Ninguna: el inventario no se modifica por MCP (los movimientos se registran como entradas o salidas en TrackFlow) |
| `VALIDATION_ERROR` | Argumentos inválidos (`details.errors[]` con `field` y `message`) | Corregir y reintentar |
| `NOT_FOUND` | El ticket o el producto no existe | — |
| `INVALID_STATUS_TRANSITION` | El ciclo de vida no permite el cambio (`current_status`, `allowed`) | Elegir uno de `allowed` |
| `BACKEND_TIMEOUT` | El backend no respondió en `MCP_BACKEND_TIMEOUT_SECONDS` (5 s) | Reintentar más tarde; no se asumió ningún resultado |
| `BACKEND_UNAVAILABLE` | Backend caído o error 5xx | Reintentar más tarde |
| `BACKEND_AUTH_FAILED` | La cuenta de servicio del servidor MCP no es válida | Revisar la configuración del servidor |
| `BACKEND_ERROR` | Respuesta inesperada del backend | — |

## Auditoría

Cada invocación deja una línea JSON en stdout y en `MCP_AUDIT_LOG`, si está definido. Incluye la tool, el cliente, el resultado y la duración, **sin el token**:

```json
{"ts": "…", "event": "tool_call", "tool": "incidents_get_ticket", "client_id": "trackflow-agent", "subject": "…", "scopes": ["incidents:read", "inventory:read", "mcp:access"], "arguments": {"incident_id": "…"}, "outcome": "ok", "duration_ms": 41.2}
```

Eventos:
- `tool_call`: una invocación, con `outcome` `ok` o `error` y su `error_code`.
- `tools_list`: qué tools se mostraron a cada cliente.
- `auth_rejected`: un rechazo 401/403 de la capa HTTP, con su `error`.

## Ejecutar en local

1. **Proveedor OIDC.** En desarrollo se usa Keycloak, aunque sirve cualquier proveedor OIDC con JWKS.

   ```bash
   docker run -d --name trackflow-keycloak-dev --restart unless-stopped -p 127.0.0.1:8180:8080 \
     -e KC_BOOTSTRAP_ADMIN_USERNAME=… -e KC_BOOTSTRAP_ADMIN_PASSWORD=… -e KC_HOSTNAME=http://localhost:8180 \
     quay.io/keycloak/keycloak:26.0.7 start-dev
   KC_ADMIN_USER=… KC_ADMIN_PASSWORD=… uv run python mcps/trackflow_mcp/dev/keycloak_setup.py \
     --resource http://127.0.0.1:8090/mcp --secrets-out ~/.config/trackflow/mcp-clients.env   # fuera del repo
   ```

2. **Backend** (`services/api`, puerto 8081) con una cuenta de servicio para el servidor MCP, creada con `POST /users`.

3. **Servidor MCP:**

   ```bash
   export OIDC_ISSUER=http://localhost:8180/realms/trackflow MCP_RESOURCE_URL=http://127.0.0.1:8090/mcp
   export TRACKFLOW_API_BASE_URL=http://127.0.0.1:8081 MCP_BACKEND_EMAIL=… MCP_BACKEND_PASSWORD=…
   uv run python -m mcps.trackflow_mcp.server        # http://127.0.0.1:8090/mcp
   ```

Variables opcionales:
- `MCP_HOST` y `MCP_PORT`;
- `MCP_ALLOWED_HOSTS`: hosts públicos admitidos en la cabecera Host;
- `MCP_CORS_ORIGINS`: clientes MCP web;
- `MCP_BACKEND_TIMEOUT_SECONDS`;
- `MCP_AUDIT_LOG`.

Con Docker Compose el servidor es opcional: `docker compose --profile mcp up`.

## Probar desde MCP Playground (Codespaces)

`localhost` no es accesible desde Playground. Hay que exponer el puerto del servidor MCP:

```bash
gh codespace ports visibility 8090:public -c "$CODESPACE_NAME"
PUBLIC=https://$CODESPACE_NAME-8090.app.github.dev
# El recurso protegido es la URL pública: es la que va en el claim aud.
KC_ADMIN_USER=… KC_ADMIN_PASSWORD=… uv run python mcps/trackflow_mcp/dev/keycloak_setup.py --resource $PUBLIC/mcp --secrets-out …
MCP_RESOURCE_URL=$PUBLIC/mcp MCP_ALLOWED_HOSTS=$CODESPACE_NAME-8090.app.github.dev \
  MCP_CORS_ORIGINS=https://mcpplaygroundonline.com uv run python -m mcps.trackflow_mcp.server
# Token para pegar en Playground (Authorization: Bearer …), válido 15 min:
curl -s -X POST $OIDC_ISSUER/protocol/openid-connect/token -d grant_type=client_credentials \
  -d client_id=trackflow-ops-console -d client_secret=$TRACKFLOW_OPS_CONSOLE_CLIENT_SECRET | jq -r .access_token
```

En Playground, URL `$PUBLIC/mcp` y transporte Streamable HTTP, con el bearer token. El flujo de cada tool y el intento de escritura sobre el inventario están documentados en [`docs/pr-evidence/mcp-server/`](../../docs/pr-evidence/mcp-server/README.md).

## El agente como cliente MCP

El agente LangGraph (`services/agent`) ya no llama a `services/api`. Sus nodos `mcp_incidents` y `mcp_inventory`:
- cargan estas tools con **`langchain-mcp-adapters`** (`services/agent/mcp_client.py`);
- se autentican como `trackflow-agent` (`AGENT_OAUTH_CLIENT_ID` / `AGENT_OAUTH_CLIENT_SECRET`) con un access token `client_credentials` que se renueva solo.

El enrutamiento RAG / tools no cambió. Ver [`docs/agent/agent-graph.md`](../../docs/agent/agent-graph.md).

## Tests

```bash
uv run pytest tests/mcp_server -q                              # OAuth, scopes, discovery, solo lectura, errores, auditoría, agente vía MCP
(cd services/api && uv run pytest tests/test_mcp_server_integration.py -q)   # contra el Incidents Manager y el inventario reales
```
