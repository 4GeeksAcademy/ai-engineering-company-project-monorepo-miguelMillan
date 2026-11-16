from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from prefect import flow, get_run_logger, runtime, task
from prefect.states import State
from prefect.tasks import task_input_hash
from sqlalchemy import create_engine, text
from sqlalchemy.exc import (
    DBAPIError,
    InterfaceError,
    OperationalError,
    TimeoutError as SQLAlchemyTimeoutError,
)

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mock_data.process.weekly_performance import (
    TransformResult,
    calculate_discrepancy_rate,
    calculate_event_counts,
    calculate_inbound_units,
    prepare_weekly_events,
)


_CACHE_EXPIRATION = timedelta(hours=1)
_PIPELINE_LOCK_NAMESPACE = 7412
_LOGGER = logging.getLogger(__name__)


def resolve_week_start(value: date | None) -> date:
    if value is not None:
        if value.weekday() != 0:
            raise ValueError("week_start debe ser un lunes de la semana ISO.")
        return value

    today_utc = datetime.now(timezone.utc).date()
    current_week_start = today_utc - timedelta(days=today_utc.weekday())
    return current_week_start - timedelta(days=7)


def _database_url(environment_variable: str) -> str:
    value = os.environ.get(environment_variable)
    if not value:
        raise RuntimeError(f"Configura {environment_variable} para ejecutar el pipeline.")
    return value


def _database_fingerprint(environment_variable: str) -> str:
    return hashlib.sha256(_database_url(environment_variable).encode()).hexdigest()


@contextmanager
def _weekly_pipeline_run_lock(week_start: str) -> Generator[bool, None, None]:
    engine = create_engine(
        _database_url("REPORTING_WRITE_DATABASE_URL"),
        pool_pre_ping=True,
    )
    lock_key = f"weekly-warehouse-client-performance:{week_start}"
    acquired = False
    try:
        with engine.connect() as connection:
            acquired = bool(
                connection.execute(
                    text(
                        "SELECT pg_try_advisory_lock(:namespace, hashtext(:lock_key))"
                    ),
                    {"namespace": _PIPELINE_LOCK_NAMESPACE, "lock_key": lock_key},
                ).scalar_one()
            )
            connection.commit()
            try:
                yield acquired
            finally:
                if acquired:
                    connection.execute(
                        text(
                            "SELECT pg_advisory_unlock(:namespace, hashtext(:lock_key))"
                        ),
                        {"namespace": _PIPELINE_LOCK_NAMESPACE, "lock_key": lock_key},
                    )
                    connection.commit()
    finally:
        engine.dispose()


def _retry_transient_database_errors(
    _task: object,
    _task_run: object,
    state: State,
) -> bool:
    if not state.is_failed():
        return False

    error = state.result(raise_on_failure=False)
    if isinstance(error, SQLAlchemyTimeoutError):
        return True
    if isinstance(error, (DBAPIError, OperationalError, InterfaceError)):
        sqlstate = (
            getattr(error.orig, "sqlstate", "")
            or getattr(error.orig, "pgcode", "")
            or ""
        )
        return error.connection_invalidated or sqlstate.startswith(("08", "40"))
    return False


# Tres reintentos con 10 s de espera cubren cortes breves y acotan la espera total.
@task(
    name="extract-telemetry-events",
    retries=3,
    retry_delay_seconds=10,
    retry_condition_fn=_retry_transient_database_errors,
    cache_key_fn=task_input_hash,
    cache_expiration=_CACHE_EXPIRATION,
    persist_result=True,
)
def extract_telemetry_events(
    week_start: str,
    source_fingerprint: str,
) -> list[dict[str, Any]]:
    del source_fingerprint
    start_date = date.fromisoformat(week_start)
    start_at = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end_at = start_at + timedelta(days=7)
    engine = create_engine(_database_url("REPORTING_READ_DATABASE_URL"), pool_pre_ping=True)
    query = text(
        """
        SELECT event_id, timestamp, event_type, tags
        FROM public.telemetry_events
        WHERE timestamp >= :start_at
          AND timestamp < :end_at
          AND event_type IN (
              'inbound_order_created',
              'outbound_order_created',
              'stock_threshold_triggered',
              'inventory_discrepancy_detected'
          )
        ORDER BY timestamp, event_id
        """
    )
    try:
        with engine.begin() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            records = connection.execute(
                query,
                {"start_at": start_at, "end_at": end_at},
            ).mappings()
            events = []
            for record in records:
                tags = record["tags"]
                if not isinstance(tags, dict):
                    raise ValueError(f"tags inválido para el evento {record['event_id']}.")
                events.append(
                    {
                        "event_id": str(record["event_id"]),
                        "timestamp": record["timestamp"].isoformat(),
                        "event_type": record["event_type"],
                        "tags": {
                            key: tags.get(key)
                            for key in ("warehouse", "client_id", "quantity")
                        },
                    }
                )
    finally:
        engine.dispose()

    get_run_logger().info("Extracción completada: %s eventos.", len(events))
    return events


