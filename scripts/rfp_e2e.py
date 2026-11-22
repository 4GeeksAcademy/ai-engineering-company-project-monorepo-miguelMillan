"""E2E reproducible de las tres partes del flujo RFP con aprobaciones humanas simuladas (resume programático).

    # PDF real + LLM del entorno + Supabase (DATABASE_URL) + checkpointer Postgres:
    uv run python scripts/rfp_e2e.py --pdf ruta/trackflow-request-2.pdf --scenario capacity-conflict
    # Fixture Markdown + LLM doble (rápido, sin red de LLM):
    uv run python scripts/rfp_e2e.py --markdown tests/pipelines/fixtures/rfp/formal-modaviva.md --fake-llm

Escenarios (las decisiones las toman los responsables del BRIEFING §2.1):
  approve-all        cada responsable aprueba su sección (la de lastmile primero, si existe).
  capacity-conflict  Ana Whitfield compromete menos capacidad que el volumen de la RFP → arbitraje volume-vs-capacity
                     (árbitro Miguel Torres) → lastmile revisa → Carlos Vega aprueba la nueva versión.
  request-changes    el primer responsable pide cambios, aprueba la revisión; el resto aprueba.

Imprime la línea de tiempo de estados y la traza de agentes, y escribe un informe JSON (``--report``).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from rfp_intake import engine  # noqa: E402

from mock_data.pipelines.rfp_intake.approval_graph import resume_approval, start_approval  # noqa: E402
from mock_data.pipelines.rfp_intake.departments import DEPARTMENTS  # noqa: E402
from mock_data.pipelines.rfp_intake.graph import run_ticket  # noqa: E402
from mock_data.pipelines.rfp_intake.llm import chat_json  # noqa: E402
from mock_data.pipelines.rfp_intake.repository import RfpRepository  # noqa: E402
from mock_data.pipelines.rfp_intake.response_graph import run_response  # noqa: E402


def fake_llm(system: str, user: str) -> dict:
    english = "en inglés" in system
    text = ("We handle this part of your operation with a clear and simple process. Every step is tracked and shared "
            "with your team. Pending prices will be confirmed with you before the proposal is final.") if english else (
            "Gestionamos esta parte de su operación con un proceso claro y sencillo. Cada paso queda registrado y se "
            "comparte con su equipo. Los precios pendientes se confirmarán antes de cerrar la propuesta.")
    return {"enfoque": text, "_model": "fake"}


def log(timeline: list, repo: RfpRepository, ticket_id: str, step: str) -> None:
    t = repo.get_ticket(ticket_id)
    sections = {s.department_id: s.approval_status for s in repo.get_sections(ticket_id)}
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "step": step, "status": t.status,
             "pipeline_stage": t.pipeline_stage, "response_stage": t.response_stage, "approval_stage": t.approval_stage,
             "sections": sections}
    timeline.append(entry)
    print(f"{entry['at']}  {step:42} status={t.status:22} approval={t.approval_stage or '—':10} {sections}")


def decide(repo, llm, ticket_id: str, dept: str, decision: str = "approve", comment: str = "", commitments=None):
    version = repo.get_section(ticket_id, dept).approval_request["version"]
    return resume_approval(ticket_id, dept, {"decision": decision, "approver": DEPARTMENTS[dept].owner,
                                             "section_version": version, "comment": comment,
                                             "commitments": commitments or {}, "decided_by": "rfp_e2e.py"}, repo, llm_fn=llm)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pdf", type=Path)
    source.add_argument("--markdown", type=Path)
    parser.add_argument("--scenario", choices=["approve-all", "capacity-conflict", "request-changes"], default="approve-all")
    parser.add_argument("--fake-llm", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    llm = fake_llm if args.fake_llm else chat_json
    repo = RfpRepository(engine())
    timeline: list = []

    ticket_id = uuid.uuid4()
    raw_dir = ROOT / "mock_data" / "raw" / "rfp"
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / f"{ticket_id}.pdf"
    if args.pdf:
        shutil.copyfile(args.pdf, target)
    converter = None if args.pdf else (lambda _p: args.markdown.read_text(encoding="utf-8"))
    repo.create_ticket(ticket_id=ticket_id, rfp_id=f"RFP-E2E-{ticket_id.hex[:6].upper()}", raw_pdf_path=str(target),
                       original_filename=(args.pdf or args.markdown).name, uploaded_by="rfp_e2e.py")
    tid = str(ticket_id)
    log(timeline, repo, tid, "subida")

    kwargs = {"converter": converter} if converter else {}
    run_ticket(tid, repo, llm_fn=llm, **kwargs)
    log(timeline, repo, tid, "Parte 1: recepción")
    if repo.get_ticket(tid).status != "analisis_completo":
        print("El documento no es una RFP: fin del flujo.")
        return 0
    run_response(tid, repo, llm_fn=llm)
    log(timeline, repo, tid, "Parte 2: generación y evaluación")
    start_approval(tid, repo, llm_fn=llm)
    log(timeline, repo, tid, "Parte 3: aprobación iniciada")

    departments = sorted(s.department_id for s in repo.get_sections(tid))
    order = (["lastmile"] if "lastmile" in departments else []) + [d for d in departments if d != "lastmile"]
    first = order[0]
    if args.scenario == "request-changes":
        decide(repo, llm, tid, first, "request_changes", comment="Acorta el alcance a lo esencial y menciona el plazo.")
        log(timeline, repo, tid, f"{first}: cambios solicitados")
    for dept in order:
        commitments = {}
        if args.scenario == "capacity-conflict" and dept == "warehouse":
            volume = (repo.get_ticket(tid).handoff.get("metadata") or {}).get("monthly_volume") or 1000
            commitments = {"capacity_monthly_orders": max(1, int(volume * 0.8))}
        decide(repo, llm, tid, dept, commitments=commitments)
        log(timeline, repo, tid, f"{dept}: aprobada por {DEPARTMENTS[dept].owner}")
    while repo.get_ticket(tid).status == "esperando_aprobación":    # revisiones forzadas por el arbitraje
        for section in repo.get_sections(tid):
            if section.approval_status == "esperando_aprobación":
                decide(repo, llm, tid, section.department_id)
                log(timeline, repo, tid, f"{section.department_id}: aprobada tras arbitraje")

    ticket = repo.get_ticket(tid)
    document = repo.get_final_document(tid)
    trace = [{"part": e.part, "node": e.node, "agent": e.agent, "department_id": e.department_id,
              "finished_at": e.finished_at.isoformat()} for e in repo.get_trace(tid)]
    print("\nTraza de agentes (orden de ejecución):")
    for e in trace:
        print(f"  {e['part']:8} {e['agent']:28} {e['node']:20} {e['department_id'] or ''}")
    print(f"\nTicket {tid}: {ticket.status} · documento final: {'sí' if document else 'no'}"
          + (f" ({document.currency}, {len(document.sections)} secciones)" if document else ""))
    report = {"ticket_id": tid, "thread_id": ticket.approval_thread_id, "final_status": ticket.status,
              "arbitration": ticket.arbitration, "timeline": timeline, "trace": trace,
              "final_document": None if document is None else {"currency": document.currency, "approvals": document.approvals,
                                                                 "content": document.content}}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if ticket.status == "terminado" else 1


if __name__ == "__main__":
    sys.exit(main())
