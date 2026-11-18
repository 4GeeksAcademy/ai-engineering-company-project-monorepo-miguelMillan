#!/usr/bin/env python3
"""Export a daily telemetry backup and invoke the existing KPI pipeline."""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.job_runner import (  # noqa: E402
    create_job,
    create_read_engine,
    create_write_engine,
    update_job,
)

_LOGGER = logging.getLogger("nightly_export")
_CSV_FIELDS = (
    "event_id",
    "timestamp",
    "session_id",
    "user_id",
    "event_type",
    "schema_version",
    "request_id",
    "tags",
)


def resolve_target_date(value: str | None = None) -> date:
    """Resolve the UTC calendar date from TARGET_DATE or yesterday."""
    raw = value if value is not None else os.environ.get("TARGET_DATE")
    if raw:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            raise ValueError("TARGET_DATE debe tener formato YYYY-MM-DD.")
        try:
            return date.fromisoformat(raw)
        except ValueError as error:
            raise ValueError("TARGET_DATE debe tener formato YYYY-MM-DD.") from error
    return datetime.now(timezone.utc).date() - timedelta(days=1)


def export_telemetry(target_date: date, read_engine, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"telemetry_{target_date.isoformat()}.csv"
    if output_path.exists():
        _LOGGER.info(
            "status=processing action=snapshot_exists_no_overwrite path=%s", output_path
        )
        return output_path

    start_at = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
    end_at = start_at + timedelta(days=1)
    query = text(
        """SELECT event_id, timestamp, session_id, user_id, event_type,
                  schema_version, request_id, tags
           FROM public.telemetry_events
           WHERE timestamp >= :start_at AND timestamp < :end_at
           ORDER BY timestamp, event_id"""
    )
    temporary_path = output_path.with_suffix(".csv.tmp")
    try:
        with read_engine.begin() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            records = connection.execute(
                query, {"start_at": start_at, "end_at": end_at}
            ).mappings()
            with temporary_path.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=_CSV_FIELDS)
                writer.writeheader()
                for record in records:
                    writer.writerow(
                        {
                            field: (
                                record[field].isoformat()
                                if isinstance(record[field], datetime)
                                else json.dumps(
                                    record[field], sort_keys=True, ensure_ascii=False
                                )
                                if field == "tags"
                                else record[field]
                            )
                            for field in _CSV_FIELDS
                        }
                    )
        temporary_path.replace(output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    _LOGGER.info("status=processing action=snapshot_exported path=%s", output_path)
    return output_path


def run_pipeline(target_date: date) -> None:
    """Launch the existing Hito 6 CLI as a subprocess, not inside the API."""
    pipeline_script = ROOT / "mock_data" / "pipelines" / "pipeline.py"
    command: Sequence[str] = (
        sys.executable,
        str(pipeline_script),
        "--week-start",
        (target_date - timedelta(days=target_date.weekday())).isoformat(),
        "--trigger",
        "scheduled",
    )
    subprocess.run(command, cwd=ROOT, check=True)


def _safe_error(error: BaseException) -> str:
    message = f"{type(error).__name__}: {error}"
    for variable in ("REPORTING_READ_DATABASE_URL", "REPORTING_WRITE_DATABASE_URL", "DATABASE_URL"):
        value = os.environ.get(variable)
        if value:
            message = message.replace(value, "[REDACTED_DATABASE_URL]")
    return message[:1000]


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s job=nightly_export status=%(levelname)s %(message)s",
    )
    run_id = None
    write_engine = read_engine = None
    try:
        target_date = resolve_target_date()
        write_engine = create_write_engine()
        run_id, skipped = create_job(write_engine, target_date)
        if skipped == "processing":
            _LOGGER.info("status=skipped reason=processing target_date=%s", target_date)
            return 0
        if skipped == "completed":
            _LOGGER.info("status=skipped reason=duplicate target_date=%s", target_date)
            return 0

        _LOGGER.info("status=processing event=start target_date=%s run_id=%s", target_date, run_id)
        read_engine = create_read_engine()
        export_telemetry(target_date, read_engine, ROOT / "mock_data" / "raw")
        run_pipeline(target_date)
        update_job(write_engine, run_id, status="completed")
        _LOGGER.info("status=completed target_date=%s run_id=%s", target_date, run_id)
        return 0
    except BaseException as error:
        safe_message = _safe_error(error)
        if write_engine is not None and run_id is not None:
            try:
                update_job(
                    write_engine,
                    run_id,
                    status="failed",
                    error_message=safe_message,
                )
            except Exception:
                _LOGGER.exception(
                    "status=failed unable_to_persist_failure run_id=%s", run_id
                )
        _LOGGER.error(
            "status=failed target_date=%s error=%s",
            locals().get("target_date", "unresolved"),
            safe_message,
            exc_info=True,
        )
        raise
    finally:
        if read_engine is not None:
            read_engine.dispose()
        if write_engine is not None:
            write_engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
