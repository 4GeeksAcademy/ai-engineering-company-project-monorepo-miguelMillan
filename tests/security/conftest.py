import pytest


@pytest.fixture(autouse=True)
def _no_shared_memory(monkeypatch):
    """Ninguna prueba toca el Redis de desarrollo (las que necesitan memoria inyectan la suya)."""
    monkeypatch.setenv("AGENT_MEMORY_ENABLED", "0")
