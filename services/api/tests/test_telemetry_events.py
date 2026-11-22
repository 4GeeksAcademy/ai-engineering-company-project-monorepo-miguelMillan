"""POST /telemetry/events: los eventos inválidos se rechazan uno a uno y el resto del lote se persiste."""

import uuid
from datetime import datetime, timezone

from sqlalchemy.dialects import postgresql

from routes.telemetry import TelemetryBatch, receive_events


class FakeSession:
    """Sesión mínima: el endpoint usa INSERT … ON CONFLICT de PostgreSQL, así que se captura la sentencia."""

    def __init__(self, existing_ids=()):
        self.existing_ids = list(existing_ids)
        self.inserted = []
        self.commits = 0

    def execute(self, statement, params=None):
        if params is not None:  # SELECT de event_id ya presentes
            return [(event_id,) for event_id in self.existing_ids]
        compiled = statement.compile(dialect=postgresql.dialect())
        self.inserted = sorted({value for key, value in compiled.params.items() if key.startswith("event_type")})
        return None

    def commit(self):
        self.commits += 1


def event(event_type, properties, **overrides):
    base = {
        "eventId": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "sessionId": str(uuid.uuid4()),
        "userId": None,
        "event_type": event_type,
        "schemaVersion": "1.0.0",
        "requestId": str(uuid.uuid4()),
        "properties": properties,
    }
    return {**base, **overrides}


INBOUND = {
    "warehouse": "zaragoza",
    "client_id": "purestep_footwear",
    "product_id": "CLT-SNK-W-42-Z",
    "product_category": "fashion",
    "quantity": 3,
    "order_id": 16,
}


def test_mixed_batch_stores_valid_business_event_and_rejects_invalid_ones():
    batch = TelemetryBatch(events=[
        event("inbound_order_created", INBOUND),
        event("backoffice_section_viewed", {"section": "inventory_inbound"}),
        event("inbound_order_created", {**INBOUND, "warehouse": "LA"}),  # almacén sin normalizar (JSON Schema)
        event("pedido_creado", {"section": "inventory_inbound"}),  # fuera de la allowlist (Pydantic)
        event("backoffice_section_viewed", {"section": "inventory_inbound"}, userId="cliente@example.com"),  # PII
    ])
    session = FakeSession()

    result = receive_events(batch, session=session)

    assert result == {"received": 5, "stored": 2, "rejected": 3}
    assert session.inserted == ["backoffice_section_viewed", "inbound_order_created"]
    assert session.commits == 1


def test_batch_with_only_invalid_events_writes_nothing():
    batch = TelemetryBatch(events=[event("pedido_creado", {}), {"event_type": "inbound_order_created"}])
    session = FakeSession()

    assert receive_events(batch, session=session) == {"received": 2, "stored": 0, "rejected": 2}
    assert session.commits == 0


def test_replayed_events_are_not_counted_as_stored_again():
    business = event("inbound_order_created", INBOUND)
    session = FakeSession(existing_ids=[uuid.UUID(business["eventId"])])

    assert receive_events(TelemetryBatch(events=[business]), session=session) == {"received": 1, "stored": 0, "rejected": 0}
