"""Cliente HTTP hacia los servicios existentes de TrackFlow (``services/api``).

El servidor MCP no reimplementa nada: delega en las rutas del Incidents
Manager y del inventario, que conservan su validación, su caché y sus reglas
de negocio (por ejemplo, el ciclo de vida de los tickets).

* Incidencias: ``GET /api/incidents``, ``GET /api/incidents/{id}``,
  ``POST /api/incidents`` y ``PATCH /api/incidents/{id}/status`` (el endpoint de
  ciclo de vida; nunca un PATCH genérico sobre el recurso).
* Inventario: ``GET /inventory/products`` y ``GET /inventory/products/{id}``.
  El cliente de inventario rechaza en un hook cualquier método distinto de GET,
  así que aunque una tool lo intentara, la escritura no saldría del proceso.

Autenticación hacia el backend: cuenta de servicio (``MCP_BACKEND_EMAIL`` /
``MCP_BACKEND_PASSWORD``) que inicia sesión en ``POST /auth/login``; el JWT se
guarda en memoria y se renueva una vez si el backend responde 401. El access
token OAuth del cliente MCP NUNCA se reenvía al backend (no es su audiencia).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from mcps.trackflow_mcp.config import Settings
from mcps.trackflow_mcp.errors import ToolErrorCode, TrackflowToolError


class InventoryWriteBlocked(RuntimeError):
    pass


async def _inventory_get_only(request: httpx.Request) -> None:
    if request.url.path.startswith("/inventory") and request.method != "GET":
        raise InventoryWriteBlocked(f"{request.method} {request.url.path} bloqueado: el inventario es de solo lectura vía MCP")


class TrackflowBackend:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._transport = transport
        self._token: str | None = None
        self._lock = asyncio.Lock()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._settings.api_base_url, transport=self._transport,
            timeout=httpx.Timeout(self._settings.backend_timeout_seconds),
            headers={"Accept": "application/json"}, event_hooks={"request": [_inventory_get_only]},
        )

    async def _login(self, client: httpx.AsyncClient) -> str:
        email, password = self._settings.backend_email, self._settings.backend_password
        if not email or not password:
            raise TrackflowToolError(ToolErrorCode.BACKEND_AUTH_FAILED,
                                     "El servidor MCP no tiene configurada la cuenta de servicio del backend.")
        response = await client.post("/auth/login", json={"email": email, "password": password})
        if response.status_code != 200:
            raise TrackflowToolError(ToolErrorCode.BACKEND_AUTH_FAILED,
                                     "El backend rechazó la cuenta de servicio del servidor MCP.",
                                     {"backend_status": response.status_code})
        return response.json()["access_token"]

    async def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> httpx.Response:
        try:
            async with self._client() as client:
                for attempt in range(2):
                    async with self._lock:
                        if self._token is None:
                            self._token = await self._login(client)
                        token = self._token
                    response = await client.request(method, path, params=params, json=json,
                                                    headers={"Authorization": f"Bearer {token}"})
                    if response.status_code == 401 and attempt == 0:
                        async with self._lock:
                            if self._token == token:
                                self._token = None
                        continue
                    if response.status_code == 401:
                        raise TrackflowToolError(ToolErrorCode.BACKEND_AUTH_FAILED,
                                                 "El backend rechazó las credenciales del servidor MCP.")
                    return response
        except httpx.TimeoutException as error:
            raise TrackflowToolError(ToolErrorCode.BACKEND_TIMEOUT,
                                     f"El servicio de TrackFlow no respondió en {self._settings.backend_timeout_seconds:g} s.",
                                     {"path": path}) from error
        except httpx.TransportError as error:
            raise TrackflowToolError(ToolErrorCode.BACKEND_UNAVAILABLE, "El servicio de TrackFlow no está disponible.",
                                     {"path": path}) from error
        raise AssertionError("unreachable")

    # ------------------------------------------------------------------ incidencias
    async def get_incident(self, incident_id: str) -> dict:
        response = await self.request("GET", f"/api/incidents/{incident_id}")
        return self._json(response, not_found=f"No existe ningún ticket con id {incident_id}.")

    async def list_incidents(self, filters: dict[str, str]) -> list[dict]:
        return self._json(await self.request("GET", "/api/incidents", params=filters))

    async def create_incident(self, payload: dict) -> dict:
        return self._json(await self.request("POST", "/api/incidents", json=payload), expected=201)

    async def update_incident_status(self, incident_id: str, status: str) -> httpx.Response:
        return await self.request("PATCH", f"/api/incidents/{incident_id}/status", json={"status": status})

    # ------------------------------------------------------------------ inventario
    async def list_products(self) -> list[dict]:
        return self._json(await self.request("GET", "/inventory/products"))

    async def get_product(self, product_id: int) -> dict:
        response = await self.request("GET", f"/inventory/products/{product_id}")
        return self._json(response, not_found=f"No existe ningún producto con id {product_id}.")

    # ------------------------------------------------------------------
    @staticmethod
    def _json(response: httpx.Response, *, expected: int = 200, not_found: str = "Recurso no encontrado.") -> Any:
        if response.status_code == expected:
            return response.json()
        if response.status_code == 404:
            raise TrackflowToolError(ToolErrorCode.NOT_FOUND, not_found)
        if response.status_code == 422:
            raise TrackflowToolError(ToolErrorCode.VALIDATION_ERROR, "El backend rechazó los datos enviados.",
                                     {"errors": response.json().get("detail")})
        if response.status_code >= 500:
            raise TrackflowToolError(ToolErrorCode.BACKEND_UNAVAILABLE, "El servicio de TrackFlow devolvió un error interno.",
                                     {"backend_status": response.status_code})
        raise TrackflowToolError(ToolErrorCode.BACKEND_ERROR, "Respuesta inesperada del servicio de TrackFlow.",
                                 {"backend_status": response.status_code})