@flow(name="extract-weekly-performance")
def extract_weekly_performance(
    week_start: str,
    source_fingerprint: str,
) -> list[dict[str, Any]]:
    return extract_telemetry_events(week_start, source_fingerprint)


@flow(name="transform-weekly-performance")
def transform_weekly_performance(
    events: list[dict[str, Any]],
    week_start: str,
) -> TransformResult:
    frame = prepare_weekly_events(events, week_start)
    if frame.empty:
        return TransformResult(rows=[], records_transformed=0)

    inbound = calculate_inbound_units(frame)
    counts = calculate_event_counts(frame)
    grouped = inbound.merge(
        calculate_discrepancy_rate(counts),
        on=["warehouse", "client_id"],
        validate="one_to_one",
    )
    grouped["week_start"] = week_start

    rows = []
    for row in grouped.to_dict(orient="records"):
        rows.append(
            {
                "warehouse": row["warehouse"],
                "client_id": row["client_id"],
                "week_start": row["week_start"],
                "inbound_units_count": int(row["inbound_units_count"]),
                "outbound_orders_count": int(row["outbound_orders_count"]),
                "stockout_events_count": int(row["stockout_events_count"]),
                "discrepancy_events_count": int(row["discrepancy_events_count"]),
                "discrepancy_rate": float(row["discrepancy_rate"]),
            }
        )

    get_run_logger().info(
        "Transformación completada: %s eventos únicos, %s combinaciones.",
        len(frame),
        len(rows),
    )
    return TransformResult(rows=rows, records_transformed=len(frame))


def _sanitize_run_error(error: Exception) -> str:
    message = str(error)
    for environment_variable in (
        "REPORTING_READ_DATABASE_URL",
        "REPORTING_WRITE_DATABASE_URL",
    ):
        database_url = os.environ.get(environment_variable)
        if database_url:
            message = message.replace(database_url, "[REDACTED_DATABASE_URL]")
    message = re.sub(
        r"(?i)(postgres(?:ql)?(?:\+\w+)?://[^:/@\s]+):[^@\s]+@",
        r"\1:[REDACTED]@",
        message,
    )
    return f"{type(error).__name__}: {message}"[:1000]


