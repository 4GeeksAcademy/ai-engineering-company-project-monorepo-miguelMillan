"""Streaming y aborto de la respuesta del agente (canal de chat en tiempo real).

No cambia la lógica del agente: el mismo grafo, los mismos nodos y las mismas guardas. Solo permite que la llamada al
modelo entregue tokens mientras los genera (``stream_mode="custom"`` de LangGraph) y que se pueda cortar a mitad.

* ``complete_text``: la llamada de generación. Sin control activo (``POST /agent/query``) es la de siempre. Con él, pide
  la respuesta en streaming y emite cada fragmento con ``get_stream_writer``; al abortar cancela la tarea y cierra la
  conexión HTTP con el modelo, aunque aún no haya llegado el primer token.
* ``StreamGuard``: la guarda de salida valida la respuesta COMPLETA antes de mostrarla. En streaming no se puede esperar
  al final, así que cada fragmento se valida con las mismas reglas antes de salir y se retiene el tramo que todavía
  podría completar un patrón prohibido (una cifra cuyo carrier aún no ha aparecido, media marca interna…).

El aborto NO usa ``interrupt()`` de LangGraph: eso pausa el grafo para un humano; aquí se detiene la generación.
"""

from __future__ import annotations

import asyncio
import re
import threading
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

from services.agent.guardrails import (
    INTERNAL_MARKERS,
    MAX_OUTPUT_CHARS,
    SessionContext,
    check_output,
    normalize,
)


class GenerationAborted(Exception):
    """La persona interrumpió la respuesta: no se produce ni se publica nada más de esta generación."""


