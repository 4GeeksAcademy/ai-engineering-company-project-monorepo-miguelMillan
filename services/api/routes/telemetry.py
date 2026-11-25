import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from jsonschema import Draft7Validator, FormatChecker
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import insert
from sqlmodel import Session

from database import get_db
from security import require_staff
from models import TelemetryEventRecord

try:
    from services.telemetry.analysis import (
        auth_failure_rate_by_day,
        error_rate_by_time,
        event_volume_by_time,
    )
except ModuleNotFoundError as error:
    if error.name != "services":
        raise
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from services.telemetry.analysis import (
        auth_failure_rate_by_day,
        error_rate_by_time,
        event_volume_by_time,
    )


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
REPORT_CACHE_TTL_SECONDS = 60
_report_cache: dict[
    tuple[datetime | None, datetime | None], tuple[float, dict[str, object]]
] = {}


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

    stored = 0
    already_present = 0
    if rows:
        from sqlalchemy.dialects.postgresql import insert as postgresql_insert
        from sqlalchemy import text
        
        # Verificar cuáles event_id ya existen antes de insertar (para distinguir stored vs already_present)
        event_ids = [row['event_id'] for row in rows]
        existing_ids_result = session.execute(
            text(
                "SELECT event_id FROM telemetry_events WHERE event_id = ANY(:ids)"
            ),
            {"ids": event_ids}
        )
        existing_ids = {row[0] for row in existing_ids_result}
        already_present = len(existing_ids)
        
        # Insertar con ON CONFLICT DO NOTHING (idempotente)
        stmt = postgresql_insert(TelemetryEventRecord).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=['event_id'])
        session.execute(stmt)
        session.commit()
        
        stored = len(rows) - already_present

    received = len(batch.events)
    logger.info(
        "Processed telemetry batch: received=%d stored=%d already_present=%d rejected=%d",
        received,
        stored,
        already_present,
        rejected,
    )
    return {"received": received, "stored": stored, "rejected": rejected}


def _resolve_report_period(
    start_date: AwareDatetime | None,
    end_date: AwareDatetime | None,
) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    resolved_start = start_date or now - timedelta(days=7)
    resolved_end = end_date or now
    if resolved_start > resolved_end:
        raise HTTPException(
            status_code=422,
            detail="start_date debe ser anterior o igual a end_date.",
        )
    return resolved_start, resolved_end


@router.get("/report", dependencies=[Depends(require_staff)])
def telemetry_report(
    start_date: AwareDatetime | None = Query(default=None),
    end_date: AwareDatetime | None = Query(default=None),
    session: Session = Depends(get_db),
):
    cache_key = (start_date, end_date)
    cached = _report_cache.get(cache_key)
    if cached is not None:
        expires_at, report = cached
        if time.monotonic() < expires_at:
            return report
        _report_cache.pop(cache_key, None)

    resolved_start, resolved_end = _resolve_report_period(start_date, end_date)
    metrics = {
        "events_per_day": event_volume_by_time(session, resolved_start, resolved_end),
        "error_rate_by_type": error_rate_by_time(session, resolved_start, resolved_end),
        "auth_failure_rate": auth_failure_rate_by_day(
            session,
            resolved_start,
            resolved_end,
        ),
    }
    report = {
        "period": {
            "from": resolved_start.isoformat(),
            "to": resolved_end.isoformat(),
        },
        "metrics": metrics,
    }
    _report_cache[cache_key] = (
        time.monotonic() + REPORT_CACHE_TTL_SECONDS,
        report,
    )
    return report