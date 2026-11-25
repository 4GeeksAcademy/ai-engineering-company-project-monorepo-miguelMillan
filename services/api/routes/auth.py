import os
from functools import lru_cache
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError
from passlib.hash import bcrypt

from models import AuthMeProfileResponse, AuthMeResponse, ChangePasswordRequest, ForgotPasswordRequest, LoginRequest, MessageResponse, ResetPasswordRequest, TokenResponse, User
from password_recovery import CONFIRMATION, request_password_reset, reset_password
from security import create_access_token, get_current_user
from security_log import account_ref, security_event
from throttle import Throttle
from user_service import get_profile_by_user_id, get_user_by_email, get_user_by_id, update_user


router = APIRouter(prefix="/auth", tags=["auth"])
MAX_PASSWORD_BYTES = 72


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """Hash de referencia: un email inexistente cuesta lo mismo que una contraseña incorrecta (sin enumeración)."""
    return bcrypt.hash("cuenta-inexistente")


def _throttle(request: Request, kind: str) -> Throttle:
    state = request.app.state
    if getattr(state, "auth_throttles", None) is None:
        state.auth_throttles = {"login": Throttle(int(os.environ.get("LOGIN_MAX_ATTEMPTS", "5")), 300),
                                "reset": Throttle(int(os.environ.get("RESET_MAX_REQUESTS", "3")), 900)}
    return state.auth_throttles[kind]


def password_matches(password: str, hashed: str | None) -> bool:
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return False
    return bcrypt.verify(password, hashed or _dummy_hash()) and hashed is not None


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(payload: ForgotPasswordRequest, request: Request, background_tasks: BackgroundTasks):
    # La respuesta es la misma siempre (no revela si la cuenta existe ni si se alcanzó el límite).
    if _throttle(request, "reset").hit(account_ref(str(payload.email))):
        security_event("password_reset_throttled", request, email=str(payload.email))
    else:
        background_tasks.add_task(request_password_reset, request.app, str(payload.email))
    return MessageResponse(message=CONFIRMATION)


@router.post("/reset-password", response_model=MessageResponse)
def reset_password_route(payload: ResetPasswordRequest, request: Request):
    reset_password(request.app, payload.token, payload.new_password)
    return MessageResponse(message="Contrasena restablecida. Ya puedes iniciar sesion.")


@router.post("/change-password", response_model=MessageResponse)
def change_password(payload: ChangePasswordRequest, request: Request, current_user: User = Depends(get_current_user)):
    with request.app.state.lock:
        user = get_user_by_id(request.app.state.db, current_user.id)
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="La sesion ya no es valida.")
        if not password_matches(payload.current_password, user.hashed_password):
            security_event("password_change_failed", request, email=str(user.email))
            raise HTTPException(status_code=400, detail="La contrasena actual es incorrecta.")
        update_user(request.app.state.db, request.app.state.lock, user.id, {"password": payload.new_password})
    security_event("password_changed", request, email=str(user.email))
    return MessageResponse(message="Contrasena actualizada correctamente.")


@router.post(
    "/login",
    response_model=TokenResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["email", "password"],
                        "properties": {
                            "email": {"type": "string", "format": "email"},
                            "password": {"type": "string"},
                        },
                        "additionalProperties": False,
                    }
                }
            },
        }
    },
)
async def login(request: Request):
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        try:
            payload = LoginRequest.model_validate(await request.json())
        except (ValidationError, ValueError) as error:
            raise HTTPException(status_code=422, detail="Indica un email y una contraseña válidos.") from error
    else:
        form = parse_qs((await request.body()).decode())
        email = (form.get("email") or form.get("username") or [None])[0]
        password = (form.get("password") or [None])[0]
        try:
            payload = LoginRequest(email=email, password=password)
        except ValidationError as error:
            raise HTTPException(status_code=422, detail="Indica un email y una contraseña válidos.") from error

    key = account_ref(str(payload.email))
    retry_after = _throttle(request, "login").hit(key)
    if retry_after:
        security_event("login_throttled", request, email=str(payload.email))
        raise HTTPException(status_code=429, detail="Demasiados intentos. Espera unos minutos o restablece tu contraseña.",
                            headers={"Retry-After": str(retry_after)})
    with request.app.state.lock:
        user = get_user_by_email(request.app.state.db, str(payload.email))
    stored = user.hashed_password if user is not None and user.is_active else None
    # bcrypt es caro a propósito: fuera del event loop, para que un intento no frene el resto de peticiones.
    if not await run_in_threadpool(password_matches, payload.password, stored):
        security_event("login_failed", request, email=str(payload.email))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email o contraseña incorrectos.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    _throttle(request, "login").clear(key)
    security_event("login_ok", request, email=str(payload.email), role=user.role.value)
    token, expires_in = create_access_token(user)
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.get("/me", response_model=AuthMeResponse)
def get_me(request: Request, current_user: User = Depends(get_current_user)):
    with request.app.state.lock:
        profile = get_profile_by_user_id(request.app.state.db, current_user.id)
    safe_profile = AuthMeProfileResponse(name=profile.name, phone=profile.phone, address=profile.address) if profile else None
    return AuthMeResponse(email=current_user.email, role=current_user.role, profile=safe_profile)