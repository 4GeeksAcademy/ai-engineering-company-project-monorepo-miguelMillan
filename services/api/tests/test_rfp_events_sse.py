"""Endpoint SSE ``GET /rfp-intake/events`` probado en el wire: uvicorn real en un puerto efímero y bytes tal como llegan.

(``TestClient`` acumula la respuesta completa, así que con un stream infinito no probaría ni las cabeceras ni el framing.)
Los eventos los produce el pipeline real de recepción sobre los fixtures de las RFP de muestra.
"""

import json
import re
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import uvicorn
import jwt

from conftest import promote

TEST_SECRET = "test-secret-key-with-at-least-32-bytes"
FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "pipelines" / "fixtures" / "rfp"
BRIEFING_FIELDS = {"ticket_id", "rfp_id", "client_name", "client_country", "services_requested", "status", "created_at"}


class Api:
    def __init__(self, base, app, token):
        self.base, self.app, self.token = base, app, token

    @property
    def repo(self):
        from mock_data.pipelines.rfp_intake.repository import RfpRepository

        return RfpRepository(self.app.state.engine)

    def new_ticket(self):
        return str(self.repo.create_ticket(rfp_id="RFP-SSE", raw_pdf_path="/x.pdf", original_filename="x.pdf", uploaded_by=None).ticket_id)

    def process(self, fixture, ticket_id=None, **kwargs):
        """Lo que hace el worker de Celery con un PDF subido."""
        from mock_data.pipelines.rfp_intake.graph import run_ticket

        ticket_id = ticket_id or self.new_ticket()
        run_ticket(ticket_id, self.repo, llm_fn=None, converter=lambda _p: (FIXTURES / fixture).read_text(encoding="utf-8"), **kwargs)
        return ticket_id

    def stream(self, last_event_id=None, token=None, read_timeout=8):
        headers = {"Authorization": f"Bearer {token or self.token}"}
        if last_event_id is not None:
            headers["Last-Event-ID"] = str(last_event_id)
        return httpx.stream("GET", f"{self.base}/rfp-intake/events", headers=headers, timeout=httpx.Timeout(5, read=read_timeout))


def blocks(response):
    """Bloques SSE crudos, separados por la línea en blanco del protocolo."""
    buffer = ""
    for chunk in response.iter_text():
        buffer += chunk
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            yield block


def field(block, name):
    return next((line[len(name) + 2:] for line in block.split("\n") if line.startswith(f"{name}: ")), None)


def named_events(response, count):
    found = []
    for block in blocks(response):
        if field(block, "event"):
            found.append(block)
            if len(found) == count:
                return found
    return found


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'sse.db'}")  # nunca Supabase en tests
    monkeypatch.setenv("RFP_SSE_POLL_SECONDS", "0.05")
    monkeypatch.setenv("RFP_SSE_KEEPALIVE_SECONDS", "0.3")
    monkeypatch.setenv("RFP_SSE_REPLAY_LIMIT", "2")
    from main import create_app

    app = create_app(tmp_path / "users.json")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", timeout_graceful_shutdown=1))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    while not server.started:
        assert thread.is_alive()
        time.sleep(0.02)
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    account = {"email": "ventas@example.com", "password": "correct-horse-battery"}
    httpx.post(f"{base}/users", json=account)
    token = httpx.post(f"{base}/auth/login", json=account).json()["access_token"]
    promote(app, account["email"])
    yield Api(base, app, token)
    server.should_exit = True
    thread.join(timeout=10)
    assert not thread.is_alive()


def test_wire_has_event_stream_headers_named_event_and_briefing_payload(api):
    with api.stream() as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "no-cache" in response.headers["cache-control"] and response.headers["x-accel-buffering"] == "no"
        stream = blocks(response)
        assert re.fullmatch(r"retry: \d+\nid: 0", next(stream))            # llega antes de que exista ningún evento
        ticket_id = api.process("informal-luna-cosmetics.md")
        block = next(b for b in stream if field(b, "event"))
    assert [line.split(":")[0] for line in block.split("\n")] == ["id", "event", "data"]
    assert field(block, "event") == "rfp_ticket_created" and field(block, "id") == "1"
    data = json.loads(field(block, "data"))
    assert set(data) == BRIEFING_FIELDS                                    # ni sobre anidado ni contenido del documento
    assert (data["ticket_id"], data["status"]) == (ticket_id, "analyzing")
    assert (data["client_name"], data["client_country"], data["services_requested"]) == ("Luna Cosmetics", "US", ["warehouse", "lastmile"])
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", data["created_at"])


def test_event_fires_on_classification_once_per_ticket_and_never_for_discarded_documents(api):
    ticket_id = api.new_ticket()
    assert api.repo.latest_event_id() == 0                                 # subir el PDF todavía no notifica
    api.process("formal-modaviva.md", ticket_id)
    api.process("formal-modaviva.md", ticket_id)                           # reintento de Celery o reproceso
    pitch = api.process("invalid-rapidship-pitch.md")
    assert [str(e.ticket_id) for e in api.repo.events_after(0, 10)] == [ticket_id]
    api.process("invalid-rapidship-pitch.md", pitch, force_accept=True)    # Ventas lo acepta: ahora sí es una RFP
    assert [str(e.ticket_id) for e in api.repo.events_after(0, 10)] == [ticket_id, pitch]


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer no-es-un-jwt"}, {"Authorization": "Basic eDp5"}])
def test_unauthenticated_clients_get_401_and_no_events(api, headers):
    api.process("formal-modaviva.md")
    response = httpx.get(f"{api.base}/rfp-intake/events", headers={**headers, "Last-Event-ID": "0"}, timeout=5)
    assert response.status_code == 401 and "rfp_ticket_created" not in response.text
    assert not response.headers["content-type"].startswith("text/event-stream")
    # El JWT tampoco se acepta en la URL, donde quedaría en logs.
    assert httpx.get(f"{api.base}/rfp-intake/events", params={"access_token": api.token, "token": api.token}, timeout=5).status_code == 401


