"""Stream SSE de tickets RFP: ``GET /rfp-intake/events`` (evento ``rfp_ticket_created``).

* Mismo JWT que el resto del API (cabecera ``Authorization``, nunca en la URL). Sin credenciales válidas: 401 y ningún
  evento. El stream se cierra cuando caduca el token o se desactiva el usuario, aunque ya estuviera abierto.
* El worker registra los eventos en el outbox ``rfp_ticket_events``; un sondeo por proceso despierta a los streams.
* ``id:`` es el id del outbox. Con ``Last-Event-ID`` se reenvía lo ocurrido durante la desconexión. Si el hueco supera
  ``RFP_SSE_REPLAY_LIMIT`` o el id no es reconocible, se envía ``rfp_stream_reset``: el cliente debe recargar la lista
  (la pérdida nunca es silenciosa).
"""

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from jose import jwt

from routes.rfp_intake import _repo
from security import get_current_user
from user_service import get_user_by_id

logger = logging.getLogger("trackflow.rfp_events")
router = APIRouter(prefix="/rfp-intake", tags=["rfp intake"])
STREAM_RESET = "rfp_stream_reset"
RETRY_MS = 1000
BATCH = 100
HEADERS = {"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"}


def _setting(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return default
    return value if value > 0 else default


class EventHub:
    """Sondea el último id del outbox (solo mientras haya streams abiertos) y avisa a los que estén esperando."""

    def __init__(self, latest_event_id: Callable[[], int], poll_seconds: float):
        self._latest_event_id, self._poll_seconds = latest_event_id, poll_seconds
        self._condition = asyncio.Condition()
        self._task: asyncio.Task | None = None
        self.latest = 0
        self.subscribers = 0

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[None]:
        self.subscribers += 1
        if self._task is None:
            self._task = asyncio.create_task(self._poll())
        try:
            yield
        finally:
            self.subscribers -= 1
            if self.subscribers == 0 and self._task is not None:
                self._task.cancel()
                self._task = None

    async def _poll(self) -> None:
        while True:
            try:
                latest = await run_in_threadpool(self._latest_event_id)
            except Exception:  # noqa: BLE001 — la base puede volver; al hacerlo, los eventos pendientes salen
                logger.exception("No se pudo leer el outbox de eventos RFP")
            else:
                if latest != self.latest:
                    async with self._condition:
                        self.latest = latest
                        self._condition.notify_all()
            await asyncio.sleep(self._poll_seconds)

    async def wait_beyond(self, event_id: int, timeout: float) -> bool:
        async def wait() -> None:
            async with self._condition:
                await self._condition.wait_for(lambda: self.latest > event_id)

        try:
            await asyncio.wait_for(wait(), timeout)
            return True
        except TimeoutError:
            return False


def _hub(request: Request) -> EventHub:
    state = request.app.state
    if getattr(state, "rfp_event_hub", None) is None:
        state.rfp_event_hub = EventHub(_repo(request).latest_event_id, _setting("RFP_SSE_POLL_SECONDS", 1))
    return state.rfp_event_hub


def frame(event: str, data: dict, event_id: int) -> str:
    return f"id: {event_id}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def resolve_cursor(repo, last_event_id: str | None, replay_limit: int) -> tuple[int, str | None]:
    """Desde qué id emitir y, si no se puede reanudar, el motivo del reset."""
    latest = repo.latest_event_id()
    if not last_event_id:
        return latest, None
    if not (last_event_id.isascii() and last_event_id.isdigit()) or int(last_event_id) > latest:
        return latest, "unknown_event_id"
    cursor = int(last_event_id)
    if len(repo.events_after(cursor, replay_limit + 1)) > replay_limit:
        return latest, "replay_limit_exceeded"
    return cursor, None


@router.get("/events", response_class=StreamingResponse,
            responses={200: {"content": {"text/event-stream": {}}}, 401: {"description": "JWT ausente o no válido"}})
async def stream_rfp_events(request: Request, user=Depends(get_current_user)):
    repo, hub, state = _repo(request), _hub(request), request.app.state
    # get_current_user ya verificó firma y caducidad; aquí solo se lee cuándo caduca para cerrar el stream a tiempo.
    token = request.headers["authorization"].split(" ", 1)[1].strip()
    # (+1: python-jose acepta el token hasta que termina el segundo de ``exp``; el stream dura lo mismo.)
    expires_at = float(jwt.get_unverified_claims(token)["exp"]) + 1
    keepalive = _setting("RFP_SSE_KEEPALIVE_SECONDS", 15)
    cursor, reset = await run_in_threadpool(resolve_cursor, repo, request.headers.get("last-event-id"),
                                            int(_setting("RFP_SSE_REPLAY_LIMIT", 200)))

    def user_is_active() -> bool:
        with state.lock:
            current = get_user_by_id(state.db, user.id)
        return current is not None and current.is_active

    async def stream(cursor: int) -> AsyncIterator[str]:
        async with hub.subscribe():
            # Cabeceras y cursor salen enseguida, aunque todavía no haya eventos.
            yield f"retry: {RETRY_MS}\nid: {cursor}\n\n"
            if reset:
                yield frame(STREAM_RESET, {"reason": reset}, cursor)
            pending = True  # al abrir, por si hay que reenviar; después solo se consulta cuando el sondeo común avisa
            while time.time() < expires_at:
                if pending:
                    seen = hub.latest
                    events = await run_in_threadpool(repo.events_after, cursor, BATCH)
                    for event in events:
                        yield frame(event.event_type, event.payload, event.id)
                        cursor = event.id
                    if events:
                        continue
                    cursor = max(cursor, seen)  # ids ya vistos por el sondeo y borrados después: no hay nada que esperar
                pending = await hub.wait_beyond(cursor, min(keepalive, max(0.05, expires_at - time.time())))
                if not pending:
                    if not await run_in_threadpool(user_is_active):
                        return
                    yield ": keep-alive\n\n"

    return StreamingResponse(stream(cursor), media_type="text/event-stream", headers=HEADERS)
