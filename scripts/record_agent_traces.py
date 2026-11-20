"""Graba los traces de los evals del agente ejecutando el grafo REAL.

Usa Qdrant (colección ``trackflow_knowledge`` indexada), los embeddings
configurados, el LLM de generación de ``LLM_API_URL``/``GENERATION_MODEL`` y,
para las tools, el API real de TrackFlow en ``AGENT_API_BASE_URL`` con el JWT
de ``AGENT_API_TOKEN`` (gestor de incidencias e inventario). Un caso puede
fijar ``record_with_env`` (p. ej. un puerto cerrado para grabar el fallback
con el servicio caído de verdad); nunca se simulan datos.
Cada caso de ``mock_data/eval/agent-eval-cases.json`` se ejecuta una vez y su
trace completo se exporta a ``mock_data/eval/agent-traces/<id>.json``; los
evals (``tests/pipelines/test_agent_evals.py``) se verifican después contra
esos archivos, sin volver a ejecutar el grafo.

    uv run python scripts/record_agent_traces.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mock_data.process.rag import load_embedding_settings, load_generation_settings  # noqa: E402
from services.agent.graph import AgentRunError, KnowledgeAgent  # noqa: E402
from services.agent.tools import api_base_url  # noqa: E402

CASES_PATH = ROOT / "mock_data" / "eval" / "agent-eval-cases.json"
TRACES_DIR = ROOT / "mock_data" / "eval" / "agent-traces"


def main() -> int:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    embedding, generation = load_embedding_settings(), load_generation_settings()
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0
    with tempfile.TemporaryDirectory() as data_dir:
        agent = KnowledgeAgent(data_dir=Path(data_dir))
        for case in cases:
            run_id = f"eval-{case['id']}"
            overrides = case.get("record_with_env", {})
            previous = {key: os.environ.get(key) for key in overrides}
            os.environ.update(overrides)
            service_url = api_base_url()
            try:
                result = agent.run(case["question"], run_id=run_id)
                summary = f"{result.final_node:16} {result.answer[:90]!r}"
            except AgentRunError as error:
                failures += 1
                summary = f"FALLÓ en {error.node}: {type(error.cause).__name__}"
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            trace = agent.traces.get_trace(run_id)
            trace["recorded_with"] = {
                "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "embedding_model": f"{embedding.provider}/{embedding.model}",
                "generation_model": generation.model,
                "api_base_url": service_url,
                "api_token_configured": bool(os.environ.get("AGENT_API_TOKEN")),
                "checkpoints": [c["next"] for c in agent.checkpoints(run_id)],
            }
            (TRACES_DIR / f"{case['id']}.json").write_text(json.dumps(trace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"{case['id']:34} {summary}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