# Tres reintentos permiten recuperar fallos transitorios del servicio de reporting.
@task(
    name="record-pipeline-run",
    retries=3,
    retry_delay_seconds=10,
    retry_condition_fn=_retry_transient_database_errors,
)
def record_pipeline_run(
    run_id: str,
    prefect_run_id: str,
    week_start: str,
    trigger: str,
    started_at: datetime,
    status: Literal["running", "succeeded", "failed", "unknown", "skipped"],
    phase: Literal["extracting", "transforming", "loading", "completed"],
    records_read: int,
    records_transformed: int,
    records_written: int,
    finished_at: datetime | None = None,
    error_message: str | None = None,
) -> None:
    engine = create_engine(
        _database_url("REPORTING_WRITE_DATABASE_URL"),
        pool_pre_ping=True,
    )
    upsert = text(
        """
        INSERT INTO reporting.pipeline_runs (
            run_id,
            prefect_run_id,
            pipeline_name,
            week_start,
            started_at,
            finished_at,
            status,
            records_read,
            records_transformed,
            records_written,
            error_message,
            trigger,
            phase,
            last_checkpoint_at
        )
        VALUES (
            CAST(:run_id AS uuid),
            CAST(:prefect_run_id AS uuid),
            'weekly_warehouse_client_performance',
            :week_start,
            :started_at,
            :finished_at,
            :status,
            :records_read,
            :records_transformed,
            :records_written,
            :error_message,
            :trigger,
            :phase,
            now()
        )
        ON CONFLICT (run_id) DO UPDATE SET
            prefect_run_id = EXCLUDED.prefect_run_id,
            finished_at = EXCLUDED.finished_at,
            status = EXCLUDED.status,
            records_read = EXCLUDED.records_read,
            records_transformed = EXCLUDED.records_transformed,
            records_written = EXCLUDED.records_written,
            error_message = EXCLUDED.error_message,
            phase = EXCLUDED.phase,
            last_checkpoint_at = now()
        """
    )
    try:
        with engine.begin() as connection:
            connection.execute(
                upsert,
                {
                    "run_id": run_id,
                    "prefect_run_id": prefect_run_id,
                    "week_start": week_start,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "status": status,
                    "records_read": records_read,
                    "records_transformed": records_transformed,
                    "records_written": records_written,
                    "error_message": error_message,
                    "trigger": trigger,
                    "phase": phase,
                },
            )
    finally:
        engine.dispose()


# Tres reintentos con 10 s de espera cubren cortes breves y acotan la espera total.
@task(
    name="load-weekly-performance",
    retries=3,
    retry_delay_seconds=10,
    retry_condition_fn=_retry_transient_database_errors,
    cache_key_fn=task_input_hash,
    cache_expiration=_CACHE_EXPIRATION,
    persist_result=True,
)
def load_weekly_performance(
    rows: list[dict[str, Any]],
    week_start: str,
    destination_fingerprint: str,
) -> int:
    del destination_fingerprint
    engine = create_engine(
        _database_url("REPORTING_WRITE_DATABASE_URL"),
        pool_pre_ping=True,
    )
    lock = text("SELECT pg_advisory_xact_lock(hashtext(:week_start)::bigint)")
    create_stage = text(
        """
        CREATE TEMP TABLE weekly_warehouse_client_performance_stage (
            warehouse text NOT NULL,
            client_id text NOT NULL,
            week_start date NOT NULL,
            inbound_units_count integer NOT NULL,
            outbound_orders_count integer NOT NULL,
            stockout_events_count integer NOT NULL,
            discrepancy_events_count integer NOT NULL,
            discrepancy_rate numeric NOT NULL,
            PRIMARY KEY (warehouse, client_id, week_start)
        ) ON COMMIT DROP
        """
    )
    stage_insert = text(
        """
        INSERT INTO weekly_warehouse_client_performance_stage (
            warehouse,
            client_id,
            week_start,
            inbound_units_count,
            outbound_orders_count,
            stockout_events_count,
            discrepancy_events_count,
            discrepancy_rate
        )
        VALUES (
            :warehouse,
            :client_id,
            :week_start,
            :inbound_units_count,
            :outbound_orders_count,
            :stockout_events_count,
            :discrepancy_events_count,
            :discrepancy_rate
        )
        """
    )
    upsert_from_stage = text(
        """
        INSERT INTO reporting.weekly_warehouse_client_performance AS target (
            warehouse,
            client_id,
            week_start,
            inbound_units_count,
            outbound_orders_count,
            stockout_events_count,
            discrepancy_events_count,
            discrepancy_rate
        )
        SELECT
            warehouse,
            client_id,
            week_start,
            inbound_units_count,
            outbound_orders_count,
            stockout_events_count,
            discrepancy_events_count,
            discrepancy_rate
        FROM weekly_warehouse_client_performance_stage
        ON CONFLICT (warehouse, client_id, week_start) DO UPDATE SET
            inbound_units_count = EXCLUDED.inbound_units_count,
            outbound_orders_count = EXCLUDED.outbound_orders_count,
            stockout_events_count = EXCLUDED.stockout_events_count,
            discrepancy_events_count = EXCLUDED.discrepancy_events_count,
            discrepancy_rate = EXCLUDED.discrepancy_rate,
            computed_at = now()
        WHERE (
            target.inbound_units_count,
            target.outbound_orders_count,
            target.stockout_events_count,
            target.discrepancy_events_count,
            target.discrepancy_rate
        ) IS DISTINCT FROM (
            EXCLUDED.inbound_units_count,
            EXCLUDED.outbound_orders_count,
            EXCLUDED.stockout_events_count,
            EXCLUDED.discrepancy_events_count,
            EXCLUDED.discrepancy_rate
        )
        """
    )
    delete_stale = text(
        """
        DELETE FROM reporting.weekly_warehouse_client_performance AS target
        WHERE target.week_start = :week_start
          AND NOT EXISTS (
              SELECT 1
              FROM weekly_warehouse_client_performance_stage AS stage
              WHERE stage.warehouse = target.warehouse
                AND stage.client_id = target.client_id
                AND stage.week_start = target.week_start
          )
        """
    )
    try:
        with engine.begin() as connection:
            connection.execute(lock, {"week_start": week_start})
            connection.execute(create_stage)
            if rows:
                connection.execute(stage_insert, rows)
            connection.execute(upsert_from_stage)
            connection.execute(delete_stale, {"week_start": week_start})
    finally:
        engine.dispose()

    get_run_logger().info("Carga completada: %s filas.", len(rows))
    return len(rows)


