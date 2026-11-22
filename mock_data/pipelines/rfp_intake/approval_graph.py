"""Grafo LangGraph de la Parte 3: aprobación humana por departamento, arbitraje y documento final.

Arranca desde el handoff de la Parte 2 (``rfp_response_handoff.v1``; ticket en ``esperando_aprobación`` o
``needs_human_review``). Un hilo de checkpointer por ejecución y ticket: ``rfp-{ticket_id}:approval-{run}``.

    START → start ─Send×N─► department_approval ─┐       (ticket: esperando_aprobación mientras falte alguna)
                                                ▼
                                            arbitrate ─┬─ sin conflictos ─────────► final_document → END  (terminado)
                                                       ├─ trigger §7, quedan rondas ─Send─► department_approval (solo afectados)
                                                       └─ sección rechazada / rondas agotadas ─► block → END (needs_human_review)

    department_approval = subgrafo de UNA rama (su propio namespace en el checkpoint):

        START → prepare ─┬─ (revisar) → revise ─┐
                         └────────────────────────┴─► request_approval → await_decision ⏸ interrupt → apply_decision
        apply_decision ─┬─ approve → END
                        ├─ request_changes / reject (quedan rondas) → revise (generador de la Parte 2 + evaluadores)
                        └─ reject sin rondas → END (rechazada)

El ``interrupt`` está DENTRO de la rama de un departamento: pausa esa rama y las demás siguen. Reanudar la rama B
(``resume_approval``) ejecuta solo B desde su interrupción; A sigue esperando en su checkpoint. ``start_approval`` y
``resume_approval`` son los dos puntos de entrada explícitos: reanudar no reinicia el flujo.
"""

from __future__ import annotations

import logging
import operator
import re
from typing import Annotated, Any, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from mock_data.pipelines.rfp_intake import approval as rules
from mock_data.pipelines.rfp_intake.checkpointing import open_checkpointer, thread_id_for, ticket_lock
from mock_data.pipelines.rfp_intake.departments import DEPARTMENTS
from mock_data.pipelines.rfp_intake.generation import HandoffError, SectionInput, section_inputs_from_handoff
from mock_data.pipelines.rfp_intake.llm import LlmFn, chat_json
from mock_data.pipelines.rfp_intake.repository import RfpRepository
from mock_data.pipelines.rfp_intake.response_graph import build_section_graph, max_iterations_from_env
from mock_data.pipelines.rfp_intake.tracing import traced

logger = logging.getLogger("trackflow.rfp_approval")
APPROVAL_ENTRY_STATUSES = ("esperando_aprobación", "needs_human_review")
RECURSION_LIMIT = 400


def merge_dicts(left: dict, right: dict) -> dict:
    return {**(left or {}), **(right or {})}


class DepartmentState(TypedDict):
    ticket_id: str
    thread_id: str
    department_id: str
    mode: str                       # review: aprobar el borrador de la Parte 2 | revise: regenerar antes
    feedback: NotRequired[str | None]
    reason: NotRequired[str | None]
    from_scratch: NotRequired[bool]
    overrides: NotRequired[dict[str, Any]]
    arbitration: NotRequired[list[dict[str, Any]]]
    inp: NotRequired[dict[str, Any]]
    content: NotRequired[str]
    evaluation: NotRequired[dict[str, Any] | None]
    provisional: NotRequired[bool]
    version: NotRequired[int]
    round: NotRequired[int]
    request: NotRequired[dict[str, Any]]
    decision: NotRequired[dict[str, Any]]
    result: NotRequired[dict[str, Any]]
    trace: Annotated[list[dict[str, Any]], operator.add]


class ApprovalState(TypedDict):
    ticket_id: str
    thread_id: str
    departments: list[str]
    client_country: NotRequired[str | None]
    arbitration_round: int
    max_arbitration_rounds: int
    results: Annotated[dict[str, dict[str, Any]], merge_dicts]
    arbitration: Annotated[list[dict[str, Any]], operator.add]
    pending_revisions: NotRequired[list[dict[str, Any]]]
    outcome: NotRequired[str]
    trace: Annotated[list[dict[str, Any]], operator.add]


