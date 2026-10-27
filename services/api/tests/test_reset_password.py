import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from conftest import TEST_SECRET


def store_reset_token(api_client, user_id, *, purpose="password_reset", expires_at=None):
    token = jwt.encode(
        {
            "sub": str(user_id),
            "purpose": purpose,
            "jti": "single-use-reset",
            "exp": expires_at or datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        TEST_SECRET,
        algorithm="HS256",
    )
    api_client.app.state.db.table("users").update(
        {"password_reset_hash": hashlib.sha256(token.encode()).hexdigest()}, doc_ids=[user_id]
    )
    return token


def test_reset_password_changes_credentials_and_consumes_token(api_client, registered_account):
    token = store_reset_token(api_client, registered_account["id"])

    response = api_client.post("/auth/reset-password", json={"token": token, "new_password": "new-password-123"})

    assert response.status_code == 200
    assert api_client.post(
        "/auth/login", json={"email": registered_account["email"], "password": "new-password-123"}
    ).status_code == 200
    assert api_client.post("/auth/reset-password", json={"token": token, "new_password": "another-password"}).status_code == 400


def test_reset_password_accepts_maximum_supported_password_length(api_client, registered_account):
    token = store_reset_token(api_client, registered_account["id"])

    response = api_client.post("/auth/reset-password", json={"token": token, "new_password": "a" * 72})

    assert response.status_code == 200


@pytest.mark.parametrize("token_kind", ["invalid", "expired", "wrong-purpose"])
def test_reset_password_rejects_invalid_tokens(api_client, registered_account, token_kind):
    if token_kind == "invalid":
        token = "not-a-jwt"
    elif token_kind == "expired":
        token = store_reset_token(
            api_client,
            registered_account["id"],
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    else:
        token = store_reset_token(api_client, registered_account["id"], purpose="access")

    response = api_client.post("/auth/reset-password", json={"token": token, "new_password": "new-password-123"})

    assert response.status_code == 400


def test_reset_password_rejects_password_outside_limits(api_client, registered_account):
    token = store_reset_token(api_client, registered_account["id"])

    assert api_client.post("/auth/reset-password", json={"token": token, "new_password": "short"}).status_code == 422
    assert api_client.post("/auth/reset-password", json={"token": token, "new_password": "a" * 73}).status_code == 422
    assert api_client.post("/auth/reset-password", json={"token": token}).status_code == 422