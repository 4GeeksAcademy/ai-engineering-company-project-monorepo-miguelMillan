"""Consulta traces de corridas del agente.

    uv run python -m services.agent.inspect            # últimas corridas
    uv run python -m services.agent.inspect <run_id>   # trace completo (JSON)
"""

import json
import sys

from services.agent.graph import default_data_dir
from services.agent.tracing import TraceStore


def main(argv: list[str]) -> int:
    store = TraceStore(default_data_dir() / "traces.sqlite")
    if len(argv) > 1:
        trace = store.get_trace(argv[1])
        if trace is None:
            print(f"No existe la corrida {argv[1]}", file=sys.stderr)
            return 1
        print(json.dumps(trace, indent=2, ensure_ascii=False))
        return 0
    for run in store.list_runs():
        sources = " → ".join(json.loads(run["sources_used"])) if run.get("sources_used") else "-"
        print(f"{run['started_at']}  {run['run_id']}  {run['status']:9}  fuentes={sources:28} final={run['final_node']}  «{run['question'][:60]}»")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
