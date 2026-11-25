"""Recepción de RFP (Hito 9, Parte 1) y generación/evaluación de la respuesta (Parte 2).

Mismo backend, mismo proceso: este router solo valida, guarda el PDF bajo ``mock_data/raw/rfp/`` (el
``data/raw/`` del BRIEFING), crea el ticket en ``analizando`` y encola el pipeline en Celery. La lógica de
agentes vive en ``mock_data/pipelines/rfp_intake/`` (el ``data/pipelines/`` del BRIEFING).

Parte 2: cuando la Parte 1 deja el ticket en ``analisis_completo``, el worker encola sola la generación
(``rfp_response.generate_ticket``). ``POST /tickets/{id}/generate`` la relanza (tras un fallo o una revisión humana)
y el detalle del ticket expone borradores, ``EvaluationResult`` y el handoff a la Parte 3.

Parte 3: al terminar la Parte 2, el worker arranca el grafo de aprobación (``rfp_approval.start``), que se pausa por
departamento. ``POST /tickets/{id}/sections/{department_id}/decision`` valida la decisión del responsable y reanuda
SOLO esa rama (``rfp_approval.resume``). ``GET /tickets/{id}/final-document`` y ``GET /tickets/{id}/trace`` exponen el
documento final y la traza de agentes.
"""

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from security import require_staff
from security_log import security_event

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from mock_data.pipelines.rfp_intake.models import RfpTicket  # noqa: E402,F401  (registra las tablas en SQLModel)
from mock_data.pipelines.rfp_intake.repository import RfpRepository  # noqa: E402
from mock_data.pipelines.rfp_intake.response_graph import GENERATION_ENTRY_STATUSES  # noqa: E402
from mock_data.pipelines.rfp_intake import approval as approval_rules  # noqa: E402
from mock_data.pipelines.rfp_intake.approval_graph import APPROVAL_ENTRY_STATUSES, section_input  # noqa: E402

logger = logging.getLogger("trackflow.rfp_intake")
router = APIRouter(prefix="/rfp-intake", tags=["rfp intake"])
MAX_PDF_BYTES = 10 * 1024 * 1024


def approver_email(name: str) -> str | None:
    """Cuenta del responsable (``RFP_APPROVERS``: JSON ``{"nombre": "email"}``). Sin mapeo, nadie decide: deny by default."""
    try:
        email = json.loads(os.environ.get("RFP_APPROVERS") or "{}").get(name)
    except (json.JSONDecodeError, AttributeError):
        logger.error("RFP_APPROVERS no es un objeto JSON; ninguna decisión está autorizada")
        return None
    return email.strip().lower() if isinstance(email, str) and email.strip() else None


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
async def upload_rfp(request: Request, file: UploadFile = File(...), user=Depends(require_staff)):
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
            "response_stage": ticket.response_stage, "response_attempts": ticket.response_attempts,
            "approval_stage": ticket.approval_stage, "approval_thread_id": ticket.approval_thread_id,
            "approval_note": ticket.approval_note,
            "client_name": meta.client_name if meta else None, "client_country": meta.client_country if meta else None,
            "departments_needed": meta.departments_needed if meta else []}


@router.get("/tickets")
def list_rfp_tickets(request: Request, ticket_id: list[uuid.UUID] | None = Query(default=None, max_length=50),
                     _user=Depends(require_staff)):
    """Con ``ticket_id`` (repetible) devuelve solo esos: la UI refresca lo que está en curso sin pedir toda la lista."""
    repo = _repo(request)
    return {"tickets": [_summary(repo, t) for t in (repo.get_tickets(ticket_id) if ticket_id else repo.list_tickets())]}


