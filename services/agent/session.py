"""Sesión autenticada del agente: qué pedidos puede consultar quien pregunta.

Pedidos autorizados = despachos con tracking (``stock_exit`` con ``exit_type = 'dispatch'``) registrados por la
cuenta autenticada (``user_uuid``). Deny by default: sin base de datos o ante cualquier error, la sesión no tiene
ningún pedido autorizado y el agente rechaza por autorización cualquier consulta de un pedido concreto.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from services.agent.guardrails import SessionContext

logger = logging.getLogger("trackflow.agent.session")
WAREHOUSE_COUNTRY = {"LA": "US", "ZGZ": "ES"}


def load_authorized_orders(engine: Any, user_uuid: str | None, limit: int = 200) -> dict[str, dict[str, Any]]:
    if engine is None or not user_uuid:
        return {}
    from sqlalchemy import text

    try:
        with engine.connect() as connection:
            rows = connection.execute(text(
                """SELECT se.tracking_number, se.warehouse, se.created_at, sku.name
                   FROM stock_exit AS se JOIN sku ON sku.id = se.sku_id
                   WHERE se.user_uuid = :user_uuid AND se.exit_type = 'dispatch' AND se.tracking_number IS NOT NULL
                   ORDER BY se.created_at DESC LIMIT :limit"""), {"user_uuid": str(user_uuid), "limit": limit}).all()
    except Exception:  # noqa: BLE001
        logger.warning("no se pudieron leer los pedidos de la sesión; se deniega por defecto", exc_info=True)
        return {}
    return {str(r[0]).upper(): {"warehouse": r[1], "country": WAREHOUSE_COUNTRY.get(r[1]),
                                "dispatched_at": r[2].isoformat() if hasattr(r[2], "isoformat") else str(r[2] or ""),
                                "product": r[3]} for r in rows}


def session_for(engine: Any, user_uuid: str | None, user_label: str | None, *, staff: bool = False,
                client_ids: Iterable[str] = ()) -> SessionContext:
    """``staff`` y ``client_ids`` salen de la cuenta autenticada (rol y marcas), nunca del mensaje."""
    clients = sorted(client_ids)
    return SessionContext(user=user_label, customer_type="b2b" if clients else "desconocido",
                          authorized_orders=load_authorized_orders(engine, user_uuid), staff=staff, client_ids=clients)
