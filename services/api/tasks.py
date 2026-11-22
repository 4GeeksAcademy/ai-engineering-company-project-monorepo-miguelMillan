import os
import random
import sys
import uuid
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


RETRY_BASE_SECONDS = 2
RETRY_MAX_SECONDS = 600
RETRY_JITTER_RATIO = 0.25


def retry_countdown(retries: int, rand=random.random) -> float:
    """Backoff exponencial con suelo garantizado y jitter solo hacia arriba.

    El retry_jitter de Celery usa ``randrange(countdown + 1)``, por lo que el
    primer reintento podía salir a 0 s. Aquí el retardo nunca baja del valor
    exponencial base (2 s, 4 s, 8 s...) y el jitter suma como mucho un 25 %
    adicional para desincronizar workers, con tope en RETRY_MAX_SECONDS.
    """
    base = min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * (2 ** retries))
    return min(RETRY_MAX_SECONDS, base + base * RETRY_JITTER_RATIO * rand())


class IncidentAnalysisTask(Task):
    # Los reintentos se programan explícitamente con retry_countdown(); no se
    # usa autoretry_for/retry_jitter de Celery para evitar countdowns de 0 s.
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
    try:
        return _analyze_stored_csv(task_id)
    except Exception as error:
        attempt = self.request.retries + 1
        if self.request.retries >= self.max_retries:
            logger.error(
                "task_id=%s attempt=%s/%s status=exhausted error=%s: %s",
                task_id, attempt, self.max_retries + 1, type(error).__name__, error,
            )
            raise
        countdown = retry_countdown(self.request.retries)
        logger.warning(
            "task_id=%s attempt=%s/%s status=retry countdown=%.2fs error=%s: %s",
            task_id, attempt, self.max_retries + 1, countdown, type(error).__name__, error,
        )
        raise self.retry(exc=error, countdown=countdown)


def _analyze_stored_csv(task_id: str) -> dict:
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


# ----------------------------------------------------------------------------
# Recepción de RFP (Hito 9, Parte 1): el pipeline agéntico corre en el worker de Celery
# ----------------------------------------------------------------------------
class RfpIntakeTask(Task):
    """Reintenta errores transitorios (LLM o base de datos caídos). El ticket siempre refleja la verdad:
    ``run_ticket`` deja ``pipeline_stage='fallido'`` + ``last_error`` en cada fallo, y al agotar los reintentos
    la tarea queda además en ``task_dead_letters``."""

    max_retries = 2

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        try:
            engine = create_sql_engine()
            try:
                with Session(engine) as session:
                    session.execute(text(
                        """INSERT INTO public.task_dead_letters (task_id, task_name, attempts, error, failed_at)
                           VALUES (:task_id, :task_name, :attempts, :error, :failed_at)
                           ON CONFLICT (task_id) DO UPDATE SET attempts = EXCLUDED.attempts, error = EXCLUDED.error,
                                                               failed_at = EXCLUDED.failed_at"""),
                        {"task_id": task_id, "task_name": self.name,
                         "attempts": getattr(self.request, "retries", self.max_retries) + 1,
                         "error": "".join(einfo.traceback) if einfo and einfo.traceback else str(exc),
                         "failed_at": datetime.now(timezone.utc)})
                    session.commit()
            finally:
                engine.dispose()
        except Exception:
            logger.exception("No se pudo persistir la tarea RFP agotada en la DLQ; task_id=%s", task_id)
        super().on_failure(exc, task_id, args, kwargs, einfo)


@app.task(bind=True, base=RfpIntakeTask, name="rfp_intake.process_ticket")
def process_rfp_ticket(self, ticket_id: str, force_accept: bool = False) -> dict:
    from mock_data.pipelines.rfp_intake.graph import run_ticket
    from mock_data.pipelines.rfp_intake.repository import RfpRepository

    engine = create_sql_engine()
    try:
        result = run_ticket(ticket_id, RfpRepository(engine), force_accept=force_accept)
        if result["status"] == "analisis_completo" and os.environ.get("RFP_AUTO_GENERATE", "1") != "0":
            # Enrutamiento a la Parte 2: el ticket quedó listo (rfp_handoff.v1 con next_status=generando_borrador).
            try:
                RfpRepository(engine).update_ticket(ticket_id, response_stage="generando")  # la UI sigue refrescando
                enqueue_rfp_response(ticket_id)
            except Exception as error:  # noqa: BLE001 — la Parte 1 terminó bien; no se repite por un fallo de la cola
                logger.exception("No se pudo encolar la Parte 2; ticket_id=%s", ticket_id)
                RfpRepository(engine).update_ticket(ticket_id, response_stage="fallido",
                                                    last_error=f"No se pudo encolar la generación: {type(error).__name__}")
        return {k: result[k] for k in ("ticket_id", "status", "pipeline_stage")}
    except KeyError:
        logger.error("Ticket RFP inexistente; ticket_id=%s", ticket_id)
        raise
    except Exception as error:
        if self.request.retries >= self.max_retries:
            raise
        raise self.retry(exc=error, countdown=retry_countdown(self.request.retries))
    finally:
        engine.dispose()


# ----------------------------------------------------------------------------
# Respuesta a RFP (Hito 9, Parte 2): generación por departamento + evaluación en paralelo
# ----------------------------------------------------------------------------
def enqueue_rfp_response(ticket_id: str) -> None:
    generate_rfp_response.apply_async(args=[ticket_id], task_id=f"rfp-gen-{ticket_id}-{uuid.uuid4().hex[:8]}")


@app.task(bind=True, base=RfpIntakeTask, name="rfp_response.generate_ticket")
def generate_rfp_response(self, ticket_id: str) -> dict:
    from mock_data.pipelines.rfp_intake.generation import HandoffError
    from mock_data.pipelines.rfp_intake.repository import RfpRepository
    from mock_data.pipelines.rfp_intake.response_graph import run_response

    engine = create_sql_engine()
    try:
        result = run_response(ticket_id, RfpRepository(engine))
        return {k: result[k] for k in ("ticket_id", "status", "response_stage")}
    except (KeyError, HandoffError):
        logger.error("Ticket RFP no listo para la Parte 2; ticket_id=%s", ticket_id)
        raise
    except Exception as error:
        if self.request.retries >= self.max_retries:
            raise
        raise self.retry(exc=error, countdown=retry_countdown(self.request.retries))
    finally:
        engine.dispose()
