import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def inventory_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'inventory.db'}")
    monkeypatch.setenv("SECRET_KEY", "inventory-test-secret-with-at-least-32-bytes")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "2")
    from main import create_app

    with TestClient(create_app(tmp_path / "auth-api.json")) as client:
        registration = client.post(
            "/users",
            json={"email": "inventory@example.com", "password": "correct-horse-battery"},
        )
        assert registration.status_code == 201
        login = client.post(
            "/auth/login",
            json={"email": "inventory@example.com", "password": "correct-horse-battery"},
        )
        assert login.status_code == 200
        client.headers.update({"Authorization": f"Bearer {login.json()['access_token']}"})
        yield client


def create_sku(client: TestClient) -> dict:
    response = client.post(
        "/inventory/products",
        json={
            "name": "Wireless headphones",
            "sku": "AUDIO-001",
            "client_name": "SoundWave Electronics",
            "client_id": "soundwave_electronics",
            "category": "electronics",
            "warehouse": "LA",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_threshold_event_is_emitted_once_when_stock_crosses_below_minimum(inventory_client):
    product = create_sku(inventory_client)
    inbound = inventory_client.post(
        "/inventory/orders/inbound",
        json={
            "sku_id": product["id"],
            "quantity": 5,
            "reference": "PO-TEST-1",
            "warehouse": "LA",
        },
    )
    assert inbound.status_code == 201
    threshold = inventory_client.patch(
        f"/inventory/products/{product['id']}/threshold",
        json={"minimum_stock": 3},
    )
    assert threshold.status_code == 200

    first_outbound = inventory_client.post(
        "/inventory/orders/outbound",
        json={
            "sku_id": product["id"],
            "quantity": 3,
            "exit_type": "dispatch",
            "tracking_number": "TRACK-1",
            "warehouse": "LA",
        },
    )
    assert first_outbound.status_code == 201
    signal = first_outbound.json()["telemetry_events"][0]
    assert signal == {
        "event_type": "stock_threshold_triggered",
        "properties": {
            "warehouse": "los_angeles",
            "client_id": "soundwave_electronics",
            "product_id": "AUDIO-001",
            "product_category": "electronics",
            "quantity": 2,
            "threshold": 3,
        },
    }

    second_outbound = inventory_client.post(
        "/inventory/orders/outbound",
        json={
            "sku_id": product["id"],
            "quantity": 1,
            "exit_type": "dispatch",
            "tracking_number": "TRACK-2",
            "warehouse": "LA",
        },
    )
    assert second_outbound.status_code == 201
    assert second_outbound.json()["telemetry_events"] == []


def test_direct_stock_edit_returns_rejection_signal(inventory_client):
    product = create_sku(inventory_client)

    response = inventory_client.put(
        f"/inventory/products/{product['id']}/stock",
        json={"quantity": 4},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "direct_stock_edit_rejected"
    assert detail["telemetry_event"] == {
        "event_type": "direct_stock_edit_rejected",
        "properties": {
            "warehouse": "los_angeles",
            "client_id": "soundwave_electronics",
            "product_id": "AUDIO-001",
            "product_category": "electronics",
            "quantity": 4,
        },
    }


def test_physical_count_persists_and_emits_only_for_a_discrepancy(inventory_client):
    product = create_sku(inventory_client)
    inbound = inventory_client.post(
        "/inventory/orders/inbound",
        json={
            "sku_id": product["id"],
            "quantity": 5,
            "reference": "PO-TEST-2",
            "warehouse": "LA",
        },
    )
    assert inbound.status_code == 201

    audit = inventory_client.post(
        "/inventory/audits",
        json={"sku_id": product["id"], "physical_stock": 2},
    )

    assert audit.status_code == 201
    result = audit.json()
    assert result["system_stock"] == 5
    assert result["physical_stock"] == 2
    assert result["discrepancy_quantity"] == 3
    assert result["telemetry_event"] == {
        "event_type": "inventory_discrepancy_detected",
        "properties": {
            "warehouse": "los_angeles",
            "client_id": "soundwave_electronics",
            "product_id": "AUDIO-001",
            "product_category": "electronics",
            "quantity": 3,
            "direction": "physical_lower",
        },
    }

    reconciled = inventory_client.post(
        "/inventory/audits",
        json={"sku_id": product["id"], "physical_stock": 5},
    )
    assert reconciled.status_code == 201
    assert reconciled.json()["telemetry_event"] is None


def test_database_initialization_migrates_existing_sku_rows(tmp_path):
    from sqlalchemy import create_engine, text

    from database import initialize_sql_database

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-inventory.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE sku ("
                "id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL, "
                "sku VARCHAR(100) NOT NULL, client_name VARCHAR(255) NOT NULL, "
                "category VARCHAR(32) NOT NULL, warehouse VARCHAR(3) NOT NULL)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO sku (id, name, sku, client_name, category, warehouse) "
                "VALUES (1, 'Headphones', 'AUDIO-001', 'SoundWave Electronics', 'electronics', 'LA')"
            )
        )

    initialize_sql_database(engine)
    with engine.connect() as connection:
        migrated = connection.execute(
            text("SELECT client_id, minimum_stock FROM sku WHERE id = 1")
        ).one()
    engine.dispose()

    assert migrated.client_id == "soundwave_electronics"
    assert migrated.minimum_stock is None
