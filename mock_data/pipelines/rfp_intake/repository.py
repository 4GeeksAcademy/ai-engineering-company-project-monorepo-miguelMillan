"""Persistencia de la recepción de RFP (SQLModel sobre PostgreSQL/Supabase).

El ticket es la fuente de verdad: cada etapa del pipeline actualiza ``pipeline_stage`` en su propia transacción,
así que si el job muere a mitad el ticket dice exactamente hasta dónde llegó (y ``last_error`` por qué).
"""

from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager, nullcontext
from typing import Any, Iterator

from sqlmodel import Session, select

from sqlalchemy import func

from mock_data.pipelines.rfp_intake.models import (DepartmentSection, FinalDocument, RfpMetadataRecord, RfpTicket,
                                                   SectionEvaluation, TraceEvent, _now)


class RfpRepository:
    def __init__(self, engine):
        self.engine = engine
        # Las ramas de departamento de la Parte 2 escriben en paralelo (hilos del grafo): cada escritura es su propia
        # transacción y además se serializa en proceso (en SQLite de tests comparten una sola conexión).
        self._write_lock = threading.RLock()
        # SQLite (tests) comparte UNA conexión entre los hilos del grafo: ahí también se serializan las lecturas.
        # En PostgreSQL cada sesión toma su propia conexión del pool y las lecturas van en paralelo.
        self._read_lock = self._write_lock if engine.url.get_backend_name() == "sqlite" else nullcontext()

    @contextmanager
    def _session(self, write: bool = False) -> Iterator[Session]:
        with (self._write_lock if write else self._read_lock), Session(self.engine) as session:
            yield session

    # ---------------------------------------------------------------- tickets
    def create_ticket(self, *, rfp_id: str, raw_pdf_path: str, original_filename: str, uploaded_by: str | None,
                      ticket_id: uuid.UUID | None = None) -> RfpTicket:
        ticket = RfpTicket(ticket_id=ticket_id or uuid.uuid4(), rfp_id=rfp_id, raw_pdf_path=raw_pdf_path,
                           original_filename=original_filename, uploaded_by=uploaded_by)
        with self._session() as session:
            session.add(ticket)
            session.commit()
            session.refresh(ticket)
        return ticket

    def get_ticket(self, ticket_id: uuid.UUID | str) -> RfpTicket | None:
        with self._session() as session:
            return session.get(RfpTicket, uuid.UUID(str(ticket_id)))

    def update_ticket(self, ticket_id: uuid.UUID | str, **fields: Any) -> RfpTicket:
        with self._session(write=True) as session:
            ticket = session.get(RfpTicket, uuid.UUID(str(ticket_id)))
            if ticket is None:
                raise KeyError(ticket_id)
            for name, value in fields.items():
                setattr(ticket, name, value)
            ticket.updated_at = _now()
            session.add(ticket)
            session.commit()
            session.refresh(ticket)
            return ticket

    def list_tickets(self, limit: int = 50) -> list[RfpTicket]:
        with self._session() as session:
            return list(session.exec(select(RfpTicket).order_by(RfpTicket.created_at.desc()).limit(limit)))

    # ---------------------------------------------------------------- metadatos y secciones
    def save_metadata(self, ticket_id: uuid.UUID | str, **fields: Any) -> None:
        tid = uuid.UUID(str(ticket_id))
        with self._session() as session:
            record = session.get(RfpMetadataRecord, tid) or RfpMetadataRecord(ticket_id=tid)
            for name, value in fields.items():
                setattr(record, name, value)
            session.add(record)
            session.commit()

    def get_metadata(self, ticket_id: uuid.UUID | str) -> RfpMetadataRecord | None:
        with self._session() as session:
            return session.get(RfpMetadataRecord, uuid.UUID(str(ticket_id)))

    def replace_sections(self, ticket_id: uuid.UUID | str, sections: list[dict[str, Any]]) -> None:
        """Reproceso idempotente: las secciones de la Parte 1 se reemplazan, no se duplican."""
        tid = uuid.UUID(str(ticket_id))
        with self._session() as session:
            for old in session.exec(select(DepartmentSection).where(DepartmentSection.ticket_id == tid)):
                session.delete(old)
            session.flush()
            for section in sections:
                session.add(DepartmentSection(ticket_id=tid, **section))
            session.commit()

    def get_sections(self, ticket_id: uuid.UUID | str) -> list[DepartmentSection]:
        with self._session() as session:
            return list(session.exec(select(DepartmentSection).where(DepartmentSection.ticket_id == uuid.UUID(str(ticket_id)))
                                     .order_by(DepartmentSection.department_id)))

    # ---------------------------------------------------------------- Parte 2: borradores y evaluaciones
    def update_section(self, ticket_id: uuid.UUID | str, department_id: str, **fields: Any) -> DepartmentSection:
        tid = uuid.UUID(str(ticket_id))
        with self._session(write=True) as session:
            section = session.exec(select(DepartmentSection).where(DepartmentSection.ticket_id == tid,
                                                                   DepartmentSection.department_id == department_id)).first()
            if section is None:
                raise KeyError(f"{ticket_id}:{department_id}")
            for name, value in fields.items():
                setattr(section, name, value)
            session.add(section)
            session.commit()
            session.refresh(section)
            return section

    def add_evaluation(self, **fields: Any) -> SectionEvaluation:
        fields["ticket_id"] = uuid.UUID(str(fields["ticket_id"]))
        with self._session(write=True) as session:
            record = SectionEvaluation(**fields)
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def get_evaluations(self, ticket_id: uuid.UUID | str, run: int | None = None) -> list[SectionEvaluation]:
        query = select(SectionEvaluation).where(SectionEvaluation.ticket_id == uuid.UUID(str(ticket_id)))
        if run is not None:
            query = query.where(SectionEvaluation.run == run)
        with self._session() as session:
            return list(session.exec(query.order_by(SectionEvaluation.department_id, SectionEvaluation.run,
                                                    SectionEvaluation.iteration)))

    def get_section(self, ticket_id: uuid.UUID | str, department_id: str) -> DepartmentSection | None:
        with self._session() as session:
            return session.exec(select(DepartmentSection).where(DepartmentSection.ticket_id == uuid.UUID(str(ticket_id)),
                                                                DepartmentSection.department_id == department_id)).first()

    def next_evaluation_run(self, ticket_id: uuid.UUID | str, department_id: str) -> int:
        """Siguiente número de ejecución del ciclo generador-evaluador de UNA sección (las revisiones de la Parte 3
        reutilizan el ciclo de la Parte 2 sin pisar su historial)."""
        with self._session() as session:
            current = session.exec(select(func.max(SectionEvaluation.run)).where(
                SectionEvaluation.ticket_id == uuid.UUID(str(ticket_id)), SectionEvaluation.department_id == department_id)).one()
        return (current or 0) + 1

    # ---------------------------------------------------------------- Parte 3: documento final y trazas
    def save_final_document(self, ticket_id: uuid.UUID | str, **fields: Any) -> FinalDocument:
        tid = uuid.UUID(str(ticket_id))
        with self._session(write=True) as session:
            document = session.get(FinalDocument, tid) or FinalDocument(ticket_id=tid, **fields)
            for name, value in fields.items():
                setattr(document, name, value)
            document.generated_at = _now()
            session.add(document)
            session.commit()
            session.refresh(document)
            return document

    def get_final_document(self, ticket_id: uuid.UUID | str) -> FinalDocument | None:
        with self._session() as session:
            return session.get(FinalDocument, uuid.UUID(str(ticket_id)))

    def add_trace(self, **fields: Any) -> None:
        fields["ticket_id"] = uuid.UUID(str(fields["ticket_id"]))
        with self._session(write=True) as session:
            session.add(TraceEvent(**fields))
            session.commit()

    def get_trace(self, ticket_id: uuid.UUID | str, part: str | None = None) -> list[TraceEvent]:
        query = select(TraceEvent).where(TraceEvent.ticket_id == uuid.UUID(str(ticket_id)))
        if part:
            query = query.where(TraceEvent.part == part)
        with self._session() as session:
            return list(session.exec(query.order_by(TraceEvent.id)))
