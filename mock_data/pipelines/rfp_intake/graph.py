"""Grafo LangGraph de recepción de RFP (dedicado; no comparte nada con el grafo del agente de CX).

    START → convert → extract → classify ─┬─ no es RFP → discard → END                      (ticket: descartado)
                                          └─ RFP → orchestrate ─Send×N─► department_worker ─┐
                                                                                             ▼
                                                              synthesize → handoff → END    (ticket: analisis_completo)

Cada nodo persiste su resultado y avanza ``pipeline_stage`` del ticket. ``run_ticket`` es el punto de entrada
que usan el worker de Celery (``services/api/tasks.py``) y el CLI de reproceso (``scripts/rfp_intake.py``).
"""

from __future__ import annotations

import logging
import operator
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from mock_data.pipelines.rfp_intake import agents
from mock_data.pipelines.rfp_intake.departments import DEPARTMENTS
from mock_data.pipelines.rfp_intake.ingest import RfpMetadata, convert_pdf, extract_metadata, readability_metrics
from mock_data.pipelines.rfp_intake.llm import LlmFn, chat_json
from mock_data.pipelines.rfp_intake.repository import RfpRepository
from mock_data.pipelines.rfp_intake.tracing import traced

logger = logging.getLogger("trackflow.rfp_intake")


class IntakeState(TypedDict):
    ticket_id: str
    rfp_id: str
    raw_pdf_path: str
    markdown: NotRequired[str]
    metadata: NotRequired[dict[str, Any]]
    readability: NotRequired[dict[str, Any]]
    classification: NotRequired[dict[str, Any]]
    force_accept: NotRequired[bool]
    sections: Annotated[list[dict[str, Any]], operator.add]
    handoff: NotRequired[dict[str, Any]]
    trace: Annotated[list[dict[str, Any]], operator.add]   # agente, input, output y timestamp de cada nodo


class WorkerInput(TypedDict):
    ticket_id: str
    subtask: dict[str, Any]


