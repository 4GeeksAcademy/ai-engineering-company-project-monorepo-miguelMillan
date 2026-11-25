"""Borra trazas y checkpoints del agente con más de N días (minimización RGPD; supresión CCPA/CPRA).

    uv run python -m services.agent.purge --days 90 [--confirm]   # AGENT_DATA_DIR, o .agent-runs
"""

from __future__ import annotations

import argparse

from services.agent.graph import default_data_dir, sqlite_checkpointer
from services.agent.tracing import TraceStore


def count(data_dir, days: int) -> int:
    return len(TraceStore(data_dir / "traces.sqlite").purge(days, dry_run=True))


def purge(data_dir, days: int) -> int:
    if days < 1:
        raise ValueError("el plazo debe ser de al menos un día")
    expired = TraceStore(data_dir / "traces.sqlite").purge(days)
    checkpointer = sqlite_checkpointer(data_dir / "checkpoints.sqlite")
    for run_id in expired:
        checkpointer.delete_thread(run_id)
    return len(expired)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, required=True)
    parser.add_argument("--confirm", action="store_true", help="sin esto solo dice cuántas corridas borraría")
    args = parser.parse_args(argv)
    if not args.confirm:
        print(f"Se borrarían {count(default_data_dir(), args.days)} corridas, sin vuelta atrás. Repite con --confirm.")
        return 1
    print(f"{purge(default_data_dir(), args.days)} corridas borradas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