class GenerationControl:
    """Señal de aborto de UNA generación. ``cancel`` se llama desde otro hilo."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[], None]] = []

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise GenerationAborted()

    def cancel(self) -> None:
        with self._lock:
            if self._cancelled.is_set():
                return
            self._cancelled.set()
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            callback()

    @contextmanager
    def on_cancel(self, callback: Callable[[], None]) -> Iterator[None]:
        with self._lock:
            if not self._cancelled.is_set():
                self._callbacks.append(callback)
        if self.cancelled:
            callback()
        try:
            yield
        finally:
            with self._lock:
                if callback in self._callbacks:
                    self._callbacks.remove(callback)


_control: ContextVar[GenerationControl | None] = ContextVar("agent_generation_control", default=None)


@contextmanager
def generation_control(control: GenerationControl) -> Iterator[None]:
    token = _control.set(control)
    try:
        yield
    finally:
        _control.reset(token)


def abort_if_cancelled() -> None:
    """Punto de control para los nodos con efectos: en una respuesta interrumpida no deben ejecutarse."""
    control = _control.get()
    if control is not None:
        control.raise_if_cancelled()


def emit_token(text: str) -> None:
    """Entrega un fragmento al consumidor del grafo (``stream_mode="custom"``)."""
    from langgraph.config import get_stream_writer

    if text:
        get_stream_writer()({"token": text})


def complete_text(client: Any, **request: Any) -> str:
    """Texto completo de la generación; si hay una generación en streaming activa, además emite sus tokens."""
    control = _control.get()
    if control is None:
        completion = client.chat.completions.create(**request)
        return completion.choices[0].message.content or ""
    control.raise_if_cancelled()
    return asyncio.run(_stream_completion(client, control, request))


async def _stream_completion(client: Any, control: GenerationControl, request: dict[str, Any]) -> str:
    from openai import AsyncOpenAI

    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    parts: list[str] = []

    def cancel() -> None:
        loop.call_soon_threadsafe(task.cancel)

    async with AsyncOpenAI(base_url=client.base_url, api_key=client.api_key, timeout=client.timeout, max_retries=0) as model:
        try:
            with control.on_cancel(cancel):
                async with await model.chat.completions.create(stream=True, **request) as stream:
                    async for chunk in stream:
                        control.raise_if_cancelled()
                        text = (chunk.choices[0].delta.content or "") if chunk.choices else ""
                        if text:
                            parts.append(text)
                            emit_token(text)
        except asyncio.CancelledError as error:
            raise GenerationAborted() from error
    return "".join(parts)


# ============================================================================
# Guarda de salida incremental
# ============================================================================
UNIT = r"%|€|\$|usd|eur|euros|dolares|centimos"
NUMBER_WITH_UNIT = re.compile(rf"\d[\d.,]*\s?(?:{UNIT})")
# Una cifra al final del texto, o seguida de media unidad («12 eu»): puede acabar siendo un importe.
NUMBER_FORMING = re.compile(r"\d[\d.,]*\s?(?P<unit>[a-z%€$]*)$")
ACCESS_CODE = re.compile(r"\b(?:codigo|clave)\s+de\s+acceso")
ORDER_FORMING = re.compile(
    r"(?:\b(?:pedidos?|orden|order|trackings?|envios?|paquete|seguimiento|shipment)\s*(?:n(?:umero|o|º|°)?\.?\s*)?(?:de\s+)?(?:#\s*)?"
    r"|#\s*)[a-z0-9-]*$")
CLAUSE_END = re.compile(r"[.!?](?=\s)|\n")
WINDOW = 140  # más largo que cualquier patrón de SENSITIVE_OUTPUT
PROMPT_LINE_HEAD = 20


def _fold(text: str) -> str:
    """Minúsculas sin acentos conservando la posición de cada carácter."""
    return "".join(unicodedata.normalize("NFD", c)[0] for c in text.lower()[: len(text)]) if text else ""


class StreamGuard:
    def __init__(self, session: SessionContext | None = None, system_prompt: str = "") -> None:
        self._session, self._system_prompt = session, system_prompt
        self._prompt_lines = [line[:60] for line in (normalize(raw).strip(" -•*") for raw in system_prompt.splitlines()) if len(line) >= 40]
        self._text = ""
        self._released = 0
        self.blocked = False

    @property
    def released(self) -> str:
        return self._text[: self._released]

    def push(self, chunk: str) -> str:
        """Añade un fragmento y devuelve lo que ya se puede mostrar (puede ser vacío)."""
        self._text += chunk
        if self.blocked:
            return ""
        # Un pedido a medio escribir («TRK-00» de «TRK-0042») no se juzga todavía: está retenido, no se muestra.
        forming = ORDER_FORMING.search(_fold(self._text))
        check = check_output(self._text[: forming.start()] if forming else self._text, self._session, self._system_prompt)
        if check.failure_type == "seguridad" or len(self._text) > MAX_OUTPUT_CHARS:
            self.blocked = True  # nada más sale; la respuesta final la decide output_guard
            return ""
        limit = max(self._released, self._safe_limit())
        out, self._released = self._text[self._released: limit], limit
        return out

    def _safe_limit(self) -> int:
        text = _fold(self._text)
        limit = len(self._text.rstrip())  # el espacio final se recorta al terminar la generación

        def hold(position: int) -> None:
            nonlocal limit
            limit = min(limit, position)

        for pattern in (NUMBER_WITH_UNIT, ACCESS_CODE):
            for match in pattern.finditer(text, max(0, len(text) - 2 * WINDOW)):
                if len(text) - match.start() < WINDOW and not CLAUSE_END.search(text, match.end()):
                    hold(match.start())
        forming = NUMBER_FORMING.search(text)
        if forming and any(unit.replace("\\", "").startswith(forming["unit"]) for unit in UNIT.split("|")):
            hold(forming.start())
        order = ORDER_FORMING.search(text)
        if order:
            hold(order.start())
        for marker in INTERNAL_MARKERS:
            folded = marker.lower()
            for size in range(min(len(folded), len(text)), 0, -1):
                if text.endswith(folded[:size]):
                    hold(len(text) - size)
                    break
        for line in self._prompt_lines:
            position = text.find(line)
            if position < 0:
                position = next((len(text) - size for size in range(min(len(line), len(text)), PROMPT_LINE_HEAD - 1, -1)
                                 if text.endswith(line[:size])), -1)
            if position >= 0:
                hold(position + PROMPT_LINE_HEAD)  # reproduce una línea del system prompt: se espera al final
        return limit
