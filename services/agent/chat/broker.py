"""Pub/sub en memoria: un canal por sesión (``chat.<session_id>``).

El productor (el hilo que ejecuta el agente) publica en un canal sin saber quién escucha; los consumidores (conexiones
WebSocket) se suscriben. Nadie conectado no detiene la generación, y varias pestañas ven lo mismo.

Válido en un solo proceso, como permite el BRIEFING §5. Con varios procesos del API, este módulo es lo que se sustituye
por un backplane (Redis Pub/Sub): el contrato es ``publish``/``subscribe``.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from typing import Any

MAX_PENDING = 5000


class Subscription:
    """Cola de un consumidor. Se lee en su bucle de eventos; se escribe desde cualquier hilo."""

    def __init__(self, loop: asyncio.AbstractEventLoop, max_pending: int) -> None:
        self._loop, self._max_pending = loop, max_pending
        self._items: deque[Any] = deque()
        self._ready = asyncio.Event()
        self.overflowed = False

    def _push(self, item: Any) -> None:  # con el lock del broker
        if self.overflowed:
            return
        if len(self._items) >= self._max_pending:
            # Consumidor demasiado lento: se desconecta y se rehidrata con un snapshot, en vez de crecer sin límite.
            self.overflowed = True
        else:
            self._items.append(item)
        try:
            self._loop.call_soon_threadsafe(self._ready.set)
        except RuntimeError:  # el bucle del consumidor ya terminó
            self.overflowed = True

    async def get(self) -> Any | None:
        """Siguiente evento en orden de publicación; ``None`` si el consumidor se quedó atrás."""
        while True:
            if self._items:
                return self._items.popleft()
            if self.overflowed:
                return None
            self._ready.clear()
            if not self._items and not self.overflowed:
                await self._ready.wait()


class ChannelBroker:
    def __init__(self, max_pending: int = MAX_PENDING) -> None:
        self._lock = threading.Lock()
        self._channels: dict[str, set[Subscription]] = {}
        self._max_pending = max_pending

    def subscribe(self, name: str) -> Subscription:
        subscription = Subscription(asyncio.get_running_loop(), self._max_pending)
        with self._lock:
            self._channels.setdefault(name, set()).add(subscription)
        return subscription

    def unsubscribe(self, name: str, subscription: Subscription) -> None:
        with self._lock:
            subscribers = self._channels.get(name, set())
            subscribers.discard(subscription)
            if not subscribers:
                self._channels.pop(name, None)

    def publish(self, name: str, item: Any) -> int:
        """Entrega a los suscritos (seguro entre hilos; el lock fija un único orden para todos). Devuelve cuántos había."""
        with self._lock:
            subscribers = list(self._channels.get(name, ()))
            for subscription in subscribers:
                subscription._push(item)
            return len(subscribers)

    def subscribers(self, name: str) -> int:
        with self._lock:
            return len(self._channels.get(name, ()))
