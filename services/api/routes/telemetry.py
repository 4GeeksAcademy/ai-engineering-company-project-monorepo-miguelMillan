import json
import logging
import os
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from jsonschema import Draft7Validator, FormatChecker
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import insert
from sqlmodel import Session

from database import get_db
from models import TelemetryEventRecord


logger = logging.getLogger(__name__)
TELEMETRY_ENDPOINT = os.environ.get("TELEMETRY_ENDPOINT")
TELEMETRY_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "telemetry" / "event-schemas.json"
)
with TELEMETRY_SCHEMA_PATH.open(encoding="utf-8") as schema_file:
    TELEMETRY_SCHEMA = json.load(schema_file)
Draft7Validator.check_schema(TELEMETRY_SCHEMA)
TELEMETRY_SCHEMA_VALIDATOR = Draft7Validator(
    TELEMETRY_SCHEMA,
    format_checker=FormatChecker(),
)


TelemetryEventType = Literal[
    "inbound_order_created",
    "outbound_order_created",
    "stock_threshold_triggered",
    "direct_stock_edit_rejected",
    "inventory_discrepancy_detected",
    "inventory_stock_loss_recorded",
    "login_attempted",
    "login_succeeded",
    "login_failed",
    "session_expired",
    "backoffice_section_viewed",
    "api_request_completed",
    "frontend_unhandled_error",
    "frontend_retry_requested",
    "frontend_web_vital_recorded",
]


class TelemetryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eventId: UUID
    timestamp: AwareDatetime
    sessionId: UUID | None
    userId: str | None = Field(default=None, pattern=r"^u_[0-9a-f]{64}$")
    event_type: TelemetryEventType
    schemaVersion: Literal["1.0.0"]
    requestId: UUID | None
    properties: dict[str, object]


class TelemetryBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[object] = Field(min_length=1)


class TelemetryReceived(BaseModel):
    received: int
    stored: int
    rejected: int


router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.post("/events", response_model=TelemetryReceived)
def receive_events(batch: TelemetryBatch, session: Session = Depends(get_db)):
    rows = []
    rejected = 0

    for raw_event in batch.events:
        try:
            event = TelemetryEvent.model_validate(raw_event)
        except ValidationError:
            rejected += 1
            continue

        if not TELEMETRY_SCHEMA_VALIDATOR.is_valid(raw_event):
            rejected += 1
            continue

        rows.append(
            {
                "event_id": event.eventId,
                "timestamp": event.timestamp,
                "session_id": event.sessionId,
                "user_id": event.userId,
                "event_type": event.event_type,
                "schema_version": event.schemaVersion,
                "request_id": event.requestId,
                "tags": event.properties,
            }
        )

    if rows:
        session.execute(insert(TelemetryEventRecord), rows)
        session.commit()

    stored = len(rows)
    received = len(batch.events)
    logger.info(
        "Processed telemetry batch: received=%d stored=%d rejected=%d",
        received,
        stored,
        rejected,
    )
    return {"received": received, "stored": stored, "rejected": rejected}