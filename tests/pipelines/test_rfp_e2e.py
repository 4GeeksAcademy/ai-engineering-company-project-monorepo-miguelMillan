"""E2E de las tres partes con fixtures y aprobaciones humanas simuladas (resume programático).

Recepción (Parte 1) → generación y evaluación (Parte 2) → aprobación por departamento y cierre (Parte 3), con la
misma secuencia de llamadas que hacen el worker de Celery y el backend. Comprueba que el ticket recorre los estados
del BRIEFING §2.3 sin saltos, que los contratos se encadenan y que ningún dato se pierde entre partes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from mock_data.pipelines.rfp_intake.approval_graph import resume_approval, start_approval
from mock_data.pipelines.rfp_intake.graph import run_ticket
from mock_data.pipelines.rfp_intake.models import RfpTicket
from mock_data.pipelines.rfp_intake.repository import RfpRepository
from mock_data.pipelines.rfp_intake.response_graph import run_response

FIXTURES = Path(__file__).parent / "fixtures" / "rfp"
NARRATIVE = {
    "es": "ModaViva busca un partner logístico en Zaragoza. Proponemos un servicio claro y ordenado. Cada paso queda "
          "registrado y se revisa con su equipo. Los precios pendientes se confirmarán pronto con ModaViva.",
    "en": "Luna Cosmetics needs storage and delivery across the US. We keep the process simple and clear. Every step "
          "is tracked and shared with your team. Pending prices will be confirmed with you soon.",
}
# Orden de estados del BRIEFING §2.3 (needs_human_review solo si alguna sección agota el límite de la Parte 2).
EXPECTED_PATH = ["analizando", "analisis_completo", "generando_borrador", "en_evaluación", "esperando_aprobación", "terminado"]


def llm(system, user):
    language = "en" if "en inglés" in system else "es"
    return {"enfoque": NARRATIVE[language], "_model": "fake"}


@pytest.fixture
def repo():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    repo = RfpRepository(engine)
    repo.status_log = []

    @event.listens_for(RfpTicket, "before_update")
    def log_status(_mapper, _connection, target):  # registra cada cambio de estado tal como se persiste
        if not repo.status_log or repo.status_log[-1] != target.status:
            repo.status_log.append(target.status)

    yield repo
    event.remove(RfpTicket, "before_update", log_status)


@pytest.mark.parametrize("fixture,currency,departments,approvals", [
    ("formal-modaviva.md", "EUR", ["reverse", "warehouse"],
     [("reverse", "Sofía Ramos", {}), ("warehouse", "Ana Whitfield", {"capacity_sku": 20000})]),
    ("informal-luna-cosmetics.md", "USD", ["lastmile", "warehouse"],
     [("warehouse", "Ana Whitfield", {"capacity_monthly_orders": 8000}), ("lastmile", "Carlos Vega", {})]),
])
def test_rfp_goes_through_the_three_parts_with_consistent_state(repo, fixture, currency, departments, approvals):
    markdown = (FIXTURES / fixture).read_text(encoding="utf-8")
    saver = SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))
    ticket = repo.create_ticket(rfp_id="RFP-E2E", raw_pdf_path="/raw/e2e.pdf", original_filename=fixture.replace(".md", ".pdf"),
                                uploaded_by="ventas@trackflow.example")
    tid = str(ticket.ticket_id)
    repo.status_log.append("analizando")

    # Parte 1 → Parte 2 → Parte 3, como las encadena el worker (tasks.py).
    assert run_ticket(tid, repo, llm_fn=None, converter=lambda _p: markdown)["status"] == "analisis_completo"
    part1 = repo.get_ticket(tid).handoff
    assert run_response(tid, repo, llm_fn=llm)["status"] == "esperando_aprobación"
    part2 = repo.get_ticket(tid).response_handoff
    started = start_approval(tid, repo, llm_fn=llm, checkpointer=saver)
    assert started["pending_departments"] == departments

    # Aprobaciones humanas simuladas: resume programático, una rama cada vez.
    for dept, approver, commitments in approvals:
        version = repo.get_section(tid, dept).approval_request["version"]
        resume_approval(tid, dept, {"decision": "approve", "approver": approver, "section_version": version,
                                    "commitments": commitments, "decided_by": "e2e@trackflow.example"},
                        repo, llm_fn=llm, checkpointer=saver)

    final = repo.get_ticket(tid)
    document = repo.get_final_document(tid)
    # Estados: la secuencia del BRIEFING, sin saltos ni retrocesos.
    assert repo.status_log == EXPECTED_PATH
    assert (final.status, final.approval_stage, final.last_error) == ("terminado", "completado", None)
    # Contratos encadenados y datos que no se pierden entre partes.
    assert part1["contract"] == "rfp_handoff.v1" and part2["contract"] == "rfp_response_handoff.v1"
    assert part2["ticket_id"] == part1["ticket_id"] == tid == str(document.ticket_id)
    assert part1["client"]["currency"] == part2["currency"] == document.currency == currency
    assert sorted(w["department_id"] for w in part1["workstreams"]) == departments == [s["department_id"] for s in document.sections]
    assert {s["approver"] for s in document.sections} == {a for _, a, _ in approvals}
    assert part1["client"]["name"] in document.content
    for question in (q for w in part1["workstreams"] for q in w["open_questions"]):
        assert question in document.content                       # las preguntas abiertas de la Parte 1 llegan al final
    for section in repo.get_sections(tid):
        assert section.approval_status == "aprobada" and section.approved_content in document.content
        assert section.approver == section.decision_history[-1]["approver"]
    # Trazabilidad de punta a punta, en orden.
    parts = [e.part for e in repo.get_trace(tid)]
    assert parts == sorted(parts, key=["intake", "response", "approval"].index)
