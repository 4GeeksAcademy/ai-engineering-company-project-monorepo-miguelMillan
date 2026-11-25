"""Registro de eventos de seguridad (OWASP A09): una línea JSON por evento en el logger ``trackflow.security``.

Nunca contiene contraseñas, tokens ni emails: la cuenta se identifica con un HMAC del email (permite correlacionar
intentos sin guardar el dato personal, RGPD art. 5.1.c).
"""

import hashlib
import hmac
import json
import logging
import os
import sys
from datetime import datetime, timezone

logger = logging.getLogger("trackflow.security")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)


def account_ref(email: str | None) -> str | None:
    if not email:
        return None
    key = os.environ.get("SECRET_KEY", "").encode()
    return hmac.new(key, email.strip().lower().encode(), hashlib.sha256).hexdigest()[:16]


def security_event(event: str, request=None, *, email: str | None = None, **fields) -> None:
    record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, "account": account_ref(email), **fields}
    if request is not None:
        record |= {"method": request.scope.get("method", "WS"), "path": request.url.path,
                   "ip": request.client.host if request.client else None}
    logger.warning(json.dumps({k: v for k, v in record.items() if v is not None}, ensure_ascii=False))
