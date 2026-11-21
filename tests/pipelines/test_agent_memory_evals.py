"""Evals de memoria sobre traces y ciclos grabados con el LLM real (``scripts/record_memory_evidence.py``).

No vuelven a ejecutar el modelo: verifican lo que hizo en la grabación con criterios explícitos.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.agent.tracing import executed_nodes, node_output

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "mock_data/eval/agent-memory-cases.json").read_text(encoding="utf-8"))["cases"]
TRACES = ROOT / "mock_data/eval/agent-memory-traces"
CYCLES_PATH = ROOT / "docs/pr-evidence/agent-memory/cycles.json"


def load_trace(case_id: str) -> dict:
    return json.loads((TRACES / f"{case_id}.json").read_text(encoding="utf-8"))


def test_documented_examples_cover_each_kind():
    kinds = [case["kind"] for case in CASES]
    assert kinds.count("memorable") >= 3 and kinds.count("not_memorable") >= 3 and kinds.count("prohibited") >= 3


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_memory_self_evaluation_case(case):
    trace = load_trace(case["id"])
    criteria = case["criteria"]
    assert trace["question"] == case["message"] and trace["status"] == "completed"
    # Desde el Hito 8 Parte 2, un guardrail de entrada puede bloquear antes de la memoria: entonces ni se evalúa.
    blocked = (node_output(trace, "input_guard") or {}).get("guard", {}).get("action") == "block"
    gate = (node_output(trace, "recall_memory") or {}).get("memory_gate") or {"open": False}
    if blocked:
        assert "proposal_tipo" not in criteria, "un caso memorizable no debe bloquearse en la entrada"
    propose = node_output(trace, "propose_memory") or {}
    proposal = propose.get("memory_proposal")
    turn = trace["recorded_with"]["turn"]

    if "gate_open" in criteria:
        assert gate["open"] is criteria["gate_open"], gate
    if criteria.get("gate_open") is False:
        # "Nada que recordar": ni siquiera se llama al modelo para auto-evaluar
        assert "memory_proposal_raw" not in propose and "memory_evaluated_by" not in propose
    if criteria.get("no_proposal"):
        assert proposal is None, proposal
        assert "Propuesta de memoria" not in turn.get("answer", "")
    if "proposal_tipo" in criteria:
        assert proposal and proposal["tipo"] == criteria["proposal_tipo"], (propose.get("memory_proposal_raw"), propose.get("memory_rejection"))
        for field, value in criteria.get("proposal_fields", {}).items():
            assert proposal[field] == value, (field, proposal)
        assert "¿Quieres que lo recuerde" in turn["answer"]  # se propone dentro de la misma respuesta
        assert executed_nodes(trace)[-2:] == ["propose_memory", "output_guard"]
    # En ningún caso se escribe en memoria al proponer
    assert turn["memory_after"] == turn["memory_before"]


def test_prohibited_attempts_never_reach_memory_and_say_why():
    for case in (c for c in CASES if c["kind"] == "prohibited"):
        propose = node_output(load_trace(case["id"]), "propose_memory") or {}
        rejection = propose.get("memory_rejection")
        gate = (node_output(load_trace(case["id"]), "recall_memory") or {}).get("memory_gate") or {"open": False}
        # O la puerta no se abrió, o el modelo no propuso, o los guardrails lo rechazaron con un código explícito
        assert not gate["open"] or propose.get("memory_proposal_raw") is None or (rejection and rejection["codes"]), case["id"]


@pytest.fixture(scope="module")
def cycles():
    return {c["id"]: c for c in json.loads(CYCLES_PATH.read_text(encoding="utf-8"))["cycles"]}


def test_approved_cycle_is_written_and_reflected_in_a_future_conversation(cycles):
    first, reply, later = cycles["ciclo-aprobado"]["turns"]
    assert first["memory_proposal"]["tipo"] == "regla_carrier" and first["memory_after"] == []
    assert reply["memory_resolution"]["label"] == "aprobar" and reply["memory_resolution"]["outcome"] == "aprobada"
    assert [e["key"] for e in reply["memory_after"]] == ["carrier:SEUR:ES"]
    assert later["session_id"] != first["session_id"]  # conversación nueva
    assert "carrier:SEUR:ES" in later["recalled"]
    assert "local" in later["answer"].lower()


def test_rejected_cycle_leaves_memory_unchanged(cycles):
    first, reply, later = cycles["ciclo-rechazado"]["turns"]
    assert first["memory_proposal"]["tipo"] == "preferencia_reporte_b2b"
    assert reply["memory_resolution"]["outcome"] == "rechazada"
    assert reply["memory_after"] == reply["memory_before"] == first["memory_after"]
    assert "30 días" in reply["answer"]  # la pregunta que seguía en el mismo mensaje se respondió con normalidad
    assert not any(key.startswith("b2b_report") for key in later["recalled"])


def test_topic_change_discards_without_writing(cycles):
    first, reply = cycles["ciclo-cambio-de-tema"]["turns"]
    assert first["memory_proposal"]["tipo"] == "contexto_incidente_recurrente"
    assert reply["memory_resolution"]["label"] == "otro_tema"
    assert reply["memory_resolution"]["outcome"] == "descartada_sin_respuesta_clara"
    assert reply["memory_after"] == reply["memory_before"]


def test_every_decision_is_audited():
    audit = json.loads(CYCLES_PATH.read_text(encoding="utf-8"))["audit"]
    outcomes = [record["outcome"] for record in audit]
    for expected in ("propuesta", "aprobada", "rechazada", "descartada_sin_respuesta_clara"):
        assert expected in outcomes
    for record in audit:
        assert record["ts"] and record["proposal_id"] and record["mensaje_origen"] and record["propuesta"]
        if record["outcome"] in ("aprobada", "rechazada"):
            assert record["reply"]  # el mensaje del usuario que tomó la decisión
        if record["outcome"] == "descartada_sin_respuesta_clara":
            assert record.get("reply") or record.get("reason")
