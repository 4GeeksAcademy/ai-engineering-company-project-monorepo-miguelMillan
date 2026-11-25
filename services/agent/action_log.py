"""Qué hizo el agente en cada corrida y por qué (NIST DETECT): legible por una persona, derivado del trace.

``action_log`` convierte los pasos registrados en una lista ordenada de acciones con su motivo y su resultado. Además, cada
corrida deja una línea JSON en el logger ``trackflow.agent.actions`` SIN el texto del cliente ni los datos devueltos: se
puede enviar a un sistema central. El detalle se consulta con permiso de personal en ``GET /agent/runs/{run_id}/actions``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

from services.agent.guardrails import SessionContext
from services.agent.json_logging import json_logger

logger = json_logger("trackflow.agent.actions")


FIXED_REASONS = frozenset({"plan_sources", "mcp_incidents", "mcp_inventory", "route_after_input_guard", "route_after_retrieve"})


def actor_label(session: SessionContext) -> str:
    role = "staff" if session.staff else "brand" if session.client_ids else "customer"
    if not session.user:
        return role
    key = os.environ.get("SECRET_KEY", "").encode()
    return f"{role}:{hmac.new(key, session.user.strip().lower().encode(), hashlib.sha256).hexdigest()[:16]}"


def _tool(result: dict[str, Any], count: int, what: str) -> tuple[str, str, str]:
    name = result.get("mcp_tool") or "ninguna tool"
    if result["ok"]:
        return f"llamó a {name}", what, f"ok, {count} resultado(s)"
    if result.get("error_type") == "forbidden":
        return "no llamó a ninguna tool", "la sesión no tiene permiso para esa fuente", "denegado"
    return f"llamó a {name}" if result.get("mcp_tool") else "no llamó a ninguna tool", what, f"error: {result.get('error_type')}"


def action_log(trace: dict[str, Any], guard_events: list[dict[str, Any]] = ()) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    plan_reasons: list[str] = []

    def add(step: dict[str, Any], action: str, why: str, outcome: str) -> None:
        actions.append({"seq": len(actions) + 1, "at": step["started_at"], "node": step["name"], "action": action,
                        "why": why, "outcome": outcome})

    for step in trace["steps"]:
        output, name = step["output"], step["name"]
        if step["status"] == "error":
            add(step, "falló", "error durante la corrida", str(output.get("error_type")))
        elif step["kind"] == "route":
            if name in ("route_after_retrieve", "route_after_input_guard"):
                add(step, f"siguió por {output['next']}", output.get("reason") or "sin motivo registrado", "ok")
        elif name == "input_guard":
            guard = output.get("guard", {})
            add(step, {"block": "bloqueó el mensaje", "redirect": "redirigió el mensaje"}.get(guard.get("action"), "admitió el mensaje"),
                guard.get("reason") or "", guard.get("code") or "sin incidencias")
        elif name == "plan_sources":
            plan = output.get("plan", {})
            plan_reasons = plan.get("reasons", [])
            add(step, f"decidió consultar: {', '.join(plan.get('sources', []))}", "; ".join(plan_reasons), "plan determinista (sin modelo)")
        elif name == "mcp_incidents":
            result = output["incident_result"]
            add(step, *_tool(result, len(result.get("incidents") or []), next((r for r in plan_reasons if "incidencias" in r), "")))
        elif name == "mcp_inventory":
            result = output["inventory_result"]
            add(step, *_tool(result, len(result.get("items") or []), next((r for r in plan_reasons if "stock" in r), "")))
        elif name == "retrieve":
            sources = sorted({f"{c.get('source_document')}#{c.get('chunk_index')}" for c in output.get("context") or []})
            add(step, "consultó la base de conocimiento", "; ".join(plan_reasons), f"fragmentos: {', '.join(sources) or 'ninguno'}")
        elif name == "generate":
            add(step, "redactó la respuesta con el modelo", "había contexto suficiente", f"{len(output.get('answer') or '')} caracteres")
        elif name == "resolve_pending_memory" and output.get("memory_resolution"):
            resolution = output["memory_resolution"]
            add(step, "resolvió una propuesta de memoria", resolution.get("rationale") or resolution["label"], resolution["outcome"])
        elif name == "propose_memory" and (output.get("memory_proposal") or output.get("memory_rejection")):
            rejected = output.get("memory_rejection")
            add(step, "propuso recordar un dato (no se guarda sin confirmación humana)", "el mensaje aporta un dato memorizable",
                f"descartada: {', '.join(rejected['codes'])}" if rejected else "pendiente de una persona")
        elif name == "output_guard":
            check = output.get("output_check", {})
            add(step, "entregó la respuesta" if check.get("ok") else "sustituyó la respuesta",
                check.get("detail") or "sin datos sensibles ni instrucciones internas", ", ".join(check.get("codes") or []) or "ok")
    for event in guard_events:
        if event["action"] == "sanitize":
            actions.append({"seq": len(actions) + 1, "at": event["ts"], "node": event["guard"], "action": "saneó contenido",
                            "why": event["detail"], "outcome": event["code"]})
    return actions


def log_actions(traces: Any, run_id: str) -> None:
    """Línea JSON por corrida, sin la pregunta, la respuesta ni los datos de las tools. Nunca rompe la respuesta."""
    try:
        trace = traces.get_trace(run_id)
        # El motivo de estos nodos es texto fijo; el de las guardas puede citar un pedido y se queda en el trace.
        actions = [{"node": a["node"], "action": a["action"], "outcome": a["outcome"],
                    **({"why": a["why"]} if a["node"] in FIXED_REASONS else {})}
                   for a in action_log(trace, traces.guardrail_events(run_id))]
        logger.info(json.dumps({"event": "agent_run", "run_id": run_id, "actor": trace.get("actor"), "status": trace["status"],
                                "sources": trace["sources_used"], "actions": actions}, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        logger.exception("no se pudo registrar el resumen de la corrida")
