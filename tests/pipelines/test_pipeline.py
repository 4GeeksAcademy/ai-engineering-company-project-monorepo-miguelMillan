from uuid import UUID

import pandas as pd
import pytest

from mock_data.process.weekly_performance import (
    calculate_discrepancy_rate,
    calculate_event_counts,
    calculate_inbound_units,
    prepare_weekly_events,
)


def telemetry_event(index, event_type, quantity=1, warehouse="los_angeles", client="fashion-co"):
    return {
        "event_id": str(UUID(int=index)),
        "timestamp": "2026-11-10T12:00:00Z",
        "event_type": event_type,
        "tags": {
            "warehouse": warehouse,
            "client_id": client,
            "product_id": "SKU-001",
            "product_category": "fashion",
            "quantity": quantity,
        },
    }


@pytest.fixture
def prepared_events():
    events = [
        telemetry_event(1, "inbound_order_created", 12),
        telemetry_event(2, "inbound_order_created", 8),
        telemetry_event(3, "outbound_order_created", 100),
        telemetry_event(4, "outbound_order_created", 50),
        telemetry_event(5, "stock_threshold_triggered", 3),
        telemetry_event(6, "stock_threshold_triggered", 2),
        telemetry_event(7, "inventory_discrepancy_detected", 5),
        telemetry_event(8, "inbound_order_created", 7, warehouse="zaragoza"),
        telemetry_event(9, "inbound_order_created", 9, client="cosmetics-co"),
    ]
    return pd.DataFrame(events).assign(
        warehouse=[event["tags"]["warehouse"] for event in events],
        client_id=[event["tags"]["client_id"] for event in events],
        inbound_units=[12, 8, 0, 0, 0, 0, 0, 7, 9],
        outbound_orders=[0, 0, 1, 1, 0, 0, 0, 0, 0],
        stockout_events=[0, 0, 0, 0, 1, 1, 0, 0, 0],
        discrepancy_events=[0, 0, 0, 0, 0, 0, 1, 0, 0],
    )


def test_inbound_units_sum_quantities_without_mixing_clients_or_warehouses(prepared_events):
    result = calculate_inbound_units.fn(prepared_events)
    assert result.to_dict(orient="records") == [
        {"warehouse": "los_angeles", "client_id": "cosmetics-co", "inbound_units_count": 9},
        {"warehouse": "los_angeles", "client_id": "fashion-co", "inbound_units_count": 20},
        {"warehouse": "zaragoza", "client_id": "fashion-co", "inbound_units_count": 7},
    ]


def test_counts_measure_events_not_quantities(prepared_events):
    result = calculate_event_counts.fn(prepared_events)
    assert result.to_dict(orient="records") == [
        {
            "warehouse": "los_angeles", "client_id": "cosmetics-co",
            "outbound_orders_count": 0, "stockout_events_count": 0,
            "discrepancy_events_count": 0,
        },
        {
            "warehouse": "los_angeles", "client_id": "fashion-co",
            "outbound_orders_count": 2, "stockout_events_count": 2,
            "discrepancy_events_count": 1,
        },
        {
            "warehouse": "zaragoza", "client_id": "fashion-co",
            "outbound_orders_count": 0, "stockout_events_count": 0,
            "discrepancy_events_count": 0,
        },
    ]


def test_discrepancy_rate_matches_hand_calculation_and_zero_denominator():
    counts = pd.DataFrame([
        {"outbound_orders_count": 2, "discrepancy_events_count": 1},
        {"outbound_orders_count": 0, "discrepancy_events_count": 3},
        {"outbound_orders_count": 4, "discrepancy_events_count": 0},
    ])
    result = calculate_discrepancy_rate.fn(counts)
    assert result["discrepancy_rate"].tolist() == [0.5, 0.0, 0.0]
    assert "discrepancy_rate" not in counts.columns


@pytest.mark.parametrize("quantity", [None, "12", True, -1, 1.5])
def test_prepare_rejects_invalid_inbound_quantity(quantity):
    with pytest.raises(ValueError, match="quantity inválido"):
        prepare_weekly_events.fn(
            [telemetry_event(1, "inbound_order_created", quantity)], "2026-11-09"
        )


def test_prepare_rejects_missing_client():
    event = telemetry_event(1, "outbound_order_created", client=None)
    with pytest.raises(ValueError, match="client_id inválido"):
        prepare_weekly_events.fn([event], "2026-11-09")


def test_prepare_deduplicates_events_and_normalizes_iso_week_boundary():
    event = telemetry_event(1, "inbound_order_created", 12)
    event["timestamp"] = "2026-11-08T23:30:00-01:00"
    prepared = prepare_weekly_events.fn([event, event.copy()], "2026-11-09")
    assert len(prepared) == 1
    assert prepared["timestamp"].iloc[0] == pd.Timestamp("2026-11-09T00:30:00Z")
    assert calculate_inbound_units.fn(prepared)["inbound_units_count"].tolist() == [12]
    with pytest.raises(ValueError, match="fuera de la semana"):
        prepare_weekly_events.fn([event], "2026-11-02")


def test_empty_input_produces_no_kpi_rows():
    prepared = prepare_weekly_events.fn([], "2026-11-09")
    assert calculate_inbound_units.fn(prepared).empty
    counts = calculate_event_counts.fn(prepared)
    assert calculate_discrepancy_rate.fn(counts).empty