SCOPE_HEADING = re.compile(r"^###\s+(Alcance y enfoque|Scope and approach)\s*$", re.M)


def narrative_of(content: str) -> str:
    match = SCOPE_HEADING.search(content or "")
    if not match:
        return ""
    rest = content[match.end():]
    end = re.search(r"^###\s", rest, re.M)
    return (rest[:end.start()] if end else rest).strip()


def section_input(repo: RfpRepository, ticket_id: str, department_id: str, overrides: dict[str, Any] | None) -> SectionInput:
    """La entrada de la sección SIEMPRE sale del handoff de la Parte 1, más los overrides del arbitraje."""
    ticket = repo.get_ticket(ticket_id)
    inp = next(i for i in section_inputs_from_handoff(ticket.handoff) if i.department_id == department_id)
    for key, value in (overrides or {}).items():
        inp.metadata[key] = value
        if key == "monthly_volume":
            for aspect in inp.key_aspects:
                if aspect.get("origen") == "metadatos" and aspect.get("aspecto") == "Volumen mensual":
                    aspect["detalle"] = f"{value} pedidos/mes"
    return inp


def build_department_graph(repo: RfpRepository, llm_fn: LlmFn | None):
    section_cycle = build_section_graph(repo, llm_fn)   # el ciclo generador-evaluador de la Parte 2

    def prepare(state: DepartmentState) -> dict[str, Any]:
        dept = state["department_id"]
        inp = section_input(repo, state["ticket_id"], dept, state.get("overrides"))
        section = repo.get_section(state["ticket_id"], dept)
        return {"inp": inp.to_dict(), "content": section.draft_content or "", "evaluation": section.evaluation_results,
                "provisional": bool(section.needs_human_review), "version": section.approval_round + 1,
                "round": section.approval_round, "reason": state.get("reason")}

    def revise(state: DepartmentState) -> dict[str, Any]:
        """Vuelve al generador de la Parte 2 de ESTE departamento con el feedback humano o del arbitraje."""
        dept = state["department_id"]
        repo.update_section(state["ticket_id"], dept, approval_status="en_revision", approval_request=None)
        final = section_cycle.invoke({
            "ticket_id": state["ticket_id"], "run": repo.next_evaluation_run(state["ticket_id"], dept),
            "max_iterations": max_iterations_from_env(), "inp": state["inp"], "iteration": 0,
            "feedback": state.get("feedback"), "narrative": None if state.get("from_scratch") else narrative_of(state.get("content", "")),
            "update_ticket_status": False, "trace_part": "approval", "trace": []})
        return {"content": final["draft"], "evaluation": final["evaluation"],
                "provisional": final["final_status"] == "needs_human_review", "version": state["version"] + 1,
                "trace": final.get("trace") or []}

    def request_approval(state: DepartmentState) -> dict[str, Any]:
        inp = SectionInput(**state["inp"])
        request = rules.build_approval_request(
            ticket_id=state["ticket_id"], rfp_id=repo.get_ticket(state["ticket_id"]).rfp_id, inp=inp, content=state["content"],
            evaluation=state.get("evaluation"), version=state["version"], round_=state["round"],
            max_rounds=rules.max_approval_rounds(), provisional=state.get("provisional", False),
            reason=state.get("reason"), arbitration=state.get("arbitration") or [])
        repo.update_section(state["ticket_id"], inp.department_id, approval_status="esperando_aprobación",
                            approval_request={k: v for k, v in request.items() if k != "section_input"},
                            approval_round=state["round"])
        return {"request": request}

    def await_decision(state: DepartmentState) -> dict[str, Any]:
        """Punto de aprobación humana: pausa SOLO esta rama. La respuesta se valida antes de seguir."""
        request, errors = state["request"], None
        while True:
            raw = interrupt({**request, "validation_errors": errors} if errors else request)
            try:
                decision = rules.validate_decision(raw, request)
                return {"decision": decision.model_dump()}
            except rules.DecisionError as error:  # nunca entra al grafo una respuesta inválida
                errors = error.errors

    def apply_decision(state: DepartmentState) -> dict[str, Any]:
        dept, decision = state["department_id"], state["decision"]
        section = repo.get_section(state["ticket_id"], dept)
        entry = {"version": state["version"], "round": state["round"], "decision": decision["decision"],
                 "approver": decision["approver"], "decided_by": decision.get("decided_by"), "comment": decision["comment"],
                 "commitments": decision["commitments"], "edited": decision.get("edited_content") is not None,
                 "decided_at": rules.now_iso()}
        history = list(section.decision_history or []) + [entry]
        if decision["decision"] == "approve":
            content = decision.get("edited_content") or state["content"]
            inp = SectionInput(**state["inp"])
            facts = rules.section_facts(dept, content, inp.currency, decision["commitments"], inp.metadata)
            from datetime import datetime

            repo.update_section(state["ticket_id"], dept, approval_status="aprobada", approved_content=content,
                                approved_at=datetime.fromisoformat(entry["decided_at"]), commitments=decision["commitments"],
                                decided_by=decision.get("decided_by"), decision_history=history, approval_request=None)
            return {"result": {"department_id": dept, "outcome": "aprobada", "version": state["version"],
                               "approver": decision["approver"], "approved_at": entry["decided_at"], "content": content,
                               "currency": inp.currency, "facts": facts, "overrides": state.get("overrides") or {}}}
        if decision["decision"] == "reject" and state["round"] >= rules.max_approval_rounds():
            repo.update_section(state["ticket_id"], dept, approval_status="rechazada", decision_history=history,
                                approval_request=None, decided_by=decision.get("decided_by"))
            return {"result": {"department_id": dept, "outcome": "rechazada", "version": state["version"],
                               "approver": decision["approver"], "comment": decision["comment"]}}
        prefix = ("Rechazada: reescribe la sección desde cero. " if decision["decision"] == "reject" else "Cambios solicitados: ")
        repo.update_section(state["ticket_id"], dept, decision_history=history, approval_round=state["round"] + 1)
        return {"feedback": f"- [{decision['approver']} · aprobación humana] {prefix}{decision['comment']}",
                "from_scratch": decision["decision"] == "reject", "round": state["round"] + 1,
                "reason": f"{decision['approver']} pidió cambios en la versión {state['version']}: {decision['comment']}"}

    def route_prepare(state: DepartmentState) -> str:
        return "revise" if state["mode"] == "revise" else "request_approval"

    def route_decision(state: DepartmentState) -> str:
        return END if state.get("result") else "revise"

    owner = lambda s: f"human:{DEPARTMENTS[s['department_id']].owner}"  # noqa: E731
    builder = StateGraph(DepartmentState)
    nodes = (("prepare", prepare, "approval_gate"), ("revise", revise, lambda s: f"{s['department_id']}_generator"),
             ("request_approval", request_approval, "approval_gate"), ("await_decision", await_decision, owner),
             ("apply_decision", apply_decision, "approval_gate"))
    for name, fn, agent in nodes:
        builder.add_node(name, traced(repo, "approval", name, agent, fn,
                                      input_keys=("department_id", "mode", "version", "round", "feedback", "decision", "reason")))
    builder.add_edge(START, "prepare")
    builder.add_conditional_edges("prepare", route_prepare, ["revise", "request_approval"])
    builder.add_edge("revise", "request_approval")
    builder.add_edge("request_approval", "await_decision")
    builder.add_edge("await_decision", "apply_decision")
    builder.add_conditional_edges("apply_decision", route_decision, ["revise", END])
    return builder.compile()


