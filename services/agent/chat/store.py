"""Persistencia del chat en PostgreSQL (SQLModel): ``ChatSession`` (BRIEFING §3) y el historial de mensajes.

Un mensaje interrumpido se conserva con los tokens ya mostrados (``status = interrupted``); la respuesta siguiente es
otra fila. ``message_id`` de una respuesta es también el ``run_id`` del agente (su trace y su checkpoint).
"""

from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from sqlalchemy import BigInteger, Column, DateTime, Integer, Text, text, update
from sqlmodel import Field, Session, SQLModel, select

from services.agent.chat.events import AGENT_ID


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> str:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


class ChatSession(SQLModel, table=True):
    __tablename__ = "chat_sessions"

    session_id: str = Field(primary_key=True, max_length=64)
    agent_id: str = Field(default=AGENT_ID, max_length=40)
    user_id: str = Field(max_length=64, index=True)
    client_id: Optional[str] = Field(default=None, max_length=64)
    status: str = Field(default="active", max_length=16)
    created_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False))

    def public(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "agent_id": self.agent_id, "user_id": self.user_id, "client_id": self.client_id,
                "status": self.status, "created_at": _utc(self.created_at)}


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_messages"

    id: Optional[int] = Field(default=None, sa_column=Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True))
    message_id: str = Field(max_length=64, unique=True)
    session_id: str = Field(foreign_key="chat_sessions.session_id", index=True, max_length=64)
    role: str = Field(max_length=16)
    text: str = Field(default="", sa_column=Column("text", Text, nullable=False))
    status: str = Field(max_length=16)
    created_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False))

    def public(self) -> dict[str, Any]:
        return {"message_id": self.message_id, "role": self.role, "text": self.text, "status": self.status, "created_at": _utc(self.created_at)}


def new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex}"


class ChatStore:
    def __init__(self, engine: Any) -> None:
        self.engine = engine
        # SQLite (tests) comparte una conexión entre hilos; en PostgreSQL cada sesión toma la suya del pool.
        self._lock = threading.RLock() if engine.url.get_backend_name() == "sqlite" else nullcontext()

    @contextmanager
    def _session(self) -> Iterator[Session]:
        with self._lock, Session(self.engine, expire_on_commit=False) as session:
            yield session

    # ---------------------------------------------------------------- sesiones
    def create_session(self, user_id: str, client_id: str | None = None) -> ChatSession:
        chat = ChatSession(session_id=f"chat_{uuid.uuid4().hex}", user_id=user_id, client_id=client_id)
        with self._session() as session:
            session.add(chat)
            session.commit()
        return chat

    def get_session(self, session_id: str, user_id: str) -> ChatSession | None:
        """La sesión, solo si es de ``user_id`` (una ajena es indistinguible de una inexistente)."""
        with self._session() as session:
            chat = session.get(ChatSession, session_id)
            return chat if chat is not None and chat.user_id == user_id else None

    def set_session_status(self, session_id: str, status: str) -> None:
        with self._session() as session:
            session.execute(update(ChatSession).where(ChatSession.session_id == session_id, ChatSession.status != "closed").values(status=status))
            session.commit()

    # ---------------------------------------------------------------- mensajes
    def start_turn(self, session_id: str, text_: str) -> tuple[ChatMessage, ChatMessage]:
        """Registra, juntos, el mensaje del usuario y la respuesta que empieza."""
        question = ChatMessage(message_id=new_message_id(), session_id=session_id, role="user", text=text_, status="completed")
        answer = ChatMessage(message_id=new_message_id(), session_id=session_id, role="assistant", status="streaming")
        with self._session() as session:
            session.add(question)
            session.flush()
            session.add(answer)
            chat = session.get(ChatSession, session_id)
            if chat is not None and chat.status != "closed":
                chat.status = "active"
                session.add(chat)
            session.commit()
        return question, answer

    def update_message(self, message_id: str, *, text_: str, status: str) -> None:
        """Nunca reescribe un mensaje ya cerrado: lo interrumpido se queda como se vio. La condición va en el ``UPDATE``: el
        guardado periódico del hilo del agente compite con la interrupción y no puede deshacerla."""
        with self._session() as session:
            session.execute(update(ChatMessage).where(ChatMessage.message_id == message_id, ChatMessage.status == "streaming")
                            .values(text=text_, status=status))
            session.commit()

    def messages(self, session_id: str) -> list[ChatMessage]:
        with self._session() as session:
            return list(session.exec(select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id)))

    def client_id_of(self, user_id: str) -> str | None:
        """La marca de quien chatea: la de sus despachos, si todos son de una sola (nunca la que diga el navegador)."""
        try:
            with self._session() as session:
                rows = session.execute(text(
                    "SELECT DISTINCT sku.client_id FROM stock_exit se JOIN sku ON sku.id = se.sku_id "
                    "WHERE se.user_uuid = :user_id AND se.exit_type = 'dispatch' LIMIT 2"), {"user_id": user_id}).all()
        except Exception:  # noqa: BLE001 — sin inventario no hay marca que asociar
            return None
        return str(rows[0][0]) if len(rows) == 1 and rows[0][0] else None