@task(
    name="write-evaluation-snapshot",
    cache_key_fn=task_input_hash,
    cache_expiration=_CACHE_EXPIRATION,
    persist_result=True,
)
def write_evaluation_snapshot(
    week_start: str,
    rows: list[dict[str, Any]],
) -> str:
    output_directory = Path(__file__).resolve().parents[1] / "eval"
    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = output_directory / f"weekly-warehouse-client-performance-{week_start}.json"
    output_path.write_text(
        json.dumps({"week_start": week_start, "entries": rows}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    get_run_logger().info("Snapshot de evaluación escrito en %s.", output_path)
    return str(output_path)


@flow(name="load-weekly-performance")
def load_weekly_performance_subflow(
    rows: list[dict[str, Any]],
    week_start: str,
    destination_fingerprint: str,
) -> int:
    return load_weekly_performance(rows, week_start, destination_fingerprint)


@flow(name="export-weekly-evaluation")
def export_weekly_evaluation(
    week_start: str,
    rows: list[dict[str, Any]],
) -> str:
    return write_evaluation_snapshot(week_start, rows)


def _execute_weekly_pipeline_run(
    run_id: str,
    prefect_run_id: str,
    week_start: str,
    trigger: Literal["scheduled", "manual", "late_event"],
    started_at: datetime,
) -> dict[str, int | str]:
    records_read = 0
    records_transformed = 0
    records_written = 0
    phase: Literal["extracting", "transforming", "loading", "completed"] = "extracting"
    load_completed = False

    try:
        record_pipeline_run(
            run_id=run_id,
            prefect_run_id=prefect_run_id,
            week_start=week_start,
            trigger=trigger,
            started_at=started_at,
            status="running",
            phase=phase,
            records_read=records_read,
            records_transformed=records_transformed,
            records_written=records_written,
        )
        source_fingerprint = _database_fingerprint("REPORTING_READ_DATABASE_URL")
        destination_fingerprint = _database_fingerprint("REPORTING_WRITE_DATABASE_URL")
        extracted = extract_weekly_performance(
            week_start,
            source_fingerprint,
        )
        records_read = len(extracted)
        phase = "transforming"
        record_pipeline_run(
            run_id=run_id,
            prefect_run_id=prefect_run_id,
            week_start=week_start,
            trigger=trigger,
            started_at=started_at,
            status="running",
            phase=phase,
            records_read=records_read,
            records_transformed=records_transformed,
            records_written=records_written,
        )
        transformed = transform_weekly_performance(
            extracted,
            week_start,
        )
        records_transformed = transformed.records_transformed
        record_pipeline_run(
            run_id=run_id,
            prefect_run_id=prefect_run_id,
            week_start=week_start,
            trigger=trigger,
            started_at=started_at,
            status="running",
            phase="loading",
            records_read=records_read,
            records_transformed=records_transformed,
            records_written=records_written,
        )
        phase = "loading"
        records_written = load_weekly_performance_subflow(
            transformed.rows,
            week_start,
            destination_fingerprint,
        )
        load_completed = True
        phase = "completed"
        record_pipeline_run(
            run_id=run_id,
            prefect_run_id=prefect_run_id,
            week_start=week_start,
            trigger=trigger,
            started_at=started_at,
            status="succeeded",
            phase=phase,
            records_read=records_read,
            records_transformed=records_transformed,
            records_written=records_written,
            finished_at=datetime.now(timezone.utc),
        )
    except Exception as error:
        failure_state = record_pipeline_run(
            run_id=run_id,
            prefect_run_id=prefect_run_id,
            week_start=week_start,
            trigger=trigger,
            started_at=started_at,
            status="unknown" if phase == "loading" or load_completed else "failed",
            phase=phase,
            records_read=records_read,
            records_transformed=records_transformed,
            records_written=records_written,
            finished_at=datetime.now(timezone.utc),
            error_message=_sanitize_run_error(error),
            return_state=True,
        )
        if failure_state.is_failed():
            get_run_logger().error(
                "No se pudo persistir el estado final de la corrida %s.",
                run_id,
            )
        raise

    snapshot_state = export_weekly_evaluation(
        week_start,
        transformed.rows,
        return_state=True,
    )
    if snapshot_state.is_failed():
        get_run_logger().warning(
            "El subflow opcional de snapshot falló; la carga principal ya se completó."
        )

    return {
        "run_id": run_id,
        "prefect_run_id": prefect_run_id,
        "week_start": week_start,
        "trigger": trigger,
        "status": "succeeded",
        "records_read": len(extracted),
        "records_transformed": transformed.records_transformed,
        "records_written": records_written,
        "evaluation_snapshot_state": snapshot_state.name,
    }


@flow(name="weekly-warehouse-client-performance", log_prints=True)
def weekly_warehouse_client_performance_flow(
    week_start: date | None = None,
    trigger: Literal["scheduled", "manual", "late_event"] = "scheduled",
    run_id: str | None = None,
) -> dict[str, int | str]:
    resolved_week_start = resolve_week_start(week_start).isoformat()
    run_id = run_id or str(uuid4())
    prefect_run_id = str(runtime.flow_run.id)
    started_at = datetime.now(timezone.utc)

    with _weekly_pipeline_run_lock(resolved_week_start) as lock_acquired:
        if not lock_acquired:
            record_pipeline_run(
                run_id=run_id,
                prefect_run_id=prefect_run_id,
                week_start=resolved_week_start,
                trigger=trigger,
                started_at=started_at,
                status="skipped",
                phase="extracting",
                records_read=0,
                records_transformed=0,
                records_written=0,
                finished_at=datetime.now(timezone.utc),
                error_message="already_running",
            )
            return {
                "run_id": run_id,
                "prefect_run_id": prefect_run_id,
                "week_start": resolved_week_start,
                "trigger": trigger,
                "status": "skipped",
                "records_read": 0,
                "records_transformed": 0,
                "records_written": 0,
            }

        return _execute_weekly_pipeline_run(
            run_id=run_id,
            prefect_run_id=prefect_run_id,
            week_start=resolved_week_start,
            trigger=trigger,
            started_at=started_at,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Ejecuta el pipeline semanal de desempeño.")
    parser.add_argument(
        "--week-start",
        type=date.fromisoformat,
        help="Lunes ISO a procesar (YYYY-MM-DD); por defecto usa la última semana cerrada.",
    )
    parser.add_argument(
        "--trigger",
        choices=("scheduled", "manual", "late_event"),
        default="manual",
    )
    arguments = parser.parse_args()
    weekly_warehouse_client_performance_flow(
        week_start=arguments.week_start,
        trigger=arguments.trigger,
    )


if __name__ == "__main__":
    main()
