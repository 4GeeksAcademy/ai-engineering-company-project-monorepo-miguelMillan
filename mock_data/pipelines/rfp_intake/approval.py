"""Reglas de la Parte 3: aprobación humana por departamento, arbitraje y documento final (BRIEFING del Hito 9).

Todo lo de este módulo es determinista y no llama al LLM:

* ``validate_decision``: valida la respuesta humana ANTES de que entre al grafo (y otra vez dentro del nodo).
* ``build_approval_request``: lo mínimo que un responsable necesita ver para decidir sin releer el documento.
* ``section_facts`` + ``detect_conflicts``: los triggers de §7 sobre estado estructurado (monedas por sección,
  promesas de devoluciones en horas, capacidad comprometida frente a volumen asumido) y su árbitro fijo.
* ``render_final_document``: el FinalDocument de §2.3 (``ticket_id``, ``sections``, ``currency``, ``generated_at``).
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from mock_data.pipelines.rfp_intake import evaluators
from mock_data.pipelines.rfp_intake.departments import CURRENCY_BY_COUNTRY, DEPARTMENTS
from mock_data.pipelines.rfp_intake.generation import SectionInput

COMMERCIAL_DIRECTOR = "Miguel Torres"   # árbitro fijo de §7 (Director Comercial)
DEFAULT_MAX_APPROVAL_ROUNDS = 3         # rondas de «solicitar cambios / rechazar» por departamento
DEFAULT_MAX_ARBITRATION_ROUNDS = 2      # rondas del ciclo entre departamentos (arbitraje → revisión → aprobación)
COMMITMENT_FIELDS = {                   # compromisos estructurados que cada responsable puede registrar al aprobar
    "warehouse": {"capacity_monthly_orders": "Capacidad comprometida (pedidos/mes)", "capacity_sku": "Capacidad comprometida (SKU)"},
    "lastmile": {"monthly_volume": "Volumen mensual asumido (pedidos/mes)"},
    "reverse": {},
}


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


def max_approval_rounds() -> int:
    return _env_int("RFP_MAX_APPROVAL_ROUNDS", DEFAULT_MAX_APPROVAL_ROUNDS)


def max_arbitration_rounds() -> int:
    return _env_int("RFP_MAX_ARBITRATION_ROUNDS", DEFAULT_MAX_ARBITRATION_ROUNDS)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================================
# Respuesta humana
# ============================================================================
class HumanDecision(BaseModel):
    decision: Literal["approve", "reject", "request_changes"]
    approver: str = Field(min_length=1, max_length=120)
    section_version: int = Field(ge=1)
    comment: str = Field(default="", max_length=2000)
    commitments: dict[str, int] = Field(default_factory=dict)
    edited_content: str | None = Field(default=None, max_length=20000)
    decided_by: str | None = Field(default=None, max_length=255)   # usuario autenticado que registra la decisión

    @field_validator("commitments")
    @classmethod
    def positive(cls, value: dict[str, int]) -> dict[str, int]:
        for key, number in value.items():
            if number <= 0 or number > 10_000_000:
                raise ValueError(f"{key} debe ser un entero positivo razonable")
        return value


class DecisionError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def validate_decision(raw: Any, request: dict[str, Any]) -> HumanDecision:
    """Valida la decisión contra la solicitud de aprobación pendiente. Lanza ``DecisionError`` con motivos concretos."""
    try:
        decision = HumanDecision.model_validate(raw)
    except ValidationError as error:
        raise DecisionError([f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()]) from error
    errors: list[str] = []
    if decision.approver != request["approver"]:
        errors.append(f"Solo {request['approver']} (responsable de {request['department']}, BRIEFING §2.1) puede decidir esta sección.")
    if decision.section_version != request["version"]:
        errors.append(f"La decisión es sobre la versión {decision.section_version}, pero la pendiente es la {request['version']}.")
    if decision.decision in ("reject", "request_changes") and len(decision.comment.strip()) < 10:
        errors.append("Explica qué hay que cambiar (al menos 10 caracteres): es el feedback que recibirá el generador.")
    if decision.decision == "request_changes" and request["round"] >= request["max_rounds"]:
        errors.append(f"Se alcanzó el límite de {request['max_rounds']} rondas de cambios: aprueba la sección o recházala.")
    allowed = COMMITMENT_FIELDS[request["department_id"]]
    unknown = sorted(set(decision.commitments) - set(allowed))
    if unknown:
        errors.append(f"Compromisos no válidos para {request['department_id']}: {', '.join(unknown)}.")
    if decision.edited_content is not None:
        if decision.decision != "approve":
            errors.append("Solo se puede aprobar con edición; para pedir cambios usa el comentario.")
        else:
            inp = SectionInput(**request["section_input"])
            compliance = evaluators.evaluate_compliance(decision.edited_content, inp)
            if not compliance["pass"]:
                errors += [f"La edición incumple {v['rule_id']}: {v['message']} («{v['evidence']}»)" for v in compliance["violations"]]
    if errors:
        raise DecisionError(errors)
    return decision


# ============================================================================
# Solicitud de aprobación: lo mínimo para decidir con confianza
# ============================================================================
PENDING = re.compile(r"^\|\s*([^|]+?)\s*\|\s*((?:Pendiente|To be)[^|]*?)\s*\|", re.M)


def build_approval_request(*, ticket_id: str, rfp_id: str, inp: SectionInput, content: str, evaluation: dict | None,
                           version: int, round_: int, max_rounds: int, provisional: bool,
                           reason: str | None, arbitration: list[dict[str, Any]]) -> dict[str, Any]:
    dept = DEPARTMENTS[inp.department_id]
    evaluation = evaluation or {}
    compliance, relevance, readability = (evaluation.get(k) or {} for k in ("compliance", "relevance", "readability"))
    return {
        "ticket_id": ticket_id, "rfp_id": rfp_id, "department_id": inp.department_id, "department": dept.name,
        "approver": dept.owner, "version": version, "round": round_, "max_rounds": max_rounds,
        "allowed_decisions": ["approve", "reject"] + (["request_changes"] if round_ < max_rounds else []),
        "client": {"name": inp.client.get("name"), "country": inp.client.get("country"), "currency": inp.currency},
        "provisional": provisional,
        "why_this_version": reason,
        "evaluation": {
            "overall_pass": evaluation.get("overall_pass"),
            "readability": {"pass": readability.get("pass"), "score": readability.get("score")},
            "relevance": {"pass": relevance.get("pass"), "missing_aspects": relevance.get("missing_aspects", [])},
            "compliance": {"pass": compliance.get("pass"), "rule_ids": compliance.get("rule_ids", []),
                           "violations": [{"rule_id": v["rule_id"], "evidence": v["evidence"]} for v in compliance.get("violations", [])]},
        },
        "pending_items": [{"concept": c, "value": v} for c, v in PENDING.findall(content)],
        "key_figures": {"currency": inp.currency, "monthly_volume": inp.metadata.get("monthly_volume"),
                        "sku_count": inp.metadata.get("sku_count"), "deadline": inp.metadata.get("deadline") or inp.metadata.get("deadline_text")},
        "open_questions": inp.open_questions,
        "arbitration": [a for a in arbitration if inp.department_id in a.get("affected_departments", [])],
        "commitment_fields": COMMITMENT_FIELDS[inp.department_id],
        "section_input": inp.to_dict(),
        "content": content,
    }


# ============================================================================
# Triggers de conflicto (BRIEFING §7) sobre estado estructurado
# ============================================================================
CURRENCY_TOKENS = {"USD": re.compile(r"\bUSD\b|US\$|\$\s?\d|\d\s?\$", re.I), "EUR": re.compile(r"\bEUR\b|€", re.I)}


def section_facts(department_id: str, content: str, section_currency: str | None, commitments: dict[str, int],
                  metadata: dict[str, Any]) -> dict[str, Any]:
    """Hechos estructurados de una sección aprobada, de los que salen los triggers (no texto libre)."""
    facts: dict[str, Any] = {
        "department_id": department_id,
        "currency": section_currency,
        "currencies_in_content": sorted(code for code, pattern in CURRENCY_TOKENS.items() if pattern.search(content)),
        "returns_promises_under_48h": evaluators.returns_promises_under_48h(content),
    }
    if department_id == "warehouse":
        facts["capacity_monthly_orders"] = commitments.get("capacity_monthly_orders")
        facts["capacity_sku"] = commitments.get("capacity_sku")
    if department_id == "lastmile":
        facts["assumed_monthly_volume"] = commitments.get("monthly_volume") or metadata.get("monthly_volume")
    return facts


def detect_conflicts(facts: dict[str, dict[str, Any]], client_country: str | None, sku_count: int | None = None) -> list[dict[str, Any]]:
    """Devuelve los triggers de §7 que disparan, con su árbitro fijo y la resolución determinista."""
    conflicts: list[dict[str, Any]] = []
    expected = CURRENCY_BY_COUNTRY.get(client_country or "")
    # currency-mismatch: dos secciones con monedas distintas, o moneda ≠ mapeo de client_country.
    declared = {d: f["currency"] for d, f in facts.items()}
    offenders = sorted(d for d, f in facts.items()
                       if (expected and f["currency"] != expected) or (expected and any(c != expected for c in f["currencies_in_content"])))
    if offenders or len(set(declared.values())) > 1:
        conflicts.append({
            "trigger": "currency-mismatch", "arbiter": COMMERCIAL_DIRECTOR,
            "evidence": {"expected_currency": expected, "client_country": client_country, "declared": declared,
                         "in_content": {d: f["currencies_in_content"] for d, f in facts.items()}},
            "affected_departments": offenders or sorted(declared),
            "resolution": f"Reescribir las secciones ofensoras en {expected} (moneda de {client_country}); "
                          "si no se resuelve tras el límite de iteraciones, se rechaza.",
            "feedback": f"[Arbitraje currency-mismatch · árbitro {COMMERCIAL_DIRECTOR}] Cotiza todo en {expected}, la moneda de "
                        f"{client_country}; elimina cualquier importe en otra moneda.",
            "overrides": {},
        })
    # returns-sla-breach: alguna sección promete devoluciones en menos de 48 h.
    breaching = {d: f["returns_promises_under_48h"] for d, f in facts.items() if f["returns_promises_under_48h"]}
    if breaching:
        only_reverse = set(breaching) == {"reverse"}
        conflicts.append({
            "trigger": "returns-sla-breach",
            "arbiter": DEPARTMENTS["reverse"].owner if only_reverse else COMMERCIAL_DIRECTOR,
            "evidence": breaching, "affected_departments": sorted(breaching),
            "resolution": "request_changes forzado en toda sección que prometa devoluciones en menos de 48 h; "
                          "sin documento final hasta cumplir.",
            "feedback": "[Arbitraje returns-sla-breach · árbitro "
                        f"{DEPARTMENTS['reverse'].owner if only_reverse else COMMERCIAL_DIRECTOR}] Elimina la promesa de procesar "
                        "devoluciones en menos de 48 horas: el mínimo es 48 h (BRIEFING §5). Frases: "
                        + "; ".join(f"«{s}»" for items in breaching.values() for s in items),
            "overrides": {},
        })
    # volume-vs-capacity: la capacidad comprometida de warehouse no soporta el volumen asumido por lastmile.
    warehouse, lastmile = facts.get("warehouse"), facts.get("lastmile")
    if warehouse and lastmile:
        capacity, volume = warehouse.get("capacity_monthly_orders"), lastmile.get("assumed_monthly_volume")
        if capacity and volume and capacity < volume:
            conflicts.append({
                "trigger": "volume-vs-capacity", "arbiter": COMMERCIAL_DIRECTOR,
                "evidence": {"warehouse_capacity_monthly_orders": capacity, "lastmile_assumed_monthly_volume": volume},
                "affected_departments": ["lastmile"],
                "resolution": f"Limitar el volumen de la propuesta a la capacidad de warehouse ({capacity} pedidos/mes); "
                              "lastmile revisa volumen y costo a la baja.",
                "feedback": f"[Arbitraje volume-vs-capacity · árbitro {COMMERCIAL_DIRECTOR}] Warehouse solo compromete "
                            f"{capacity} pedidos/mes: cotiza la última milla para {capacity} pedidos/mes (no {volume}) y revisa el costo a la baja.",
                "overrides": {"lastmile": {"monthly_volume": capacity}},
            })
    return conflicts


# ============================================================================
# Documento final (BRIEFING §2.3: FinalDocument)
# ============================================================================
def render_final_document(*, ticket_id: str, rfp_id: str, client: dict[str, Any], currency: str,
                          sections: list[dict[str, Any]], arbitration: list[dict[str, Any]], generated_at: str,
                          out_of_scope: list[str], proposal_volume: int | None = None, rfp_volume: int | None = None) -> str:
    es = client.get("country") != "US"
    title = "Propuesta económica de TrackFlow" if es else "TrackFlow pricing proposal"
    lines = [f"# {title} — {client.get('name') or rfp_id}", "",
             f"- **{'Referencia RFP' if es else 'RFP reference'}:** {rfp_id}",
             f"- **{'Ticket' if es else 'Ticket'}:** {ticket_id}",
             f"- **{'Moneda' if es else 'Currency'}:** {currency}",
             f"- **{'Generado' if es else 'Generated'}:** {generated_at}",
             *([f"- **{'Volumen de la propuesta' if es else 'Proposal volume'}:** {proposal_volume} "
                f"{'pedidos/mes' if es else 'orders/month'} ({'limitado por arbitraje; la RFP pedía' if es else 'capped by arbitration; the RFP asked for'} "
                f"{rfp_volume})"] if proposal_volume and rfp_volume and proposal_volume != rfp_volume else []), "",
             f"## {'Aprobaciones' if es else 'Approvals'}", "",
             "| Departamento | Responsable | Aprobado | Versión |" if es else "| Department | Owner | Approved | Version |",
             "|---|---|---|---|"]
    lines += [f"| {s['department']} | {s['approver']} | {s['approved_at']} | {s['version']} |" for s in sections]
    if arbitration:
        lines += ["", f"## {'Arbitrajes resueltos' if es else 'Resolved arbitration'}", ""]
        lines += [f"- **{a['trigger']}** (árbitro: {a['arbiter']}): {a['resolution']}" for a in arbitration]
    if out_of_scope:
        lines += ["", f"## {'Fuera de alcance' if es else 'Out of scope'}", "",
                  *[f"- {item}" for item in out_of_scope]]
    for section in sections:
        lines += ["", section["content"].strip()]
    return "\n".join(lines).strip() + "\n"
