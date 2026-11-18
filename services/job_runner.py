"""State management for standalone scheduled jobs in ``public.job_runs``."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

JOB_NAME = "nightly_export"


def _database_url(*variables: str) -> str:
    for variable in variables:
        value = os.environ.get(variable)
        if value:
            return value
    raise RuntimeError(
        "Configura REPORTING_WRITE_DATABASE_URL o DATABASE_URL para el job."
    )


def create_write_engine() -> Engine:
    return create_engine(
        _database_url("REPORTING_WRITE_DATABASE_URL", "DATABASE_URL"),
        pool_pre_ping=True,
    )


def create_read_engine() -> Engine:
    return create_engine(
        _database_url("REPORTING_READ_DATABASE_URL", "DATABASE_URL"),
        pool_pre_ping=True,
    )


def has_processing_lock(engine: Engine) -> bool:
    """Return whether any nightly_export run is currently processing."""
    with engine.connect() as connection:
        return bool(
            connection.execute(
                text(
                    """SELECT EXISTS (
                           SELECT 1 FROM public.job_runs
                           WHERE job_name = :job_name AND status = 'processing'
                       )"""
                ),
                {"job_name": JOB_NAME},
            ).scalar_one()
        )


def has_completed_for_date(engine: Engine, target_date: date) -> bool:
    """Check completion by both job name and target date."""
    with engine.connect() as connection:
        return bool(
            connection.execute(
                text(
                    """SELECT EXISTS (
                           SELECT 1 FROM public.job_runs
                           WHERE job_name = :job_name
                             AND target_date = :target_date
                             AND status = 'completed'
                       )"""
                ),
                {"job_name": JOB_NAME, "target_date": target_date},
            ).scalar_one()
        )


def create_job(engine: Engine, target_date: date) -> tuple[UUID, str | None]:
    """Atomically check row-state lock/idempotency and start a run.

    A table lock closes the check-then-insert race without a separate lock table,
    column, or advisory-lock mechanism. The durable lock itself is the processing
    status row. Cancelled and duplicate invocations are also persisted as their
    own terminal rows, while returning the reason for an informational log.
    """
    with engine.begin() as connection:
        connection.execute(text("LOCK TABLE public.job_runs IN SHARE ROW EXCLUSIVE MODE"))
        processing = connection.execute(
            text(
                """SELECT EXISTS (
                       SELECT 1 FROM public.job_runs
                       WHERE job_name = :job_name AND status = 'processing'
                   )"""
            ),
            {"job_name": JOB_NAME},
        ).scalar_one()
        if processing:
            cancelled_id = connection.execute(
                text(
                    """INSERT INTO public.job_runs (
                           job_name, target_date, status, finished_at, error_message
                       ) VALUES (
                           :job_name, :target_date, 'failed', :finished_at,
                           'Cancelled: another nightly_export run is processing.'
                       ) RETURNING id"""
                ),
                {
                    "job_name": JOB_NAME,
                    "target_date": target_date,
                    "finished_at": datetime.now(timezone.utc),
                },
            ).scalar_one()
            return cancelled_id, "processing"

        completed = connection.execute(
            text(
                """SELECT EXISTS (
                       SELECT 1 FROM public.job_runs
                       WHERE job_name = :job_name AND target_date = :target_date
                         AND status = 'completed'
                   )"""
            ),
            {"job_name": JOB_NAME, "target_date": target_date},
        ).scalar_one()
        if completed:
            duplicate_id = connection.execute(
                text(
                    """INSERT INTO public.job_runs (
                           job_name, target_date, status, finished_at
                       ) VALUES (:job_name, :target_date, 'completed', :finished_at)
                       RETURNING id"""
                ),
                {
                    "job_name": JOB_NAME,
                    "target_date": target_date,
                    "finished_at": datetime.now(timezone.utc),
                },
            ).scalar_one()
            return duplicate_id, "completed"

        run_id = connection.execute(
            text(
                """INSERT INTO public.job_runs (job_name, target_date, status)
                   VALUES (:job_name, :target_date, 'pending') RETURNING id"""
            ),
            {"job_name": JOB_NAME, "target_date": target_date},
        ).scalar_one()
        connection.execute(
            text(
                """UPDATE public.job_runs
                   SET status = 'processing', started_at = :started_at
                   WHERE id = :id"""
            ),
            {"id": run_id, "started_at": datetime.now(timezone.utc)},
        )
        return run_id, None


def update_job(
    engine: Engine,
    run_id: UUID,
    *,
    status: str,
    error_message: str | None = None,
) -> None:
    """Update the state/timestamps of a job run."""
    if status not in {"pending", "processing", "completed", "failed"}:
        raise ValueError(f"Estado de job no permitido: {status}")
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                """UPDATE public.job_runs
                   SET status = :status,
                       started_at = CASE WHEN :status = 'processing'
                                         THEN COALESCE(started_at, :now)
                                         ELSE started_at END,
                       finished_at = CASE WHEN :status IN ('completed', 'failed')
                                          THEN :now ELSE NULL END,
                       error_message = :error_message
                   WHERE id = :id"""
            ),
            {
                "id": run_id,
                "status": status,
                "now": now,
                "error_message": error_message,
            },
        )


def fail_processing_jobs(engine: Engine, error_message: str) -> None:
    """Mark processing runs failed if an exception escapes the runner.

    An abrupt process/host termination cannot execute this recovery path; such
    rows intentionally remain processing and require operator intervention.
    """
    with engine.begin() as connection:
        connection.execute(
            text(
                """UPDATE public.job_runs
                   SET status = 'failed', finished_at = :now,
                       error_message = :error_message
                   WHERE job_name = :job_name AND status = 'processing'"""
            ),
            {
                "job_name": JOB_NAME,
                "now": datetime.now(timezone.utc),
                "error_message": error_message[:1000],
            },
        )
