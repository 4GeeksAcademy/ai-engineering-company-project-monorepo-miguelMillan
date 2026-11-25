"""Límite de intentos por clave (OWASP A07), en memoria del proceso.

Se indexa por cuenta y no por IP: ``X-Forwarded-For`` lo escribe el cliente y, detrás del proxy, todas las peticiones
comparten IP. El API corre en un solo proceso; con varios habría que mover el contador a Redis.
"""

import threading
import time


class Throttle:
    def __init__(self, limit: int, window_seconds: float, max_keys: int = 10_000):
        self.limit, self.window, self.max_keys = limit, window_seconds, max_keys
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> int:
        """Reserva un intento ANTES de comprobar (dos peticiones en paralelo no comparten cupo).

        Devuelve 0 si se permite o los segundos que faltan para volver a intentarlo."""
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._hits.get(key, ()) if now - t < self.window]
            if len(recent) >= self.limit:
                self._hits[key] = recent
                return max(1, int(self.window - (now - recent[0])) + 1)
            if key not in self._hits and len(self._hits) >= self.max_keys:  # memoria acotada
                self._hits = {k: v for k, v in self._hits.items() if now - v[-1] < self.window}
                while len(self._hits) >= self.max_keys:
                    self._hits.pop(next(iter(self._hits)))
            self._hits[key] = [*recent, now]
            return 0

    def clear(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)
