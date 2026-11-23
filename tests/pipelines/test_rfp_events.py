"""Evento ``rfp_ticket_created``: cuándo lo registra el pipeline y con qué payload (BRIEFING de tiempo real, §2 y §3).

El endpoint SSE (cabeceras, framing, JWT, replay) se prueba en el wire en services/api/tests/test_rfp_events_sse.py.
"""

from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from mock_data.pipelines.rfp_intake.graph import run_ticket
from mock_data.pipelines.rfp_intake.repository import RfpRepository

FIXTURES = Path(__file__).parent / "fixtures" / "rfp"


@pytest.fixture
def repo():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return RfpRepository(engine)


def process(repo, fixture, llm_fn=None):
    ticket = repo.create_ticket(rfp_id="RFP-EV", raw_pdf_path="/x.pdf", original_filename="x.pdf", uploaded_by=None)
    markdown = (FIXTURES / fixture).read_text(encoding="utf-8")
    run_ticket(str(ticket.ticket_id), repo, llm_fn=llm_fn, converter=lambda _p: markdown)
    return str(ticket.ticket_id)


@pytest.mark.parametrize("fixture,client,country,services", [
    ("formal-modaviva.md", "ModaViva, S.L.", "ES", ["warehouse", "reverse"]),
    ("informal-luna-cosmetics.md", "Luna Cosmetics", "US", ["warehouse", "lastmile"]),
])
def test_valid_rfp_registers_the_briefing_payload(repo, fixture, client, country, services):
    ticket_id = process(repo, fixture)
    [event] = repo.events_after(0, 10)
    assert event.event_type == "rfp_ticket_created"
    assert event.payload == {
        "ticket_id": ticket_id, "rfp_id": repo.get_ticket(ticket_id).rfp_id, "client_name": client, "client_country": country,
        "services_requested": services, "status": "analyzing", "created_at": event.payload["created_at"]}


def test_the_event_is_registered_before_the_analysis_continues(repo, monkeypatch):
    """Sale en cuanto se clasifica, no al terminar el flujo (que tarda minutos con el LLM)."""
    from mock_data.pipelines.rfp_intake import agents

    seen = []
    original = agents.orchestrate
    monkeypatch.setattr(agents, "orchestrate", lambda *a, **k: (seen.append(len(repo.events_after(0, 10))), original(*a, **k))[1])
    process(repo, "formal-modaviva.md")
    assert seen == [1]


def test_discarded_document_never_registers_an_event(repo):
    process(repo, "invalid-rapidship-pitch.md")
    assert repo.events_after(0, 10) == []
