import sys
from datetime import datetime, timezone
from pathlib import Path

from celery import Task
from celery.utils.log import get_task_logger
from sqlalchemy import text
from sqlmodel import Session

from celery_app import app
from database import create_sql_engine
from task_storage import clean_stale_task_inputs, delete_task_input, load_task_input


logger = get_task_logger(__name__)
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


class IncidentAnalysisTask(Task):
    autoretry_for = (Exception,)
    retry_backoff = True
    retry_backoff_max = 600
    retry_jitter = True
    max_retries = 3

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Persist the terminal failure after Celery exhausts its retries."""
        try:
            engine = create_sql_engine()
            try:
                with Session(engine) as session:
                    session.execute(
                        text(
                            """
                            INSERT INTO public.task_dead_letters (task_id, task_name, attempts, error, failed_at)
                            VALUES (:task_id, :task_name, :attempts, :error, :failed_at)
                            ON CONFLICT (task_id) DO UPDATE SET
                                attempts = EXCLUDED.attempts,
                                error = EXCLUDED.error,
                                failed_at = EXCLUDED.failed_at
                            """
                        ),
                        {
                            "task_id": task_id,
                            "task_name": self.name,
                            "attempts": getattr(self.request, "retries", self.max_retries) + 1,
                            "error": "".join(einfo.traceback) if einfo and einfo.traceback else str(exc),
                            "failed_at": datetime.now(timezone.utc),
                        },
                    )
                    session.commit()
            finally:
                engine.dispose()
        except Exception:
            logger.exception("No se pudo persistir la tarea agotada en la DLQ; task_id=%s", task_id)
        try:
            delete_task_input(task_id)
        except Exception:
            logger.exception("No se pudo eliminar el input de tarea fallida; task_id=%s", task_id)
        super().on_failure(exc, task_id, args, kwargs, einfo)


@app.task(bind=True, base=IncidentAnalysisTask, name="tasks.analyze_incident_csv")
def analyze_incident_csv(self, task_id: str) -> dict:
    """Load a task's CSV by ID and return its report and downloadable CSV."""
    clean_stale_task_inputs()
    csv_text = load_task_input(task_id)
    if len(csv_text.encode("utf-8")) > 10 * 1024 * 1024:
        raise ValueError("El archivo supera el límite de 10 MB.")

    if str(REPOSITORY_ROOT) not in sys.path:
        sys.path.insert(0, str(REPOSITORY_ROOT))
    from scripts.incidents_analysis import analyze_csv_text, results_csv_text

    report = analyze_csv_text(csv_text)
    result = {"report": report, "results_csv": results_csv_text(report)}
    delete_task_input(task_id)
    return result
