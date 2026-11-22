"""Recepción de RFP (Hito 9, Parte 1): subida de PDF, ticket y consulta de estado.

Mismo backend, mismo proceso: este router solo valida, guarda el PDF bajo ``mock_data/raw/rfp/`` (el
``data/raw/`` del BRIEFING), crea el ticket en ``analizando`` y encola el pipeline en Celery. La lógica de
agentes vive en ``mock_data/pipelines/rfp_intake/`` (el ``data/pipelines/`` del BRIEFING).
"""

import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool

from security import get_current_user

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from mock_data.pipelines.rfp_intake.models import RfpTicket  # noqa: E402,F401  (registra las tablas en SQLModel)
from mock_data.pipelines.rfp_intake.repository import RfpRepository  # noqa: E402

logger = logging.getLogger("trackflow.rfp_intake")
router = APIRouter(prefix="/rfp-intake", tags=["rfp intake"])
MAX_PDF_BYTES = 10 * 1024 * 1024


def rfp_raw_dir() -> Path:
    return Path(os.environ.get("RFP_RAW_DIR") or REPOSITORY_ROOT / "mock_data" / "raw" / "rfp")


def _repo(request: Request) -> RfpRepository:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="La base de datos de tickets RFP no está disponible.")
    return RfpRepository(engine)


def _enqueue(ticket_id: str, force_accept: bool = False) -> None:
    from tasks import process_rfp_ticket

    process_rfp_ticket.apply_async(args=[ticket_id], kwargs={"force_accept": force_accept},
                                   task_id=f"rfp-{ticket_id}-{uuid.uuid4().hex[:8]}")


def _store_pdf(ticket_id: uuid.UUID, content: bytes) -> Path:
    directory = rfp_raw_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{ticket_id}.pdf"
    tmp = target.with_suffix(".pdf.tmp")
    tmp.write_bytes(content)
    tmp.replace(target)  # atómico: el worker nunca lee un PDF a medias
    return target


def _user_label(user) -> str:
    return str(getattr(user, "email", None) or getattr(user, "id", "usuario"))


@router.post("/tickets", status_code=202)
async def upload_rfp(request: Request, file: UploadFile = File(...), user=Depends(get_current_user)):
    """Crea un ticket por PDF y responde enseguida; el análisis corre en el worker de Celery."""
    name = (file.filename or "").strip()
    content = await file.read(MAX_PDF_BYTES + 1)
    if not name.lower().endswith(".pdf") or not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=422, detail="Sube la RFP en formato PDF.")
    if len(content) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="El PDF supera los 10 MB.")
    repo = _repo(request)
    ticket_id = uuid.uuid4()
    path = await run_in_threadpool(_store_pdf, ticket_id, content)
    rfp_id = f"RFP-{datetime.now(timezone.utc):%Y%m%d}-{ticket_id.hex[:6].upper()}"
    ticket = await run_in_threadpool(lambda: repo.create_ticket(
        ticket_id=ticket_id, rfp_id=rfp_id, raw_pdf_path=str(path), original_filename=name[:255],
        uploaded_by=_user_label(user)))
    try:
        await run_in_threadpool(_enqueue, str(ticket_id))
    except Exception as error:  # noqa: BLE001 — el ticket dice la verdad: no se pudo encolar
        logger.exception("No se pudo encolar el pipeline RFP; ticket=%s", ticket_id)
        await run_in_threadpool(lambda: repo.update_ticket(ticket_id, pipeline_stage="fallido",
                                                           last_error=f"No se pudo encolar el análisis: {type(error).__name__}"))
        raise HTTPException(status_code=503, detail="El ticket se creó, pero la cola de análisis no está disponible. "
                                                    "Reintenta el análisis más tarde.") from error
    return {"ticket_id": str(ticket.ticket_id), "rfp_id": ticket.rfp_id, "status": ticket.status,
            "status_url": f"/rfp-intake/tickets/{ticket.ticket_id}"}


def _summary(repo: RfpRepository, ticket) -> dict:
    meta = repo.get_metadata(ticket.ticket_id)
    return {"ticket_id": str(ticket.ticket_id), "rfp_id": ticket.rfp_id, "status": ticket.status,
            "pipeline_stage": ticket.pipeline_stage, "original_filename": ticket.original_filename,
            "created_at": ticket.created_at.isoformat(), "updated_at": ticket.updated_at.isoformat(),
            "attempts": ticket.attempts, "last_error": ticket.last_error, "discard_reason": ticket.discard_reason,
            "client_name": meta.client_name if meta else None, "client_country": meta.client_country if meta else None,
            "departments_needed": meta.departments_needed if meta else []}


@router.get("/tickets")
def list_rfp_tickets(request: Request, _user=Depends(get_current_user)):
    repo = _repo(request)
    return {"tickets": [_summary(repo, t) for t in repo.list_tickets()]}


@router.get("/tickets/{ticket_id}")
def get_rfp_ticket(ticket_id: uuid.UUID, request: Request, _user=Depends(get_current_user)):
    repo = _repo(request)
    ticket = repo.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket RFP no encontrado.")
    meta = repo.get_metadata(ticket_id)
    sections = repo.get_sections(ticket_id)
    return {
        **_summary(repo, ticket), "raw_pdf_path": ticket.raw_pdf_path, "handoff": ticket.handoff,
        "metadata": None if meta is None else {
            "client_name": meta.client_name, "client_country": meta.client_country, "currency": meta.currency,
            "services_requested": meta.services_requested, "monthly_volume": meta.monthly_volume, "sku_count": meta.sku_count,
            "deadline": meta.deadline, "deadline_text": meta.deadline_text, "budget_range": meta.budget_range,
            "departments_needed": meta.departments_needed, "readability": meta.readability, "details": meta.details,
            "classification": meta.classification, "markdown_chars": len(meta.markdown or "")},
        "sections": [{"department_id": s.department_id, "key_aspects": s.key_aspects, "open_questions": s.open_questions,
                      "approver": s.approver, "approval_status": s.approval_status, "worker_meta": s.worker_meta}
                     for s in sections],
    }


@router.post("/tickets/{ticket_id}/reprocess", status_code=202)
async def reprocess_rfp_ticket(ticket_id: uuid.UUID, request: Request, force_accept: bool = False,
                               _user=Depends(get_current_user)):
    """Re-ejecuta el pipeline (tras un fallo técnico, o forzando la aceptación de un descarte que Ventas revisó)."""
    repo = _repo(request)
    ticket = repo.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket RFP no encontrado.")
    if ticket.status not in ("analizando", "descartado", "analisis_completo"):
        raise HTTPException(status_code=409, detail="El ticket ya avanzó a la Parte 2 y no se reprocesa desde aquí.")
    await run_in_threadpool(lambda: repo.update_ticket(ticket_id, status="analizando", pipeline_stage="recibido",
                                                       last_error=None))
    await run_in_threadpool(_enqueue, str(ticket_id), force_accept)
    return {"ticket_id": str(ticket_id), "status": "analizando"}
