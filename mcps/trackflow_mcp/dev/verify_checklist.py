"""Verifica en vivo el checklist del servidor MCP contra un servidor en marcha (sin imprimir tokens ni secretos).

    OIDC_ISSUER=… TRACKFLOW_OPS_CONSOLE_CLIENT_SECRET=… TRACKFLOW_AGENT_CLIENT_SECRET=… \\
    TRACKFLOW_INVENTORY_VIEWER_CLIENT_SECRET=… uv run python mcps/trackflow_mcp/dev/verify_checklist.py http://127.0.0.1:8090/mcp
"""

from __future__ import annotations

import json
import os
import sys

import httpx

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8090/mcp"
ISSUER = os.environ["OIDC_ISSUER"]
TICKET = "d32a9b66-b81b-5dce-b90f-c4e8bfc27161"


def token(client: str) -> str:
    secret = os.environ[client.upper().replace("-", "_") + "_CLIENT_SECRET"]
    return httpx.post(f"{ISSUER}/protocol/openid-connect/token", data={
        "grant_type": "client_credentials", "client_id": client, "client_secret": secret}).raise_for_status().json()["access_token"]


def rpc(method, params=None, bearer=None):
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    return httpx.post(URL, headers=headers, timeout=30, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def call(bearer, name, arguments):
    result = rpc("tools/call", {"name": name, "arguments": arguments}, bearer).json()["result"]
    if result["isError"]:
        return "ERROR", json.loads(result["content"][0]["text"])["error"]
    return "OK", result["structuredContent"]


def show(title, value):
    print(f"\n### {title}\n{json.dumps(value, ensure_ascii=False, indent=2) if not isinstance(value, str) else value}")


ops, agent, viewer = token("trackflow-ops-console"), token("trackflow-agent"), token("trackflow-inventory-viewer")
print(f"Servidor MCP: {URL}")

prm_url = URL.replace("/mcp", "/.well-known/oauth-protected-resource/mcp")
show("1. Protected Resource Metadata (GET " + prm_url + ")", httpx.get(prm_url).json())
init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "verify", "version": "1"}}, ops).json()["result"]
show("1. initialize → serverInfo", init["serverInfo"])
tools = rpc("tools/list", bearer=ops).json()["result"]["tools"]
show("1/5. tools/list (trackflow-ops-console): nombre, descripción, esquema de entrada y salida, anotaciones", [
    {"name": t["name"], "title": t.get("title"), "description": t["description"],
     "inputSchema.properties": {k: {kk: vv for kk, vv in v.items() if kk in ("type", "enum", "description", "anyOf")}
                                for k, v in t["inputSchema"]["properties"].items()},
     "inputSchema.required": t["inputSchema"].get("required", []),
     "outputSchema.properties": sorted(t["outputSchema"]["properties"]), "annotations": t.get("annotations")} for t in tools])

for label, bearer in (("sin token", None), ("token basura", "abc.def.ghi")):
    for method, params in (("tools/list", {}), ("tools/call", {"name": "incidents_get_ticket", "arguments": {"incident_id": TICKET}})):
        r = rpc(method, params, bearer)
        show(f"2. {method} {label}", {"http_status": r.status_code, "body": r.json(), "www-authenticate": r.headers.get("www-authenticate")})

st, created = call(ops, "incidents_create_ticket", {"title": "Verificación MCP: etiqueta ilegible en recepción",
    "description": "Ticket creado desde el servidor MCP para verificar el checklist.", "category": "warehouse_incident",
    "origin": "internal", "branch": "la_warehouse"})
show("3. incidents_create_ticket → POST /api/incidents", {st: created})
show("3. incidents_update_ticket_status open→in_progress → PATCH /api/incidents/{id}/status",
     dict([call(ops, "incidents_update_ticket_status", {"incident_id": created["id"], "status": "in_progress"})]))
show("3. incidents_get_ticket (consulta del estado)", dict([call(ops, "incidents_get_ticket", {"incident_id": created["id"]})]))
show("3. incidents_list_tickets status=open, category=returns_issue, limit=2",
     dict([call(agent, "incidents_list_tickets", {"status": "open", "category": "returns_issue", "limit": 2})]))

show("4. inventory_query_products list ZGZ", dict([call(viewer, "inventory_query_products", {"operation": "list", "warehouse": "ZGZ"})]))
show("4. inventory_query_products get product_id=1", dict([call(viewer, "inventory_query_products", {"operation": "get", "product_id": 1})]))
for args, name in (({"operation": "update", "product_id": 1, "current_stock": 999}, "inventory_query_products"),
                   ({"operation": "get", "product_id": 1, "minimum_stock": 1}, "inventory_query_products"),
                   ({"product_id": 1, "current_stock": 0}, "inventory_update_stock")):
    show(f"4. intento de escritura {name} {args} (token con TODOS los scopes)", dict([call(ops, name, args)]))
show("4. stock tras los intentos (sin cambios)", dict([call(viewer, "inventory_query_products", {"operation": "get", "product_id": 1})]))

r = rpc("tools/list", bearer=token("trackflow-inventory-viewer").replace("a", "b", 1))
show("6. autenticación: token con firma alterada", {"http_status": r.status_code, "error": r.json().get("error")})
show("6. autorización: trackflow-inventory-viewer llama incidents_get_ticket", dict([call(viewer, "incidents_get_ticket", {"incident_id": TICKET})]))
show("6. autorización: trackflow-agent intenta incidents_update_ticket_status", dict([call(agent, "incidents_update_ticket_status", {"incident_id": TICKET, "status": "discarded"})]))
show("6. validación: incident_id no UUID", dict([call(ops, "incidents_get_ticket", {"incident_id": "482"})]))
show("6. validación: status fuera de dominio", dict([call(ops, "incidents_list_tickets", {"status": "closed"})]))
show("6. ciclo de vida: in_progress → open", dict([call(ops, "incidents_update_ticket_status", {"incident_id": created["id"], "status": "open"})]))
show("6. no encontrado", dict([call(ops, "incidents_get_ticket", {"incident_id": "00000000-0000-4000-8000-000000000000"})]))
