from datetime import date
from typing import Any

from sqlalchemy import text
from sqlmodel import Session


def get_weekly_warehouse_client_performance(
    session: Session,
    week_start: date | None = None,
) -> dict[str, Any] | None:
    if week_start is None:
        week_start = session.execute(
            text(
                "SELECT MAX(week_start) "
                "FROM reporting.weekly_warehouse_client_performance"
            )
        ).scalar_one_or_none()
        if week_start is None:
            return None

    records = session.execute(
        text(
            """
            SELECT
                warehouse,
                client_id,
                inbound_units_count,
                outbound_orders_count,
                stockout_events_count,
                discrepancy_events_count,
                discrepancy_rate
            FROM reporting.weekly_warehouse_client_performance
            WHERE week_start = :week_start
            ORDER BY warehouse, client_id
            """
        ),
        {"week_start": week_start},
    ).mappings()

    entries = [
        {
            "warehouse": record["warehouse"],
            "client_id": record["client_id"],
            "inbound_units_count": record["inbound_units_count"],
            "outbound_orders_count": record["outbound_orders_count"],
            "stockout_events_count": record["stockout_events_count"],
            "discrepancy_events_count": record["discrepancy_events_count"],
            "discrepancy_rate": float(record["discrepancy_rate"]),
        }
        for record in records
    ]
    return {"week_start": week_start, "entries": entries}
