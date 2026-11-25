"""Límite de peticiones por cuenta a todo lo que dispara un modelo (HTTP y WebSocket)."""

import os

from fastapi import Depends, HTTPException, Request

from security import get_current_user, require_staff
from security_log import security_event
from throttle import Throttle

# (variable de entorno, máximo por defecto, ventana en segundos)
LIMITS = {
    "ai": (("AI_REQUESTS_PER_MINUTE", 15, 60), ("AI_REQUESTS_PER_DAY", 300, 86_400)),
    "rfp": (("RFP_RUNS_PER_HOUR", 20, 3600),),  # cada RFP son muchas llamadas al modelo
}


def _limit(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        return default
    return value if value > 0 else default


def check_rate(app, user, kind: str = "ai") -> int:
    """0 si se permite; si no, segundos hasta poder repetir. Cuenta por cuenta, no por IP (véase ``throttle.py``)."""
    throttles = getattr(app.state, "ai_throttles", None)
    if throttles is None:
        throttles = app.state.ai_throttles = {
            k: [Throttle(_limit(name, default), window) for name, default, window in rules] for k, rules in LIMITS.items()}
    for throttle in throttles[kind]:
        retry_after = throttle.hit(str(user.user_uuid))
        if retry_after:
            return retry_after
    return 0


def _enforce(request: Request, user, kind: str):
    retry_after = check_rate(request.app, user, kind)
    if retry_after:
        security_event("ai_rate_limited", request, email=str(user.email), kind=kind)
        raise HTTPException(status_code=429, detail="Has hecho demasiadas consultas al asistente. Espera un momento.",
                            headers={"Retry-After": str(retry_after)})
    return user


def limited_user(request: Request, user=Depends(get_current_user)):
    return _enforce(request, user, "ai")


def limited_staff_rfp(request: Request, user=Depends(require_staff)):
    return _enforce(request, user, "rfp")
