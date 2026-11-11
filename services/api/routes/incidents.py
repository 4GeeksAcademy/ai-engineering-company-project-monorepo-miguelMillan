from collections import Counter
from time import monotonic
from typing import get_args
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from tinydb import Query

from models import Incident, IncidentBranch, IncidentCategory, IncidentCreate, IncidentCreatedResponse, IncidentDetailResponse, IncidentListItemResponse, IncidentOrigin, IncidentStatus, IncidentStatusResponse, IncidentStatusUpdate, IncidentSummaryResponse, User
from security import get_current_user


router = APIRouter(prefix="/api/incidents", tags=["incidents"], dependencies=[Depends(get_current_user)])
INCIDENT_CACHE_TTL = 5
INCIDENT_CACHE_LIMIT = 32
TRANSITIONS = {
    IncidentStatus.open: {IncidentStatus.in_progress, IncidentStatus.discarded},
    IncidentStatus.in_progress: {IncidentStatus.resolved, IncidentStatus.discarded},
    IncidentStatus.resolved: set(),
    IncidentStatus.discarded: set(),
}


def cached_incidents(request: Request, key: tuple):
    cached = request.app.state.incident_cache.get(key)
    if cached is not None:
        expires, value = cached
        if expires > monotonic():
            return value
        del request.app.state.incident_cache[key]
    return None


def store_incidents(request: Request, key: tuple, value):
    cache = request.app.state.incident_cache
    if len(cache) >= INCIDENT_CACHE_LIMIT:
        cache.pop(next(iter(cache)))
    cache[key] = (monotonic() + INCIDENT_CACHE_TTL, value)


def find_incident(request: Request, incident_id: UUID):
    document = request.app.state.db.table("incidents").get(Query().id == str(incident_id))
    if document is None:
        raise HTTPException(status_code=404, detail="Incidencia no encontrada.")
    return document


@router.post("", response_model=IncidentCreatedResponse, status_code=201)
def create_incident(payload: IncidentCreate, request: Request):
    incident = Incident(**payload.model_dump())
    with request.app.state.lock:
        request.app.state.db.table("incidents").insert(incident.model_dump(mode="json"))
        request.app.state.incident_cache.clear()
    return IncidentCreatedResponse.model_validate(incident)


@router.get("", response_model=list[IncidentListItemResponse])
def list_incidents(
    request: Request,
    status: IncidentStatus | None = None,
    origin: IncidentOrigin | None = None,
    branch: IncidentBranch | None = None,
    category: IncidentCategory | None = None,
    current_user: User = Depends(get_current_user),
):
    filters = {"status": status, "origin": origin, "branch": branch, "category": category}
    key = ("list", current_user.id, status, origin, branch, category)
    with request.app.state.lock:
        cached = cached_incidents(request, key)
        if cached is not None:
            return cached
        result = [
            IncidentListItemResponse.model_validate(Incident.model_validate(document))
            for document in request.app.state.db.table("incidents").all()
            if all(value is None or document.get(field) == value for field, value in filters.items())
        ]
        store_incidents(request, key, result)
        return result


@router.get("/summary", response_model=IncidentSummaryResponse)
def incident_summary(request: Request, current_user: User = Depends(get_current_user)):
    key = ("summary", current_user.id)
    with request.app.state.lock:
        cached = cached_incidents(request, key)
        if cached is not None:
            return cached
        incidents = [Incident.model_validate(document) for document in request.app.state.db.table("incidents").all()]
        status_counts = Counter(incident.status for incident in incidents)
        category_counts = Counter(incident.category for incident in incidents)
        origin_counts = Counter(incident.origin for incident in incidents)
        branch_counts = Counter(incident.branch for incident in incidents)
        result = IncidentSummaryResponse(
            total=len(incidents),
            by_status={value: status_counts[value] for value in IncidentStatus},
            by_category={value: category_counts[value] for value in get_args(IncidentCategory)},
            by_origin={value: origin_counts[value] for value in get_args(IncidentOrigin)},
            by_branch={value: branch_counts[value] for value in get_args(IncidentBranch)},
        )
        store_incidents(request, key, result)
        return result


@router.get("/{incident_id}", response_model=IncidentDetailResponse)
def get_incident(incident_id: UUID, request: Request):
    with request.app.state.lock:
        incident = Incident.model_validate(find_incident(request, incident_id))
    return IncidentDetailResponse.model_validate(incident)


@router.patch("/{incident_id}/status", response_model=IncidentStatusResponse)
def update_incident_status(incident_id: UUID, payload: IncidentStatusUpdate, request: Request):
    with request.app.state.lock:
        document = find_incident(request, incident_id)
        incident = Incident.model_validate(document)
        if payload.status not in TRANSITIONS[incident.status]:
            raise HTTPException(status_code=400, detail={
                "field": "status",
                "message": f"No se permite pasar de {incident.status.value} a {payload.status.value}. Los estados resolved y discarded son finales.",
            })
        incident.status = payload.status
        request.app.state.db.table("incidents").update(
            incident.model_dump(mode="json", include={"status", "updated_at"}), doc_ids=[document.doc_id],
        )
        request.app.state.incident_cache.clear()
    return IncidentStatusResponse.model_validate(incident)