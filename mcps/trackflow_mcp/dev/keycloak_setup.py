"""Configura (de forma idempotente) el realm OAuth 2.1 / OIDC de desarrollo en Keycloak.

El servidor MCP es agnóstico al proveedor: solo necesita el ``issuer`` (OIDC
discovery + JWKS). Este script prepara un Keycloak local para desarrollo,
pruebas y la validación en MCP Playground:

* realm ``trackflow``;
* client scopes ``mcp:access``, ``incidents:read``, ``incidents:write`` e
  ``inventory:read`` (no existe ``inventory:write``: el inventario es de solo
  lectura por diseño);
* un mapper de audiencia que pone el identificador del recurso MCP en ``aud``
  (Keycloak no implementa el parámetro ``resource`` de RFC 8707, así que la
  audiencia se fija por client scope);
* tres clientes confidenciales ``client_credentials`` con mínimo privilegio:

  ============================  ================================================
  cliente                       scopes
  ============================  ================================================
  trackflow-agent               mcp:access incidents:read inventory:read
  trackflow-ops-console         mcp:access incidents:read incidents:write
                                inventory:read
  trackflow-inventory-viewer    mcp:access inventory:read
  ============================  ================================================

Los secretos de los clientes los genera Keycloak y se escriben SOLO en el
fichero indicado con ``--secrets-out`` (fuera del repositorio).

Uso::

    KC_ADMIN_USER=… KC_ADMIN_PASSWORD=… uv run python mcps/trackflow_mcp/dev/keycloak_setup.py \\
        --resource http://127.0.0.1:8090/mcp --secrets-out /ruta/fuera/del/repo/mcp-clients.env
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

REALM = "trackflow"
SCOPES = {
    "mcp:access": "Conectarse al servidor MCP de TrackFlow (listar tools).",
    "incidents:read": "Consultar tickets del Incidents Manager.",
    "incidents:write": "Crear tickets y cambiar su estado por el ciclo de vida del Incidents Manager.",
    "inventory:read": "Consultar productos y stock del inventario (solo lectura).",
}
AUDIENCE_SCOPE = "trackflow-mcp-audience"
CLIENTS = {
    "trackflow-agent": ["mcp:access", "incidents:read", "inventory:read"],
    "trackflow-ops-console": ["mcp:access", "incidents:read", "incidents:write", "inventory:read"],
    "trackflow-inventory-viewer": ["mcp:access", "inventory:read"],
}
# Scopes por defecto del realm que no aportan nada a un resource server y
# solo inflarían el token. ``basic`` se conserva: añade el claim ``sub``.
DROP_DEFAULT_SCOPES = {"profile", "email", "roles", "web-origins", "acr", "microprofile-jwt", "organization"}


def admin_client(base_url: str) -> httpx.Client:
    user, password = os.environ.get("KC_ADMIN_USER"), os.environ.get("KC_ADMIN_PASSWORD")
    if not user or not password:
        sys.exit("Define KC_ADMIN_USER y KC_ADMIN_PASSWORD en el entorno.")
    token = httpx.post(
        f"{base_url}/realms/master/protocol/openid-connect/token",
        data={"grant_type": "password", "client_id": "admin-cli", "username": user, "password": password},
        timeout=15,
    ).raise_for_status().json()["access_token"]
    return httpx.Client(base_url=f"{base_url}/admin/realms", headers={"Authorization": f"Bearer {token}"}, timeout=15)


def ensure_realm(api: httpx.Client) -> None:
    if api.get(f"/{REALM}").status_code == 404:
        api.post("", json={"realm": REALM, "enabled": True, "accessTokenLifespan": 900}).raise_for_status()


def ensure_scope(api: httpx.Client, name: str, description: str, *, in_token: bool, mappers: list | None = None) -> str:
    existing = {s["name"]: s for s in api.get(f"/{REALM}/client-scopes").raise_for_status().json()}
    body = {
        "name": name, "description": description, "protocol": "openid-connect",
        "attributes": {"include.in.token.scope": str(in_token).lower(), "display.on.consent.screen": "true",
                       "consent.screen.text": description},
    }
    if name in existing:
        scope_id = existing[name]["id"]
        api.put(f"/{REALM}/client-scopes/{scope_id}", json={**existing[name], **body}).raise_for_status()
    else:
        api.post(f"/{REALM}/client-scopes", json=body).raise_for_status()
        scope_id = next(s["id"] for s in api.get(f"/{REALM}/client-scopes").json() if s["name"] == name)
    for mapper in mappers or []:
        current = {m["name"]: m for m in api.get(f"/{REALM}/client-scopes/{scope_id}/protocol-mappers/models").json()}
        if mapper["name"] in current:
            api.put(f"/{REALM}/client-scopes/{scope_id}/protocol-mappers/models/{current[mapper['name']]['id']}",
                    json={**current[mapper["name"]], **mapper}).raise_for_status()
        else:
            api.post(f"/{REALM}/client-scopes/{scope_id}/protocol-mappers/models", json=mapper).raise_for_status()
    return scope_id


def ensure_client(api: httpx.Client, client_id: str, scope_ids: dict[str, str], wanted: list[str]) -> str:
    found = api.get(f"/{REALM}/clients", params={"clientId": client_id}).raise_for_status().json()
    body = {
        "clientId": client_id, "enabled": True, "protocol": "openid-connect", "publicClient": False,
        "serviceAccountsEnabled": True, "standardFlowEnabled": False, "directAccessGrantsEnabled": False,
        "implicitFlowEnabled": False, "fullScopeAllowed": False,
        "description": "Cliente del servidor MCP de TrackFlow (client_credentials, mínimo privilegio).",
    }
    if found:
        internal_id = found[0]["id"]
        api.put(f"/{REALM}/clients/{internal_id}", json={**found[0], **body}).raise_for_status()
    else:
        api.post(f"/{REALM}/clients", json=body).raise_for_status()
        internal_id = api.get(f"/{REALM}/clients", params={"clientId": client_id}).json()[0]["id"]

    keep = set(wanted) | {AUDIENCE_SCOPE, "basic"}
    for kind in ("default-client-scopes", "optional-client-scopes"):
        for scope in api.get(f"/{REALM}/clients/{internal_id}/{kind}").json():
            if scope["name"] not in keep or kind == "optional-client-scopes":
                api.delete(f"/{REALM}/clients/{internal_id}/{kind}/{scope['id']}")
    for name in keep:
        if name in scope_ids:
            api.put(f"/{REALM}/clients/{internal_id}/default-client-scopes/{scope_ids[name]}").raise_for_status()
    return api.get(f"/{REALM}/clients/{internal_id}/client-secret").raise_for_status().json()["value"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keycloak-url", default="http://localhost:8180")
    parser.add_argument("--resource", required=True, help="Identificador del recurso MCP (va en el claim aud)")
    parser.add_argument("--secrets-out", required=True, type=Path, help="Fichero .env FUERA del repo para los secretos")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[3]
    if repo_root in args.secrets_out.resolve().parents:
        sys.exit("--secrets-out debe estar fuera del repositorio: los secretos nunca se versionan.")

    with admin_client(args.keycloak_url) as api:
        ensure_realm(api)
        scope_ids = {name: ensure_scope(api, name, desc, in_token=True) for name, desc in SCOPES.items()}
        scope_ids[AUDIENCE_SCOPE] = ensure_scope(
            api, AUDIENCE_SCOPE, "Pone el recurso MCP en el claim aud.", in_token=False,
            mappers=[{"name": "mcp-resource-audience", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
                      "config": {"included.custom.audience": args.resource, "access.token.claim": "true",
                                 "id.token.claim": "false", "introspection.token.claim": "true"}}],
        )
        basic = [s for s in api.get(f"/{REALM}/client-scopes").json() if s["name"] == "basic"]
        if basic:
            scope_ids["basic"] = basic[0]["id"]
        for scope in api.get(f"/{REALM}/default-default-client-scopes").json():
            if scope["name"] in DROP_DEFAULT_SCOPES:
                api.delete(f"/{REALM}/default-default-client-scopes/{scope['id']}")
        secrets = {client: ensure_client(api, client, scope_ids, wanted) for client, wanted in CLIENTS.items()}

    args.secrets_out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"export OIDC_ISSUER={args.keycloak_url}/realms/{REALM}", f"export MCP_RESOURCE_URL={args.resource}"]
    for client, secret in secrets.items():
        key = client.upper().replace("-", "_")
        lines += [f"export {key}_CLIENT_ID={client}", f"export {key}_CLIENT_SECRET={secret}"]
    args.secrets_out.write_text("\n".join(lines) + "\n")
    args.secrets_out.chmod(0o600)
    print(f"Realm '{REALM}' listo: {len(SCOPES)} scopes, {len(CLIENTS)} clientes. Secretos en {args.secrets_out}")


if __name__ == "__main__":
    main()
