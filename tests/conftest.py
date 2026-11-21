"""Configuración común de tests/.

La memoria del agente usa Redis (AGENT_MEMORY_REDIS_URL). Ningún test debe leer ni
escribir el Redis de desarrollo: por defecto se desactiva, y los tests de memoria
inyectan su propio store (fakeredis).
"""

import pytest


@pytest.fixture(autouse=True)
def _agent_memory_disabled_by_default(monkeypatch):
    monkeypatch.setenv("AGENT_MEMORY_ENABLED", "0")