def build_graph(repo: RfpRepository, llm_fn: LlmFn | None = chat_json, converter=convert_pdf):
    def stage(state: IntakeState, name: str, **fields: Any) -> None:
        repo.update_ticket(state["ticket_id"], pipeline_stage=name, **fields)

    def convert(state: IntakeState) -> dict[str, Any]:
        markdown = converter(Path(state["raw_pdf_path"]))
        repo.save_metadata(state["ticket_id"], markdown=markdown)
        stage(state, "convertido")
        return {"markdown": markdown}

    def extract(state: IntakeState) -> dict[str, Any]:
        meta = extract_metadata(state["markdown"])
        metrics = readability_metrics(state["markdown"])
        details = {k: v for k, v in meta.to_dict().items() if k not in (
            "client_name", "client_country", "currency", "services_requested", "monthly_volume", "sku_count", "deadline",
            "deadline_text", "budget_range", "departments_needed")}
        repo.save_metadata(state["ticket_id"], client_name=meta.client_name, client_country=meta.client_country,
                           currency=meta.currency, services_requested=meta.services_requested,
                           monthly_volume=meta.monthly_volume, sku_count=meta.sku_count, deadline=meta.deadline,
                           deadline_text=meta.deadline_text, budget_range=meta.budget_range,
                           departments_needed=meta.departments_needed, readability=metrics, details=details)
        if meta.rfp_reference:
            stage(state, "metadatos", rfp_id=meta.rfp_reference)
        else:
            stage(state, "metadatos")
        return {"metadata": meta.to_dict(), "readability": metrics, "rfp_id": meta.rfp_reference or state["rfp_id"]}

    def classify(state: IntakeState) -> dict[str, Any]:
        result = agents.classify(state["markdown"], llm_fn)
        if state.get("force_accept") and not result.is_rfp:
            result.is_rfp, result.decision_rule, result.needs_sales_review = True, "forzado_por_humano", True
            result.reason = f"Aceptado manualmente tras descarte: {result.reason}"
        repo.save_metadata(state["ticket_id"], classification=result.to_dict())
        stage(state, "clasificado")
        return {"classification": result.to_dict()}

    def route_after_classify(state: IntakeState) -> str:
        return "orchestrate" if state["classification"]["is_rfp"] else "discard"

    def discard(state: IntakeState) -> dict[str, Any]:
        stage(state, "clasificado", status="descartado", discard_reason=state["classification"]["reason"], last_error=None)
        return {}

    def orchestrate(state: IntakeState) -> dict[str, Any]:
        stage(state, "orquestado")
        return {}

    def fan_out(state: IntakeState) -> list[Send] | str:
        subtasks = agents.orchestrate(state["markdown"], RfpMetadata(**state["metadata"]))
        if not subtasks:  # RFP válida que no pide nada que cubran los tres departamentos
            return "synthesize"
        return [Send("department_worker", {"ticket_id": state["ticket_id"], "subtask": asdict(s)}) for s in subtasks]

    def department_worker(payload: WorkerInput) -> dict[str, Any]:
        result = agents.run_worker(agents.Subtask(**payload["subtask"]), llm_fn)
        return {"sections": [result.to_dict()]}

    def synthesize(state: IntakeState) -> dict[str, Any]:
        sections = [agents.SectionResult(**s) for s in state.get("sections", [])]
        repo.replace_sections(state["ticket_id"], [
            {"department_id": s.department_id, "key_aspects": s.key_aspects, "open_questions": s.open_questions,
             "worker_meta": {**s.meta, "facts": s.facts}, "approver": DEPARTMENTS[s.department_id].owner}
            for s in sections])
        stage(state, "workers")
        classification = agents.Classification(**state["classification"])
        handoff = agents.synthesize(state["ticket_id"], state["rfp_id"], RfpMetadata(**state["metadata"]), classification,
                                    sections)
        stage(state, "sintetizado")
        return {"handoff": handoff}

    def handoff(state: IntakeState) -> dict[str, Any]:
        # Enrutamiento a la Parte 2: estado analisis_completo + contrato rfp_handoff.v1 en el propio ticket.
        stage(state, "handoff", status="analisis_completo", handoff=state["handoff"], last_error=None, discard_reason=None)
        return {}

    agents_by_node = {"convert": "markitdown_converter", "extract": "metadata_extractor", "classify": "classifier",
                      "discard": "classifier", "orchestrate": "orchestrator",
                      "department_worker": lambda s: f"{s['subtask']['department_id']}_worker",
                      "synthesize": "synthesizer", "handoff": "router"}
    builder = StateGraph(IntakeState)
    for name, fn in (("convert", convert), ("extract", extract), ("classify", classify), ("discard", discard),
                     ("orchestrate", orchestrate), ("department_worker", department_worker),
                     ("synthesize", synthesize), ("handoff", handoff)):
        builder.add_node(name, traced(repo, "intake", name, agents_by_node[name], fn))
    builder.add_edge(START, "convert")
    builder.add_edge("convert", "extract")
    builder.add_edge("extract", "classify")
    builder.add_conditional_edges("classify", route_after_classify, {"orchestrate": "orchestrate", "discard": "discard"})
    builder.add_edge("discard", END)
    builder.add_conditional_edges("orchestrate", fan_out, ["department_worker", "synthesize"])
    builder.add_edge("department_worker", "synthesize")
    builder.add_edge("synthesize", "handoff")
    builder.add_edge("handoff", END)
    return builder.compile()


def run_ticket(ticket_id: str, repo: RfpRepository, llm_fn: LlmFn | None = chat_json, converter=convert_pdf,
               force_accept: bool = False) -> dict[str, Any]:
    """Ejecuta (o re-ejecuta) el pipeline de un ticket. Si falla, el ticket queda ``analizando`` con
    ``pipeline_stage='fallido'`` y ``last_error``: nunca ``descartado`` por un error técnico."""
    ticket = repo.get_ticket(ticket_id)
    if ticket is None:
        raise KeyError(ticket_id)
    repo.update_ticket(ticket_id, status="analizando", pipeline_stage="recibido", attempts=ticket.attempts + 1,
                       last_error=None, discard_reason=None, handoff=None)
    graph = build_graph(repo, llm_fn, converter)
    try:
        state = graph.invoke({"ticket_id": str(ticket_id), "rfp_id": ticket.rfp_id, "raw_pdf_path": ticket.raw_pdf_path,
                              "force_accept": force_accept, "sections": [], "trace": []})
    except Exception as error:
        logger.exception("pipeline RFP falló; ticket=%s", ticket_id)
        repo.update_ticket(ticket_id, pipeline_stage="fallido", last_error=f"{type(error).__name__}: {str(error)[:900]}")
        raise
    final = repo.get_ticket(ticket_id)
    return {"ticket_id": str(ticket_id), "status": final.status, "pipeline_stage": final.pipeline_stage,
            "classification": state.get("classification"), "handoff": state.get("handoff")}
