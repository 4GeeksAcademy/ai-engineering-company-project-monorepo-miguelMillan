import pytest
from fastapi.testclient import TestClient


TEST_SECRET = "test-secret-key-with-at-least-32-bytes"


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "2")
    from main import create_app

    with TestClient(create_app(tmp_path / "auth-api.json")) as client:
        yield client


@pytest.fixture
def register_account(api_client):
    def create_account(email="user@example.com", password="correct-horse-battery", profile=None):
        payload = {"email": email, "password": password}
        if profile:
            payload.update(profile)
        return api_client.post("/users", json=payload)

    return create_account


@pytest.fixture
def registered_account(register_account):
    response = register_account()
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def access_token(api_client, registered_account):
    response = api_client.post(
        "/auth/login",
        json={"email": registered_account["email"], "password": "correct-horse-battery"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


@pytest.fixture
def authenticated_client(api_client, access_token):
    api_client.headers.update({"Authorization": f"Bearer {access_token}"})
    return api_client