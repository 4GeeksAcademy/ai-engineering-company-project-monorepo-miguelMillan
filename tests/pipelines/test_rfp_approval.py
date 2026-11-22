"""Aprobación humana por departamento, arbitraje y documento final (Hito 9, Parte 3).

Cada prueba siembra el estado con las Partes 1 y 2 reales sobre los fixtures del BRIEFING (ModaViva: warehouse +
reverse, EUR; Luna Cosmetics: warehouse + lastmile, USD) y un LLM doble; la Parte 3 corre con un checkpointer SQLite.
Las decisiones humanas se simulan con ``resume_approval`` (el mismo punto de entrada que usa el backend).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from mock_data.pipelines.rfp_intake import approval as rules
from mock_data.pipelines.rfp_intake.approval_graph import build_approval_graph, pending_approvals, resume_approval, start_approval
from mock_data.pipelines.rfp_intake.graph import run_ticket
from mock_data.pipelines.rfp_intake.repository import RfpRepository
from mock_data.pipelines.rfp_intake.response_graph import run_response

FIXTURES = Path(__file__).parent / "fixtures" / "rfp"
FORMAL = (FIXTURES / "formal-modaviva.md").read_text(encoding="utf-8")
INFORMAL = (FIXTURES / "informal-luna-cosmetics.md").read_text(encoding="utf-8")
GOOD_ES = ("ModaViva busca un partner logístico en Zaragoza. Proponemos un servicio claro y ordenado. "
           "Cada paso queda registrado y se revisa con su equipo. Los precios pendientes se confirmarán pronto. "
           "Trabajamos con reglas claras y reportes mensuales para ModaViva.")
GOOD_EN = ("Luna Cosmetics needs storage and delivery across the US. We keep the process simple and clear. "
           "Every step is tracked and shared with your team. Pending prices will be confirmed with you soon. "
           "You get a clear monthly report on every order we handle.")
OWNERS = {"warehouse": "Ana Whitfield", "lastmile": "Carlos Vega", "reverse": "Sofía Ramos"}


@pytest.fixture
def repo():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return RfpRepository(engine)


@pytest.fixture
def saver():
    return SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))


class FakeLlm:
    """LLM doble: el generador devuelve ``narrative`` (modificable durante la prueba)."""

    def __init__(self, narrative: str):
        self.narrative, self.calls = narrative, []

    def __call__(self, system, user):
        self.calls.append(system.splitlines()[0])
        return {"enfoque": self.narrative, "_model": "fake"}


def seeded(repo, markdown: str, llm, name: str = "rfp") -> str:
    """Partes 1 y 2 reales: deja el ticket con su handoff rfp_response_handoff.v1."""
    ticket = repo.create_ticket(rfp_id=f"RFP-{name}", raw_pdf_path=f"/no/existe/{name}.pdf", original_filename=f"{name}.pdf",
                                uploaded_by="ventas@trackflow.example")
    run_ticket(str(ticket.ticket_id), repo, llm_fn=None, converter=lambda _p: markdown)
    run_response(str(ticket.ticket_id), repo, llm_fn=llm)
    return str(ticket.ticket_id)


def decide(repo, saver, llm, ticket_id, dept, decision="approve", **extra):
    request = repo.get_section(ticket_id, dept).approval_request
    payload = {"decision": decision, "approver": OWNERS[dept], "section_version": request["version"],
               "comment": extra.pop("comment", "" if decision == "approve" else "Ajusta el texto según lo hablado."), **extra}
    return resume_approval(ticket_id, dept, payload, repo, llm_fn=llm, checkpointer=saver)


def statuses(repo, ticket_id):
    return {s.department_id: s.approval_status for s in repo.get_sections(ticket_id)}


# ============================================================================
# Interrupción y reanudación
# ============================================================================
def test_interrupt_per_department_then_resume_generates_the_final_document(repo, saver):
    llm = FakeLlm(GOOD_ES)
    ticket_id = seeded(repo, FORMAL, llm)

    started = start_approval(ticket_id, repo, llm_fn=llm, checkpointer=saver)
    assert started["pending_departments"] == ["reverse", "warehouse"] and started["thread_id"] == f"rfp-{ticket_id}:approval-1"
    assert repo.get_ticket(ticket_id).status == "esperando_aprobación" and started["approval_stage"] == "esperando"
    assert statuses(repo, ticket_id) == {"reverse": "esperando_aprobación", "warehouse": "esperando_aprobación"}
    request = repo.get_section(ticket_id, "warehouse").approval_request
    assert (request["approver"], request["version"], request["client"]["currency"]) == ("Ana Whitfield", 1, "EUR")
    assert request["evaluation"]["compliance"]["pass"] is True and request["pending_items"]  # lo mínimo para decidir

    after_one = decide(repo, saver, llm, ticket_id, "warehouse")
    assert after_one["pending_departments"] == ["reverse"] and repo.get_ticket(ticket_id).status == "esperando_aprobación"
    assert repo.get_final_document(ticket_id) is None             # falta una aprobación: no hay documento

    done = decide(repo, saver, llm, ticket_id, "reverse")
    ticket = repo.get_ticket(ticket_id)
    assert (done["pending_departments"], ticket.status, ticket.approval_stage) == ([], "terminado", "completado")
    document = repo.get_final_document(ticket_id)
    assert document.currency == "EUR" and [s["department_id"] for s in document.sections] == ["reverse", "warehouse"]
    assert {a["approver"] for a in document.approvals} == {"Ana Whitfield", "Sofía Ramos"}
    assert "Warehouse Operations — Propuesta económica" in document.content and "ModaViva, S.L." in document.content


def test_resume_is_an_explicit_entry_point_not_a_restart(repo, saver):
    llm = FakeLlm(GOOD_ES)
    ticket_id = seeded(repo, FORMAL, llm)
    start_approval(ticket_id, repo, llm_fn=llm, checkpointer=saver)
    decide(repo, saver, llm, ticket_id, "warehouse")
    decide(repo, saver, llm, ticket_id, "reverse")
    trace = repo.get_trace(ticket_id, part="approval")
    assert [e.node for e in trace].count("start") == 1                       # nunca se reinició
    assert sum(1 for e in trace if e.node == "request_approval") == 2         # una solicitud por rama, no repetida
    with pytest.raises(Exception):
        start_approval(ticket_id, repo, llm_fn=llm, checkpointer=saver)      # un ticket terminado no se reinicia


def test_department_b_is_approved_while_department_a_is_still_interrupted(repo, saver):
    """Ramas paralelas reales: lastmile pide cambios, se regenera y se aprueba mientras warehouse sigue pausado."""
    llm = FakeLlm(GOOD_EN)
    ticket_id = seeded(repo, INFORMAL, llm, "luna")
    start_approval(ticket_id, repo, llm_fn=llm, checkpointer=saver)

    decide(repo, saver, llm, ticket_id, "lastmile", "request_changes", comment="Explain the carrier choice in one line.")
    assert statuses(repo, ticket_id) == {"lastmile": "esperando_aprobación", "warehouse": "esperando_aprobación"}
    assert repo.get_section(ticket_id, "lastmile").approval_request["version"] == 2    # B avanzó: nueva versión
    assert repo.get_section(ticket_id, "warehouse").approval_request["version"] == 1   # A no se movió

    result = decide(repo, saver, llm, ticket_id, "lastmile")
    assert result["pending_departments"] == ["warehouse"]
    assert statuses(repo, ticket_id) == {"lastmile": "aprobada", "warehouse": "esperando_aprobación"}

    trace = repo.get_trace(ticket_id, part="approval")
    lastmile_approved = next(e.id for e in trace if e.node == "apply_decision" and e.department_id == "lastmile"
                             and e.output.get("result", {}).get("outcome") == "aprobada")
    warehouse_events = [e for e in trace if e.department_id == "warehouse"]
    assert [e.node for e in warehouse_events] == ["prepare", "request_approval"]       # A sigue en su interrupción
    assert any(e.agent == "lastmile_generator" for e in trace)                          # B regeneró con su agente
    assert lastmile_approved > max(e.id for e in warehouse_events)
    assert "warehouse" in pending_approvals(build_approval_graph(repo, llm, saver), repo.get_ticket(ticket_id).approval_thread_id)


def test_human_response_is_validated_before_it_enters_the_graph(repo, saver):
    llm = FakeLlm(GOOD_ES)
    ticket_id = seeded(repo, FORMAL, llm)
    start_approval(ticket_id, repo, llm_fn=llm, checkpointer=saver)
    content = repo.get_section(ticket_id, "reverse").draft_content
    invalid = [
        {"decision": "approve", "approver": "Carlos Vega", "section_version": 1},                     # no es su sección
        {"decision": "approve", "approver": "Sofía Ramos", "section_version": 7},                     # versión obsoleta
        {"decision": "request_changes", "approver": "Sofía Ramos", "section_version": 1, "comment": "no"},  # sin motivo
        {"decision": "approve", "approver": "Sofía Ramos", "section_version": 1, "commitments": {"capacity_sku": 5}},
        {"decision": "approve", "approver": "Sofía Ramos", "section_version": 1,
         "edited_content": content + "\nProcesamos cada devolución en 24 horas.\n"},                   # edición que incumple §5.3
        {"decision": "aprobar", "approver": "Sofía Ramos", "section_version": 1},                     # decisión desconocida
    ]
    for payload in invalid:
        with pytest.raises(rules.DecisionError):
            resume_approval(ticket_id, "reverse", payload, repo, llm_fn=llm, checkpointer=saver)
    with pytest.raises(rules.DecisionError):
        resume_approval(ticket_id, "lastmile", {"decision": "approve", "approver": "Carlos Vega", "section_version": 1},
                        repo, llm_fn=llm, checkpointer=saver)                                          # sin rama pendiente
    assert statuses(repo, ticket_id) == {"reverse": "esperando_aprobación", "warehouse": "esperando_aprobación"}
    assert not [e for e in repo.get_trace(ticket_id, part="approval") if e.node == "apply_decision"]


# ============================================================================
# Límite de iteraciones
# ============================================================================
def test_approval_round_limit_blocks_the_document_without_discarding_the_ticket(repo, saver, monkeypatch):
    monkeypatch.setenv("RFP_MAX_APPROVAL_ROUNDS", "1")
    llm = FakeLlm(GOOD_ES)
    ticket_id = seeded(repo, FORMAL, llm)
    start_approval(ticket_id, repo, llm_fn=llm, checkpointer=saver)

    decide(repo, saver, llm, ticket_id, "reverse", "request_changes", comment="Añade el detalle del restock por talla.")
    request = repo.get_section(ticket_id, "reverse").approval_request
    assert (request["version"], request["round"], request["allowed_decisions"]) == (2, 1, ["approve", "reject"])
    with pytest.raises(rules.DecisionError, match="límite"):
        decide(repo, saver, llm, ticket_id, "reverse", "request_changes", comment="Otra vuelta más, por favor.")

    decide(repo, saver, llm, ticket_id, "reverse", "reject", comment="No podemos ofrecer este servicio así.")
    decide(repo, saver, llm, ticket_id, "warehouse")
    ticket = repo.get_ticket(ticket_id)
    assert (ticket.status, ticket.approval_stage) == ("needs_human_review", "bloqueado")
    assert "reverse" in ticket.approval_note and repo.get_final_document(ticket_id) is None
    assert statuses(repo, ticket_id) == {"reverse": "rechazada", "warehouse": "aprobada"}


def test_arbitration_round_limit_stops_a_conflict_that_never_resolves(repo, saver, monkeypatch):
    monkeypatch.setenv("RFP_MAX_ARBITRATION_ROUNDS", "1")
    llm = FakeLlm(GOOD_EN)
    ticket_id = seeded(repo, INFORMAL, llm, "luna")
    start_approval(ticket_id, repo, llm_fn=llm, checkpointer=saver)
    decide(repo, saver, llm, ticket_id, "lastmile", commitments={"monthly_volume": 5000})
    decide(repo, saver, llm, ticket_id, "warehouse", commitments={"capacity_monthly_orders": 4000})   # ronda 1 de arbitraje
    decide(repo, saver, llm, ticket_id, "lastmile", commitments={"monthly_volume": 5000})             # Carlos insiste en 5000
    ticket = repo.get_ticket(ticket_id)
    assert (ticket.status, ticket.approval_stage) == ("needs_human_review", "bloqueado")
    assert [a["trigger"] for a in ticket.arbitration] == ["volume-vs-capacity", "volume-vs-capacity"]
    assert "volume-vs-capacity" in ticket.approval_note and repo.get_final_document(ticket_id) is None


# ============================================================================
# Arbitraje (BRIEFING §7)
# ============================================================================
def test_arbitration_volume_vs_capacity_caps_the_volume_and_sends_lastmile_back(repo, saver):
    llm = FakeLlm(GOOD_EN)
    ticket_id = seeded(repo, INFORMAL, llm, "luna")
    start_approval(ticket_id, repo, llm_fn=llm, checkpointer=saver)
    decide(repo, saver, llm, ticket_id, "lastmile")                                                    # asume 5000/mes (RFP)
    decide(repo, saver, llm, ticket_id, "warehouse", commitments={"capacity_monthly_orders": 4000})

    ticket = repo.get_ticket(ticket_id)
    conflict = ticket.arbitration[0]
    assert (conflict["trigger"], conflict["arbiter"], conflict["affected_departments"]) == ("volume-vs-capacity", "Miguel Torres", ["lastmile"])
    assert conflict["evidence"] == {"warehouse_capacity_monthly_orders": 4000, "lastmile_assumed_monthly_volume": 5000}
    assert ticket.status == "esperando_aprobación" and statuses(repo, ticket_id)["lastmile"] == "esperando_aprobación"
    request = repo.get_section(ticket_id, "lastmile").approval_request
    assert request["version"] == 2 and "4000 orders/month" in request["content"] and request["arbitration"][0]["trigger"] == "volume-vs-capacity"

    decide(repo, saver, llm, ticket_id, "lastmile")
    assert repo.get_ticket(ticket_id).status == "terminado"
    document = repo.get_final_document(ticket_id)
    assert document.currency == "USD" and document.arbitration[0]["trigger"] == "volume-vs-capacity"
    assert "4000 orders/month" in document.content and "Miguel Torres" in document.content
    assert "**Proposal volume:** 4000 orders/month (capped by arbitration; the RFP asked for 5000)" in document.content
    assert "UPS, FedEx and DHL" in document.content
    arbiter_runs = [e for e in repo.get_trace(ticket_id, part="approval") if e.node == "arbitrate"]
    assert len(arbiter_runs) == 2 and arbiter_runs[0].agent == "arbiter:Miguel Torres"


def test_arbitration_returns_sla_breach_forces_changes_with_sofia_ramos_as_arbiter(repo, saver):
    llm = FakeLlm(GOOD_ES + " Procesamos cada devolución en 24 horas.")
    ticket_id = seeded(repo, FORMAL, llm)                               # la Parte 2 lo marca, pero llega igual (provisional)
    start_approval(ticket_id, repo, llm_fn=llm, checkpointer=saver)
    assert repo.get_section(ticket_id, "reverse").approval_request["provisional"] is True
    llm.narrative = GOOD_ES                                             # el generador corregirá en la revisión forzada
    decide(repo, saver, llm, ticket_id, "warehouse")
    decide(repo, saver, llm, ticket_id, "reverse")                      # Sofía aprueba sin leer → el arbitraje lo para

    ticket = repo.get_ticket(ticket_id)
    triggers = {a["trigger"]: a for a in ticket.arbitration}
    assert set(triggers) == {"returns-sla-breach"}
    assert triggers["returns-sla-breach"]["affected_departments"] == ["reverse", "warehouse"]
    assert triggers["returns-sla-breach"]["arbiter"] == "Miguel Torres"   # otra sección además de reverse la embebe
    assert statuses(repo, ticket_id) == {"reverse": "esperando_aprobación", "warehouse": "esperando_aprobación"}
    assert repo.get_final_document(ticket_id) is None                  # sin documento final hasta cumplir
    decide(repo, saver, llm, ticket_id, "warehouse")
    decide(repo, saver, llm, ticket_id, "reverse")
    assert repo.get_ticket(ticket_id).status == "terminado"
    assert "24 horas" not in repo.get_final_document(ticket_id).content


def test_conflict_triggers_are_detected_on_structured_state():
    facts = {
        "warehouse": rules.section_facts("warehouse", "Precio 16 EUR por m³.", "EUR", {"capacity_monthly_orders": 900}, {}),
        "lastmile": rules.section_facts("lastmile", "Costo final 4 USD por envío.", "USD", {}, {"monthly_volume": 1200}),
        "reverse": rules.section_facts("reverse", "Las devoluciones se procesan el mismo día.", "EUR", {}, {}),
    }
    assert facts["lastmile"]["currencies_in_content"] == ["USD"] and facts["reverse"]["returns_promises_under_48h"]
    conflicts = {c["trigger"]: c for c in rules.detect_conflicts(facts, "ES")}
    assert set(conflicts) == {"currency-mismatch", "returns-sla-breach", "volume-vs-capacity"}
    assert conflicts["currency-mismatch"]["affected_departments"] == ["lastmile"] and conflicts["currency-mismatch"]["arbiter"] == "Miguel Torres"
    assert conflicts["returns-sla-breach"]["arbiter"] == "Sofía Ramos"     # solo reverse la promete
    assert conflicts["volume-vs-capacity"]["overrides"] == {"lastmile": {"monthly_volume": 900}}
    clean = {d: rules.section_facts(d, "Todo en EUR.", "EUR", {}, {}) for d in ("warehouse", "reverse")}
    assert rules.detect_conflicts(clean, "ES") == []


# ============================================================================
# Identidad del checkpointer y trazabilidad
# ============================================================================
def test_concurrent_tickets_never_share_a_checkpoint(repo, saver):
    llm = FakeLlm(GOOD_ES)
    first, second = seeded(repo, FORMAL, llm, "a"), seeded(repo, FORMAL, llm, "b")
    start_approval(first, repo, llm_fn=llm, checkpointer=saver)
    start_approval(second, repo, llm_fn=llm, checkpointer=saver)
    threads = {repo.get_ticket(t).approval_thread_id for t in (first, second)}
    assert threads == {f"rfp-{first}:approval-1", f"rfp-{second}:approval-1"}
    decide(repo, saver, llm, first, "warehouse")
    decide(repo, saver, llm, first, "reverse")
    assert repo.get_ticket(first).status == "terminado"
    assert statuses(repo, second) == {"reverse": "esperando_aprobación", "warehouse": "esperando_aprobación"}


def test_every_node_execution_is_traced_with_agent_input_output_and_timestamp(repo, saver):
    llm = FakeLlm(GOOD_ES)
    ticket_id = seeded(repo, FORMAL, llm)
    start_approval(ticket_id, repo, llm_fn=llm, checkpointer=saver)
    decide(repo, saver, llm, ticket_id, "warehouse")
    decide(repo, saver, llm, ticket_id, "reverse")
    trace = repo.get_trace(ticket_id)
    assert [p for p in dict.fromkeys(e.part for e in trace)] == ["intake", "response", "approval"]
    agents = {e.agent for e in trace}
    assert {"classifier", "orchestrator", "synthesizer", "warehouse_generator", "compliance_evaluator", "approval_gate",
            "human:Ana Whitfield", "human:Sofía Ramos", "arbiter:Miguel Torres", "document_assembler"} <= agents
    assert all(e.started_at <= e.finished_at and e.status == "ok" for e in trace)
    human = next(e for e in trace if e.agent == "human:Ana Whitfield")
    assert human.output["decision"]["decision"] == "approve" and human.input["version"] == 1
    # La traza también vive en el estado del grafo (checkpoint).
    state = build_approval_graph(repo, llm, saver).get_state({"configurable": {"thread_id": repo.get_ticket(ticket_id).approval_thread_id}})
    assert [e["node"] for e in state.values["trace"]][-1] == "final_document"
