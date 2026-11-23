"""Evento en tiempo real ``rfp_ticket_created`` (BRIEFING «Sistemas en Tiempo Real», §2 y §3).

Se registra cuando el documento se clasificó como RFP válida y el flujo empieza a procesarlo (``status = analyzing``),
no al subir el PDF: un documento descartado nunca notifica. Queda en el outbox ``rfp_ticket_events`` (PostgreSQL), que
une el worker de Celery, donde se clasifica, con el proceso del API, donde viven los streams SSE.

El payload usa los nombres y valores del BRIEFING. La base conserva los suyos (``analizando``, ``last_mile``…): la
traducción ocurre solo aquí, en el borde del evento.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mock_data.pipelines.rfp_intake.departments import DEPARTMENT_BY_SERVICE

RFP_TICKET_CREATED = "rfp_ticket_created"
# Estados internos → estados del BRIEFING §2. ``needs_human_review`` no existe en él y no se emite.
WIRE_STATUS = {"analizando": "analyzing", "analisis_completo": "intake_complete", "generando_borrador": "drafting",
               "en_evaluación": "under_evaluation", "esperando_aprobación": "waiting_for_approval", "terminado": "done",
               "descartado": "discarded"}


def _utc_z(value: datetime) -> str:
    if value.tzinfo is None:  # SQLite devuelve fechas sin zona; siempre se guardan en UTC
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ticket_created_payload(ticket, metadata: dict[str, Any]) -> dict[str, Any]:
    """Solo los campos del §3, sin sobre ``{"event", "data"}`` ni contenido del documento."""
    return {
        "ticket_id": str(ticket.ticket_id), "rfp_id": ticket.rfp_id,
        "client_name": metadata.get("client_name"), "client_country": metadata.get("client_country"),
        "services_requested": [DEPARTMENT_BY_SERVICE[s] for s in metadata.get("services_requested") or []],
        "status": WIRE_STATUS[ticket.status], "created_at": _utc_z(ticket.created_at),
    }
