"""Freno de coste de las llamadas a modelos (generación y embeddings por API) y parada de emergencia.

Todo cliente de modelo se crea en ``mock_data.process.rag._openai_client``: ahí se cobra una llamada. Es la última barrera
contra un bucle (un grafo que se relanza, una cola que reintenta): los límites por usuario viven en el API
(``services/api/rate_limit.py``).

* ``LLM_MAX_CALLS_PER_HOUR`` (600 por defecto) por proceso.
* ``AI_KILL_SWITCH=1`` o la existencia de ``AI_KILL_SWITCH_FILE``: ninguna llamada sale, sin reiniciar el servicio.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from pathlib import Path

logger = logging.getLogger("trackflow.ai.budget")
DEFAULT_MAX_CALLS_PER_HOUR = 600
DEFAULT_MAX_OUTPUT_TOKENS = 800
WINDOW_SECONDS = 3600.0


class ModelUnavailable(RuntimeError):
    """El modelo no se llama por decisión propia (presupuesto agotado o parada de emergencia)."""


class ModelBudgetExceeded(ModelUnavailable):
    pass


class KillSwitchActive(ModelUnavailable):
    pass


def _positive(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        return default
    return value if value > 0 else default


def max_output_tokens() -> int:
    return _positive("LLM_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS)


def kill_switch_active() -> bool:
    path = os.environ.get("AI_KILL_SWITCH_FILE")
    return os.environ.get("AI_KILL_SWITCH", "0") == "1" or bool(path and Path(path).exists())


class ModelBudget:
    def __init__(self, clock=time.monotonic):
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()
        self._clock = clock

    def charge(self) -> None:
        if kill_switch_active():
            logger.warning("llamada al modelo bloqueada: parada de emergencia activa")
            raise KillSwitchActive("La IA está detenida por la parada de emergencia.")
        limit, now = _positive("LLM_MAX_CALLS_PER_HOUR", DEFAULT_MAX_CALLS_PER_HOUR), self._clock()
        with self._lock:
            while self._calls and now - self._calls[0] >= WINDOW_SECONDS:
                self._calls.popleft()
            if len(self._calls) >= limit:
                logger.error("presupuesto de llamadas al modelo agotado: %s en la última hora", limit)
                raise ModelBudgetExceeded(f"Se alcanzó el máximo de {limit} llamadas al modelo por hora.")
            self._calls.append(now)

    def used(self) -> int:
        now = self._clock()
        with self._lock:
            return sum(1 for t in self._calls if now - t < WINDOW_SECONDS)

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()


BUDGET = ModelBudget()
