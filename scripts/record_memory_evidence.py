"""Graba la evidencia de memoria del agente con la pila REAL (LLM, Qdrant, servidor MCP y Redis).

1. Evals de auto-evaluación (``mock_data/eval/agent-memory-cases.json``): cada caso en su propia conversación.
   El trace completo va a ``mock_data/eval/agent-memory-traces/<id>.json``.
2. Ciclos completos de confirmación (``docs/pr-evidence/agent-memory/cycles.json``): un ciclo aprobado que se
   refleja en una conversación futura, uno rechazado que deja la memoria sin cambios y uno con cambio de tema.
   Cada turno guarda la respuesta, la propuesta, la resolución y una foto de la memoria antes y después.

Usa una base de Redis dedicada (``MEMORY_EVIDENCE_REDIS_URL``, por defecto ``redis://127.0.0.1:6379/4``) que se
vacía al empezar: nunca toca la memoria de desarrollo ni la colección RAG.

    uv run python scripts/record_memory_evidence.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import redis  # noqa: E402

from mock_data.process.rag import load_generation_settings  # noqa: E402
from services.agent.graph import AgentRunError, KnowledgeAgent  # noqa: E402
from services.agent.memory import RedisMemoryStore  # noqa: E402

CASES_PATH = ROOT / "mock_data" / "eval" / "agent-memory-cases.json"
TRACES_DIR = ROOT / "mock_data" / "eval" / "agent-memory-traces"
EVIDENCE_DIR = ROOT / "docs" / "pr-evidence" / "agent-memory"

CYCLES = [
    {"id": "ciclo-aprobado", "description": "Propuesta aprobada que se refleja en una conversación futura",
     "turns": [("conv-aprobado", "En realidad SEUR ya no cubre esa zona rural de Zaragoza, hay que usar el carrier local desde el mes pasado."),
               ("conv-aprobado", "Sí, guárdalo."),
               ("conv-dia-siguiente", "¿Qué carrier usamos para un envío a la zona rural de Zaragoza?")]},
    {"id": "ciclo-rechazado", "description": "Propuesta rechazada: la memoria queda sin cambios",
     "turns": [("conv-rechazado", "El cliente de cosméticos siempre quiere su reporte mensual con el desglose de devoluciones primero, antes que el volumen de envíos."),
               ("conv-rechazado", "No, no lo guardes. ¿Cuál es la ventana de devolución estándar?"),
               ("conv-rechazado-2", "Prepara el reporte mensual del cliente de cosméticos: ¿qué métricas destaco?")]},
    {"id": "ciclo-cambio-de-tema", "description": "El usuario cambia de tema: la propuesta se descarta por defecto",
     "turns": [("conv-tema", "Esos retrasos reportados en incidencias de Los Ángeles esta semana son por la huelga portuaria, no por un problema nuestro — ya van tres tickets sobre lo mismo."),
               ("conv-tema", "¿Cuál es la ventana de devolución estándar?")]},
]


def snapshot(store: RedisMemoryStore) -> list[dict]:
    return [entry.model_dump() for entry in store.entries()]


def run_turn(agent: KnowledgeAgent, store: RedisMemoryStore, session: str, message: str, run_id: str) -> dict:
    before = snapshot(store)
    started = time.perf_counter()
    try:
        result = agent.run(message, run_id=run_id, session_id=session, user="valentina.cruz@trackflow.example")
        outcome = {"answer": result.answer, "final_node": result.final_node, "sources": result.sources,
                   "memory_proposal": result.memory_proposal, "memory_resolution": result.memory_resolution}
    except AgentRunError as error:
        outcome = {"error": f"{error.node}: {type(error.cause).__name__}"}
    trace = agent.traces.get_trace(run_id)
    nodes = [s["name"] for s in trace["steps"] if s["kind"] == "node"]
    propose = next((s["output"] for s in trace["steps"] if s["name"] == "propose_memory"), {}) or {}
    recall = next((s["output"] for s in trace["steps"] if s["name"] == "recall_memory"), {}) or {}
    return {"session_id": session, "message": message, "run_id": run_id, "seconds": round(time.perf_counter() - started, 1),
            "nodes": nodes, "memory_gate": recall.get("memory_gate"), "recalled": [m["key"] for m in recall.get("memories") or []],
            "model_proposal_raw": propose.get("memory_proposal_raw"), "memory_rejection": propose.get("memory_rejection"),
            **outcome, "memory_before": before, "memory_after": snapshot(store)}, trace


def main() -> int:
    url = os.environ.get("MEMORY_EVIDENCE_REDIS_URL", "redis://127.0.0.1:6379/4")
    client = redis.Redis.from_url(url, decode_responses=True)
    client.flushdb()
    store = RedisMemoryStore(client)
    model = load_generation_settings().model
    only = set(sys.argv[1:])
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as data_dir:
        agent = KnowledgeAgent(data_dir=Path(data_dir), memory_store=store)
        if not only or "evals" in only:
            for case in json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]:
                turn, trace = run_turn(agent, store, f"eval-{case['id']}", case["message"], f"memory-eval-{case['id']}")
                trace["recorded_with"] = {"recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                          "generation_model": model, "turn": {k: v for k, v in turn.items() if k != "message"}}
                (TRACES_DIR / f"{case['id']}.json").write_text(json.dumps(trace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                proposal = turn.get("memory_proposal")
                print(f"{case['id']:42} gate={turn['memory_gate']['open'] if turn['memory_gate'] else None!s:5} "
                      f"propuesta={(proposal or {}).get('tipo')} rechazo={(turn['memory_rejection'] or {}).get('codes')} "
                      f"{turn['seconds']}s", flush=True)
                if proposal:  # cada caso es independiente: la propuesta del eval se descarta (queda auditado)
                    store.discard(proposal["id"], reason="fin del caso de evaluación")
        if not only or "cycles" in only:
            cycles = []
            for cycle in CYCLES:
                turns = []
                for index, (session, message) in enumerate(cycle["turns"]):
                    turn, _ = run_turn(agent, store, session, message, f"{cycle['id']}-{index + 1}")
                    turns.append(turn)
                    print(f"{cycle['id']} turno {index + 1}: resolución={(turn.get('memory_resolution') or {}).get('outcome')} "
                          f"propuesta={(turn.get('memory_proposal') or {}).get('tipo')} memoria={[e['key'] for e in turn['memory_after']]} "
                          f"{turn['seconds']}s", flush=True)
                cycles.append({"id": cycle["id"], "description": cycle["description"], "turns": turns})
            evidence = {"recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "generation_model": model,
                        "redis": url.rsplit("@", 1)[-1], "cycles": cycles, "final_memory": snapshot(store), "audit": store.audit_log()}
            (EVIDENCE_DIR / "cycles.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
