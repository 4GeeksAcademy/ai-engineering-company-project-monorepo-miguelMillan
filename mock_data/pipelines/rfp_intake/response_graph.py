"""Grafo LangGraph de la Parte 2: generación y evaluación de la propuesta económica por departamento.

Arranca SOLO desde tickets que la Parte 1 dejó listos: ``status = analisis_completo`` + contrato ``rfp_handoff.v1``
con ``next_status = generando_borrador`` en ``rfp_tickets.handoff``. La entrada es ``ticket_id`` + el payload del
synthesizer (workstreams con ``key_aspects``); el PDF y el Markdown no se vuelven a leer.

    START → load_handoff ─Send×N─► department_section ─┐            (ticket: generando_borrador)
                                                      ▼
                                       assemble → END              (ticket: esperando_aprobación | needs_human_review)

    department_section = subgrafo del ciclo generador-evaluador de UN departamento (su agente generador propio,
    ``generators/<department_id>.py``). Las ramas de departamento corren en paralelo y no se esperan entre sí:
    solo ``assemble`` espera a todas.

        START → generate ─┬─► eval_readability ─┐
                          ├─► eval_relevance  ──┼─► judge ─┬─ pasa / límite agotado → END
                          └─► eval_compliance ─┘           └─ falla y quedan iteraciones → generate (con feedback)

Los tres evaluadores corren en paralelo y cada uno escribe SOLO su clave del estado (``readability``,
``relevance``, ``compliance``): no hay escrituras concurrentes sobre el mismo campo (LangGraph lo rechazaría).
``judge`` es el único que une los tres bloques, persiste el ``EvaluationResult`` y decide la ruta.
"""

from __future__ import annotations

import logging
import operator
import os
from typing import Annotated, Any, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from mock_data.pipelines.rfp_intake import evaluators
from mock_data.pipelines.rfp_intake.departments import DEPARTMENTS
from mock_data.pipelines.rfp_intake.generation import HandoffError, SectionInput, section_inputs_from_handoff
from mock_data.pipelines.rfp_intake.generators import generator_for
from mock_data.pipelines.rfp_intake.llm import LlmFn, chat_json
from mock_data.pipelines.rfp_intake.repository import RfpRepository
from mock_data.pipelines.rfp_intake.tracing import traced

logger = logging.getLogger("trackflow.rfp_response")
RESPONSE_CONTRACT = "rfp_response_handoff.v1"
DEFAULT_MAX_ITERATIONS = 3
# Desde estos estados se puede (re)generar. analisis_completo es la entrada normal desde la Parte 1; los demás
# permiten regenerar tras un fallo técnico o tras una revisión humana, siempre antes de cualquier aprobación.
GENERATION_ENTRY_STATUSES = ("analisis_completo", "generando_borrador", "en_evaluación", "needs_human_review",
                             "esperando_aprobación")


def max_iterations_from_env() -> int:
    try:
        return max(1, int(os.environ.get("RFP_MAX_ITERATIONS", DEFAULT_MAX_ITERATIONS)))
    except ValueError:
        return DEFAULT_MAX_ITERATIONS


class SectionState(TypedDict):
    ticket_id: str
    run: int
    max_iterations: int
    inp: dict[str, Any]
    iteration: int
    draft: NotRequired[str]
    narrative: NotRequired[str]
    generator_meta: NotRequired[dict[str, Any]]
    readability: NotRequired[dict[str, Any]]
    relevance: NotRequired[dict[str, Any]]
    compliance: NotRequired[dict[str, Any]]
    evaluation: NotRequired[dict[str, Any]]
    feedback: NotRequired[str]
    history: NotRequired[list[dict[str, Any]]]
    final_status: NotRequired[str]
    # La Parte 3 reutiliza este ciclo para revisar una sección: no cambia el estado del ticket (sigue en
    # esperando_aprobación) y la traza se etiqueta como «approval».
    update_ticket_status: NotRequired[bool]
    trace_part: NotRequired[str]
    trace: Annotated[list[dict[str, Any]], operator.add]


class ResponseState(TypedDict):
    ticket_id: str
    run: int
    max_iterations: int
    inputs: list[dict[str, Any]]
    sections: Annotated[list[dict[str, Any]], operator.add]
    response_handoff: NotRequired[dict[str, Any]]
    trace: Annotated[list[dict[str, Any]], operator.add]


