"""Chat en tiempo real con el First-line CX agent (BRIEFING «Sistemas en Tiempo Real», Parte 2).

* ``POST /chat/sessions`` crea la ``ChatSession``; ``WS /chat/ws?session_id=…`` la conecta. Sin ``session_id`` de una
  sesión existente **de quien se autentica** no hay conexión.
* El JWT es el del API: ``?token=`` o un primer frame ``{"event": "auth", "data": {"token": …}}`` (el navegador no puede
  poner ``Authorization``). El frame es lo que usa el backoffice: no deja el token en URLs ni logs. Sin token válido no
  se envía ni se acepta ningún evento de chat, y la conexión se cierra al caducar.
* Esta ruta solo traduce frames: el agente publica en el canal de la sesión (``services/agent/chat``) y cada conexión se
  suscribe.

Códigos de cierre: 4401 sin autenticar · 4404 sesión inexistente o ajena · 4410 sesión cerrada · 1009 frame demasiado
grande · 1013 el cliente no da abasto (al volver se rehidrata).
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect

import routes.agent as agent_routes
from security import authenticate_token, get_current_user, is_staff
from services.agent.chat import events
from services.agent.chat.engine import ChatEngine
from services.agent.chat.store import ChatStore

logger = logging.getLogger("trackflow.chat")
router = APIRouter(prefix="/chat", tags=["chat"])
MAX_FRAME_BYTES = 32_768
UNAUTHORIZED, NOT_FOUND, CLOSED, TOO_BIG, OVERLOADED = 4401, 4404, 4410, 1009, 1013


class _RedactToken(logging.Filter):
    """uvicorn registra la URL completa del handshake: el JWT de ``?token=`` no puede quedar en los logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args and isinstance(record.args, tuple):
            record.args = tuple(re.sub(r"(token=)[^&\s\"]+", r"\1***", a) if isinstance(a, str) else a for a in record.args)
        return True


for _name in ("uvicorn.error", "uvicorn.access"):
    logging.getLogger(_name).addFilter(_RedactToken())


