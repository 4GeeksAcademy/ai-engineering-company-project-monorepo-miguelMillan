"""Trazabilidad de los grafos RFP: para cada ejecución de nodo, qué agente, con qué input, qué output y cuándo.

``traced`` envuelve un nodo de LangGraph y:

* añade la entrada a la clave ``trace`` del estado (reductor ``operator.add``), así el checkpoint la conserva;
* la persiste en ``rfp_trace_events`` (PostgreSQL), así se puede reconstruir el orden de cualquier ejecución aunque el
  grafo siga interrumpido o haya fallado.

Los inputs y outputs se resumen (textos largos recortados, listas acotadas): la traza dice qué pasó sin duplicar el
borrador completo, que ya está en su tabla. Las interrupciones (``GraphInterrupt``) no se registran como ejecución:
el nodo se registra cuando el humano responde y el nodo termina.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from langgraph.errors import GraphInterrupt
from langgraph.types import Command

logger = logging.getLogger("trackflow.rfp_trace")
MAX_TEXT = 240
MAX_ITEMS = 8
SKIP_KEYS = {"trace", "markdown"}


def summarize(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        return value if len(value) <= MAX_TEXT else value[:MAX_TEXT] + f"… ({len(value)} caracteres)"
    if isinstance(value, dict):
        if depth > 2:
            return f"{{{len(value)} claves}}"
        return {str(k): summarize(v, depth + 1) for k, v in list(value.items())[:20] if k not in SKIP_KEYS}
    if isinstance(value, (list, tuple)):
        items = [summarize(v, depth + 1) for v in list(value)[:MAX_ITEMS]]
        return items + ([f"… {len(value) - MAX_ITEMS} más"] if len(value) > MAX_ITEMS else [])
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_TEXT]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def traced(repo, part: str | Callable[[dict], str], node: str, agent: str | Callable[[dict], str], fn: Callable, *,
           input_keys: tuple[str, ...] | None = None, thread_id: Callable[[dict], str | None] | None = None):
    """Envuelve ``fn(state[, config])``. ``agent`` puede depender del estado (p. ej. ``"{department}_worker"``)."""

    def wrapper(state: dict, *args: Any):
        started = _now()
        ticket_id = state.get("ticket_id")
        department = (state.get("inp") or {}).get("department_id") or (state.get("subtask") or {}).get("department_id") \
            or state.get("department_id")
        name = agent(state) if callable(agent) else agent
        source = {k: state.get(k) for k in input_keys} if input_keys else {k: v for k, v in state.items() if k not in SKIP_KEYS}
        entry_base = {"part": part(state) if callable(part) else part, "node": node, "agent": name, "department_id": department,
                      "thread_id": thread_id(state) if thread_id else state.get("thread_id"), "input": summarize(source)}
        try:
            out = fn(state, *args)
        except GraphInterrupt:
            raise
        except Exception as error:
            _persist(repo, ticket_id, {**entry_base, "status": "error", "output": {"error": f"{type(error).__name__}: {error}"[:MAX_TEXT]},
                                       "started_at": started, "finished_at": _now()})
            raise
        payload = out.update if isinstance(out, Command) else out
        entry = {**entry_base, "status": "ok", "output": summarize(payload if isinstance(payload, dict) else {"result": payload}),
                 "started_at": started, "finished_at": _now()}
        _persist(repo, ticket_id, entry)
        state_entry = {**entry, "started_at": entry["started_at"].isoformat(), "finished_at": entry["finished_at"].isoformat()}
        if isinstance(out, Command):
            update = dict(out.update or {})
            update["trace"] = list(update.get("trace") or []) + [state_entry]
            return Command(update=update, goto=out.goto, graph=out.graph, resume=out.resume)
        if isinstance(out, dict):
            return {**out, "trace": list(out.get("trace") or []) + [state_entry]}
        return out

    wrapper.__name__ = getattr(fn, "__name__", node)
    return wrapper


def _persist(repo, ticket_id: str | None, entry: dict[str, Any]) -> None:
    if repo is None or not ticket_id:
        return
    try:
        repo.add_trace(ticket_id=ticket_id, **entry)
    except Exception:  # noqa: BLE001 — la traza nunca tumba el flujo de negocio
        logger.exception("No se pudo persistir la traza; ticket=%s nodo=%s", ticket_id, entry.get("node"))