def build_section_graph(repo: RfpRepository, llm_fn: LlmFn | None):
    def generate(state: SectionState) -> dict[str, Any]:
        inp = SectionInput(**state["inp"])
        iteration = state.get("iteration", 0) + 1
        repo.update_section(state["ticket_id"], inp.department_id, section_status="generando")
        # El agente generador PROPIO del departamento (generators/<department_id>.py).
        draft = generator_for(inp.department_id).generate(inp, llm_fn, iteration=iteration, feedback=state.get("feedback"),
                                                          previous_narrative=state.get("narrative"))
        # El borrador ya existe: pasa a evaluación (el ticket también, la primera vez que ocurre).
        repo.update_section(state["ticket_id"], inp.department_id, section_status="en_evaluacion", draft_content=draft.content,
                            iterations=iteration)
        if state.get("update_ticket_status", True):
            repo.update_ticket(state["ticket_id"], status="en_evaluación", response_stage="evaluando")
        return {"iteration": iteration, "draft": draft.content, "narrative": draft.narrative, "generator_meta": draft.meta}

    # Cada evaluador recibe solo lo que necesita y devuelve solo su propia clave.
    def eval_readability(state: SectionState) -> dict[str, Any]:
        return {"readability": evaluators.evaluate_readability(state["draft"], state["inp"]["language"])}

    def eval_relevance(state: SectionState) -> dict[str, Any]:
        return {"relevance": evaluators.evaluate_relevance(state["draft"], state["narrative"], SectionInput(**state["inp"]))}

    def eval_compliance(state: SectionState) -> dict[str, Any]:
        return {"compliance": evaluators.evaluate_compliance(state["draft"], SectionInput(**state["inp"]))}

    def judge(state: SectionState) -> dict[str, Any]:
        department_id = state["inp"]["department_id"]
        result = evaluators.combine(f"{state['ticket_id']}:{department_id}", department_id, state["iteration"],
                                    state["readability"], state["relevance"], state["compliance"])
        repo.add_evaluation(ticket_id=state["ticket_id"], department_id=department_id, run=state["run"],
                            iteration=state["iteration"], draft_content=state["draft"], readability=result.readability,
                            relevance=result.relevance, compliance=result.compliance, overall_pass=result.overall_pass,
                            feedback_for_generator=result.feedback_for_generator, generator_meta=state.get("generator_meta") or {})
        evaluation = result.to_dict()
        history = (state.get("history") or []) + [{"iteration": state["iteration"], "overall_pass": result.overall_pass,
                                                    "failed": [k for k in ("readability", "relevance", "compliance")
                                                               if not evaluation[k]["pass"]]}]
        update: dict[str, Any] = {"evaluation": evaluation, "feedback": result.feedback_for_generator, "history": history}
        if result.overall_pass:
            update["final_status"] = "evaluacion_superada"
        elif state["iteration"] >= state["max_iterations"]:
            update["final_status"] = "needs_human_review"   # se conserva el último borrador + su EvaluationResult
        if "final_status" in update:
            repo.update_section(state["ticket_id"], department_id, section_status=update["final_status"],
                                needs_human_review=update["final_status"] == "needs_human_review",
                                draft_content=state["draft"], evaluation_results=evaluation, iterations=state["iteration"])
        else:
            repo.update_section(state["ticket_id"], department_id, evaluation_results=evaluation)
        return update

    def route(state: SectionState) -> str:
        return END if state.get("final_status") else "generate"

    agents_by_node = {"generate": lambda s: f"{s['inp']['department_id']}_generator",
                      "eval_readability": "readability_evaluator", "eval_relevance": "relevance_evaluator",
                      "eval_compliance": "compliance_evaluator", "judge": "evaluation_judge"}
    part = lambda s: s.get("trace_part") or "response"  # noqa: E731
    builder = StateGraph(SectionState)
    for name, fn in (("generate", generate), ("eval_readability", eval_readability), ("eval_relevance", eval_relevance),
                     ("eval_compliance", eval_compliance), ("judge", judge)):
        builder.add_node(name, traced(repo, part, name, agents_by_node[name], fn,
                                      input_keys=("ticket_id", "run", "iteration", "feedback", "draft", "inp")))
    builder.add_edge(START, "generate")
    for evaluator in ("eval_readability", "eval_relevance", "eval_compliance"):
        builder.add_edge("generate", evaluator)
    builder.add_edge(["eval_readability", "eval_relevance", "eval_compliance"], "judge")  # espera a los tres
    builder.add_conditional_edges("judge", route, ["generate", END])
    return builder.compile()


