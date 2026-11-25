"""Acciones irreversibles pendientes de que otra persona las confirme (``confirmations.py``)."""

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from confirmations import decide, list_actions
from routes.incidents import change_status
from routes.inventory import execute_outbound
from schemas import StockExitCreate
from security import require_staff

router = APIRouter(prefix="/actions", tags=["human confirmation"])


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=3, max_length=500)  # por qué se confirma o se rechaza: queda registrado


def _executors(request: Request) -> dict:
    def dispatch(action: dict):
        with Session(request.app.state.engine) as session:
            return execute_outbound(StockExitCreate(**action["payload"]), session, action["requested_by_uuid"]).model_dump(mode="json")

    def close_incident(action: dict):
        return change_status(request, action["payload"]["incident_id"], action["payload"]["status"]).model_dump(mode="json")

    return {"dispatch_high_value": dispatch, "incident_close": close_incident}


@router.get("")
def get_actions(request: Request, status: Literal["pending", "executed", "rejected", "failed", "expired", "all"] = "pending",
                _user=Depends(require_staff)):
    return {"actions": list_actions(request.app, status)}


@router.post("/{action_id}/confirm")
def confirm_action(action_id: str, body: Decision, request: Request, user=Depends(require_staff)):
    return decide(request, action_id, True, body.reason, user, _executors(request))


@router.post("/{action_id}/reject")
def reject_action(action_id: str, body: Decision, request: Request, user=Depends(require_staff)):
    return decide(request, action_id, False, body.reason, user, {})
