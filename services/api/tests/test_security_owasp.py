"""Regresión de las vulnerabilidades críticas y altas de la auditoría OWASP (docs/security/owasp-audit.md) en el backend.

Cada prueba ES la prueba de concepto del hallazgo: contra el código anterior al arreglo falla (el ataque funciona).
"""

import json
import re

import pytest

ACCOUNT = "correct-horse-battery"
ANY_UUID = "00000000-0000-4000-8000-000000000000"
# Toda ruta HTTP debe estar en una de estas listas. Lo que no está aquí es SOLO para el personal (deny by default).
PUBLIC = {"GET /health", "POST /users", "POST /auth/login", "POST /auth/forgot-password", "POST /auth/reset-password",
          "POST /telemetry/events"}
CUSTOMER = {"GET /auth/me", "POST /auth/change-password", "GET /users", "GET /users/{user_id}", "PUT /users/{user_id}",
            "DELETE /users/{user_id}", "GET /profiles/me", "PUT /profiles/me", "POST /knowledge/query", "POST /agent/query",
            "POST /chat/sessions", "GET /chat/sessions/{session_id}", "POST /chat/sessions/{session_id}/close"}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-with-at-least-32-bytes")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'sec.db'}")
    monkeypatch.setenv("RFP_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("TASKS_INTERNAL_TOKEN", "internal-token-of-the-gateway")
    from fastapi.testclient import TestClient
    from main import create_app

    with TestClient(create_app(tmp_path / "sec.json"), raise_server_exceptions=False) as client:
        yield client


def login(api, email, role=None):
    api.post("/users", json={"email": email, "password": ACCOUNT})
    if role:  # como lo hace un administrador; nunca la propia cuenta
        with api.app.state.lock:
            api.app.state.db.table("users").update({"role": role}, lambda d: d["email"] == email)
    token = api.post("/auth/login", json={"email": email, "password": ACCOUNT}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def http_routes(app):
    schema = app.openapi()["paths"]
    found = {f"{method.upper()} {path}" for path, item in schema.items() for method in item if method in ("get", "post", "put", "patch", "delete")}
    return found | {"POST /internal/tasks/analyze"}  # fuera del esquema público


def call(api, route, headers):
    method, path = route.split(" ", 1)
    path = re.sub(r"\{(supplier_id|product_id|user_id)\}", "1", path)
    path = re.sub(r"\{department_id\}", "warehouse", path)
    path = re.sub(r"\{\w+\}", ANY_UUID, path)
    if path == "/rfp-intake/events" and headers:
        headers = {**headers, "Last-Event-ID": "0"}
    return api.request(method, path, headers=headers)


# ---------------------------------------------------------------- A01 · control de acceso roto
def test_a01_every_route_is_denied_by_default_to_anonymous_and_self_registered_accounts(api):
    anonymous, customer = [], []
    headers = login(api, "cliente@example.com")
    for route in sorted(http_routes(api.app) - PUBLIC):
        if call(api, route, {}).status_code != 401:
            anonymous.append(route)
        if route not in CUSTOMER and route != "GET /rfp-intake/events" and call(api, route, headers).status_code != 403:
            customer.append(route)
    assert anonymous == [] and customer == []


def test_a01_the_customer_list_contains_no_internal_route(api):
    assert CUSTOMER | PUBLIC <= http_routes(api.app)
    assert not [r for r in CUSTOMER if re.search(r"suppliers|inventory|rfp|reporting|incidents|tasks|memory|guardrails|telemetry", r)]


def test_a01_a_self_registered_account_cannot_delete_suppliers_nor_change_rates(api):
    staff, customer = login(api, "ops@example.com", "manager"), login(api, "cliente@example.com")
    supplier = api.post("/suppliers", headers=staff, json={
        "name": "UPS Ground", "country": "USA", "categories": ["carrier_last_mile"], "rate_per_shipment": 7.45, "currency": "USD", "status": "active"})
    assert supplier.status_code == 201, supplier.text
    path = f"/suppliers/{supplier.json()['id']}"
    assert api.get("/suppliers", headers=customer).status_code == 403                     # tarifas negociadas (confidencial)
    assert api.patch(f"{path}/rate", headers=customer, json={"rate_per_shipment": 0.01}).status_code == 403
    assert api.delete(path, headers=customer).status_code == 403
    assert api.get(path, headers=staff).json()["rate_per_shipment"] == 7.45


def test_a01_registration_cannot_choose_a_role_nor_an_account_promote_itself(api):
    assert api.post("/users", json={"email": "x@example.com", "password": ACCOUNT, "role": "admin"}).status_code == 422
    headers = login(api, "cliente@example.com")
    me = next(d.doc_id for d in api.app.state.db.table("users").all() if d["email"] == "cliente@example.com")
    for body in ({"role": "admin"}, {"role": "manager"}, {"client_id": "glowlab"}):
        assert api.put(f"/users/{me}", headers=headers, json=body).status_code in (403, 422)


def test_a01_a_customer_cannot_claim_someone_elses_tracking_number(api):
    """Registrar una salida con el tracking de otro lo convertía en «pedido de tu sesión» para el agente."""
    customer = login(api, "cliente@example.com")
    body = {"sku_id": 1, "quantity": 1, "exit_type": "dispatch", "tracking_number": "VICTIMA-12345", "warehouse": "LA"}
    assert api.post("/inventory/orders/outbound", headers=customer, json=body).status_code == 403


@pytest.mark.parametrize("who,expected", [("sin mapeo", 403), ("otra persona del equipo", 403), ("su responsable", 202)])
def test_a01_an_rfp_section_is_only_decided_by_the_account_of_its_approver(api, monkeypatch, who, expected):
    import routes.rfp_intake as rfp
    from mock_data.pipelines.rfp_intake.graph import run_ticket
    from mock_data.pipelines.rfp_intake.repository import RfpRepository
    from mock_data.pipelines.rfp_intake.response_graph import run_response
    from mock_data.pipelines.rfp_intake.approval_graph import start_approval
    from langgraph.checkpoint.sqlite import SqliteSaver
    from pathlib import Path
    import sqlite3

    monkeypatch.setattr(rfp, "_enqueue_approval_resume", lambda *a: None)
    if who != "sin mapeo":
        monkeypatch.setenv("RFP_APPROVERS", json.dumps({"Ana Whitfield": "ana@example.com"}))
    repo = RfpRepository(api.app.state.engine)
    tid = str(repo.create_ticket(rfp_id="R", raw_pdf_path="/x", original_filename="x", uploaded_by=None).ticket_id)
    markdown = (Path(__file__).resolve().parents[3] / "tests/pipelines/fixtures/rfp/formal-modaviva.md").read_text(encoding="utf-8")
    run_ticket(tid, repo, llm_fn=None, converter=lambda _p: markdown)
    run_response(tid, repo, llm_fn=None)
    start_approval(tid, repo, llm_fn=None, checkpointer=SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)))
    email = "ana@example.com" if who == "su responsable" else "otra@example.com"
    response = api.post(f"/rfp-intake/tickets/{tid}/sections/warehouse/decision", headers=login(api, email, "manager"),
                        json={"decision": "approve", "approver": "Ana Whitfield", "section_version": 1})   # dice ser Ana
    assert response.status_code == expected, response.text


