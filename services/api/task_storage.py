"""Durable shared-volume storage for task inputs passed by reference in Celery."""

import os
from pathlib import Path
from uuid import UUID


DEFAULT_TASK_INPUT_DIR = Path("/var/lib/trackflow/task-inputs")


def task_input_path(task_id: str) -> Path:
    """Return a safe path for a task input, validating the externally supplied ID."""
    safe_id = str(UUID(task_id))
    root = Path(os.environ.get("TASK_INPUT_DIR", DEFAULT_TASK_INPUT_DIR))
    return root / f"{safe_id}.csv"


def _fsync_enabled() -> bool:
    return os.environ.get("TASK_INPUT_FSYNC", "0").strip().lower() in {"1", "true", "yes"}


def store_task_input(task_id: str, content: bytes) -> Path:
    """Atomically persist an uploaded task input on the API/worker shared volume.

    La escritura es atómica (temporal + rename): el worker nunca lee un archivo
    a medias. Por defecto NO hace fsync, porque en el camino síncrono del 202
    costaba ~190 ms (p50) y hasta ~600 ms con 10 MB, y por sí solo rompía el
    objetivo de <200 ms. Sin fsync los datos sobreviven a la caída del proceso
    API (quedan en la page cache del host), pero no a un corte de energía o
    caída del kernel antes del writeback; en ese caso el worker no encuentra
    el input y la tarea termina en la DLQ tras sus reintentos. Quien prefiera
    durabilidad frente a latencia puede activar ``TASK_INPUT_FSYNC=1``.
    """
    destination = task_input_path(task_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    try:
        with temporary.open("wb") as output:
            output.write(content)
            if _fsync_enabled():
                output.flush()
                os.fsync(output.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def load_task_input(task_id: str) -> str:
    """Read and decode a previously stored CSV input."""
    return task_input_path(task_id).read_text(encoding="utf-8")


def delete_task_input(task_id: str) -> None:
    """Remove a task input after successful or terminal processing."""
    task_input_path(task_id).unlink(missing_ok=True)


def clean_stale_task_inputs(max_age_seconds: int = 7 * 24 * 60 * 60) -> int:
    """Remove abandoned inputs left by jobs killed before terminal cleanup."""
    import time

    root = Path(os.environ.get("TASK_INPUT_DIR", DEFAULT_TASK_INPUT_DIR))
    if not root.exists():
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    for path in root.glob("*.csv"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        except FileNotFoundError:
            continue
    return removed
