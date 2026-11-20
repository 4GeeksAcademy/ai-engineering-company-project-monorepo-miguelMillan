"""Evals del agente LangGraph contra traces grabados (no contra ejecuciones en vivo).

    uv run pytest tests/pipelines/test_agent_evals.py

Casos y criterios: ``mock_data/eval/agent-eval-cases.json``.
Traces: ``mock_data/eval/agent-traces/<id>.json`` (grabados con
``scripts/record_agent_traces.py`` usando Qdrant, embeddings y LLM reales).

Además del enrutamiento, los criterios ``answer_contains_*`` exigen que la
respuesta siga anclada en la base de conocimiento de TrackFlow (SEUR para
Aragón rural, 30 días de ventana de devolución, aprobación de Miguel Torres
para descuentos, SLA no garantizado en Black Friday con hasta un 40 %).
"""

import json
import unicodedata
from pathlib import Path

import pytest

from services.agent.graph import sources_used
from services.agent.tools import STATUS_LABELS
from services.agent.tracing import executed_nodes, node_output

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "mock_data" / "eval" / "agent-eval-cases.json").read_text(encoding="utf-8"))["cases"]
TRACES_DIR = ROOT / "mock_data" / "eval" / "agent-traces"


def _normalize(text: str) -> str:
    """Minúsculas y sin tildes, para que 'días'/'dias' o 'garantiza'/'Garantiza' no rompan el criterio."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def load_trace(case_id: str) -> dict:
    path = TRACES_DIR / f"{case_id}.json"
    assert path.exists(), f"Falta el trace grabado {path}; ejecuta scripts/record_agent_traces.py"
    return json.loads(path.read_text(encoding="utf-8"))


def final_answer(trace: dict) -> str:
    final = node_output(trace, trace["final_node"])
    assert final and "answer" in final, f"El nodo final {trace['final_node']} no produjo respuesta"
    return final["answer"]


def test_there_are_at_least_three_evals_including_grounding():
    assert len(CASES) >= 3
    assert any("answer_contains_all" in case["criteria"] and case["criteria"].get("final_node") == "generate" for case in CASES)


def test_routing_evals_cover_tool_only_rag_only_and_fallback():
    sources = [case["criteria"].get("sources_used") for case in CASES]
    assert ["incidents"] in sources and ["inventory"] in sources  # se resuelve con una tool, no con el RAG
    assert ["rag"] in sources  # se resuelve con el RAG, no con una tool
    assert ["incidents", "rag"] in sources  # ambos, en ese orden
    assert any(case["criteria"].get("incident_tool_error") == "unavailable" for case in CASES)  # fallback servicio caído


def check_case(case: dict, trace: dict) -> list[str]:
    """Devuelve la lista de criterios incumplidos (vacía = el caso pasa)."""
    criteria, failures = case["criteria"], []
    nodes = executed_nodes(trace)
    if trace["question"] != case["question"]:
        failures.append("el trace no corresponde a la pregunta del caso")
    if trace["status"] != criteria["status"]:
        failures.append(f"status {trace['status']} != {criteria['status']}")
    if trace["final_node"] != criteria["final_node"]:
        failures.append(f"final_node {trace['final_node']} != {criteria['final_node']}")
    if "node_order" in criteria and nodes != criteria["node_order"]:
        failures.append(f"orden de nodos {nodes} != {criteria['node_order']}")
    if "retrieve" in nodes and "generate" in nodes and nodes.index("retrieve") > nodes.index("generate"):
        failures.append("retrieve debe ejecutarse antes que generate")
    failures += [f"{n} no debía ejecutarse" for n in criteria.get("nodes_not_executed", []) if n in nodes]
    for route, destination in criteria.get("route_decisions", {}).items():
        decision = next((st["output"]["next"] for st in trace["steps"] if st["kind"] == "route" and st["name"] == route), None)
        if decision != destination:
            failures.append(f"{route} → {decision}, se esperaba {destination}")

    retrieved = (node_output(trace, "retrieve") or {}).get("context", [])
    if "retrieved_count" in criteria and len(retrieved) != criteria["retrieved_count"]:
        failures.append(f"{len(retrieved)} chunks recuperados, se esperaban {criteria['retrieved_count']}")
    for expected in criteria.get("retrieved_includes", []):
        if not any(c["source_document"] == expected["source_document"] and c["chunk_index"] == expected["chunk_index"] for c in retrieved):
            failures.append(f"no se recuperó {expected}")
    if "generate" in nodes and not (retrieved and all(c["score"] >= 0.45 for c in retrieved)):
        failures.append("generate se ejecutó sin contexto por encima de min_score")

    # Fuentes usadas y su orden (Parte 2): el trace debe dejar claro si se usó RAG, una tool o ambos.
    if "sources_used" in criteria:
        if sources_used(trace) != criteria["sources_used"]:
            failures.append(f"fuentes {sources_used(trace)} != {criteria['sources_used']}")
        if trace.get("sources_used") != criteria["sources_used"]:
            failures.append(f"agent_runs.sources_used {trace.get('sources_used')} != {criteria['sources_used']}")

    final = node_output(trace, trace["final_node"]) or {}
    answer = _normalize(final.get("answer", ""))
    incident = (node_output(trace, "lookup_incidents") or {}).get("incident_result")
    inventory = (node_output(trace, "lookup_inventory") or {}).get("inventory_result")
    if criteria.get("incident_tool_ok") and not (incident and incident["ok"]):
        failures.append(f"la tool de incidencias no respondió OK: {incident and incident.get('error_type')}")
    if "incident_tool_error" in criteria and (not incident or incident.get("error_type") != criteria["incident_tool_error"]):
        failures.append(f"se esperaba error {criteria['incident_tool_error']} en la tool de incidencias")
    if "incident_query" in criteria and incident:
        sent = {k: v for k, v in incident["query"].items() if v}
        if sent != criteria["incident_query"]:
            failures.append(f"filtros enviados {sent} != {criteria['incident_query']}")
    if criteria.get("answer_reflects_incident_status") and incident and incident["ok"]:
        live_status = STATUS_LABELS[incident["incidents"][0]["status"]]  # el dato que devolvió el servicio real
        if _normalize(live_status) not in answer:
            failures.append(f"la respuesta no refleja el estado real «{live_status}» del ticket")
    if criteria.get("answer_contains_incident_total") and incident and incident["ok"]:
        if f"hay {incident['total']} tickets" not in answer:
            failures.append(f"la respuesta no da el total real ({incident['total']}) de tickets")
    if criteria.get("inventory_tool_ok") and not (inventory and inventory["ok"]):
        failures.append(f"la tool de inventario no respondió OK: {inventory and inventory.get('error_type')}")
    if criteria.get("answer_contains_inventory_stock") and inventory and inventory["ok"]:
        for item in inventory["items"]:
            if f"{item['current_stock']} unidades" not in answer:
                failures.append(f"la respuesta no da el stock real ({item['current_stock']}) de {item['sku']}")
    failures += [f"la respuesta no debía contener «{f}»" for f in criteria.get("answer_must_not_contain", []) if _normalize(f) in answer]
    failures += [f"la respuesta no menciona «{f}»" for f in criteria.get("answer_contains_all", []) if _normalize(f) not in answer]
    if "answer_contains_any" in criteria and not any(_normalize(f) in answer for f in criteria["answer_contains_any"]):
        failures.append(f"la respuesta no contiene ninguno de {criteria['answer_contains_any']}")
    return failures


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_agent_eval_case(case):
    trace = load_trace(case["id"])
    failures = check_case(case, trace)
    assert not failures, f"{case['id']}: {failures}\nrespuesta: {final_answer(trace)!r}"


def test_perfect_trace_with_ungrounded_answer_fails_the_eval():
    """Un trace con enrutamiento correcto pero respuesta que ignora la política es un fallo."""
    case = next(c for c in CASES if c["id"] == "carrier-aragon-rural")
    trace = json.loads(json.dumps(load_trace(case["id"])))
    for step in trace["steps"]:
        if step["kind"] == "node" and step["name"] == "generate":
            step["output"]["answer"] = "Para Aragón rural recomendamos MRW, que llega a todos los pueblos en 24 horas."

    failures = check_case(case, trace)
    assert failures == ["la respuesta no menciona «SEUR»"]


def test_eval_detects_generation_forced_on_empty_context():
    case = next(c for c in CASES if c["id"] == "out-of-domain-no-context")
    trace = json.loads(json.dumps(load_trace(case["id"])))
    trace["steps"].append({"kind": "node", "name": "generate", "status": "ok", "output": {"answer": "Una paella lleva arroz."}})
    trace["final_node"] = "generate"

    failures = check_case(case, trace)
    assert "generate no debía ejecutarse" in failures
    assert any("generate se ejecutó sin contexto" in f for f in failures)


def test_traces_were_recorded_with_real_models_and_checkpoints():
    for case in CASES:
        recorded = load_trace(case["id"])["recorded_with"]
        assert recorded["generation_model"] and recorded["embedding_model"]
        assert recorded["generation_model"] != recorded["embedding_model"].split("/", 1)[-1]
        assert recorded["checkpoints"][0] == ["__start__"] and recorded["checkpoints"][-1] == []
