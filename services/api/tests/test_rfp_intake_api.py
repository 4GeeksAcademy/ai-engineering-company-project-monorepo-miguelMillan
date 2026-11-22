"""API de recepción de RFP: subida → ticket «analizando» + PDF en data/raw + encolado; consulta y reproceso.
Parte 2: (re)generación de la respuesta y detalle con borradores y EvaluationResult."""

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_SECRET = "test-secret-key-with-at-least-32-bytes"
PDF = b"%PDF-1.4\n% RFP de prueba\n"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'rfp.db'}")  # nunca Supabase en tests
    monkeypatch.setenv("RFP_RAW_DIR", str(tmp_path / "raw" / "rfp"))
    import routes.rfp_intake as rfp_routes
    from main import create_app

    enqueued = []
    monkeypatch.setattr(rfp_routes, "_enqueue", lambda ticket_id, force_accept=False: enqueued.append((ticket_id, force_accept)))
    monkeypatch.setattr(rfp_routes, "_enqueue_generation", lambda ticket_id: enqueued.append((ticket_id, "generate")))
    with TestClient(create_app(tmp_path / "users.json")) as api:
        api.post("/users", json={"email": "ventas@example.com", "password": "correct-horse-battery"})
        token = api.post("/auth/login", json={"email": "ventas@example.com", "password": "correct-horse-battery"}).json()["access_token"]
        api.headers.update({"Authorization": f"Bearer {token}"})
        api.enqueued = enqueued
        api.raw_dir = tmp_path / "raw" / "rfp"
        yield api


def test_upload_creates_an_analyzing_ticket_stores_the_pdf_and_enqueues(client):
    response = client.post("/rfp-intake/tickets", files={"file": ("trackflow-request-1.pdf", PDF, "application/pdf")})
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "analizando" and body["rfp_id"].startswith("RFP-")
    ticket_id = body["ticket_id"]
    assert (client.raw_dir / f"{ticket_id}.pdf").read_bytes() == PDF
    assert client.enqueued == [(ticket_id, False)]
    detail = client.get(f"/rfp-intake/tickets/{ticket_id}").json()
    assert detail["status"] == "analizando" and detail["original_filename"] == "trackflow-request-1.pdf"
    assert [t["ticket_id"] for t in client.get("/rfp-intake/tickets").json()["tickets"]] == [ticket_id]


@pytest.mark.parametrize("name,content", [("rfp.docx", b"PK\x03\x04"), ("rfp.pdf", b"no soy un pdf")])
def test_only_pdfs_are_accepted(client, name, content):
    assert client.post("/rfp-intake/tickets", files={"file": (name, content)}).status_code == 422
    assert client.enqueued == []


def test_upload_requires_authentication(client):
    client.headers.pop("Authorization")
    assert client.post("/rfp-intake/tickets", files={"file": ("rfp.pdf", PDF)}).status_code == 401


def test_queue_down_leaves_a_truthful_ticket(client, monkeypatch):
    import routes.rfp_intake as rfp_routes

    def down(*_a, **_k):
        raise ConnectionError("redis caído")

    monkeypatch.setattr(rfp_routes, "_enqueue", down)
    response = client.post("/rfp-intake/tickets", files={"file": ("rfp.pdf", PDF)})
    assert response.status_code == 503
    ticket = client.get("/rfp-intake/tickets").json()["tickets"][0]
    assert (ticket["status"], ticket["pipeline_stage"]) == ("analizando", "fallido") and "encolar" in ticket["last_error"]


def test_reprocess_and_not_found(client):
    ticket_id = client.post("/rfp-intake/tickets", files={"file": ("rfp.pdf", PDF)}).json()["ticket_id"]
    assert client.post(f"/rfp-intake/tickets/{ticket_id}/reprocess", params={"force_accept": True}).status_code == 202
    assert client.enqueued[-1] == (ticket_id, True)
    assert client.get(f"/rfp-intake/tickets/{uuid.uuid4()}").status_code == 404


FORMAL_FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "pipelines" / "fixtures" / "rfp" / "formal-modaviva.md"


def test_part2_generate_endpoint_and_detail_with_drafts_and_evaluations(client):
    from mock_data.pipelines.rfp_intake.graph import run_ticket
    from mock_data.pipelines.rfp_intake.repository import RfpRepository
    from mock_data.pipelines.rfp_intake.response_graph import run_response

    ticket_id = client.post("/rfp-intake/tickets", files={"file": ("trackflow-request-1.pdf", PDF)}).json()["ticket_id"]
    assert client.post(f"/rfp-intake/tickets/{ticket_id}/generate").status_code == 409  # Parte 1 sin terminar
    repo = RfpRepository(client.app.state.engine)
    markdown = FORMAL_FIXTURE.read_text(encoding="utf-8")
    run_ticket(ticket_id, repo, llm_fn=None, converter=lambda _p: markdown)  # lo que hace el worker

    response = client.post(f"/rfp-intake/tickets/{ticket_id}/generate")
    assert response.status_code == 202 and client.enqueued[-1] == (ticket_id, "generate")
    run_response(ticket_id, repo, llm_fn=None)  # lo que hace el worker de la Parte 2

    detail = client.get(f"/rfp-intake/tickets/{ticket_id}").json()
    assert detail["status"] == "esperando_aprobación" and detail["response_stage"] == "handoff"
    assert detail["response_handoff"]["contract"] == "rfp_response_handoff.v1"
    for section in detail["sections"]:
        assert section["section_status"] == "evaluacion_superada" and section["draft_content"].startswith("## ")
        assert section["evaluation_result"]["overall_pass"] is True and section["evaluation_history"][0]["iteration"] == 1
