"""Trace persistente de cada corrida del agente (log estructurado en SQLite).

Cada corrida (``run_id``) guarda, en orden, qué nodos se ejecutaron, qué
decidió cada arista condicional y qué produjo cada nodo. El trace se puede
consultar después de la corrida (``TraceStore.get_trace`` o
``uv run python -m services.agent.inspect <run_id>``), no solo se imprime.

Se usa un log propio en lugar de LangSmith porque el proyecto no tiene acceso
a esa herramienta y así el trace queda junto al checkpoint, sin enviar
preguntas ni chunks de la base de conocimiento a un servicio externo.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id      TEXT PRIMARY KEY,
    question    TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    final_node  TEXT,
    error_type  TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_trace_steps (
    run_id      TEXT NOT NULL REFERENCES agent_runs(run_id),
    seq         INTEGER NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('node', 'route')),
    name        TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('ok', 'error')),
    output      TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class TraceStore:
    """Almacén de traces en SQLite, seguro para varios hilos del servidor."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock, self._connection:
            self._connection.executescript(SCHEMA)
        self._next_seq: dict[str, int] = {}

    def start_run(self, run_id: str, question: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO agent_runs (run_id, question, status, started_at) VALUES (?, ?, 'running', ?)",
                (run_id, question, _now()),
            )
            self._next_seq[run_id] = 0

    def record(self, run_id: str, *, kind: str, name: str, started_at: str, duration_ms: float,
               status: str, output: dict[str, Any]) -> None:
        with self._lock, self._connection:
            seq = self._next_seq.get(run_id, 0)
            self._next_seq[run_id] = seq + 1
            self._connection.execute(
                "INSERT INTO agent_trace_steps (run_id, seq, kind, name, started_at, duration_ms, status, output) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, seq, kind, name, started_at, round(duration_ms, 3), status,
                 json.dumps(output, ensure_ascii=False, default=str)),
            )

    def finish_run(self, run_id: str, *, status: str, final_node: str | None, error_type: str | None = None) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE agent_runs SET status = ?, final_node = ?, error_type = ?, finished_at = ? WHERE run_id = ?",
                (status, final_node, error_type, _now(), run_id),
            )
            self._next_seq.pop(run_id, None)

    def get_trace(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._connection.execute("SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None:
                return None
            steps = self._connection.execute(
                "SELECT * FROM agent_trace_steps WHERE run_id = ? ORDER BY seq", (run_id,)
            ).fetchall()
        return {
            **dict(run),
            "steps": [{**dict(step), "output": json.loads(step["output"])} for step in steps],
        }

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]


def executed_nodes(trace: dict[str, Any]) -> list[str]:
    """Nombres de los nodos ejecutados, en orden (sin las decisiones de aristas)."""
    return [step["name"] for step in trace["steps"] if step["kind"] == "node"]


def node_output(trace: dict[str, Any], node: str) -> dict[str, Any] | None:
    """Salida registrada del primer paso ``node`` del trace."""
    return next((step["output"] for step in trace["steps"] if step["kind"] == "node" and step["name"] == node), None)
