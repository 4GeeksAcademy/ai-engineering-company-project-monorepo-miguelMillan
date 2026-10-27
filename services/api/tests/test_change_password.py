def test_change_password_updates_credentials(api_client, registered_account, access_token):
    headers = {"Authorization": f"Bearer {access_token}"}

    response = api_client.post(
        "/auth/change-password",
        json={"current_password": "correct-horse-battery", "new_password": "new-password-123"},
        headers=headers,
    )

    assert response.status_code == 200
    assert api_client.post(
        "/auth/login", json={"email": registered_account["email"], "password": "new-password-123"}
    ).status_code == 200
    assert api_client.post(
        "/auth/login", json={"email": registered_account["email"], "password": "correct-horse-battery"}
    ).status_code == 401


def test_change_password_accepts_minimum_length(api_client, access_token):
    response = api_client.post(
        "/auth/change-password",
        json={"current_password": "correct-horse-battery", "new_password": "12345678"},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200


def test_change_password_rejects_wrong_current_password_and_missing_session(api_client, access_token):
    payload = {"current_password": "wrong-password", "new_password": "new-password-123"}

    assert api_client.post("/auth/change-password", json=payload, headers={"Authorization": f"Bearer {access_token}"}).status_code == 400
    assert api_client.post("/auth/change-password", json=payload).status_code == 401
    assert api_client.post(
        "/auth/change-password", json={"current_password": "correct-horse-battery"}, headers={"Authorization": f"Bearer {access_token}"}
    ).status_code == 422