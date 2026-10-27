import argparse
import json
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services" / "api"))

from pydantic import ValidationError
from tinydb import Query

from database import open_database
from models import Incident
from packages.shared.incident_validation import CSVFormatError, RULE_LABELS, csv_value, validated_csv_rows


STATUS_MAP = {"OPEN": "open", "CLOSED": "resolved", "DISCARDED": "discarded"}
CATEGORY_MAP = {
    "LOST_PARCEL": "lost_parcel",
    "DELAYED_DELIVERY": "carrier_issue",
    "WRONG_ADDRESS": "delivery_failure",
    "RETURN_REQUEST": "returns_issue",
    "DAMAGE": "carrier_issue",
}
BRANCH_MAP = {"US": "la_office", "ES": "zaragoza_office"}


def incident_from_row(row) -> Incident:
    description = row["description"]
    title = description[:120].strip()
    created_at = datetime.combine(date.fromisoformat(csv_value(row, "date")), time.min, tzinfo=timezone.utc)
    source_id = csv_value(row, "incident_id")
    identity = ["incident_id", source_id] if source_id else ["title_created_at", title, created_at.isoformat()]
    return Incident(
        id=uuid5(NAMESPACE_URL, "trackflow:incidents:" + json.dumps(identity, ensure_ascii=True)),
        title=title,
        description=description,
        category=CATEGORY_MAP[csv_value(row, "category")],
        status=STATUS_MAP[csv_value(row, "status")],
        origin="customer",
        branch=BRANCH_MAP[csv_value(row, "country")],
        created_at=created_at,
        updated_at=created_at,
    )


def seed_incidents(csv_path: Path, database_path: Path | None = None) -> dict:
    rows = list(validated_csv_rows(csv_path.read_text(encoding="utf-8-sig"), require_incident_id=False))
    report = {"total": len(rows), "inserted": 0, "skipped": 0, "invalid_records": []}
    with open_database(database_path) as database:
        incidents = database.table("incidents")
        query = Query()
        for row_number, row, issues, _ in rows:
            if not issues:
                try:
                    incident = incident_from_row(row)
                except ValidationError as error:
                    issues = ["model:" + ".".join(str(part) for part in item["loc"]) for item in error.errors()]
                except (KeyError, ValueError):
                    issues = ["unmapped_value"]
            if issues:
                report["invalid_records"].append({"row": row_number, "issues": issues})
                continue

            document = incident.model_dump(mode="json")
            duplicate = query.id == document["id"]
            if not csv_value(row, "incident_id"):
                duplicate |= (query.title == document["title"]) & (query.created_at == document["created_at"])
            if incidents.contains(duplicate):
                report["skipped"] += 1
                continue
            incidents.insert(document)
            report["inserted"] += 1
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Importa el CSV historico de incidencias en TinyDB sin duplicados.")
    parser.add_argument("csv_path", nargs="?", type=Path, default=ROOT / "scripts" / "incidents-trackflow.csv")
    parser.add_argument("--db", type=Path, help="Base TinyDB alternativa; por defecto se usa la misma que la API.")
    args = parser.parse_args()
    try:
        report = seed_incidents(args.csv_path, args.db)
    except CSVFormatError as error:
        print(f"Error de formato: {error}", file=sys.stderr)
        return 2
    except (OSError, UnicodeError, json.JSONDecodeError):
        print("No se pudo leer el CSV o abrir/escribir la base TinyDB. Comprueba las rutas, permisos y formato.", file=sys.stderr)
        return 2

    print(f"Filas leidas: {report['total']}")
    print(f"Incidencias insertadas: {report['inserted']}")
    print(f"Duplicados omitidos: {report['skipped']}")
    print(f"Filas invalidas descartadas: {len(report['invalid_records'])}")
    for record in report["invalid_records"]:
        reasons = ", ".join(RULE_LABELS.get(issue, issue) for issue in record["issues"])
        print(f"  Fila {record['row']}: {reasons}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())