def _seconds(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _engine(app_state: Any) -> ChatEngine:
    database = getattr(app_state, "engine", None)
    if database is None:
        raise HTTPException(status_code=503, detail="El chat no está disponible en este momento.")
    if getattr(app_state, "chat_engine", None) is None:
        app_state.chat_engine = ChatEngine(ChatStore(database), lambda: agent_routes.get_agent())
    return app_state.chat_engine


# ---------------------------------------------------------------- sesiones
@router.post("/sessions", status_code=201)
def create_session(request: Request, user=Depends(get_current_user)):
    store = _engine(request.app.state).store
    user_id = str(user.user_uuid)
    return store.create_session(user_id, store.client_id_of(user_id)).public()


@router.get("/sessions/{session_id}")
def get_session(session_id: str, request: Request, user=Depends(get_current_user)):
    chat = _engine(request.app.state).store.get_session(session_id, str(user.user_uuid))
    if chat is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    return chat.public()


@router.post("/sessions/{session_id}/close")
async def close_session(session_id: str, request: Request, user=Depends(get_current_user)):
    engine = _engine(request.app.state)
    if await asyncio.to_thread(engine.store.get_session, session_id, str(user.user_uuid)) is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    await engine.interrupt(session_id, "", user=None, context=None)  # nada sigue generando en una sesión cerrada
    await asyncio.to_thread(engine.store.set_session_status, session_id, "closed")
    return {"session_id": session_id, "status": "closed"}


# ---------------------------------------------------------------- WebSocket
async def _authenticate(websocket: WebSocket):
    """Identidad por ``?token=`` (se rechaza antes de aceptar el handshake) o por el primer frame."""
    token = websocket.query_params.get("token")
    if token is not None:
        identity = await asyncio.to_thread(authenticate_token, websocket.app.state, token)
        if identity is not None:
            await websocket.accept()
        return identity
    await websocket.accept()
    try:
        frame = await asyncio.wait_for(websocket.receive_text(), _seconds("CHAT_AUTH_TIMEOUT_SECONDS", 5))
        parsed = json.loads(frame) if len(frame) <= MAX_FRAME_BYTES else None
        token = parsed["data"]["token"] if parsed["event"] == "auth" else None
    except (TimeoutError, ValueError, KeyError, TypeError):
        return None
    return await asyncio.to_thread(authenticate_token, websocket.app.state, token if isinstance(token, str) else None)


@router.websocket("/ws")
async def chat_socket(websocket: WebSocket):
    try:
        identity = await _authenticate(websocket)
        if identity is None:
            await websocket.close(UNAUTHORIZED)
            return
        user, expires_at = identity
        engine = _engine(websocket.app.state)
        session_id = websocket.query_params.get("session_id", "")
        chat = await asyncio.to_thread(engine.store.get_session, session_id, str(user.user_uuid)) if session_id else None
        if chat is None or chat.status == "closed":
            await websocket.close(CLOSED if chat is not None else NOT_FOUND)
            return
    except (WebSocketDisconnect, RuntimeError):  # se fue durante el handshake
        return
    subscription, snapshot = await engine.connect(session_id)
    tasks: list[asyncio.Task] = []
    try:
        await websocket.send_json(snapshot)  # primero se restaura el hilo; después llegan tokens
        tasks = [asyncio.create_task(step) for step in (
            _send(websocket, subscription), _receive(websocket, engine, session_id, user), _watch(websocket, user.id, user.session_version, expires_at))]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        code = next((task.result() for task in done if not task.cancelled() and task.exception() is None), None)
        for task in done:
            if task.exception() is not None and not isinstance(task.exception(), (WebSocketDisconnect, RuntimeError)):
                logger.error("chat: conexión terminada por un error", exc_info=task.exception())
                code = 1011
        if code is not None:
            await websocket.close(code)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await engine.disconnect(session_id, subscription)


async def _send(websocket: WebSocket, subscription) -> int:
    while (event := await subscription.get()) is not None:
        await websocket.send_json(event)
    return OVERLOADED


async def _watch(websocket: WebSocket, user_id: int, session_version: int, expires_at: float) -> int:
    """El token se validó al conectar; la conexión no puede sobrevivirle, ni al usuario desactivado, ni a un cambio de contraseña."""
    from user_service import get_user_by_id

    def active() -> bool:
        with websocket.app.state.lock:
            user = get_user_by_id(websocket.app.state.db, user_id)
        return user is not None and user.is_active and user.session_version == session_version

    while (left := expires_at - time.time()) > 0:
        await asyncio.sleep(min(left, _seconds("CHAT_WATCH_SECONDS", 15)))
        if not await asyncio.to_thread(active):
            return UNAUTHORIZED
    return UNAUTHORIZED


async def _receive(websocket: WebSocket, engine: ChatEngine, session_id: str, user) -> int | None:
    from services.agent.session import session_for
    from user_service import get_user_by_id

    label, database = agent_routes._user_label(user), getattr(websocket.app.state, "engine", None)

    def context():  # en el hilo del agente: pedidos, rol y marcas se leen en cada turno, no los de cuando conectó
        with websocket.app.state.lock:
            current = get_user_by_id(websocket.app.state.db, user.id) or user
        return session_for(database, str(user.user_uuid), label, staff=is_staff(current), client_ids=current.client_ids)

    while True:
        frame = await websocket.receive_text()
        if len(frame) > MAX_FRAME_BYTES:
            return TOO_BIG
        try:
            event = events.parse_client_event(frame, session_id)
        except events.ContractError as error:
            await websocket.send_json(events.error(session_id, "invalid_event", str(error)))
            continue
        if event.event == "ping":
            await websocket.send_json(events.pong(session_id))
            continue
        engine.abort(session_id)  # lo primero, cortar: cualquier mensaje nuevo redirige la respuesta en curso
        chat = await asyncio.to_thread(engine.store.get_session, session_id, str(user.user_uuid))
        if chat is None or chat.status == "closed":
            return CLOSED
        try:
            if event.event == "user_message":
                await engine.submit(session_id, event.data.text, user=label, context=context)
            else:
                await engine.interrupt(session_id, event.data.new_input, user=label, context=context)
        except Exception:  # noqa: BLE001 — un fallo al procesar un mensaje no tira la conversación
            logger.exception("chat: no se pudo procesar %s; sesión=%s", event.event, session_id)
            await websocket.send_json(events.error(session_id, "chat_unavailable", "No se pudo procesar el mensaje. Inténtalo de nuevo."))
