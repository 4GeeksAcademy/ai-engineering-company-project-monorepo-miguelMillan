from passlib.hash import bcrypt


def test_registration_creates_user_profile_and_hashed_password(api_client, register_account):
    response = register_account(
        email="new-user@example.com",
        profile={"name": "Ana User", "phone": "+1 555 0100", "address": "Los Angeles"},
    )

    assert response.status_code == 201
    user_id = response.json()["id"]
    stored_user = api_client.app.state.db.table("users").get(doc_id=user_id)
    profile = api_client.app.state.db.table("profiles").get(lambda item: item["user_id"] == user_id)
    assert stored_user["role"] == "user"
    assert bcrypt.verify("correct-horse-battery", stored_user["hashed_password"])
    assert profile["name"] == "Ana User"
    assert profile["phone"] == "+1 555 0100"
    assert profile["address"] == "Los Angeles"


def test_registration_accepts_minimum_password_and_omitted_profile(register_account):
    response = register_account(email="minimum@example.com", password="12345678")

    assert response.status_code == 201


def test_registration_rejects_duplicate_and_invalid_accounts(api_client, register_account):
    assert register_account(email="duplicate@example.com").status_code == 201
    assert register_account(email="duplicate@example.com").status_code == 409
    assert register_account(email="not-an-email").status_code == 422
    assert register_account(email="short-password@example.com", password="short").status_code == 422
    assert api_client.post(
        "/users", json={"email": "extra@example.com", "password": "correct-horse-battery", "role": "admin"}
    ).status_code == 422