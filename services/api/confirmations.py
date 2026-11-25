"""Confirmación humana de las acciones irreversibles (CONTEXT.md §5, NIST PROTECT).

Dos mecanismos, según quién actúa:

* **Segunda persona** (``request_action`` → ``decide``): la acción queda pendiente y solo se ejecuta cuando OTRA persona del
  personal la confirma. Para el despacho de alto valor y para todo lo que pida una cuenta automática (rol ``service``, es
  decir, un agente a través de MCP).
* **Confirmación explícita** (``require_confirmation``): borrar exige repetir la petición con el código recibido, que es de
  un solo uso y solo vale para esa persona, esa acción y ese recurso.
"""

import hmac
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException, Request

from security_log import security_event

PENDING_HOURS = 24
CONFIRMATION_SECONDS = 300
MAX_CODES = 1000
TABLE = "pending_actions"

# CONTEXT.md §5 (y las demás acciones sin vuelta atrás que existen en el código). ``path`` dice cómo se protege hoy.
IRREVERSIBLE_ACTIONS = {
    "approve_return_above_threshold": {"context": "§5.1", "path": None,
                                       "control": "No existe motor de devoluciones ni ninguna ruta o tool que apruebe una."},
    "dispatch_high_value": {"context": "§5.2", "path": "POST /inventory/orders/outbound", "control": "segunda persona"},
    "change_carrier_in_transit": {"context": "§5.3", "path": None,
                                  "control": "Una salida registrada no se puede modificar: no hay ruta ni tool que lo haga."},
    "share_b2b_data": {"context": "§5.4", "path": None,
                       "control": "El agente filtra por marca y los informes son solo para personal. No hay función de compartir."},
    "incident_close": {"context": "estado final", "path": "PATCH /api/incidents/{id}/status", "control": "segunda persona si lo pide un agente"},
    "supplier_delete": {"context": "borrar datos", "path": "DELETE /suppliers/{id}", "control": "confirmación explícita"},
    "user_delete": {"context": "borrar datos", "path": "DELETE /users/{id}", "control": "confirmación explícita"},
    "rfp_section_decision": {"context": "aprobar procesos", "path": "POST /rfp-intake/.../decision", "control": "cuenta del responsable"},
    "memory_write": {"context": "afecta a todas las conversaciones", "path": "POST /agent/memory/.../confirm", "control": "personal"},
}


def high_value_threshold() -> float:
    """Importe (USD en LA, EUR en ZGZ) desde el que un despacho necesita otra persona.

    CONTEXT.md no fija la cifra («alto valor», «definido por el equipo»): 1000 es provisional y la ratifica negocio."""
    try:
        value = float(os.environ.get("HIGH_VALUE_DISPATCH_THRESHOLD", "1000"))
    except ValueError:
        return 0.0  # mal configurado: todo necesita confirmación
    return value if value > 0 else 0.0


def _table(app):
    return app.state.db.table(TABLE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def request_action(request: Request, kind: str, payload: dict, summary: str, user) -> dict:
    action = {"id": str(uuid4()), "kind": kind, "payload": payload, "summary": summary, "status": "pending",
              "requested_by": str(user.email), "requested_by_uuid": str(user.user_uuid), "requested_role": user.role.value,
              "requested_at": _now().isoformat(), "expires_at": (_now() + timedelta(hours=PENDING_HOURS)).isoformat(),
              "decided_by": None, "decided_at": None, "reason": None}
    with request.app.state.lock:
        _table(request.app).insert(action)
    security_event("action_requested", request, email=str(user.email), action=kind, action_id=action["id"])
    return action


def public(action: dict) -> dict:
    return {k: v for k, v in action.items() if k not in ("payload", "requested_by_uuid")}


def list_actions(app, status: str = "pending") -> list[dict]:
    with app.state.lock:
        rows = [dict(d) for d in _table(app).all()]
    now = _now().isoformat()
    for row in rows:
        if row["status"] == "pending" and row["expires_at"] <= now:
            row["status"] = "expired"
    return [public(r) for r in rows if status == "all" or r["status"] == status]


def decide(request: Request, action_id: str, approve: bool, reason: str, user, executors: dict) -> dict:
    """Reserva la acción bajo lock (no se ejecuta dos veces) y, si se aprueba, la ejecuta revalidándola."""
    app = request.app
    with app.state.lock:
        document = _table(app).get(lambda d: d["id"] == action_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Acción no encontrada.")
        if document["status"] != "pending" or document["expires_at"] <= _now().isoformat():
            raise HTTPException(status_code=409, detail="La acción ya no está pendiente.")
        if approve and document["requested_by_uuid"] == str(user.user_uuid):
            security_event("action_self_approval_denied", request, email=str(user.email), action=document["kind"], action_id=action_id)
            raise HTTPException(status_code=403, detail="Debe confirmarla otra persona, no quien la solicitó.")
        _table(app).update({"status": "executing" if approve else "rejected", "decided_by": str(user.email),
                            "decided_at": _now().isoformat(), "reason": reason}, doc_ids=[document.doc_id])
    result = None
    if approve:
        try:
            result = executors[document["kind"]](document)
        except Exception:
            with app.state.lock:
                _table(app).update({"status": "failed"}, doc_ids=[document.doc_id])
            security_event("action_failed", request, email=str(user.email), action=document["kind"], action_id=action_id)
            raise
        with app.state.lock:
            _table(app).update({"status": "executed"}, doc_ids=[document.doc_id])
    security_event("action_confirmed" if approve else "action_rejected", request, email=str(user.email),
                   action=document["kind"], action_id=action_id)
    with app.state.lock:
        final = dict(_table(app).get(doc_id=document.doc_id))
    return {**public(final), "result": result}


def require_confirmation(request: Request, kind: str, resource: str, summary: str, user) -> None:
    """Primera petición: 428 con un código. Segunda, con ``X-Confirmation: <código>``: se permite una vez."""
    codes = getattr(request.app.state, "confirmation_codes", None)
    if codes is None:
        codes = request.app.state.confirmation_codes = {}
    key, now = (str(user.user_uuid), kind, resource), time.monotonic()
    sent = request.headers.get("x-confirmation", "")
    with request.app.state.lock:
        code, expires = codes.get(key, ("", 0.0))
        if sent and code and expires > now and hmac.compare_digest(sent.encode(), code.encode()):
            del codes[key]
            security_event("action_confirmed", request, email=str(user.email), action=kind, resource=resource)
            return
        code = secrets.token_urlsafe(16)
        codes[key] = (code, now + CONFIRMATION_SECONDS)
        while len(codes) > MAX_CODES:
            codes.pop(next(iter(codes)))
    raise HTTPException(status_code=428, detail={
        "code": "CONFIRMATION_REQUIRED", "action": kind, "summary": summary, "confirmation": code,
        "expires_in": CONFIRMATION_SECONDS, "message": "Acción irreversible: repite la petición con X-Confirmation para confirmarla."})
