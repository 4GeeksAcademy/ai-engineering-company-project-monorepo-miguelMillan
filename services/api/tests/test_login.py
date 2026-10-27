def test_login_accepts_json_and_oauth_form_and_authenticates(api_client, register_account):
    register_account(email="login@example.com")

    json_login = api_client.post(
        "/auth/login", json={"email": "login@example.com", "password": "correct-horse-battery"}
    )
    form_login = api_client.post(
        "/auth/login", data={"username": "login@example.com", "password": "correct-horse-battery"}
    )

    assert json_login.status_code == 200
    assert api_client.get("/auth/me", headers={"Authorization": f"Bearer {form_login.json()['access_token']}"}).status_code == 200


def test_login_rejects_missing_or_malformed_credentials(api_client, register_account):
    register_account(email="valid@example.com")

    assert api_client.post("/auth/login", json={"email": "valid@example.com"}).status_code == 422
    assert api_client.post(
        "/auth/login", content=b"{invalid", headers={"content-type": "application/json"}
    ).status_code == 422
    assert api_client.post(
        "/auth/login", data={"email": "valid@example.com"}
    ).status_code == 422


def test_login_rejects_wrong_unknown_and_inactive_accounts(api_client, register_account):
    register_account(email="inactive@example.com")
    api_client.app.state.db.table("users").update({"is_active": False}, lambda item: item["email"] == "inactive@example.com")

    assert api_client.post(
        "/auth/login", json={"email": "inactive@example.com", "password": "correct-horse-battery"}
    ).status_code == 401
    assert api_client.post(
        "/auth/login", json={"email": "inactive@example.com", "password": "wrong-password"}
    ).status_code == 401
    assert api_client.post(
        "/auth/login", json={"email": "unknown@example.com", "password": "correct-horse-battery"}
    ).status_code == 401