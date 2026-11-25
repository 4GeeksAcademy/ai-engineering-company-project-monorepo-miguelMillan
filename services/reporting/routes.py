from datetime import date
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlmodel import Session

from database import get_db
from mock_data.pipelines.queries import get_weekly_warehouse_client_performance
from mock_data.pipelines.runs import get_latest_pipeline_run
from security import require_staff

from .schemas import (
    LatestPipelineRunResponse,
    PipelineRunAcceptedResponse,
    PipelineRunRequest,
    WeeklyWarehouseClientPerformanceResponse,
)


PIPELINE_NAME = "weekly_warehouse_client_performance"
router = APIRouter(
    prefix="/reporting",
    tags=["reporting"],
    dependencies=[Depends(require_staff)],
)


@router.get(
    "/pipeline-runs/latest",
    response_model=LatestPipelineRunResponse,
)
def latest_pipeline_run(session: Session = Depends(get_db)):
    run = get_latest_pipeline_run(session, PIPELINE_NAME)
    if run is None:
        raise HTTPException(status_code=404, detail="No hay corridas registradas.")
    return run


@router.post(
    "/pipeline-runs",
    response_model=PipelineRunAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_pipeline_run(
    background_tasks: BackgroundTasks,
    payload: PipelineRunRequest | None = None,
):
    try:
        from mock_data.pipelines.pipeline import (
            resolve_week_start,
            weekly_warehouse_client_performance_flow,
        )
    except ModuleNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El runtime del pipeline no esta instalado.",
        ) from error

    try:
        week_start = resolve_week_start(payload.week_start if payload else None)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    run_id = uuid4()
    background_tasks.add_task(
        weekly_warehouse_client_performance_flow,
        week_start=week_start,
        trigger="manual",
        run_id=str(run_id),
    )
    return {
        "run_id": run_id,
        "pipeline_name": PIPELINE_NAME,
        "week_start": week_start,
        "status": "running",
        "trigger": "manual",
    }


@router.get(
    "/weekly-warehouse-client-performance",
    response_model=WeeklyWarehouseClientPerformanceResponse,
)
def weekly_warehouse_client_performance(
    week_start: date | None = Query(default=None),
    session: Session = Depends(get_db),
):
    if week_start is not None and week_start.weekday() != 0:
        raise HTTPException(
            status_code=422,
            detail="week_start debe ser un lunes de la semana ISO.",
        )

    report = get_weekly_warehouse_client_performance(session, week_start)
    if report is None:
        raise HTTPException(
            status_code=404,
            detail="Todavia no hay un reporte semanal disponible.",
        )
    return report
