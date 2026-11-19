import hmac
import os
from uuid import UUID

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from security import get_current_user
from task_storage import delete_task_input, store_task_input
from tasks import analyze_incident_csv


router = APIRouter(tags=["background tasks"])
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


async def _enqueue_csv(request: Request, content: bytes):
    task_id = request.headers.get("x-task-id")
    if not task_id:
        raise HTTPException(status_code=400, detail="Falta el identificador de tarea generado por el gateway.")
    try:
        task_id = str(UUID(task_id))
        # Escritura en disco y publicación en Redis son bloqueantes: se ejecutan
        # en el threadpool para no frenar el event loop del resto de peticiones.
        await run_in_threadpool(store_task_input, task_id, content)
        await run_in_threadpool(lambda: analyze_incident_csv.apply_async(args=[task_id], task_id=task_id))
    except Exception as error:
        if task_id:
            try:
                delete_task_input(task_id)
            except ValueError:
                pass
        raise HTTPException(status_code=503, detail="El servicio de tareas no está disponible.") from error
    return {"task_id": task_id, "status_url": f"/tasks/{task_id}"}


async def _enqueue_uploaded_file(request: Request, file: UploadFile):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail='El archivo debe tener extensión .csv.')
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="El archivo supera el límite de 10 MB.")
    try:
        content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=422, detail="El archivo no contiene texto UTF-8 válido.") from error
    return await _enqueue_csv(request, content)


@router.post("/api/incidents/analyze", status_code=202)
async def submit_incident_analysis(request: Request, file: UploadFile = File(...), _user=Depends(get_current_user)):
    return await _enqueue_uploaded_file(request, file)


@router.post("/internal/tasks/analyze", status_code=202, include_in_schema=False)
async def submit_incident_analysis_from_gateway(
    request: Request,
    file: UploadFile = File(...),
    internal_token: str = Header(default="", alias="x-internal-task-token"),
):
    """Gateway adapter preserving the legacy unauthenticated upload contract."""
    configured_token = os.environ.get("TASKS_INTERNAL_TOKEN")
    if not configured_token or not hmac.compare_digest(configured_token, internal_token):
        raise HTTPException(status_code=401, detail="No autorizado.")
    return await _enqueue_uploaded_file(request, file)


@router.get("/tasks/{task_id}")
def get_task_status(task_id: UUID, _user=Depends(get_current_user)):
    task = AsyncResult(str(task_id), app=analyze_incident_csv.app)
    state = task.state.lower()
    if state == "revoked":
        state = "failure"
    response = {"task_id": str(task_id), "status": state}
    if task.successful():
        response["result"] = task.result["report"]
    elif task.failed():
        response["error"] = "La tarea agotó sus reintentos y no pudo completarse."
    return response


@router.get("/api/incidents/results/export")
def export_incident_results(task_id: UUID, _user=Depends(get_current_user)):
    task = AsyncResult(str(task_id), app=analyze_incident_csv.app)
    if not task.successful():
        raise HTTPException(status_code=409, detail="Los resultados de esta tarea todavía no están disponibles.")
    return Response(
        content=task.result["results_csv"],
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="results.csv"'},
    )