@router.get("/tickets/{ticket_id}")
def get_rfp_ticket(ticket_id: uuid.UUID, request: Request, _user=Depends(require_staff)):
    repo = _repo(request)
    ticket = repo.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket RFP no encontrado.")
    meta = repo.get_metadata(ticket_id)
    sections = repo.get_sections(ticket_id)
    history: dict[str, list] = {}
    for evaluation in repo.get_evaluations(ticket_id, run=ticket.response_attempts or None):
        history.setdefault(evaluation.department_id, []).append({
            "iteration": evaluation.iteration, "overall_pass": evaluation.overall_pass,
            "readability_pass": evaluation.readability.get("pass"), "relevance_pass": evaluation.relevance.get("pass"),
            "compliance_pass": evaluation.compliance.get("pass"), "rule_ids": evaluation.compliance.get("rule_ids", []),
            "feedback_for_generator": evaluation.feedback_for_generator, "created_at": evaluation.created_at.isoformat()})
    return {
        **_summary(repo, ticket), "raw_pdf_path": ticket.raw_pdf_path, "handoff": ticket.handoff,
        "response_handoff": ticket.response_handoff, "arbitration": ticket.arbitration or [],
        "final_document": None if (document := repo.get_final_document(ticket_id)) is None else {
            "currency": document.currency, "generated_at": document.generated_at.isoformat(),
            "approvals": document.approvals, "url": f"/rfp-intake/tickets/{ticket_id}/final-document"},
        "metadata": None if meta is None else {
            "client_name": meta.client_name, "client_country": meta.client_country, "currency": meta.currency,
            "services_requested": meta.services_requested, "monthly_volume": meta.monthly_volume, "sku_count": meta.sku_count,
            "deadline": meta.deadline, "deadline_text": meta.deadline_text, "budget_range": meta.budget_range,
            "departments_needed": meta.departments_needed, "readability": meta.readability, "details": meta.details,
            "classification": meta.classification, "markdown_chars": len(meta.markdown or "")},
        "sections": [{"department_id": s.department_id, "key_aspects": s.key_aspects, "open_questions": s.open_questions,
                      "approver": s.approver, "approval_status": s.approval_status, "worker_meta": s.worker_meta,
                      "section_status": s.section_status, "iterations": s.iterations,
                      "needs_human_review": s.needs_human_review, "draft_content": s.draft_content,
                      "evaluation_result": s.evaluation_results, "evaluation_history": history.get(s.department_id, []),
                      "approval_round": s.approval_round, "approval_request": s.approval_request,
                      "decision_history": s.decision_history or [], "commitments": s.commitments or {},
                      "approved_at": s.approved_at.isoformat() if s.approved_at else None}
                     for s in sections],
    }


@router.post("/tickets/{ticket_id}/reprocess", status_code=202)
async def reprocess_rfp_ticket(ticket_id: uuid.UUID, request: Request, force_accept: bool = False,
                               _user=Depends(require_staff)):
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


def _enqueue_generation(ticket_id: str) -> None:
    from tasks import enqueue_rfp_response

    enqueue_rfp_response(ticket_id)


@router.post("/tickets/{ticket_id}/generate", status_code=202)
async def generate_rfp_response(ticket_id: uuid.UUID, request: Request, _user=Depends(require_staff)):
    """(Re)genera y evalúa las secciones (Parte 2) de un ticket que la Parte 1 dejó listo."""
    repo = _repo(request)
    ticket = repo.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket RFP no encontrado.")
    if ticket.status not in GENERATION_ENTRY_STATUSES or not ticket.handoff:
        raise HTTPException(status_code=409, detail="La Parte 2 solo arranca desde tickets con análisis completo.")
    if ticket.status in ("generando_borrador", "en_evaluación") and ticket.response_stage != "fallido":
        raise HTTPException(status_code=409, detail="La generación de este ticket ya está en curso.")
    if any(s.approval_status != "pendiente" for s in repo.get_sections(ticket_id)):
        raise HTTPException(status_code=409, detail="Alguna sección ya tiene una decisión de aprobación.")
    await run_in_threadpool(lambda: repo.update_ticket(ticket_id, response_stage="generando", last_error=None))
    try:
        await run_in_threadpool(_enqueue_generation, str(ticket_id))
    except Exception as error:  # noqa: BLE001
        logger.exception("No se pudo encolar la Parte 2; ticket=%s", ticket_id)
        await run_in_threadpool(lambda: repo.update_ticket(ticket_id, response_stage="fallido",
                                                           last_error=f"No se pudo encolar la generación: {type(error).__name__}"))
        raise HTTPException(status_code=503, detail="La cola de generación no está disponible.") from error
    return {"ticket_id": str(ticket_id), "status": ticket.status, "response_stage": "generando"}



# ============================================================================
# Parte 3: aprobación humana por departamento, documento final y traza
# ============================================================================
class DecisionIn(BaseModel):
    decision: str
    approver: str
    section_version: int
    comment: str = ""
    commitments: dict[str, int] = Field(default_factory=dict)
    edited_content: str | None = None


def _enqueue_approval_resume(ticket_id: str, department_id: str, decision: dict) -> None:
    from tasks import enqueue_rfp_approval_resume

    enqueue_rfp_approval_resume(ticket_id, department_id, decision)


def _enqueue_approval_start(ticket_id: str) -> None:
    from tasks import enqueue_rfp_approval_start

    enqueue_rfp_approval_start(ticket_id)


