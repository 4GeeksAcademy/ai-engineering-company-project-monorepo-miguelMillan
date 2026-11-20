"""Configuración del servidor MCP de TrackFlow (solo variables de entorno; nunca secretos en el repo)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # Transporte Streamable HTTP
    host: str = "127.0.0.1"
    port: int = 8090
    mcp_path: str = "/mcp"
    # Identificador del recurso protegido (RFC 9728 / RFC 8707). Es la URL con la
    # que los clientes llegan al endpoint MCP y el valor exigido en el claim ``aud``.
    resource_url: str = "http://127.0.0.1:8090/mcp"
    # Proveedor OAuth 2.1 / OIDC (cualquiera que publique discovery + JWKS).
    oidc_issuer: str = "http://localhost:8180/realms/trackflow"
    # Hosts públicos adicionales (p. ej. el puerto reenviado de Codespaces).
    allowed_hosts: list[str] = field(default_factory=list)
    # Orígenes de navegador autorizados por CORS (p. ej. un MCP Playground web).
    cors_origins: list[str] = field(default_factory=list)
    # Backend de TrackFlow (Incidents Manager + inventario) y su cuenta de servicio.
    api_base_url: str = "http://127.0.0.1:8081"
    backend_email: str | None = None
    backend_password: str | None = None
    backend_timeout_seconds: float = 5.0
    # Auditoría: además de stdout, una línea JSON por invocación en este fichero.
    audit_log_path: str | None = None


def load_settings() -> Settings:
    env = os.environ
    port = int(env.get("MCP_PORT", "8090"))
    return Settings(
        host=env.get("MCP_HOST", "127.0.0.1"),
        port=port,
        resource_url=env.get("MCP_RESOURCE_URL", f"http://127.0.0.1:{port}/mcp").rstrip("/"),
        oidc_issuer=env.get("OIDC_ISSUER", "http://localhost:8180/realms/trackflow").rstrip("/"),
        allowed_hosts=_csv(env.get("MCP_ALLOWED_HOSTS")),
        cors_origins=_csv(env.get("MCP_CORS_ORIGINS")),
        api_base_url=env.get("TRACKFLOW_API_BASE_URL", "http://127.0.0.1:8081").rstrip("/"),
        backend_email=env.get("MCP_BACKEND_EMAIL"),
        backend_password=env.get("MCP_BACKEND_PASSWORD"),
        backend_timeout_seconds=float(env.get("MCP_BACKEND_TIMEOUT_SECONDS", "5")),
        audit_log_path=env.get("MCP_AUDIT_LOG"),
    )
