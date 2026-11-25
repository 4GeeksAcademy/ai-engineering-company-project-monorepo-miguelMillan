import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordBearer
import jwt
from jwt.exceptions import PyJWTError

from models import User, UserRole
from security_log import security_event
from user_service import get_user_by_id


load_dotenv(Path(__file__).with_name(".env"), override=False)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)
http_bearer_scheme = HTTPBearer(auto_error=False)
ALGORITHM = "HS256"


def auth_settings() -> tuple[str, int]:
    secret_key = os.environ.get("SECRET_KEY", "")
    try:
        expires_minutes = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    except ValueError as error:
        raise RuntimeError("ACCESS_TOKEN_EXPIRE_MINUTES debe ser un entero positivo.") from error
    if len(secret_key) < 32:
        raise RuntimeError("Configura SECRET_KEY en services/api/.env con al menos 32 caracteres.")
    if expires_minutes < 1:
        raise RuntimeError("ACCESS_TOKEN_EXPIRE_MINUTES debe ser mayor que cero.")
    return secret_key, expires_minutes


def create_access_token(user: User) -> tuple[str, int]:
    secret_key, expires_minutes = auth_settings()
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": str(user.id), "role": user.role.value, "purpose": "access", "ver": user.session_version, "iat": now, "exp": now + timedelta(minutes=expires_minutes)},
        secret_key,
        algorithm=ALGORITHM,
    )
    return token, expires_minutes * 60


def get_current_user(
    request: Request,
    oauth_token: Annotated[str | None, Depends(oauth2_scheme)],
    bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(http_bearer_scheme)],
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales de autenticación no válidas.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    identity = authenticate_token(request.app.state, oauth_token or (bearer.credentials if bearer is not None else None))
    if identity is None:
        raise unauthorized
    return identity[0]


def authenticate_token(state: Any, token: str | None) -> tuple[User, float] | None:
    """Usuario activo y caducidad de un JWT de acceso, o ``None``. La misma regla para HTTP, SSE y WebSocket."""
    try:
        if token is None:
            return None
        secret_key, _ = auth_settings()
        payload = jwt.decode(token, secret_key, algorithms=[ALGORITHM], options={"require": ["sub", "exp"]})
        if payload.get("purpose", "access") != "access":
            return None
        with state.lock:
            user = get_user_by_id(state.db, int(payload["sub"]))
        expires_at = float(payload["exp"])
    except (PyJWTError, KeyError, TypeError, ValueError, RuntimeError):
        return None
    # ``ver``: cambiar o restablecer la contraseña invalida los tokens ya emitidos (A07).
    if user is None or not user.is_active or payload.get("ver", 0) != user.session_version:
        return None
    return user, expires_at


STAFF_ROLES = frozenset({UserRole.admin, UserRole.manager})


def is_staff(user: Any) -> bool:
    """Personal de TrackFlow. Una cuenta autorregistrada (rol ``user``) es un cliente, nunca personal."""
    return getattr(user, "role", None) in STAFF_ROLES


def require_roles(*roles: UserRole):
    def check(request: Request, user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            security_event("access_denied", request, email=str(user.email), role=user.role.value)
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tu cuenta no tiene permiso para esta operación.")
        return user

    return check


require_staff = require_roles(UserRole.admin, UserRole.manager)
# La cuenta de servicio del servidor MCP: solo lo que sus tools necesitan (incidencias y lectura de productos).
require_staff_or_service = require_roles(UserRole.admin, UserRole.manager, UserRole.service)