def build_response_graph(repo: RfpRepository, llm_fn: LlmFn | None = chat_json):
    section_graph = build_section_graph(repo, llm_fn)

    def load_handoff(state: ResponseState) -> dict[str, Any]:
        repo.update_ticket(state["ticket_id"], status="generando_borrador", response_stage="generando")
        return {}

    def fan_out(state: ResponseState) -> list[Send] | str:
        if not state["inputs"]:
            return "assemble"
        return [Send("department_section", {"ticket_id": state["ticket_id"], "run": state["run"],
                                            "max_iterations": state["max_iterations"], "inp": inp}) for inp in state["inputs"]]

    def department_section(payload: dict[str, Any]) -> dict[str, Any]:
        steps = 6 * payload["max_iterations"] + 5
        final = section_graph.invoke({**payload, "iteration": 0, "trace": []}, {"recursion_limit": steps})
        return {"trace": final.get("trace") or [], "sections": [{"department_id": payload["inp"]["department_id"], "language": payload["inp"]["language"],
                              "currency": SectionInput(**payload["inp"]).currency, "status": final["final_status"],
                              "iterations": final["iteration"], "draft_content": final["draft"],
                              "evaluation_result": final["evaluation"], "history": final["history"],
                              "generator_meta": final.get("generator_meta") or {}}]}

    def assemble(state: ResponseState) -> dict[str, Any]:
        ticket = repo.get_ticket(state["ticket_id"])
        part1 = ticket.handoff or {}
        sections = sorted(state.get("sections", []), key=lambda s: s["department_id"])
        needs_review = [s["department_id"] for s in sections if s["status"] == "needs_human_review"]
        iterations = [s["iterations"] for s in sections]
        handoff = {
            "contract": RESPONSE_CONTRACT, "ticket_id": state["ticket_id"], "rfp_id": ticket.rfp_id, "run": state["run"],
            "source_contract": part1.get("contract"),
            "next_status": "esperando_aprobación",
            "needs_human_review": bool(needs_review), "needs_human_review_sections": needs_review,
            "max_iterations": state["max_iterations"],
            "avg_iterations_per_section": round(sum(iterations) / len(iterations), 2) if iterations else 0,
            "client": part1.get("client"), "currency": (part1.get("client") or {}).get("currency"),
            "sections": [{
                "department_id": s["department_id"], "department": DEPARTMENTS[s["department_id"]].name,
                "approver": DEPARTMENTS[s["department_id"]].owner, "section_status": s["status"],
                "provisional": s["status"] == "needs_human_review", "iterations": s["iterations"],
                "currency": s["currency"], "language": s["language"], "draft_content": s["draft_content"],
                "evaluation_result": s["evaluation_result"], "iteration_history": s["history"],
            } for s in sections],
            "conflicts": part1.get("conflicts") or [],
            "out_of_scope_requests": part1.get("out_of_scope_requests") or [],
        }
        # Nunca se descarta el ticket: con secciones provisionales pasa igual a la Parte 3, marcado needs_human_review.
        status = "needs_human_review" if needs_review else "esperando_aprobación"
        repo.update_ticket(state["ticket_id"], status=status, response_stage="handoff", response_handoff=handoff, last_error=None)
        return {"response_handoff": handoff}

    builder = StateGraph(ResponseState)
    builder.add_node("load_handoff", traced(repo, "response", "load_handoff", "response_coordinator", load_handoff))
    builder.add_node("department_section", department_section)  # su subgrafo traza cada nodo
    builder.add_node("assemble", traced(repo, "response", "assemble", "response_assembler", assemble))
    builder.add_edge(START, "load_handoff")
    builder.add_conditional_edges("load_handoff", fan_out, ["department_section", "assemble"])
    builder.add_edge("department_section", "assemble")
    builder.add_edge("assemble", END)
    return builder.compile()


def run_response(ticket_id: str, repo: RfpRepository, llm_fn: LlmFn | None = chat_json,
                 max_iterations: int | None = None) -> dict[str, Any]:
    """Genera y evalúa las secciones de un ticket listo. Si falla, el ticket conserva su estado con
    ``response_stage='fallido'`` y ``last_error``; nunca se descarta."""
    ticket = repo.get_ticket(ticket_id)
    if ticket is None:
        raise KeyError(ticket_id)
    if ticket.status not in GENERATION_ENTRY_STATUSES:
        raise HandoffError(f"El ticket está en «{ticket.status}»: la Parte 2 solo arranca desde analisis_completo.")
    if any(s.approval_status != "pendiente" for s in repo.get_sections(ticket_id)):
        raise HandoffError("Alguna sección ya tiene una decisión de aprobación: no se regenera.")
    inputs = section_inputs_from_handoff(ticket.handoff)  # HandoffError si no hay contrato válido de la Parte 1
    max_iterations = max_iterations or max_iterations_from_env()
    run = ticket.response_attempts + 1
    repo.update_ticket(ticket_id, response_attempts=run, response_stage="generando", response_handoff=None, last_error=None)
    for inp in inputs:
        repo.update_section(ticket_id, inp.department_id, section_status="pendiente", iterations=0, needs_human_review=False,
                            draft_content=None, evaluation_results=None)
    graph = build_response_graph(repo, llm_fn)
    try:
        state = graph.invoke({"ticket_id": str(ticket_id), "run": run, "max_iterations": max_iterations,
                              "inputs": [inp.to_dict() for inp in inputs], "sections": [], "trace": []})
    except Exception as error:
        logger.exception("Parte 2 falló; ticket=%s", ticket_id)
        repo.update_ticket(ticket_id, response_stage="fallido", last_error=f"{type(error).__name__}: {str(error)[:900]}")
        raise
    final = repo.get_ticket(ticket_id)
    return {"ticket_id": str(ticket_id), "status": final.status, "response_stage": final.response_stage,
            "response_handoff": state.get("response_handoff")}