def test_customers_cannot_open_the_operations_stream_and_losing_the_role_closes_it(api):
    with api.stream() as response:                                         # personal
        stream = blocks(response)
        next(stream)
        promote(api.app, "ventas@example.com", "user")
        deadline = time.time() + 5
        for block in stream:                                               # se agota: perdió el rol con el stream abierto
            assert block.startswith(":") and time.time() < deadline
    with api.stream(last_event_id="0") as response:
        assert response.status_code == 403
    with api.stream(token=api.token) as response:
        assert response.status_code == 403


def test_stream_closes_when_the_token_expires_or_the_user_is_deactivated(api):
    claims = jwt.decode(api.token, options={"verify_signature": False})
    short = jwt.encode({**claims, "exp": datetime.now(timezone.utc) + timedelta(seconds=2)}, TEST_SECRET, algorithm="HS256")
    with api.stream(token=short) as response:
        assert response.status_code == 200
        started = time.time()
        list(response.iter_text())                                         # termina sola: el servidor la cierra
        assert time.time() - started < 5
    with api.stream(token=short) as response:
        assert response.status_code == 401
    with api.stream() as response:
        stream = blocks(response)
        next(stream)
        with api.app.state.lock:
            api.app.state.db.table("users").update({"is_active": False})
        assert all(b.startswith(":") for b in stream)                      # se agota: la conexión se cerró


def test_keep_alive_comments_keep_the_connection_open_and_later_events_still_arrive(api):
    with api.stream() as response:
        stream = blocks(response)
        next(stream)
        assert [next(stream) for _ in range(4)] == [": keep-alive"] * 4    # más de 1 s sin eventos y sigue abierta
        ticket_id = api.process("formal-modaviva.md")
        assert json.loads(field(next(b for b in stream if field(b, "event")), "data"))["ticket_id"] == ticket_id


def test_last_event_id_replays_only_what_was_missed_while_disconnected(api):
    with api.stream() as response:
        first = api.process("formal-modaviva.md")
        block = named_events(response, 1)[0]
    missed = [api.process("informal-luna-cosmetics.md"), api.process("formal-modaviva.md")]   # sin nadie conectado
    with api.stream(last_event_id=field(block, "id")) as response:
        replayed = [json.loads(field(b, "data"))["ticket_id"] for b in named_events(response, 2)]
    assert replayed == missed and first not in replayed
    with api.stream() as response:                                         # sin Last-Event-ID: solo eventos posteriores
        stream = blocks(response)
        assert next(stream).endswith("id: 3")
        assert next(stream) == ": keep-alive"


def test_unrecoverable_gap_is_announced_never_silent(api):
    for _ in range(3):                                                     # RFP_SSE_REPLAY_LIMIT = 2
        api.process("formal-modaviva.md")
    for last_event_id, reason in (("0", "replay_limit_exceeded"), ("999", "unknown_event_id"), ("abc", "unknown_event_id")):
        with api.stream(last_event_id=last_event_id) as response:
            block = named_events(response, 1)[0]
        assert field(block, "event") == "rfp_stream_reset" and field(block, "id") == "3"
        assert json.loads(field(block, "data")) == {"reason": reason}


def test_every_connected_client_receives_the_event_and_disconnecting_frees_the_subscription(api):
    with api.stream() as one, api.stream() as two:
        first, second = blocks(one), blocks(two)
        next(first), next(second)
        assert api.app.state.rfp_event_hub.subscribers == 2
        ticket_id = api.process("formal-modaviva.md")
        for stream in (first, second):
            assert json.loads(field(next(b for b in stream if field(b, "event")), "data"))["ticket_id"] == ticket_id
    deadline = time.time() + 5
    while api.app.state.rfp_event_hub.subscribers and time.time() < deadline:
        time.sleep(0.05)
    assert api.app.state.rfp_event_hub.subscribers == 0


def test_idle_streams_share_one_poll_instead_of_querying_the_database_each(api, monkeypatch):
    from mock_data.pipelines.rfp_intake.repository import RfpRepository

    reads = []
    original = RfpRepository.events_after
    monkeypatch.setattr(RfpRepository, "events_after", lambda *a, **k: (reads.append(1), original(*a, **k))[1])
    with api.stream() as one, api.stream() as two:
        streams = [blocks(one), blocks(two)]
        for stream in streams:
            next(stream)
            assert [next(stream) for _ in range(3)] == [": keep-alive"] * 3
        assert len(reads) == 2                                             # solo la lectura inicial de cada uno
        ticket_id = api.process("formal-modaviva.md")
        for stream in streams:
            assert json.loads(field(next(b for b in stream if field(b, "event")), "data"))["ticket_id"] == ticket_id


def test_list_can_be_limited_to_given_tickets(api):
    ids = [api.new_ticket() for _ in range(3)]
    headers = {"Authorization": f"Bearer {api.token}"}
    response = httpx.get(f"{api.base}/rfp-intake/tickets", params=[("ticket_id", ids[0]), ("ticket_id", ids[2])], headers=headers)
    assert {t["ticket_id"] for t in response.json()["tickets"]} == {ids[0], ids[2]}
    assert len(httpx.get(f"{api.base}/rfp-intake/tickets", headers=headers).json()["tickets"]) == 3