# ---------------------------------------------------------------- A04/A05 · gateway interno
def test_a05_the_internal_gateway_token_is_not_enough_to_upload(api):
    files = {"file": ("x.csv", b"a,b\n1,2\n", "text/csv")}
    headers = {"x-internal-task-token": "internal-token-of-the-gateway", "x-task-id": ANY_UUID}
    assert api.post("/internal/tasks/analyze", headers=headers, files=files).status_code == 401
    assert api.post("/internal/tasks/analyze", headers={**headers, **login(api, "cliente@example.com")}, files=files).status_code == 403


# ---------------------------------------------------------------- A07 · autenticación
def test_a07_password_guessing_is_throttled_even_from_many_addresses(api):
    login(api, "victima@example.com")
    codes = [api.post("/auth/login", json={"email": "victima@example.com", "password": f"intento-{i}"},
                      headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code for i in range(12)]
    assert codes[:5] == [401] * 5 and codes[-1] == 429
    blocked = api.post("/auth/login", json={"email": "victima@example.com", "password": ACCOUNT})
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
    assert api.post("/auth/login", json={"email": "otra@example.com", "password": "x"}).status_code == 401   # no bloquea al resto


def test_a07_changing_the_password_revokes_the_tokens_already_issued(api):
    stolen = login(api, "ana@example.com")
    assert api.post("/auth/change-password", headers=stolen, json={
        "current_password": ACCOUNT, "new_password": "another-Horse-battery-9"}).status_code == 200
    assert api.get("/auth/me", headers=stolen).status_code == 401
    fresh = api.post("/auth/login", json={"email": "ana@example.com", "password": "another-Horse-battery-9"}).json()["access_token"]
    assert api.get("/auth/me", headers={"Authorization": f"Bearer {fresh}"}).status_code == 200


def test_a07_login_takes_the_same_path_for_unknown_accounts(api, monkeypatch):
    import routes.auth as auth

    checks = []
    original = auth.bcrypt.verify
    monkeypatch.setattr(auth.bcrypt, "verify", lambda *a, **k: (checks.append(1), original(*a, **k))[1])
    api.post("/auth/login", json={"email": "nadie@example.com", "password": "x"})
    assert checks == [1]


# ---------------------------------------------------------------- A05 / A09
def test_a05_security_headers_on_every_response(api):
    for path in ("/health", "/suppliers", "/no-existe"):
        headers = api.get(path).headers
        assert headers["x-content-type-options"] == "nosniff" and headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "no-referrer" and "default-src 'none'" in headers["content-security-policy"]


def test_a05_api_documentation_is_off_in_production(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    from main import create_app

    app = create_app(tmp_path / "p.json")
    assert app.openapi_url is None and app.docs_url is None and app.redoc_url is None


def test_a09_security_events_are_logged_without_secrets(api, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="trackflow.security"):
        headers = login(api, "cliente@example.com")
        api.post("/auth/login", json={"email": "cliente@example.com", "password": "contraseña-incorrecta"})
        api.get("/suppliers", headers=headers)
    events = [json.loads(r.getMessage()) for r in caplog.records if r.name == "trackflow.security"]
    assert {"login_failed", "access_denied"} <= {e["event"] for e in events}
    text = json.dumps(events)
    assert "contraseña-incorrecta" not in text and headers["Authorization"][7:] not in text and "cliente@example.com" not in text


# ---------------------------------------------------------------- mínimo privilegio y cuenta robada
def test_a05_the_mcp_service_account_only_reaches_what_its_tools_need(api):
    service = login(api, "mcp@example.com", "service")
    allowed = {route for route in http_routes(api.app) - PUBLIC - CUSTOMER if route != "GET /rfp-intake/events"
               and call(api, route, service).status_code != 403}
    assert allowed == {"GET /api/incidents", "POST /api/incidents", "GET /api/incidents/summary", "GET /api/incidents/{incident_id}",
                       "PATCH /api/incidents/{incident_id}/status", "GET /inventory/products", "GET /inventory/products/{product_id}"}


@pytest.mark.parametrize("change", [{"email": "ladron@example.com"}, {"password": "contraseña-del-ladrón"}])
def test_a07_a_stolen_token_is_not_enough_to_take_over_the_account(api, change):
    stolen = login(api, "victima@example.com")
    me = next(d.doc_id for d in api.app.state.db.table("users").all() if d["email"] == "victima@example.com")
    assert api.put(f"/users/{me}", headers=stolen, json=change).status_code == 403
    assert api.put(f"/users/{me}", headers=stolen, json={**change, "current_password": "adivinada"}).status_code == 403
    assert api.put(f"/users/{me}", headers=stolen, json={**change, "current_password": ACCOUNT}).status_code == 200


def test_a07_passwords_longer_than_bcrypt_allows_are_rejected_not_500(api):
    assert api.post("/users", json={"email": "x@example.com", "password": "á" * 40}).status_code == 422
    login(api, "y@example.com")
    assert api.post("/auth/login", json={"email": "y@example.com", "password": "x" * 100}).status_code == 401


def test_a07_reset_emails_cannot_be_used_to_flood_a_mailbox(api, monkeypatch):
    import routes.auth as auth

    sent = []
    monkeypatch.setattr(auth, "request_password_reset", lambda app, email: sent.append(email))
    answers = {api.post("/auth/forgot-password", json={"email": "victima@example.com"}).text for _ in range(10)}
    assert len(sent) == 3 and len(answers) == 1                                          # y no se revela el límite


def test_a02_tokens_with_another_algorithm_or_no_signature_are_rejected(api):
    import jwt

    claims = {"sub": "1", "role": "admin", "purpose": "access", "ver": 0, "exp": 4102444800}
    login(api, "ana@example.com")
    for forged in (jwt.encode(claims, None, algorithm="none"), jwt.encode(claims, "otra-clave-de-más-de-32-caracteres!!", algorithm="HS256"),
                   jwt.encode(claims, "test-secret-key-with-at-least-32-bytes" * 2, algorithm="HS512")):
        assert api.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_a01_the_accounts_cli_is_the_only_way_to_grant_roles_and_brands(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SUPPLIERS_DB_PATH", str(tmp_path / "users.json"))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-with-at-least-32-bytes")
    from threading import RLock

    import accounts
    from database import open_database
    from user_service import create_user, get_user_by_email

    with open_database(tmp_path / "users.json") as db:
        create_user(db, RLock(), "marca@example.com", ACCOUNT, {})
    assert accounts.main(["set-role", "nadie@example.com", "admin"]) == 1
    assert accounts.main(["set-role", "marca@example.com", "manager"]) == 0
    assert accounts.main(["set-clients", "marca@example.com", "glowlab_cosmetics"]) == 0
    with open_database(tmp_path / "users.json") as db:
        user = get_user_by_email(db, "marca@example.com")
    assert (user.role.value, user.client_ids) == ("manager", ["glowlab_cosmetics"])


def test_a01_the_agent_session_comes_from_the_account(api):
    from routes.agent import build_session_context
    from user_service import get_user_by_email

    request = type("R", (), {"app": api.app})()
    login(api, "cliente@example.com"), login(api, "cx@example.com", "manager"), login(api, "mcp@example.com", "service")
    sessions = {email: build_session_context(request, get_user_by_email(api.app.state.db, email))
                for email in ("cliente@example.com", "cx@example.com", "mcp@example.com")}
    assert [s.staff for s in sessions.values()] == [False, True, False] and not any(s.client_ids for s in sessions.values())
