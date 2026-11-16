"""SQL-backed Pandas metrics for TrackFlow operational telemetry."""

from __future__ import annotations

from datetime import datetime
import json

import pandas as pd
from sqlmodel import Session, select

from models import TelemetryEventRecord

ERROR_EVENT_TYPES = frozenset(
    {
        "auth_access_denied",
        "frontend_data_load_failed",
        "frontend_unhandled_error",
        "frontend_chunk_load_failed",
        "inventory_order_write_failed",
        "login_failed",
        "outbound_stock_insufficient",
    }
)
AUTH_EVENT_TYPES = frozenset({"login_failed", "login_succeeded"})


def _load_events(
    session: Session,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    event_types: frozenset[str] | None = None,
    include_tags: bool = False,
) -> pd.DataFrame:
    columns = [TelemetryEventRecord.timestamp, TelemetryEventRecord.event_type]
    names = ["timestamp", "event_type"]
    if include_tags:
        columns.append(TelemetryEventRecord.tags)
        names.append("tags")
    query = select(*columns)
    if start_date is not None:
        query = query.where(
            TelemetryEventRecord.timestamp >= pd.to_datetime(start_date, utc=True).to_pydatetime()
        )
    if end_date is not None:
        query = query.where(
            TelemetryEventRecord.timestamp <= pd.to_datetime(end_date, utc=True).to_pydatetime()
        )
    if event_types is not None:
        query = query.where(TelemetryEventRecord.event_type.in_(event_types))
    prepared = pd.DataFrame(session.exec(query).all(), columns=names)
    prepared["timestamp"] = pd.to_datetime(prepared["timestamp"], utc=True, errors="raise")
    return prepared


def _with_tags(
    events: pd.DataFrame,
) -> pd.DataFrame:
    tags = pd.json_normalize(events["tags"].tolist()).set_index(events.index)
    return pd.concat([events.drop(columns=["tags"]), tags], axis=1)


def _serializable_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def event_volume_by_time(
    session: Session,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    frequency: str = "1D",
) -> list[dict[str, object]]:
    """Return event volume grouped into UTC time windows and event type."""

    prepared = _load_events(session, start_date, end_date)
    result = (
        prepared.groupby(
            [pd.Grouper(key="timestamp", freq=frequency), "event_type"],
            as_index=False,
        )
        .size()
        .rename(columns={"size": "event_count"})
        .sort_values(["timestamp", "event_type"])
    )
    return _serializable_records(result)


def error_rate_by_time(
    session: Session,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    frequency: str = "1D",
) -> list[dict[str, object]]:
    """Return total events, operational errors, and error rate by UTC window."""

    prepared = _load_events(session, start_date, end_date).assign(
        is_error=lambda frame: frame["event_type"].isin(ERROR_EVENT_TYPES)
    )
    result = (
        prepared.groupby(pd.Grouper(key="timestamp", freq=frequency))
        .agg(
            total_events=("event_type", "count"),
            error_events=("is_error", "sum"),
        )
        .reset_index()
        .assign(
            error_rate=lambda frame: frame["error_events"]
            .div(frame["total_events"])
            .mul(100)
        )
        .sort_values("timestamp")
    )
    return _serializable_records(result)


def auth_failure_rate_by_day(
    session: Session,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict[str, object]]:
    """Return daily failed-login rate over all login attempts."""

    prepared = (
        _load_events(session, start_date, end_date, event_types=AUTH_EVENT_TYPES)
        .assign(is_failed=lambda frame: frame["event_type"].eq("login_failed"))
    )
    result = (
        prepared.groupby(pd.Grouper(key="timestamp", freq="1D"))
        .agg(
            login_attempts=("event_type", "count"),
            failed_logins=("is_failed", "sum"),
        )
        .reset_index()
        .assign(
            failure_rate=lambda frame: frame["failed_logins"]
            .div(frame["login_attempts"])
            .mul(100)
        )
        .sort_values("timestamp")
    )
    return _serializable_records(result)


def latency_by_route(
    session: Session,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    frequency: str = "1D",
) -> list[dict[str, object]]:
    """Return request count and mean latency for completed API requests."""

    api_events = _with_tags(
        _load_events(
            session,
            start_date,
            end_date,
            event_types=frozenset({"api_request_completed"}),
            include_tags=True,
        )
    )
    if api_events.empty:
        return []

    required_columns = {"route_group", "duration_ms"}
    missing_columns = required_columns.difference(api_events.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"Telemetry events are missing required columns: {missing}")

    result = (
        api_events[["timestamp", "route_group", "duration_ms"]]
        .assign(duration_ms=lambda frame: pd.to_numeric(frame["duration_ms"], errors="raise"))
        .groupby(
            [pd.Grouper(key="timestamp", freq=frequency), "route_group"],
            as_index=False,
        )
        .agg(
            request_count=("duration_ms", "count"),
            mean_latency_ms=("duration_ms", "mean"),
        )
        .sort_values(["timestamp", "route_group"])
    )
    return _serializable_records(result)


def dominant_event_types(
    session: Session,
    limit: int = 10,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict[str, object]]:
    """Return the most frequent event types with their share of total volume."""

    if limit < 1:
        raise ValueError("limit must be greater than zero")

    prepared = _load_events(session, start_date, end_date)
    result = (
        prepared.groupby("event_type", as_index=False)
        .agg(event_count=("event_type", "count"))
        .assign(
            volume_share=lambda frame: frame["event_count"]
            .div(frame["event_count"].sum())
            .mul(100)
        )
        .sort_values(["event_count", "event_type"], ascending=[False, True])
        .head(limit)
    )
    return _serializable_records(result)