def build_approval_graph(repo: RfpRepository, llm_fn: LlmFn | None = chat_json, checkpointer=None):
    department_graph = build_department_graph(repo, llm_fn)

    def start(state: ApprovalState) -> dict[str, Any]:
        repo.update_ticket(state["ticket_id"], status="esperando_aprobación", approval_stage="en_curso")
        return {}

    def fan_out(state: ApprovalState) -> list[Send]:
        return [Send("department_approval", {"ticket_id": state["ticket_id"], "thread_id": state["thread_id"],
                                             "department_id": d, "mode": "review", "trace": []}) for d in state["departments"]]

    def department_approval(payload: dict[str, Any], config) -> dict[str, Any]:
        final = department_graph.invoke(payload, config)
        return {"results": {payload["department_id"]: final["result"]}, "trace": final.get("trace") or []}

    def arbitrate(state: ApprovalState) -> dict[str, Any]:
        """Nodo de arbitraje: triggers de §7 sobre los hechos estructurados de las secciones aprobadas."""
        results = state["results"]
        rejected = sorted(d for d, r in results.items() if r["outcome"] == "rechazada")
        if rejected:
            return {"outcome": "bloqueado", "pending_revisions": [],
                    "arbitration": [{"trigger": "department-rejected", "arbiter": rules.COMMERCIAL_DIRECTOR,
                                     "affected_departments": rejected, "round": state["arbitration_round"],
                                     "resolution": "Sección rechazada tras agotar las rondas de cambios: no hay documento final; "
                                                   "decide Miguel Torres con el responsable.", "decided_at": rules.now_iso()}]}
        ticket = repo.get_ticket(state["ticket_id"])
        facts = {d: r["facts"] for d, r in results.items()}
        meta = (ticket.handoff or {}).get("metadata") or {}
        conflicts = rules.detect_conflicts(facts, state.get("client_country"), meta.get("sku_count"))
        if not conflicts:
            return {"outcome": "sin_conflictos", "pending_revisions": []}
        round_ = state["arbitration_round"] + 1
        records = [{**c, "round": round_, "decided_at": rules.now_iso()} for c in conflicts]
        repo.update_ticket(state["ticket_id"], arbitration=list(ticket.arbitration or []) + records)
        if round_ > state["max_arbitration_rounds"]:
            return {"outcome": "bloqueado", "arbitration": records, "pending_revisions": [], "arbitration_round": round_}
        revisions: dict[str, dict[str, Any]] = {}
        for conflict in conflicts:
            for dept in conflict["affected_departments"]:
                item = revisions.setdefault(dept, {"feedback": [], "overrides": dict(results[dept].get("overrides") or {})})
                item["feedback"].append(conflict["feedback"])
                item["overrides"].update(conflict["overrides"].get(dept, {}))
        for dept in revisions:
            repo.update_section(state["ticket_id"], dept, approval_status="en_revision")
        return {"outcome": "revisar", "arbitration": records, "arbitration_round": round_,
                "pending_revisions": [{"department_id": d, **v} for d, v in sorted(revisions.items())]}

    def route_arbitration(state: ApprovalState):
        if state.get("outcome") == "bloqueado":
            return "block"
        if state.get("outcome") == "revisar":
            return [Send("department_approval", {
                "ticket_id": state["ticket_id"], "thread_id": state["thread_id"], "department_id": r["department_id"],
                "mode": "revise", "feedback": "\n".join(f"- {f}" for f in r["feedback"]), "overrides": r["overrides"],
                "reason": "Arbitraje: " + " | ".join(r["feedback"]), "arbitration": state.get("arbitration") or [],
                "trace": []}) for r in state["pending_revisions"]]
        return "final_document"

    def final_document(state: ApprovalState) -> dict[str, Any]:
        ticket = repo.get_ticket(state["ticket_id"])
        handoff = ticket.response_handoff or {}
        client = handoff.get("client") or {}
        currency = client.get("currency")
        sections = [{"department_id": d, "department": DEPARTMENTS[d].name, "approver": r["approver"],
                     "approved_at": r["approved_at"], "version": r["version"], "content": r["content"]}
                    for d, r in sorted(state["results"].items())]
        generated_at = rules.now_iso()
        content = rules.render_final_document(ticket_id=state["ticket_id"], rfp_id=ticket.rfp_id, client=client,
                                              currency=currency, sections=sections, arbitration=list(ticket.arbitration or []),
                                              generated_at=generated_at, out_of_scope=handoff.get("out_of_scope_requests") or [],
                                              proposal_volume=min((r.get("overrides") or {}).get("monthly_volume") or 10**9
                                                                  for r in state["results"].values()) if any(
                                                  (r.get("overrides") or {}).get("monthly_volume") for r in state["results"].values()) else None,
                                              rfp_volume=((ticket.handoff or {}).get("metadata") or {}).get("monthly_volume"))
        repo.save_final_document(state["ticket_id"], rfp_id=ticket.rfp_id, currency=currency, sections=sections,
                                 approvals=[{k: s[k] for k in ("department_id", "approver", "approved_at", "version")} for s in sections],
                                 arbitration=list(ticket.arbitration or []), content=content)
        repo.update_ticket(state["ticket_id"], status="terminado", approval_stage="completado", approval_note=None, last_error=None)
        return {"outcome": "terminado"}

    def block(state: ApprovalState) -> dict[str, Any]:
        last = (state.get("arbitration") or [{}])[-1]
        note = (f"Sin documento final: {last.get('trigger')} sigue sin resolverse tras {state['max_arbitration_rounds']} "
                f"ronda(s) de arbitraje (árbitro: {last.get('arbiter')})." if last.get("trigger") != "department-rejected" else
                f"Sin documento final: {', '.join(last.get('affected_departments', []))} rechazó su sección tras agotar las rondas.")
        repo.update_ticket(state["ticket_id"], status="needs_human_review", approval_stage="bloqueado", approval_note=note)
        return {"outcome": "bloqueado"}

    builder = StateGraph(ApprovalState)
    builder.add_node("start", traced(repo, "approval", "start", "approval_coordinator", start))
    builder.add_node("department_approval", department_approval)  # su subgrafo traza cada nodo
    builder.add_node("arbitrate", traced(repo, "approval", "arbitrate",
                                         lambda s: f"arbiter:{rules.COMMERCIAL_DIRECTOR}", arbitrate,
                                         input_keys=("arbitration_round", "results")))
    builder.add_node("final_document", traced(repo, "approval", "final_document", "document_assembler", final_document,
                                              input_keys=("results",)))
    builder.add_node("block", traced(repo, "approval", "block", "approval_coordinator", block))
    builder.add_edge(START, "start")
    builder.add_conditional_edges("start", fan_out, ["department_approval"])
    builder.add_edge("department_approval", "arbitrate")
    builder.add_conditional_edges("arbitrate", route_arbitration, ["department_approval", "final_document", "block"])
    builder.add_edge("final_document", END)
    builder.add_edge("block", END)
    return builder.compile(checkpointer=checkpointer)


