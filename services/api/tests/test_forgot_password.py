def test_forgot_password_schedules_reset_for_existing_user(api_client, register_account, monkeypatch):
    register_account(email="recover@example.com")
    sent = []
    monkeypatch.setattr("password_recovery.send_reset_email", lambda email, link: sent.append((email, link)))

    response = api_client.post("/auth/forgot-password", json={"email": "recover@example.com"})

    assert response.status_code == 200
    assert sent[0][0] == "recover@example.com"
    assert "token=" in sent[0][1]


def test_forgot_password_does_not_enumerate_unknown_email(api_client, monkeypatch):
    sent = []
    monkeypatch.setattr("password_recovery.send_reset_email", lambda email, link: sent.append((email, link)))

    known = api_client.post("/auth/forgot-password", json={"email": "known@example.com"})
    unknown = api_client.post("/auth/forgot-password", json={"email": "unknown@example.com"})

    assert known.json()["message"] == unknown.json()["message"]
    assert sent == []


def test_forgot_password_rejects_missing_or_invalid_email(api_client):
    assert api_client.post("/auth/forgot-password", json={"email": "not-an-email"}).status_code == 422
    assert api_client.post("/auth/forgot-password", json={}).status_code == 422