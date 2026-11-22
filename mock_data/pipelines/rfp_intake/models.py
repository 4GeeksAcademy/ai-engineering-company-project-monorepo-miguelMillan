"""Entidades de la recepción de RFP en PostgreSQL (Supabase) vía SQLModel (BRIEFING §2.3).

Tablas (migración ``supabase/migrations/20261122100000_create_rfp_intake.sql``):

* ``rfp_tickets``: el ticket (fuente de verdad del estado) y el contrato de handoff a la Parte 2.
* ``rfp_metadata``: metadatos de la RFP, el Markdown convertido, métricas de legibilidad y la clasificación.
* ``rfp_department_sections``: una fila por departamento activo con ``key_aspects`` (Parte 1); las columnas
  de borrador/evaluación/aprobación quedan listas para las Partes 2 y 3.

TinyDB y los archivos JSON no son fuente de verdad de estas entidades.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel

# Estados del ticket a lo largo del Hito 9 (BRIEFING §2.3). La Parte 1 usa los tres primeros.
TICKET_STATUSES = ("analizando", "descartado", "analisis_completo", "generando_borrador", "en_evaluación",
                   "needs_human_review", "esperando_aprobación", "terminado")
# Etapas internas del pipeline: el ticket sabe exactamente hasta dónde llegó si un job falla a mitad.
PIPELINE_STAGES = ("recibido", "convertido", "metadatos", "clasificado", "orquestado", "workers", "sintetizado",
                   "handoff", "fallido")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(**kwargs: Any) -> Any:
    return Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False, **kwargs))


class RfpTicket(SQLModel, table=True):
    __tablename__ = "rfp_tickets"

    ticket_id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    rfp_id: str = Field(max_length=80, index=True)
    status: str = Field(default="analizando", max_length=32, index=True)
    pipeline_stage: str = Field(default="recibido", max_length=32)
    raw_pdf_path: str = Field(max_length=500)
    original_filename: str = Field(max_length=255)
    uploaded_by: Optional[str] = Field(default=None, max_length=255)
    attempts: int = Field(default=0)
    last_error: Optional[str] = Field(default=None)
    discard_reason: Optional[str] = Field(default=None)
    # Contrato de handoff a la Parte 2 (rfp_handoff.v1): ticket_id + payload del synthesizer.
    handoff: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = _ts()
    updated_at: datetime = _ts()


class RfpMetadataRecord(SQLModel, table=True):
    __tablename__ = "rfp_metadata"

    ticket_id: uuid.UUID = Field(primary_key=True, foreign_key="rfp_tickets.ticket_id")
    client_name: Optional[str] = Field(default=None, max_length=255)
    client_country: Optional[str] = Field(default=None, max_length=2)
    currency: Optional[str] = Field(default=None, max_length=3)
    services_requested: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    monthly_volume: Optional[int] = None
    sku_count: Optional[int] = None
    deadline: Optional[str] = Field(default=None, max_length=10)
    deadline_text: Optional[str] = Field(default=None, max_length=120)
    budget_range: Optional[str] = Field(default=None, max_length=120)
    departments_needed: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    readability: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    details: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))  # resto de metadatos + evidencias
    classification: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    markdown: str = ""
    created_at: datetime = _ts()


class DepartmentSection(SQLModel, table=True):
    __tablename__ = "rfp_department_sections"
    __table_args__ = (UniqueConstraint("ticket_id", "department_id", name="uq_rfp_section_ticket_department"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_id: uuid.UUID = Field(foreign_key="rfp_tickets.ticket_id", index=True)
    department_id: str = Field(max_length=16)          # warehouse | lastmile | reverse
    key_aspects: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    open_questions: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    worker_meta: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    # Partes 2 y 3
    draft_content: Optional[str] = None
    evaluation_results: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    approval_status: str = Field(default="pendiente", max_length=24)
    approver: Optional[str] = Field(default=None, max_length=120)  # responsable del BRIEFING §2.1
    approved_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: datetime = _ts()
