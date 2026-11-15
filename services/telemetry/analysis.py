"""Pure Pandas metrics for TrackFlow operational telemetry."""

from __future__ import annotations

from datetime import datetime
import json

import pandas as pd


REQUIRED_COLUMNS = frozenset({"timestamp", "event_type"})
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


def _prepare_events(
    events: pd.DataFrame,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> pd.DataFrame:
    missing_columns = REQUIRED_COLUMNS.difference(events.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"Telemetry events are missing required columns: {missing}")

    prepared = events.copy()
    prepared["timestamp"] = pd.to_datetime(prepared["timestamp"], utc=True, errors="raise")
    if start_date is not None:
        start_timestamp = pd.Timestamp(start_date)
        if start_timestamp.tzinfo is None:
            start_timestamp = start_timestamp.tz_localize("UTC")
        else:
            start_timestamp = start_timestamp.tz_convert("UTC")
        prepared = prepared.loc[prepared["timestamp"] >= start_timestamp]
    if end_date is not None:
        end_timestamp = pd.Timestamp(end_date)
        if end_timestamp.tzinfo is None:
            end_timestamp = end_timestamp.tz_localize("UTC")
        else:
            end_timestamp = end_timestamp.tz_convert("UTC")
        prepared = prepared.loc[prepared["timestamp"] <= end_timestamp]
    return prepared


def _with_tags(
    events: pd.DataFrame,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> pd.DataFrame:
    prepared = _prepare_events(events, start_date, end_date)
    if "tags" not in prepared.columns:
        return prepared

    tags = pd.json_normalize(prepared["tags"].tolist()).set_index(prepared.index)
    return pd.concat([prepared.drop(columns=["tags"]), tags], axis=1)


def _serializable_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def event_volume_by_time(
    events: pd.DataFrame,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    frequency: str = "1D",
) -> list[dict[str, object]]:
    """Return event volume grouped into UTC time windows and event type."""

    prepared = _prepare_events(events, start_date, end_date)
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
    events: pd.DataFrame,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    frequency: str = "1D",
) -> list[dict[str, object]]:
    """Return total events, operational errors, and error rate by UTC window."""

    prepared = _prepare_events(events, start_date, end_date).assign(
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
    events: pd.DataFrame,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict[str, object]]:
    """Return daily failed-login rate over all login attempts."""

    prepared = (
        _prepare_events(events, start_date, end_date)
        .loc[lambda frame: frame["event_type"].isin(AUTH_EVENT_TYPES)]
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
    events: pd.DataFrame,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    frequency: str = "1D",
) -> list[dict[str, object]]:
    """Return request count and mean latency for completed API requests."""

    prepared = _with_tags(events, start_date, end_date)
    api_events = prepared.loc[
        prepared["event_type"].eq("api_request_completed")
    ]
    if api_events.empty:
        return []

    required_columns = {"route_group", "duration_ms"}
    missing_columns = required_columns.difference(prepared.columns)
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
    events: pd.DataFrame,
    limit: int = 10,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict[str, object]]:
    """Return the most frequent event types with their share of total volume."""

    if limit < 1:
        raise ValueError("limit must be greater than zero")

    prepared = _prepare_events(events, start_date, end_date)
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
