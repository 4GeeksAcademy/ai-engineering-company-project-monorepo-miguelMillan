from typing import Any

from sqlalchemy import text
from sqlmodel import Session


def get_latest_pipeline_run(
    session: Session,
    pipeline_name: str,
) -> dict[str, Any] | None:
    record = session.execute(
        text(
            """
            SELECT
                run_id::text AS run_id,
                prefect_run_id::text AS prefect_run_id,
                pipeline_name,
                week_start,
                status,
                trigger,
                started_at,
                finished_at,
                records_read,
                records_transformed,
                records_written,
                error_message
            FROM reporting.pipeline_runs
            WHERE pipeline_name = :pipeline_name
            ORDER BY started_at DESC, run_id DESC
            LIMIT 1
            """
        ),
        {"pipeline_name": pipeline_name},
    ).mappings().first()
    return dict(record) if record is not None else None
