# Evidencias del servidor MCP de TrackFlow

Ejecución real del 2026-11-20 en GitHub Codespaces, con todo esto en marcha:
- el servidor MCP (`uv run python -m mcps.trackflow_mcp.server`), expuesto en el puerto reenviado **público** `https://<codespace>-8090.app.github.dev/mcp`;
- Keycloak 26 como proveedor OIDC;
- el API real de TrackFlow: el Incidents Manager, con 95 tickets del seed oficial, y el inventario en Postgres/Supabase.

Ningún archivo contiene tokens ni secretos (revisado con `grep`).

| Archivo | Qué es |
|---|---|
| [`checklist-public-url.txt`](checklist-public-url.txt) | Salida de `mcps/trackflow_mcp/dev/verify_checklist.py` **contra la URL pública de Codespaces**: PRM, discovery, 401 sin token, flujo completo de cada tool, intentos de escritura en inventario y códigos de error |
| [`checklist-localhost.txt`](checklist-localhost.txt) | La misma verificación contra `127.0.0.1:8090` |
| [`backend-access-log.txt`](backend-access-log.txt) | Log de acceso del API durante la verificación pública. Incidencias: `POST /api/incidents` y `PATCH /api/incidents/{id}/status`, nunca un PATCH genérico. Inventario: **solo GET**, también durante los intentos de escritura |
| [`audit-log-public-url.jsonl`](audit-log-public-url.jsonl) | Log de auditoría del servidor MCP: una línea por invocación con tool, cliente, scopes, resultado, código de error y duración, más los rechazos 401 |
| [`agent-via-public-mcp.json`](agent-via-public-mcp.json) | El agente LangGraph contra la URL pública: `plan_sources` → `mcp_incidents` (tool MCP `incidents_get_ticket`) → `compose_answer` |

## Checklist

| Criterio | Resultado | Evidencia |
|---|---|---|
| El servidor vive en `mcps/`, arranca y expone sus tools con el discovery estándar | ✅ | `mcps/trackflow_mcp/`. `initialize` devuelve `serverInfo` e instrucciones; `tools/list` devuelve 5 tools |
| Sin access token válido no se puede listar ni ejecutar ninguna tool | ✅ | `tools/list` y `tools/call` sin token → **401 `missing_auth_header`**; con token basura o firma alterada → **401 `invalid_token`**. `WWW-Authenticate` apunta a la PRM. Lo aplica MCP Auth (`mcpauth`) antes de llegar a MCP |
| Tickets: crear, cambiar el estado con `PATCH /api/incidents/{id}/status` y consultar contra el Incidents Manager real | ✅ | Ticket `c5f670bf-…` creado (`open`), pasado a `in_progress` y consultado. El log del API muestra `POST /api/incidents` 201, `PATCH …/status` 200 y, para la transición inválida, `PATCH …/status` 400 → `INVALID_STATUS_TRANSITION` |
| Inventario: responde a las consultas y rechaza explícitamente cualquier escritura | ✅ | `list` (ZGZ, 4 productos) y `get` (id 1, 58 uds.). `operation=update` con `current_stock` 999, `minimum_stock` y la tool inexistente `inventory_update_stock` → **`INVENTORY_READ_ONLY`** con `reason` y `allowed_operations`, **incluso con el token que tiene todos los scopes**. El stock sigue en 58 y el backend solo recibió GET |
| Descripción y esquema claros, verificables desde el discovery | ✅ | Sección 1/5 de `checklist-public-url.txt`: nombre, título, descripción con el scope requerido, `inputSchema` con enums y descripciones, `outputSchema` y anotaciones (`readOnlyHint`, `idempotentHint`). Las instrucciones del servidor listan scopes, transiciones y códigos de error |
| Los errores de autenticación, autorización y validación tienen códigos y mensajes distintos | ✅ | Autenticación: HTTP 401 `missing_auth_header` / `invalid_token` / `invalid_audience` / `invalid_issuer`. Base scope: HTTP 403 `missing_required_scopes`. Autorización por tool: `INSUFFICIENT_SCOPE` (con `required_scope` y `granted_scopes`). Validación: `VALIDATION_ERROR` (con `field`). Además: `INVALID_STATUS_TRANSITION`, `NOT_FOUND` e `INVENTORY_READ_ONLY` |
| Al menos un log por invocación de tool con cliente, tool y resultado | ✅ | `audit-log-public-url.jsonl`: 16 `tool_call` y 4 `auth_rejected` en la verificación pública |
| El agente ya no llama directamente al Incidents Manager | ✅ | Nodos `mcp_incidents` / `mcp_inventory` con `langchain-mcp-adapters` y OAuth `client_credentials` (`trackflow-agent`). La implementación HTTP directa se eliminó. Los evals se regrabaron con los servicios reales **a través del servidor MCP**: 17 passed, con el criterio `mcp_tool` |
| MCP Playground con la URL pública reenviada de Codespaces | ⚠️ Parcial | El puerto 8090 es **público** y el servidor está configurado para esa URL: es su `resource`, el `aud` de los tokens, el host permitido y el CORS de `mcpplaygroundonline.com`, con el preflight comprobado. Los flujos de cada tool se ejecutaron contra esa URL pública con un cliente MCP. **La sesión en la web de MCP Playground la tiene que hacer una persona**: el asistente no tiene navegador. Pasos en la sección siguiente |

## Probar en MCP Playground

1. Abrir https://mcpplaygroundonline.com.
2. Elegir **Streamable HTTP** y la URL `https://<codespace>-8090.app.github.dev/mcp`.
3. Conseguir el token: autenticación **Bearer token**, con un token del cliente `trackflow-ops-console`. Caduca en 15 min:

   ```bash
   source <fichero de secretos fuera del repo>
   curl -s -X POST $OIDC_ISSUER/protocol/openid-connect/token -d grant_type=client_credentials \
     -d client_id=trackflow-ops-console -d client_secret=$TRACKFLOW_OPS_CONSOLE_CLIENT_SECRET | jq -r .access_token
   ```

4. Conectar sin token: debe fallar (401).
5. Conectar con el token: aparecen las 5 tools.
6. Ejecutar un flujo por tool:
   - `incidents_create_ticket`;
   - `incidents_update_ticket_status` → `in_progress`;
   - `incidents_get_ticket`;
   - `incidents_list_tickets` con `status=open`;
   - `inventory_query_products` con `operation=list`.
7. Intento de escritura: `inventory_query_products` con `{"operation": "update", "product_id": 1, "current_stock": 999}` → `INVENTORY_READ_ONLY`.
