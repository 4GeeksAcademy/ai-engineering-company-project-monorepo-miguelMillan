"""Resumen de activaciones de guardrails (por guardrail, tipo de fallo y código).

    uv run python -m services.agent.guardrail_report                       # todo el trace store por defecto
    uv run python -m services.agent.guardrail_report --session <id> --since 2026-11-21T00:00:00
    uv run python -m services.agent.guardrail_report --traces ruta/traces.sqlite --json
"""

from __future__ import annotations

import argparse
import json
import sys

from services.agent.graph import default_data_dir
from services.agent.tracing import TraceStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--traces", help="SQLite de traces (por defecto AGENT_DATA_DIR/traces.sqlite)")
    parser.add_argument("--session", help="Filtra por session_id (conversación)")
    parser.add_argument("--since", help="Filtra desde una marca de tiempo ISO 8601")
    parser.add_argument("--json", action="store_true", help="Salida JSON")
    args = parser.parse_args(argv)
    summary = TraceStore(args.traces or default_data_dir() / "traces.sqlite").guardrail_summary(args.session, args.since)
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0
    print(f"Activaciones de guardrails: {summary['total']}  (sesión={args.session or 'todas'}, desde={args.since or 'siempre'})")
    print("Por tipo de fallo: " + ", ".join(f"{k}={v}" for k, v in sorted(summary["by_failure_type"].items())))
    print(f"{'guardrail':24} {'acción':9} {'tipo':12} {'código':32} {'veces':>5}")
    for row in summary["by_guard"]:
        print(f"{row['guard']:24} {row['action']:9} {str(row['failure_type']):12} {str(row['code']):32} {row['n']:>5}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
