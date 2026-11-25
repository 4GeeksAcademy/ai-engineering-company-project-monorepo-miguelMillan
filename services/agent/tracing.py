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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id      TEXT PRIMARY KEY,
    question    TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    final_node  TEXT,
    error_type  TEXT,
    sources_used TEXT,
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
CREATE TABLE IF NOT EXISTS guardrail_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    run_id       TEXT,
    session_id   TEXT,
    guard        TEXT NOT NULL,
    action       TEXT NOT NULL CHECK (action IN ('block', 'redirect', 'sanitize', 'replace')),
    failure_type TEXT CHECK (failure_type IN ('estructural', 'contenido', 'seguridad')),
    code         TEXT,
    detail       TEXT
);
CREATE INDEX IF NOT EXISTS ix_guardrail_events_session ON guardrail_events (session_id);
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
            columns = {row[1] for row in self._connection.execute("PRAGMA table_info(agent_runs)")}
            for column in ("sources_used", "actor", "session_id"):  # traces de versiones anteriores
                if column not in columns:
                    self._connection.execute(f"ALTER TABLE agent_runs ADD COLUMN {column} TEXT")
        self._next_seq: dict[str, int] = {}

    # ------------------------------------------------------------------ guardrails
    def record_guardrail(self, *, run_id: str | None, session_id: str | None, guard: str, action: str,
                         failure_type: str | None, code: str | None, detail: str = "") -> None:
        """Una fila por cada vez que un guardrail bloquea, redirige, sanea o sustituye algo."""
        from services.agent.guardrails import log_event

        event = {"ts": _now(), "run_id": run_id, "session_id": session_id, "guard": guard, "action": action,
                 "failure_type": failure_type, "code": code, "detail": (detail or "")[:500]}
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO guardrail_events (ts, run_id, session_id, guard, action, failure_type, code, detail) "
                "VALUES (:ts, :run_id, :session_id, :guard, :action, :failure_type, :code, :detail)", event)
        log_event(event)

    def guardrail_summary(self, session_id: str | None = None, since: str | None = None) -> dict[str, Any]:
        """Cuántas veces se activó cada guardrail (por guardrail, tipo de fallo y código)."""
        where, params = [], {}
        if session_id:
            where.append("session_id = :session_id")
            params["session_id"] = session_id
        if since:
            where.append("ts >= :since")
            params["since"] = since
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._lock:
            rows = self._connection.execute(
                f"SELECT guard, action, failure_type, code, COUNT(*) AS n FROM guardrail_events {clause} "
                "GROUP BY guard, action, failure_type, code ORDER BY n DESC, guard", params).fetchall()
            by_type = self._connection.execute(
                f"SELECT COALESCE(failure_type, 'n/a') AS t, COUNT(*) AS n FROM guardrail_events {clause} GROUP BY t", params).fetchall()
        return {"session_id": session_id, "since": since, "total": sum(r["n"] for r in rows),
                "by_failure_type": {r["t"]: r["n"] for r in by_type},
                "by_guard": [dict(r) for r in rows]}

    def start_run(self, run_id: str, question: str, *, actor: str | None = None, session_id: str | None = None) -> None:
        """``actor``: rol y referencia seudónima de quien pregunta (nunca su email)."""
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO agent_runs (run_id, question, status, started_at, actor, session_id) VALUES (?, ?, 'running', ?, ?, ?)",
                (run_id, question, _now(), actor, session_id),
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

    def finish_run(self, run_id: str, *, status: str, final_node: str | None, error_type: str | None = None,
                   sources: list[str] | None = None) -> None:
        """Cierra la corrida guardando qué fuentes usó y en qué orden (RAG, tools o ambas)."""
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE agent_runs SET status = ?, final_node = ?, error_type = ?, sources_used = ?, finished_at = ? "
                "WHERE run_id = ?",
                (status, final_node, error_type, json.dumps(sources or []), _now(), run_id),
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
        run = dict(run)
        run["sources_used"] = json.loads(run["sources_used"]) if run.get("sources_used") else []
        return {
            **run,
            "steps": [{**dict(step), "output": json.loads(step["output"])} for step in steps],
        }

    def purge(self, older_than_days: int, *, dry_run: bool = False) -> list[str]:
        """Borra corridas, pasos y eventos con más de N días (minimización y derecho de supresión: RGPD, CCPA/CPRA)."""
        limit = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat(timespec="milliseconds")
        with self._lock, self._connection:
            expired = [r["run_id"] for r in self._connection.execute("SELECT run_id FROM agent_runs WHERE started_at < ?", (limit,))]
            if dry_run:
                return expired
            self._connection.execute(
                "DELETE FROM agent_trace_steps WHERE run_id IN (SELECT run_id FROM agent_runs WHERE started_at < ?)", (limit,))
            self._connection.execute("DELETE FROM guardrail_events WHERE ts < ?", (limit,))
            self._connection.execute("DELETE FROM agent_runs WHERE started_at < ?", (limit,))
        return expired

    def guardrail_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._connection.execute(
                "SELECT ts, guard, action, failure_type, code, detail FROM guardrail_events WHERE run_id = ? ORDER BY id", (run_id,))]

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
