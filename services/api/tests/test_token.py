from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from conftest import TEST_SECRET


def make_token(user_id, **claims):
    return jwt.encode(
        {"sub": str(user_id), "exp": datetime.now(timezone.utc) + timedelta(minutes=5), **claims},
        TEST_SECRET,
        algorithm="HS256",
    )


def test_access_token_authenticates_active_user(api_client, registered_account, access_token):
    response = api_client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})

    assert response.status_code == 200
    assert response.json()["email"] == registered_account["email"]


@pytest.mark.parametrize("token_kind", ["malformed", "expired", "wrong-purpose", "wrong-signature", "unknown-user"])
def test_invalid_access_tokens_are_rejected(api_client, registered_account, token_kind):
    if token_kind == "malformed":
        token = "not-a-jwt"
    elif token_kind == "expired":
        token = jwt.encode(
            {"sub": str(registered_account["id"]), "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
            TEST_SECRET,
            algorithm="HS256",
        )
    elif token_kind == "wrong-purpose":
        token = make_token(registered_account["id"], purpose="password_reset")
    elif token_kind == "wrong-signature":
        token = jwt.encode(
            {"sub": str(registered_account["id"]), "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            "different-secret-with-at-least-32-bytes",
            algorithm="HS256",
        )
    else:
        token = make_token(99999, purpose="access")

    response = api_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_inactive_user_token_is_rejected(api_client, registered_account, access_token):
    api_client.app.state.db.table("users").update({"is_active": False}, doc_ids=[registered_account["id"]])

    response = api_client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})

    assert response.status_code == 401