@router.post("/tickets/{ticket_id}/sections/{department_id}/decision", status_code=202)
async def decide_section(ticket_id: uuid.UUID, department_id: str, body: DecisionIn, request: Request,
                         user=Depends(require_staff)):
    """El responsable del departamento aprueba, rechaza o pide cambios. Se valida AQUÍ (y otra vez en el grafo)
    antes de reanudar solo su rama."""
    repo = _repo(request)
    ticket = repo.get_ticket(ticket_id)
    section = repo.get_section(ticket_id, department_id) if ticket else None
    if ticket is None or section is None:
        raise HTTPException(status_code=404, detail="Ticket o sección no encontrados.")
    if section.approval_status != "esperando_aprobación" or not section.approval_request:
        raise HTTPException(status_code=409, detail="Esta sección no está esperando una decisión.")
    # ``approver`` llega en el cuerpo: quién es de verdad lo dice la cuenta autenticada, no lo que escriba.
    if approver_email(section.approval_request["approver"]) != str(user.email).lower():
        security_event("rfp_decision_denied", request, email=str(user.email), department=department_id)
        raise HTTPException(status_code=403, detail=f"Solo la cuenta de {section.approval_request['approver']} puede decidir esta sección.")
    pending = {**section.approval_request,
               "section_input": section_input(repo, str(ticket_id), department_id, None).to_dict()}
    decision = {**body.model_dump(), "decided_by": _user_label(user)}
    try:
        validated = approval_rules.validate_decision(decision, pending)
    except approval_rules.DecisionError as error:
        raise HTTPException(status_code=422, detail={"message": "Decisión no válida.", "errors": error.errors}) from error
    # Antes de encolar: así el worker nunca encuentra un estado más viejo que el que deja él.
    await run_in_threadpool(lambda: repo.update_section(ticket_id, department_id, approval_status="procesando"))
    await run_in_threadpool(lambda: repo.update_ticket(ticket_id, approval_stage="en_curso"))
    try:
        await run_in_threadpool(_enqueue_approval_resume, str(ticket_id), department_id, validated.model_dump())
    except Exception as error:  # noqa: BLE001
        logger.exception("No se pudo encolar la reanudación; ticket=%s", ticket_id)
        await run_in_threadpool(lambda: repo.update_section(ticket_id, department_id, approval_status="esperando_aprobación"))
        await run_in_threadpool(lambda: repo.update_ticket(ticket_id, approval_stage="esperando"))
        raise HTTPException(status_code=503, detail="La cola de aprobación no está disponible.") from error
    return {"ticket_id": str(ticket_id), "department_id": department_id, "decision": validated.decision,
            "approval_status": "procesando"}


@router.post("/tickets/{ticket_id}/approval/start", status_code=202)
async def start_ticket_approval(ticket_id: uuid.UUID, request: Request, _user=Depends(require_staff)):
    """Arranca (o vuelve a arrancar tras un fallo o un bloqueo) el flujo de aprobación en un hilo nuevo."""
    repo = _repo(request)
    ticket = repo.get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket RFP no encontrado.")
    if ticket.status not in APPROVAL_ENTRY_STATUSES or not ticket.response_handoff or ticket.response_stage != "handoff":
        raise HTTPException(status_code=409, detail="La aprobación arranca cuando la Parte 2 dejó las secciones listas.")
    if ticket.approval_stage in ("iniciando", "en_curso", "esperando", "completado"):
        raise HTTPException(status_code=409, detail="El flujo de aprobación ya está en marcha: se reanuda por departamento.")
    await run_in_threadpool(lambda: repo.update_ticket(ticket_id, approval_stage="iniciando", approval_thread_id=None))
    await run_in_threadpool(_enqueue_approval_start, str(ticket_id))
    return {"ticket_id": str(ticket_id), "approval_stage": "iniciando"}


@router.get("/tickets/{ticket_id}/final-document")
def get_final_document(ticket_id: uuid.UUID, request: Request, format: str = "json", _user=Depends(require_staff)):
    repo = _repo(request)
    document = repo.get_final_document(ticket_id)
    if document is None:
        raise HTTPException(status_code=404, detail="El documento final todavía no existe: faltan aprobaciones.")
    if format == "md":
        return PlainTextResponse(document.content, media_type="text/markdown; charset=utf-8", headers={
            "Content-Disposition": f'attachment; filename="propuesta-{document.rfp_id}.md"'})
    return {"ticket_id": str(document.ticket_id), "rfp_id": document.rfp_id, "currency": document.currency,
            "generated_at": document.generated_at.isoformat(), "sections": document.sections, "approvals": document.approvals,
            "arbitration": document.arbitration, "content": document.content}


@router.get("/tickets/{ticket_id}/trace")
def get_trace(ticket_id: uuid.UUID, request: Request, _user=Depends(require_staff)):
    repo = _repo(request)
    if repo.get_ticket(ticket_id) is None:
        raise HTTPException(status_code=404, detail="Ticket RFP no encontrado.")
    return {"events": [{"id": e.id, "part": e.part, "node": e.node, "agent": e.agent, "department_id": e.department_id,
                        "status": e.status, "thread_id": e.thread_id, "input": e.input, "output": e.output,
                        "started_at": e.started_at.isoformat(), "finished_at": e.finished_at.isoformat()}
                       for e in repo.get_trace(ticket_id)]}
