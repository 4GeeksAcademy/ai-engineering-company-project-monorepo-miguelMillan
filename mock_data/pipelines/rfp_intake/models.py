"""Entidades de la recepción de RFP en PostgreSQL (Supabase) vía SQLModel (BRIEFING §2.3).

Tablas (migración ``supabase/migrations/20261122100000_create_rfp_intake.sql``):

* ``rfp_tickets``: el ticket (fuente de verdad del estado) y el contrato de handoff a la Parte 2.
* ``rfp_metadata``: metadatos de la RFP, el Markdown convertido, métricas de legibilidad y la clasificación.
* ``rfp_department_sections``: una fila por departamento activo con ``key_aspects`` (Parte 1), el último borrador
  y su ``EvaluationResult`` (Parte 2) y la aprobación (Parte 3).
* ``rfp_section_evaluations`` (Parte 2, migración ``20261122150000_rfp_response_generation.sql``): historial de cada
  iteración del ciclo generador-evaluador: el borrador y su ``EvaluationResult`` estructurado.
* Parte 3 (migración ``20261122200000_rfp_approval.sql``): aprobación por departamento en la sección, arbitraje en el
  ticket, ``rfp_final_documents`` (FinalDocument del BRIEFING §2.3) y ``rfp_trace_events`` (qué agente hizo qué).

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
# Parte 2: etapa de la generación/evaluación (columna aparte: la etapa de la Parte 1 se conserva).
RESPONSE_STAGES = ("generando", "evaluando", "handoff", "fallido")
# Estado de cada sección en la Parte 2.
SECTION_STATUSES = ("pendiente", "generando", "en_evaluacion", "evaluacion_superada", "needs_human_review")
# Parte 3: aprobación humana por sección y etapa del flujo de aprobación del ticket.
APPROVAL_STATUSES = ("pendiente", "esperando_aprobación", "procesando", "en_revision", "aprobada", "rechazada")
APPROVAL_STAGES = ("iniciando", "en_curso", "esperando", "completado", "bloqueado", "fallido")


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
    # Parte 2: generación y evaluación. ``response_handoff`` es el contrato rfp_response_handoff.v1 a la Parte 3.
    response_stage: Optional[str] = Field(default=None, max_length=16)
    response_attempts: int = Field(default=0)
    response_handoff: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    # Parte 3: hilo del checkpointer (namespaced por ticket), etapa del flujo de aprobación y arbitrajes.
    approval_thread_id: Optional[str] = Field(default=None, max_length=120)
    approval_run: int = Field(default=0)
    approval_stage: Optional[str] = Field(default=None, max_length=16)
    approval_note: Optional[str] = Field(default=None)
    arbitration: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
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
    # Parte 2: último borrador y su EvaluationResult (el historial completo está en rfp_section_evaluations).
    draft_content: Optional[str] = None
    evaluation_results: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    section_status: str = Field(default="pendiente", max_length=24)
    iterations: int = Field(default=0)
    needs_human_review: bool = Field(default=False)
    # Parte 3: aprobación humana por departamento (approver = responsable del BRIEFING §2.1).
    approval_status: str = Field(default="pendiente", max_length=24)
    approver: Optional[str] = Field(default=None, max_length=120)
    approved_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    approval_round: int = Field(default=0)
    approval_request: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    decision_history: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    commitments: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    approved_content: Optional[str] = None
    decided_by: Optional[str] = Field(default=None, max_length=255)
    created_at: datetime = _ts()


class SectionEvaluation(SQLModel, table=True):
    """Una iteración del ciclo generador-evaluador: el borrador evaluado y su EvaluationResult estructurado."""

    __tablename__ = "rfp_section_evaluations"
    __table_args__ = (UniqueConstraint("ticket_id", "department_id", "run", "iteration", name="uq_rfp_section_evaluation"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_id: uuid.UUID = Field(foreign_key="rfp_tickets.ticket_id", index=True)
    department_id: str = Field(max_length=16)
    run: int = Field(default=1)                         # ejecución de la Parte 2 (response_attempts)
    iteration: int
    draft_content: str
    readability: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    relevance: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    compliance: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    overall_pass: bool
    feedback_for_generator: str = ""
    generator_meta: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = _ts()


class FinalDocument(SQLModel, table=True):
    """Documento final (BRIEFING §2.3: ticket_id, sections, currency, generated_at) con las secciones aprobadas."""

    __tablename__ = "rfp_final_documents"

    ticket_id: uuid.UUID = Field(primary_key=True, foreign_key="rfp_tickets.ticket_id")
    rfp_id: str = Field(max_length=80)
    currency: str = Field(max_length=3)
    sections: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    approvals: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    arbitration: list = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    content: str
    generated_at: datetime = _ts()


class TraceEvent(SQLModel, table=True):
    """Una ejecución de nodo de cualquier parte del flujo: agente, input, output y timestamp."""

    __tablename__ = "rfp_trace_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    ticket_id: uuid.UUID = Field(foreign_key="rfp_tickets.ticket_id", index=True)
    part: str = Field(max_length=16)                    # intake | response | approval
    thread_id: Optional[str] = Field(default=None, max_length=120)
    node: str = Field(max_length=64)
    agent: str = Field(max_length=80)
    department_id: Optional[str] = Field(default=None, max_length=16)
    status: str = Field(default="ok", max_length=16)    # ok | error
    input: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    output: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    started_at: datetime = _ts()
    finished_at: datetime = _ts()
