"""Checkpointer del grafo de aprobación (Parte 3) y candado por ticket.

* PostgreSQL (Supabase) cuando ``DATABASE_URL`` es Postgres: ``langgraph-checkpoint-postgres`` (``PostgresSaver``) sobre
  una conexión psycopg 3 en autocommit y sin prepared statements (el pooler de Supabase en modo transacción no los
  admite). Las tablas las crea ``setup()`` la primera vez.
* SQLite en otro caso (desarrollo y tests): ``SqliteSaver`` sobre ``RFP_CHECKPOINT_SQLITE`` (por defecto
  ``mock_data/raw/rfp/checkpoints.sqlite``, ignorado por git). Nunca el checkpointer en memoria fuera de tests.

Cada ejecución del grafo usa ``thread_id = rfp-{ticket_id}:approval-{run}``: dos tickets nunca comparten checkpoint, y
las ramas por departamento quedan separadas dentro del hilo por el namespace de su subgrafo.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_local_locks: dict[str, threading.Lock] = {}
_local_locks_guard = threading.Lock()


def thread_id_for(ticket_id: str, run: int) -> str:
    return f"rfp-{ticket_id}:approval-{run}"


def _postgres_url(url: str) -> str | None:
    for prefix in ("postgresql+psycopg2://", "postgresql+psycopg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix):]
    return None


@contextmanager
def open_checkpointer(database_url: str | None = None) -> Iterator:
    url = database_url if database_url is not None else os.environ.get("DATABASE_URL", "")
    pg_url = _postgres_url(url)
    if pg_url:
        import psycopg
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.rows import dict_row

        with psycopg.connect(pg_url, autocommit=True, prepare_threshold=None, row_factory=dict_row) as conn:
            saver = PostgresSaver(conn)
            saver.setup()
            yield saver
        return
    from langgraph.checkpoint.sqlite import SqliteSaver

    path = Path(os.environ.get("RFP_CHECKPOINT_SQLITE") or REPOSITORY_ROOT / "mock_data" / "raw" / "rfp" / "checkpoints.sqlite")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    try:
        yield SqliteSaver(conn)
    finally:
        conn.close()


@contextmanager
def ticket_lock(ticket_id: str, database_url: str | None = None) -> Iterator[None]:
    """Serializa start/resume del MISMO ticket (dos workers no avanzan a la vez el mismo hilo). Tickets distintos no
    se bloquean entre sí. En Postgres: advisory lock de sesión; en local: un lock por ticket en el proceso."""
    url = database_url if database_url is not None else os.environ.get("DATABASE_URL", "")
    pg_url = _postgres_url(url)
    if pg_url:
        import psycopg

        key = int.from_bytes(hashlib.sha256(f"rfp-{ticket_id}".encode()).digest()[:8], "big", signed=True)
        # Lock de TRANSACCIÓN: con el pooler en modo transacción, la transacción abierta fija la conexión del servidor
        # mientras dura (un lock de sesión podría quedar en otra conexión). Se libera al cerrar la transacción.
        with psycopg.connect(pg_url, autocommit=False, prepare_threshold=None) as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (key,))
            try:
                yield
            finally:
                conn.rollback()
        return
    with _local_locks_guard:
        lock = _local_locks.setdefault(str(ticket_id), threading.Lock())
    with lock:
        yield
