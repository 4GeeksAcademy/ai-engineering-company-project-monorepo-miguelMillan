"""Motor del chat: turnos, streaming, interrupción y rehidratación, desacoplado de las conexiones.

* El agente corre en un hilo (``ThreadPoolExecutor``) y **publica** en el canal de la sesión; las conexiones solo se
  suscriben. Una desconexión no aborta nada: al volver, el snapshot trae lo generado mientras tanto.
* ``Generation`` es la valla de la interrupción: publicar un token y abortar toman el mismo lock, así que después de
  ``generation_interrupted`` no puede salir ningún ``token_chunk`` de esa generación.
* Los turnos de una sesión se serializan con un lock por sesión: el turno nuevo empieza cuando el anterior está cerrado.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from services.agent.chat import events
from services.agent.chat.broker import ChannelBroker, Subscription
from services.agent.chat.store import ChatStore
from services.agent.streaming import GenerationAborted, GenerationControl

logger = logging.getLogger("trackflow.chat")
FLUSH_SECONDS = 2.0  # cada cuánto se guarda el texto parcial, por si el proceso muere


class Generation:
    """Una respuesta en curso. ``running → interrupted | completed | failed``; la primera transición gana."""

    def __init__(self, session_id: str, message_id: str, broker: ChannelBroker) -> None:
        self.session_id, self.message_id = session_id, message_id
        self.control = GenerationControl()
        self.finished = asyncio.Event()
        self.lock = threading.Lock()
        self.state = "running"
        self.text = ""
        self.sequence = 0
        self._broker, self._channel = broker, events.channel(session_id)

    def emit(self, token: str) -> None:
        with self.lock:
            if self.state != "running":
                raise GenerationAborted()
            self.sequence += 1
            self.text += token
            self._broker.publish(self._channel, events.token_chunk(self.session_id, token, self.sequence))

    def abort(self) -> bool:
        """Corta la generación y lo anuncia, en un solo paso. ``False`` si ya había terminado."""
        with self.lock:
            if self.state != "running":
                return False
            self.state = "interrupted"
            self._broker.publish(self._channel, events.generation_interrupted(self.session_id, self.message_id))
        self.control.cancel()
        return True

    def close(self, state: str) -> bool:
        with self.lock:
            if self.state != "running":
                return False
            self.state = state
            return True

    def live(self) -> dict[str, Any]:
        status = "streaming" if self.state == "running" else self.state
        return {"text": self.text, "status": status} | ({"sequence": self.sequence} if status == "streaming" else {})


class _Runtime:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.generation: Generation | None = None
        self.users = 0


class ChatEngine:
    def __init__(self, store: ChatStore, agent: Callable[[], Any], *, broker: ChannelBroker | None = None, workers: int = 16) -> None:
        self.store, self.agent = store, agent
        self.broker = broker or ChannelBroker()
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="chat-generation")
        self._runtimes: dict[str, _Runtime] = {}

    def shutdown(self) -> None:
        """Al apagar el API: corta lo que se esté generando y lo deja guardado como interrumpido."""
        for runtime in list(self._runtimes.values()):
            generation = runtime.generation
            if generation is not None and generation.abort():
                try:
                    self.store.update_message(generation.message_id, text_=generation.text, status="interrupted")
                    self.store.set_session_status(generation.session_id, "interrupted")
                except Exception:  # noqa: BLE001 — si no se pudo, el snapshot lo reconcilia al volver
                    logger.exception("no se pudo guardar una respuesta al apagar")
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _runtime(self, session_id: str) -> _Runtime:
        return self._runtimes.setdefault(session_id, _Runtime())

    # ---------------------------------------------------------------- conexión y rehidratación
    async def connect(self, session_id: str) -> tuple[Subscription, events.Envelope]:
        """Suscribe y devuelve el estado del que parte la suscripción: ni se repite ni se pierde ningún token."""
        runtime = self._runtime(session_id)
        runtime.users += 1
        try:
            async with runtime.lock:
                rows = await asyncio.to_thread(self.store.messages, session_id)
                return await self._snapshot(session_id, runtime, rows, subscribe=True)
        except BaseException:
            self._release(session_id, runtime)
            raise

    async def disconnect(self, session_id: str, subscription: Subscription) -> None:
        self.broker.unsubscribe(events.channel(session_id), subscription)
        self._release(session_id, self._runtime(session_id))

    def _release(self, session_id: str, runtime: _Runtime) -> None:
        runtime.users -= 1
        generation = runtime.generation
        if runtime.users <= 0 and (generation is None or generation.finished.is_set()) and not runtime.lock.locked():
            self._runtimes.pop(session_id, None)

    async def _snapshot(self, session_id: str, runtime: _Runtime, rows: list[Any], subscribe: bool) -> tuple[Any, events.Envelope]:
        messages = [row.public() for row in rows]
        generation = runtime.generation
        orphans = [m for m in messages if m["status"] == "streaming" and (generation is None or m["message_id"] != generation.message_id)]
        for message in orphans:  # el proceso que la generaba murió: se conserva lo guardado como interrumpido
            message["status"] = "interrupted"
            await asyncio.to_thread(self.store.update_message, message["message_id"], text_=message["text"], status="interrupted")
        # Con el lock de la generación no sale ningún token entre la foto y la suscripción.
        with generation.lock if generation is not None else threading.Lock():
            for message in messages:
                if generation is not None and message["message_id"] == generation.message_id:
                    message.update(generation.live())
            subscription = self.broker.subscribe(events.channel(session_id)) if subscribe else None
        return subscription, events.session_snapshot(session_id, messages)

    # ---------------------------------------------------------------- turnos
    async def submit(self, session_id: str, text: str, *, user: str | None, context: Any) -> None:
        """Mensaje del usuario. Si el agente está respondiendo, lo redirige (igual que ``interrupt``)."""
        await self.interrupt(session_id, text, user=user, context=context)

    def abort(self, session_id: str) -> bool:
        """Corta ya la respuesta en curso, sin esperar a la base de datos ni al lock de la sesión."""
        runtime = self._runtimes.get(session_id)
        return runtime is not None and runtime.generation is not None and runtime.generation.abort()

    async def interrupt(self, session_id: str, new_input: str, *, user: str | None, context: Any) -> None:
        runtime = self._runtime(session_id)
        self.abort(session_id)
        async with runtime.lock:
            await self._settle(session_id, runtime.generation)
            text = new_input.strip()
            if text:
                await self._start(session_id, runtime, text, user, context)

    async def _settle(self, session_id: str, generation: Generation | None) -> None:
        """Deja cerrada la generación anterior antes de empezar otra."""
        if generation is None:
            return
        generation.abort()
        if generation.state == "interrupted" and not generation.finished.is_set():
            await asyncio.to_thread(self.store.update_message, generation.message_id, text_=generation.text, status="interrupted")
            await asyncio.to_thread(self.store.set_session_status, session_id, "interrupted")
            await asyncio.to_thread(self._discard_unseen, session_id, generation.message_id)
            generation.finished.set()
        else:
            await generation.finished.wait()

    def _discard_unseen(self, session_id: str, message_id: str) -> None:
        try:
            self.agent().discard_unseen_proposal(session_id, message_id)
        except Exception:  # noqa: BLE001
            logger.warning("no se pudo revisar la propuesta de memoria de una respuesta interrumpida", exc_info=True)

    async def _start(self, session_id: str, runtime: _Runtime, text: str, user: str | None, context: Any) -> None:
        try:
            _question, answer = await asyncio.to_thread(self.store.start_turn, session_id, text)
        except Exception:  # noqa: BLE001 — sin persistencia no hay turno: no se responde algo que no se podrá rehidratar
            logger.exception("no se pudo registrar el turno; sesión=%s", session_id)
            self.broker.publish(events.channel(session_id), events.error(session_id, "chat_unavailable", "No se pudo registrar tu mensaje. Inténtalo de nuevo."))
            return
        generation = Generation(session_id, answer.message_id, self.broker)
        runtime.generation = generation
        self.broker.publish(events.channel(session_id), events.user_message(session_id, text))
        loop = asyncio.get_running_loop()
        self._executor.submit(self._produce, loop, generation, text, user, context)

    # ---------------------------------------------------------------- productor (hilo del agente)
    def _produce(self, loop: asyncio.AbstractEventLoop, generation: Generation, text: str, user: str | None, context: Any) -> None:
        session_id, message_id = generation.session_id, generation.message_id
        channel, flushed = events.channel(session_id), time.monotonic()

        def on_token(token: str) -> None:
            nonlocal flushed
            generation.emit(token)
            if time.monotonic() - flushed >= FLUSH_SECONDS:
                flushed = time.monotonic()
                self.store.update_message(message_id, text_=generation.text, status="streaming")

        try:
            generation.control.raise_if_cancelled()
            resolved = context() if callable(context) else context
            result = self.agent().stream(text, on_token=on_token, control=generation.control, run_id=message_id,
                                         session_id=session_id, user=user, session=resolved)
            if not generation.close("completed"):
                raise GenerationAborted()
            if result.replaced:
                generation.text = result.answer
            self.store.update_message(message_id, text_=generation.text, status="completed")
            self.broker.publish(channel, events.generation_completed(session_id, message_id))
            if result.replaced:  # la guarda sustituyó la respuesta: el historial es la versión válida
                self.broker.publish(channel, events.session_snapshot(session_id, [m.public() for m in self.store.messages(session_id)]))
        except GenerationAborted:
            self._discard_unseen(session_id, message_id)
            return  # la cierra quien la interrumpió
        except Exception:  # noqa: BLE001 — el chat informa; el detalle queda en el log y en el trace del agente
            logger.exception("la generación falló; sesión=%s mensaje=%s", session_id, message_id)
            generation.close("failed")
            if generation.state == "interrupted":
                return
            try:
                self.store.update_message(message_id, text_=generation.text, status="failed")
            except Exception:  # noqa: BLE001
                logger.exception("no se pudo guardar el fallo de la generación")
            self.broker.publish(channel, events.error(session_id, "agent_unavailable", "El agente no pudo completar la respuesta. Inténtalo de nuevo.", message_id=message_id))
        finally:
            try:
                loop.call_soon_threadsafe(self._produced, generation)
            except RuntimeError:  # el servidor se está apagando
                pass

    def _produced(self, generation: Generation) -> None:
        if generation.state != "interrupted":
            generation.finished.set()
        runtime = self._runtimes.get(generation.session_id)
        if runtime is not None and runtime.users <= 0 and generation.finished.is_set() and runtime.generation is generation \
                and not runtime.lock.locked():
            self._runtimes.pop(generation.session_id, None)
