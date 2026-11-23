"""Contrato de eventos del WebSocket de chat (BRIEFING «Sistemas en Tiempo Real», Parte 2, §3 y §4).

Cada frame es ``{"event": <nombre>, "data": {…}}`` y todo ``data`` lleva ``session_id``. No comparte nada con los eventos
SSE de tickets RFP. A los eventos del BRIEFING se añaden tres de transporte: ``auth`` (primer frame), ``error`` y
``ping``/``pong`` (el navegador no expone los ping del protocolo y no detectaría una conexión muerta).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

AGENT_ID = "first_line_cx"
MAX_TEXT = 4000
SessionStatus = Literal["active", "interrupted", "closed"]
MessageStatus = Literal["streaming", "completed", "interrupted", "failed"]


# ---------------------------------------------------------------- cliente → servidor
class _Data(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str


class UserMessageData(_Data):
    text: str = Field(min_length=1, max_length=MAX_TEXT)


class InterruptRequestedData(_Data):
    new_input: str = Field(default="", max_length=MAX_TEXT)  # vacío: solo detener


class UserMessage(BaseModel):
    event: Literal["user_message"]
    data: UserMessageData


class InterruptRequested(BaseModel):
    event: Literal["interrupt_requested"]
    data: InterruptRequestedData


class Ping(BaseModel):
    event: Literal["ping"]
    data: _Data


ClientEvent = Annotated[UserMessage | InterruptRequested | Ping, Field(discriminator="event")]
_client_event = TypeAdapter(ClientEvent)


class ContractError(ValueError):
    pass


def parse_client_event(frame: str, session_id: str) -> UserMessage | InterruptRequested | Ping:
    try:
        parsed = _client_event.validate_json(frame)
    except ValidationError as error:
        raise ContractError("; ".join(f"{'.'.join(map(str, e['loc'])) or 'frame'}: {e['msg']}" for e in error.errors()[:3])) from error
    if parsed.data.session_id != session_id:
        raise ContractError("session_id no corresponde a esta conexión")
    return parsed


# ---------------------------------------------------------------- servidor → cliente
Envelope = dict[str, Any]


def _event(name: str, session_id: str, **data: Any) -> Envelope:
    return {"event": name, "data": {"session_id": session_id, **data}}


def token_chunk(session_id: str, token: str, sequence: int) -> Envelope:
    return _event("token_chunk", session_id, token=token, sequence=sequence)


def user_message(session_id: str, text: str) -> Envelope:
    return _event("user_message", session_id, text=text)


def generation_interrupted(session_id: str, message_id: str) -> Envelope:
    return _event("generation_interrupted", session_id, message_id=message_id, status="interrupted")


def generation_completed(session_id: str, message_id: str) -> Envelope:
    return _event("generation_completed", session_id, message_id=message_id)


def session_snapshot(session_id: str, messages: list[dict[str, Any]]) -> Envelope:
    return _event("session_snapshot", session_id, messages=messages)


def error(session_id: str, code: str, message: str, **extra: Any) -> Envelope:
    return _event("error", session_id, code=code, message=message, **extra)


def pong(session_id: str) -> Envelope:
    return _event("pong", session_id)


def channel(session_id: str) -> str:
    return f"chat.{session_id}"