# ============================================================================
# Puntos de entrada: arrancar y reanudar (nunca reiniciar)
# ============================================================================
def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}


def pending_approvals(graph, thread_id: str) -> dict[str, Any]:
    """Interrupciones pendientes por departamento (del checkpoint): {department_id: Interrupt}."""
    return {i.value["department_id"]: i for i in graph.get_state(_config(thread_id)).interrupts}


def _settle(repo: RfpRepository, graph, ticket_id: str, thread_id: str) -> dict[str, Any]:
    ticket = repo.get_ticket(ticket_id)
    pending = pending_approvals(graph, thread_id)
    if ticket.approval_stage not in ("completado", "bloqueado"):
        repo.update_ticket(ticket_id, approval_stage="esperando" if pending else ticket.approval_stage)
    ticket = repo.get_ticket(ticket_id)
    return {"ticket_id": str(ticket_id), "status": ticket.status, "approval_stage": ticket.approval_stage,
            "thread_id": thread_id, "pending_departments": sorted(pending)}


def start_approval(ticket_id: str, repo: RfpRepository, llm_fn: LlmFn | None = chat_json, checkpointer=None) -> dict[str, Any]:
    ticket = repo.get_ticket(ticket_id)
    if ticket is None:
        raise KeyError(ticket_id)
    handoff = ticket.response_handoff or {}
    if ticket.status not in APPROVAL_ENTRY_STATUSES or handoff.get("contract") != "rfp_response_handoff.v1" \
            or ticket.response_stage != "handoff":
        raise HandoffError("La Parte 3 arranca desde el handoff rfp_response_handoff.v1 de la Parte 2.")
    if ticket.approval_stage in ("iniciando", "en_curso", "esperando", "completado") and ticket.approval_thread_id:
        raise HandoffError(f"El flujo de aprobación ya existe ({ticket.approval_thread_id}): se reanuda, no se reinicia.")
    run = ticket.approval_run + 1
    thread_id = thread_id_for(ticket_id, run)
    departments = [s["department_id"] for s in handoff.get("sections", [])]
    for dept in departments:
        repo.update_section(ticket_id, dept, approval_status="pendiente", approval_round=0, approval_request=None,
                            decision_history=[], commitments={}, approved_content=None, approved_at=None, decided_by=None)
    repo.update_ticket(ticket_id, approval_run=run, approval_thread_id=thread_id, approval_stage="en_curso",
                       approval_note=None, arbitration=[], last_error=None)
    db_url = _db_url(repo)
    with ticket_lock(ticket_id, db_url), _maybe_checkpointer(checkpointer, db_url) as saver:
        graph = build_approval_graph(repo, llm_fn, saver)
        try:
            graph.invoke({"ticket_id": str(ticket_id), "thread_id": thread_id, "departments": departments,
                          "client_country": (handoff.get("client") or {}).get("country"), "arbitration_round": 0,
                          "max_arbitration_rounds": rules.max_arbitration_rounds(), "results": {}, "arbitration": [],
                          "trace": []}, _config(thread_id))
        except Exception as error:
            _fail(repo, ticket_id, error)
            raise
        return _settle(repo, graph, ticket_id, thread_id)


