"""State management for standalone scheduled jobs in ``public.job_runs``."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import Engine

JOB_NAME = "nightly_export"
_LOGGER = logging.getLogger("nightly_export.job_runner")


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


CANCELLED_MESSAGE = "Cancelled: another nightly_export run is processing."
PROCESSING_LOCK_INDEX = "ux_job_runs_one_processing_per_job"


class _AlreadyCompleted(Exception):
    """Interno: otra ejecución completó el mismo día justo antes de tomar el lock; se deshace el claim."""


def _finish_without_running(engine: Engine, run_id: UUID, status: str, error_message: str | None) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """UPDATE public.job_runs
                   SET status = :status, finished_at = :now, error_message = :error_message
                   WHERE id = :id AND status = 'pending'"""
            ),
            {"id": run_id, "status": status, "now": datetime.now(timezone.utc), "error_message": error_message},
        )


def create_job(engine: Engine, target_date: date) -> tuple[UUID, str | None]:
    """Registra una ejecución y toma el lock con la transición atómica pending → processing.

    El lock ES la fila en ``processing``: el índice único parcial ``ux_job_runs_one_processing_per_job``
    (``job_name`` WHERE ``status = 'processing'``) impide que exista una segunda. No hay LOCK TABLE ni
    advisory locks ni tabla de locks aparte.

    1. Se inserta la fila en ``pending`` y se confirma (estado observable desde otras sesiones).
    2. Un único ``UPDATE … SET status = 'processing' WHERE id = … AND status = 'pending' AND NOT EXISTS
       (día ya completado)`` hace la transición. Si otra ejecución está en ``processing``, Postgres
       rechaza el UPDATE por el índice único (``UniqueViolation``) y esta ejecución se cancela: queda
       ``failed`` con ``CANCELLED_MESSAGE`` y se devuelve ``"processing"``.
    3. Con el lock tomado se vuelve a comprobar ``completed`` (nueva instantánea en READ COMMITTED): si
       otra ejecución terminó el mismo día justo entre medias, se deshace el claim y se registra como
       duplicado (``"completed"``).
    """
    with engine.begin() as connection:
        run_id = connection.execute(
            text(
                """INSERT INTO public.job_runs (job_name, target_date, status)
                   VALUES (:job_name, :target_date, 'pending') RETURNING id"""
            ),
            {"job_name": JOB_NAME, "target_date": target_date},
        ).scalar_one()

    params = {"id": run_id, "job_name": JOB_NAME, "target_date": target_date}
    try:
        with engine.begin() as connection:
            claimed = connection.execute(
                text(
                    """UPDATE public.job_runs
                       SET status = 'processing', started_at = :started_at
                       WHERE id = :id AND status = 'pending'
                         AND NOT EXISTS (
                             SELECT 1 FROM public.job_runs AS done
                             WHERE done.job_name = :job_name AND done.target_date = :target_date
                               AND done.status = 'completed'
                         )
                       RETURNING id"""
                ),
                {**params, "started_at": datetime.now(timezone.utc)},
            ).scalar_one_or_none()
            if claimed is not None:
                completed_meanwhile = connection.execute(
                    text(
                        """SELECT EXISTS (
                               SELECT 1 FROM public.job_runs
                               WHERE job_name = :job_name AND target_date = :target_date
                                 AND status = 'completed'
                           )"""
                    ),
                    params,
                ).scalar_one()
                if completed_meanwhile:
                    raise _AlreadyCompleted
                return run_id, None
    except IntegrityError as error:
        # Única restricción que este UPDATE puede violar: el índice único de `processing`.
        constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
        _LOGGER.info("status=skipped reason=processing lock=unique_index constraint=%s run_id=%s",
                     constraint or PROCESSING_LOCK_INDEX, run_id)
        _finish_without_running(engine, run_id, "failed", CANCELLED_MESSAGE)
        return run_id, "processing"
    except _AlreadyCompleted:
        pass
    except BaseException:
        _finish_without_running(engine, run_id, "failed", "Cancelled: error while acquiring the processing lock.")
        raise

    # El día ya estaba completado: no-op idempotente, registrado como su propia fila.
    _finish_without_running(engine, run_id, "completed", None)
    return run_id, "completed"


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
