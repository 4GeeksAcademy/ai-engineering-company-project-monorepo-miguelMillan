from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import pandas as pd
from prefect import task
from prefect.tasks import task_input_hash


_EVENT_TYPES = {
    "inbound_order_created",
    "outbound_order_created",
    "stock_threshold_triggered",
    "inventory_discrepancy_detected",
}
_WAREHOUSES = {"los_angeles", "zaragoza"}
_CACHE_EXPIRATION = timedelta(hours=1)
_DIMENSIONS = ["warehouse", "client_id"]


@dataclass(frozen=True)
class TransformResult:
    rows: list[dict[str, Any]]
    records_transformed: int


@task(
    name="prepare-weekly-events",
    cache_key_fn=task_input_hash,
    cache_expiration=_CACHE_EXPIRATION,
    persist_result=True,
)
def prepare_weekly_events(events: list[dict[str, Any]], week_start: str) -> pd.DataFrame:
    start_date = date.fromisoformat(week_start)
    if start_date.weekday() != 0:
        raise ValueError("week_start debe ser un lunes de la semana ISO.")
    frame = pd.DataFrame(events)
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "warehouse", "client_id", "inbound_units", "outbound_orders",
                "stockout_events", "discrepancy_events",
            ]
        )

    required = {"event_id", "timestamp", "event_type", "tags"}
    if not required.issubset(frame.columns):
        raise ValueError("La extracción requiere event_id, timestamp, event_type y tags.")
    if frame["event_id"].isna().any():
        raise ValueError("La extracción contiene event_id nulo.")
    if not frame["event_type"].isin(_EVENT_TYPES).all():
        raise ValueError("La extracción contiene event_type inválido.")
    frame["timestamp"] = pd.to_datetime(
        frame["timestamp"], utc=True, errors="coerce", format="mixed"
    )
    if frame["timestamp"].isna().any():
        raise ValueError("La extracción contiene timestamps inválidos.")
    frame = (
        frame.sort_values("timestamp")
        .drop_duplicates(subset="event_id", keep="last")
        .reset_index(drop=True)
    )
    start_at = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end_at = start_at + timedelta(days=7)
    if ((frame["timestamp"] < start_at) | (frame["timestamp"] >= end_at)).any():
        raise ValueError("La extracción contiene eventos fuera de la semana solicitada.")

    warehouses: list[str] = []
    clients: list[str] = []
    quantities: list[int] = []
    for event in frame.to_dict(orient="records"):
        tags = event["tags"]
        if not isinstance(tags, dict):
            raise ValueError(f"tags inválido para el evento {event['event_id']}.")
        warehouse = tags.get("warehouse")
        client_id = tags.get("client_id")
        if not isinstance(warehouse, str) or warehouse not in _WAREHOUSES:
            raise ValueError(f"warehouse inválido para el evento {event['event_id']}.")
        if not isinstance(client_id, str) or not client_id.strip():
            raise ValueError(f"client_id inválido para el evento {event['event_id']}.")

        quantity = 0
        if event["event_type"] == "inbound_order_created":
            raw_quantity = tags.get("quantity")
            if (
                isinstance(raw_quantity, bool)
                or not isinstance(raw_quantity, (int, float))
                or not math.isfinite(raw_quantity)
                or raw_quantity < 0
                or not float(raw_quantity).is_integer()
            ):
                raise ValueError(f"quantity inválido para el evento {event['event_id']}.")
            quantity = int(raw_quantity)
        warehouses.append(warehouse)
        clients.append(client_id.strip())
        quantities.append(quantity)

    frame["warehouse"] = warehouses
    frame["client_id"] = clients
    frame["inbound_units"] = quantities
    frame["outbound_orders"] = (frame["event_type"] == "outbound_order_created").astype(int)
    frame["stockout_events"] = (frame["event_type"] == "stock_threshold_triggered").astype(int)
    frame["discrepancy_events"] = (
        frame["event_type"] == "inventory_discrepancy_detected"
    ).astype(int)
    return frame


@task(
    name="calculate-inbound-units",
    cache_key_fn=task_input_hash,
    cache_expiration=_CACHE_EXPIRATION,
    persist_result=True,
)
def calculate_inbound_units(events: pd.DataFrame) -> pd.DataFrame:
    return events.groupby(_DIMENSIONS, as_index=False, sort=True).agg(
        inbound_units_count=("inbound_units", "sum")
    )


@task(
    name="calculate-event-counts",
    cache_key_fn=task_input_hash,
    cache_expiration=_CACHE_EXPIRATION,
    persist_result=True,
)
def calculate_event_counts(events: pd.DataFrame) -> pd.DataFrame:
    return events.groupby(_DIMENSIONS, as_index=False, sort=True).agg(
        outbound_orders_count=("outbound_orders", "sum"),
        stockout_events_count=("stockout_events", "sum"),
        discrepancy_events_count=("discrepancy_events", "sum"),
    )


@task(
    name="calculate-discrepancy-rate",
    cache_key_fn=task_input_hash,
    cache_expiration=_CACHE_EXPIRATION,
    persist_result=True,
)
def calculate_discrepancy_rate(counts: pd.DataFrame) -> pd.DataFrame:
    result = counts.copy()
    denominator = result["outbound_orders_count"]
    result["discrepancy_rate"] = (
        result["discrepancy_events_count"]
        .div(denominator.where(denominator != 0, 1))
        .where(denominator != 0, 0.0)
    )
    return result