def resume_approval(ticket_id: str, department_id: str, decision: dict[str, Any], repo: RfpRepository,
                    llm_fn: LlmFn | None = chat_json, checkpointer=None) -> dict[str, Any]:
    """Reanuda SOLO la rama de ``department_id`` con la decisión humana (validada antes de entrar al grafo)."""
    ticket = repo.get_ticket(ticket_id)
    if ticket is None:
        raise KeyError(ticket_id)
    if not ticket.approval_thread_id:
        raise HandoffError("Este ticket no tiene un flujo de aprobación en curso.")
    db_url = _db_url(repo)
    with ticket_lock(ticket_id, db_url), _maybe_checkpointer(checkpointer, db_url) as saver:
        graph = build_approval_graph(repo, llm_fn, saver)
        pending = pending_approvals(graph, ticket.approval_thread_id)
        try:
            if department_id not in pending:
                raise rules.DecisionError([f"{department_id} no tiene una aprobación pendiente."])
            interrupt_ = pending[department_id]
            validated = rules.validate_decision(decision, interrupt_.value)   # antes de dejarla entrar al grafo
        except rules.DecisionError:
            section = repo.get_section(ticket_id, department_id)
            if section is not None and section.approval_status == "procesando" and department_id in pending:
                repo.update_section(ticket_id, department_id, approval_status="esperando_aprobación")
                repo.update_ticket(ticket_id, approval_stage="esperando")
            raise
        repo.update_section(ticket_id, department_id, approval_status="procesando")
        repo.update_ticket(ticket_id, approval_stage="en_curso")
        try:
            graph.invoke(Command(resume={interrupt_.id: validated.model_dump()}), _config(ticket.approval_thread_id))
        except Exception as error:
            _fail(repo, ticket_id, error)
            raise
        return _settle(repo, graph, ticket_id, ticket.approval_thread_id)


def _fail(repo: RfpRepository, ticket_id: str, error: Exception) -> None:
    logger.exception("Parte 3 falló; ticket=%s", ticket_id)
    repo.update_ticket(ticket_id, approval_stage="fallido", last_error=f"{type(error).__name__}: {str(error)[:900]}")


def _db_url(repo: RfpRepository) -> str:
    """La misma base que el repositorio (Supabase en producción, SQLite en tests)."""
    return repo.engine.url.render_as_string(hide_password=False)


class _maybe_checkpointer:
    """Usa el checkpointer recibido (tests) o abre el de la base del repositorio (Postgres/SQLite)."""

    def __init__(self, checkpointer, database_url: str):
        self.checkpointer, self.database_url, self._cm = checkpointer, database_url, None

    def __enter__(self):
        if self.checkpointer is not None:
            return self.checkpointer
        self._cm = open_checkpointer(self.database_url)
        return self._cm.__enter__()

    def __exit__(self, *exc):
        if self._cm is not None:
            return self._cm.__exit__(*exc)
        return False
