import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordBearer
from jose import JWTError, jwt

from models import User
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
        {"sub": str(user.id), "role": user.role.value, "purpose": "access", "iat": now, "exp": now + timedelta(minutes=expires_minutes)},
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
        payload = jwt.decode(token, secret_key, algorithms=[ALGORITHM], options={"require_sub": True, "require_exp": True})
        if payload.get("purpose", "access") != "access":
            return None
        with state.lock:
            user = get_user_by_id(state.db, int(payload["sub"]))
        expires_at = float(payload["exp"])
    except (JWTError, KeyError, TypeError, ValueError, RuntimeError):
        return None
    return (user, expires_at) if user is not None and user.is_active else None