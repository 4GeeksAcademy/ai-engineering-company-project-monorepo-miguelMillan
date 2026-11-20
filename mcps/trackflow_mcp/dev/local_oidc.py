"""Proveedor OAuth 2.1 / OIDC local SOLO para tests y utilidades para servir apps ASGI en un hilo.

Publica discovery (``/.well-known/openid-configuration``), JWKS con una clave
RSA real y un endpoint de token ``client_credentials``. mcpauth lo descubre y
valida sus firmas exactamente igual que con Keycloak. No es un proveedor para
producción: los clientes y su secreto son fijos y los tokens se firman en memoria.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

CLIENT_SCOPES = {
    "trackflow-agent": "mcp:access incidents:read inventory:read",
    "trackflow-ops-console": "mcp:access incidents:read incidents:write inventory:read",
    "trackflow-inventory-viewer": "mcp:access inventory:read",
    "sin-acceso-mcp": "incidents:read",
}
CLIENT_SECRET = "secreto-de-prueba"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class OidcProvider:
    issuer: str
    key: rsa.RSAPrivateKey
    resource: str = ""
    issued: list[str] = field(default_factory=list)

    def token(self, client_id: str = "trackflow-ops-console", *, scope: str | None = None, aud: str | None = None,
              iss: str | None = None, exp_in: int = 300, key: rsa.RSAPrivateKey | None = None) -> str:
        now = int(time.time())
        claims = {"iss": iss or self.issuer, "aud": aud or self.resource, "sub": f"sa-{client_id}",
                  "client_id": client_id, "azp": client_id, "scope": scope if scope is not None else CLIENT_SCOPES[client_id],
                  "iat": now, "exp": now + exp_in}
        token = jwt.encode(claims, key or self.key, algorithm="RS256", headers={"kid": "test-key"})
        self.issued.append(token)
        return token


def start_local_oidc() -> tuple[OidcProvider, ThreadingHTTPServer]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": "test-key", "use": "sig", "alg": "RS256"})
    port = free_port()
    provider = OidcProvider(issuer=f"http://127.0.0.1:{port}/realms/trackflow", key=key)

    class Handler(BaseHTTPRequestHandler):
        def _json(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            base = provider.issuer
            if self.path == "/realms/trackflow/.well-known/openid-configuration":
                return self._json(200, {
                    "issuer": base, "jwks_uri": f"{base}/protocol/openid-connect/certs",
                    "token_endpoint": f"{base}/protocol/openid-connect/token",
                    "authorization_endpoint": f"{base}/protocol/openid-connect/auth",
                    "response_types_supported": ["code"], "grant_types_supported": ["client_credentials", "authorization_code"],
                    "code_challenge_methods_supported": ["S256"], "subject_types_supported": ["public"],
                    "id_token_signing_alg_values_supported": ["RS256"],
                })
            if self.path == "/realms/trackflow/protocol/openid-connect/certs":
                return self._json(200, {"keys": [jwk]})
            self._json(404, {})

        def do_POST(self):
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()).items()}
            client = form.get("client_id")
            if form.get("grant_type") != "client_credentials" or client not in CLIENT_SCOPES or form.get("client_secret") != CLIENT_SECRET:
                return self._json(401, {"error": "invalid_client"})
            self._json(200, {"access_token": provider.token(client), "token_type": "Bearer", "expires_in": 300})

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return provider, server




def serve(app, port: int):
    """Sirve una app ASGI con uvicorn en un hilo (con lifespan) y espera a que arranque."""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(250):
        if server.started:
            break
        time.sleep(0.02)
    return server, thread
