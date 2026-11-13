import logging
import os
from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


logger = logging.getLogger(__name__)
TELEMETRY_ENDPOINT = os.environ.get("TELEMETRY_ENDPOINT")


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

    events: list[TelemetryEvent] = Field(min_length=1)


class TelemetryReceived(BaseModel):
    received: int


router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.post("/events", response_model=TelemetryReceived)
def receive_events(batch: TelemetryBatch):
    event_types = [event.event_type for event in batch.events]
    logger.info("Received %d telemetry events: %s", len(event_types), event_types)
    return {"received": len(event_types)}