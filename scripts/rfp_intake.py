"""CLI de la recepción de RFP (Parte 1) y de la generación/evaluación de la respuesta (Parte 2), sin un segundo API.

    uv run python scripts/rfp_intake.py list
    uv run python scripts/rfp_intake.py reprocess <ticket_id> [--force-accept]   # en primer plano
    uv run python scripts/rfp_intake.py process ruta/a/rfp.pdf                   # crea ticket y lo procesa aquí
    uv run python scripts/rfp_intake.py generate <ticket_id> [--max-iterations 3] # Parte 2 en primer plano
    uv run python scripts/rfp_intake.py approval-start <ticket_id>                # Parte 3: arranca y se pausa
    uv run python scripts/rfp_intake.py decide <ticket_id> <department> approve|reject|request_changes \
        [--comment …] [--commitment capacity_monthly_orders=4000]                 # reanuda SOLO esa rama

Usa la misma base (``DATABASE_URL``, PostgreSQL/Supabase) y el mismo pipeline (``mock_data/pipelines/rfp_intake``)
que el backend y el worker de Celery.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlmodel import create_engine  # noqa: E402

from mock_data.pipelines.rfp_intake.graph import run_ticket  # noqa: E402
from mock_data.pipelines.rfp_intake.repository import RfpRepository  # noqa: E402
from mock_data.pipelines.rfp_intake.response_graph import run_response  # noqa: E402
from mock_data.pipelines.rfp_intake.approval_graph import resume_approval, start_approval  # noqa: E402
from mock_data.pipelines.rfp_intake.departments import DEPARTMENTS  # noqa: E402


def engine():
    url = os.environ["DATABASE_URL"]
    if url.startswith(("postgres://", "postgresql://")):
        # Entorno raíz: psycopg 3; entorno de services/api: psycopg2. Se usa el que esté instalado.
        try:
            import psycopg  # noqa: F401
        except ModuleNotFoundError:
            return create_engine("postgresql+psycopg2://" + url.split("://", 1)[1], pool_pre_ping=True)
        url = "postgresql+psycopg://" + url.split("://", 1)[1]
        return create_engine(url, pool_pre_ping=True, connect_args={"prepare_threshold": None})
    return create_engine(url)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    reprocess = sub.add_parser("reprocess")
    reprocess.add_argument("ticket_id")
    reprocess.add_argument("--force-accept", action="store_true")
    process = sub.add_parser("process")
    process.add_argument("pdf", type=Path)
    generate = sub.add_parser("generate")
    generate.add_argument("ticket_id")
    generate.add_argument("--max-iterations", type=int, default=None)
    approval_start = sub.add_parser("approval-start")
    approval_start.add_argument("ticket_id")
    decide = sub.add_parser("decide")
    decide.add_argument("ticket_id")
    decide.add_argument("department", choices=sorted(DEPARTMENTS))
    decide.add_argument("decision", choices=["approve", "reject", "request_changes"])
    decide.add_argument("--comment", default="")
    decide.add_argument("--commitment", action="append", default=[], help="clave=entero, p. ej. capacity_monthly_orders=4000")
    args = parser.parse_args()
    repo = RfpRepository(engine())
    if args.command == "list":
        for ticket in repo.list_tickets():
            print(f"{ticket.ticket_id}  {ticket.status:20} {ticket.pipeline_stage:11} {ticket.response_stage or '—':10} "
                  f"{ticket.rfp_id:22} {ticket.original_filename}")
        return 0
    if args.command == "approval-start":
        print(json.dumps(start_approval(args.ticket_id, repo), ensure_ascii=False))
        return 0
    if args.command == "decide":
        section = repo.get_section(args.ticket_id, args.department)
        decision = {"decision": args.decision, "approver": DEPARTMENTS[args.department].owner,
                    "section_version": (section.approval_request or {}).get("version", 0), "comment": args.comment,
                    "commitments": {k: int(v) for k, v in (c.split("=", 1) for c in args.commitment)}, "decided_by": "cli"}
        print(json.dumps(resume_approval(args.ticket_id, args.department, decision, repo), ensure_ascii=False))
        return 0
    if args.command == "generate":
        result = run_response(args.ticket_id, repo, max_iterations=args.max_iterations)
        handoff = result["response_handoff"] or {}
        print(json.dumps({"ticket_id": result["ticket_id"], "status": result["status"], "response_stage": result["response_stage"],
                          "sections": [{k: s[k] for k in ("department_id", "section_status", "iterations")}
                                       for s in handoff.get("sections", [])]}, ensure_ascii=False))
        return 0
    if args.command == "reprocess":
        result = run_ticket(args.ticket_id, repo, force_accept=args.force_accept)
    else:
        ticket_id = uuid.uuid4()
        target = Path(os.environ.get("RFP_RAW_DIR") or ROOT / "mock_data" / "raw" / "rfp") / f"{ticket_id}.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.pdf, target)
        repo.create_ticket(ticket_id=ticket_id, rfp_id=f"RFP-{datetime.now(timezone.utc):%Y%m%d}-{ticket_id.hex[:6].upper()}",
                           raw_pdf_path=str(target), original_filename=args.pdf.name, uploaded_by="cli")
        result = run_ticket(str(ticket_id), repo)
    print(json.dumps({k: result[k] for k in ("ticket_id", "status", "pipeline_stage")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
