import csv
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from scripts.nightly_export import export_telemetry, resolve_target_date


class _Result:
    def __init__(self, rows=()):
        self._rows = rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self._rows)


class _Connection:
    def __init__(self, rows=()):
        self.rows = rows
        self.calls = 0

    def execute(self, _query, _parameters=None):
        self.calls += 1
        return _Result(self.rows if self.calls == 2 else ())


class _Engine:
    def __init__(self, rows=()):
        self.connection = _Connection(rows)

    def begin(self):
        engine = self

        class _Transaction:
            def __enter__(self):
                return engine.connection

            def __exit__(self, *_args):
                return False

        return _Transaction()


def test_resolve_target_date_from_value_and_environment(monkeypatch):
    monkeypatch.setenv("TARGET_DATE", "2026-11-16")
    assert resolve_target_date() == date(2026, 11, 16)
    assert resolve_target_date("2026-11-17") == date(2026, 11, 17)


@pytest.mark.parametrize("value", ["2026-1-17", "2026-11-17T00:00:00", "2026-W47-2"])
def test_resolve_target_date_rejects_non_iso_calendar_date(value):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        resolve_target_date(value)


def test_export_telemetry_writes_daily_csv_and_preserves_tags(tmp_path: Path):
    engine = _Engine(
        [
            {
                "event_id": "event-1",
                "timestamp": datetime(2026, 11, 17, 12, tzinfo=timezone.utc),
                "session_id": None,
                "user_id": "user-1",
                "event_type": "inbound_order_created",
                "schema_version": "1.0",
                "request_id": None,
                "tags": {"warehouse": "LA", "quantity": 2},
            }
        ]
    )
    output = export_telemetry(date(2026, 11, 17), engine, tmp_path)

    assert output.name == "telemetry_2026-11-17.csv"
    with output.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == 1
    assert rows[0]["timestamp"] == "2026-11-17T12:00:00+00:00"
    assert rows[0]["tags"] == '{"quantity": 2, "warehouse": "LA"}'


def test_existing_snapshot_is_not_overwritten(tmp_path: Path):
    output = tmp_path / "telemetry_2026-11-17.csv"
    output.write_text("original", encoding="utf-8")

    result = export_telemetry(date(2026, 11, 17), _Engine(), tmp_path)

    assert result == output
    assert output.read_text(encoding="utf-8") == "original"
