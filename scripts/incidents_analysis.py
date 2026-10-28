import csv
import io
import json
import sys
from collections import Counter
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.shared.incident_validation import (
    CARRIERS_BY_COUNTRY,
    CATEGORIES,
    COUNTRIES,
    CSVFormatError,
    CUSTOMER_TYPES,
    REQUIRED_FIELDS,
    RULE_LABELS,
    STATUSES,
    csv_value as _value,
    record_issues as _record_issues,
    validated_csv_rows,
)

REFERENCE = {
    "total_records": 100,
    "valid_records": 95,
    "invalid_records": 5,
    "by_category": {
        "LOST_PARCEL": 14,
        "DELAYED_DELIVERY": 38,
        "WRONG_ADDRESS": 19,
        "RETURN_REQUEST": 17,
        "DAMAGE": 7,
    },
    "by_status": {"OPEN": 29, "CLOSED": 52, "DISCARDED": 14},
    "by_country": {"US": 50, "ES": 45},
    "invalid_by_rule": {
        "tracking_number": 1,
        "carrier_country": 1,
        "category": 1,
        "customer_email": 1,
        "closed_without_score": 1,
    },
    "score_counts": {1: 6, 2: 11, 3: 15, 4: 14, 5: 6},
    "average_score": 3.06,
}


def analyze_csv_text(csv_text):
    category_counts = Counter()
    status_counts = Counter()
    country_counts = Counter()
    score_counts = Counter()
    invalid_counts = Counter({rule: 0 for rule in RULE_LABELS})
    invalid_records = []
    total_records = 0
    valid_records = 0
    closed_records = 0

    for row_number, row, issues, score in validated_csv_rows(csv_text):
        total_records += 1
        if issues:
            invalid_counts.update(issues)
            invalid_records.append({"row": row_number, "issues": issues})
            continue

        valid_records += 1
        category_counts[_value(row, "category")] += 1
        status_counts[_value(row, "status")] += 1
        country_counts[_value(row, "country")] += 1
        if _value(row, "status") == "CLOSED":
            closed_records += 1
            score_counts[score] += 1

    scored_records = sum(score_counts.values())
    score_total = sum(score * count for score, count in score_counts.items())
    average_score = round(score_total / scored_records, 2) if scored_records else None

    return {
        "totals": {
            "total_records": total_records,
            "valid_records": valid_records,
            "invalid_records": total_records - valid_records,
        },
        "by_category": {key: category_counts[key] for key in CATEGORIES},
        "by_status": {key: status_counts[key] for key in STATUSES},
        "by_country": {key: country_counts[key] for key in ("US", "ES")},
        "satisfaction": {
            "closed_records": closed_records,
            "scored_records": scored_records,
            "average_score": average_score,
            "score_counts": {score: score_counts[score] for score in range(1, 6)},
        },
        "invalid_by_rule": dict(invalid_counts),
        "invalid_records": invalid_records,
    }


def reference_mismatches(report):
    actual = report["totals"]
    mismatches = []

    for key in ("total_records", "valid_records", "invalid_records"):
        if actual[key] != REFERENCE[key]:
            mismatches.append(f"{key}: esperado {REFERENCE[key]}, obtenido {actual[key]}")

    for section in ("by_category", "by_status", "by_country"):
        expected = REFERENCE[section]
        for key, expected_value in expected.items():
            actual_value = report[section][key]
            if actual_value != expected_value:
                mismatches.append(
                    f"{section}.{key}: esperado {expected_value}, obtenido {actual_value}"
                )

    for rule, expected_value in REFERENCE["invalid_by_rule"].items():
        actual_value = report["invalid_by_rule"][rule]
        if actual_value != expected_value:
            mismatches.append(
                f"invalid_by_rule.{rule}: esperado {expected_value}, obtenido {actual_value}"
            )
    for rule, actual_value in report["invalid_by_rule"].items():
        if rule not in REFERENCE["invalid_by_rule"] and actual_value:
            mismatches.append(f"invalid_by_rule.{rule}: esperado 0, obtenido {actual_value}")

    for score, expected_value in REFERENCE["score_counts"].items():
        actual_value = report["satisfaction"]["score_counts"][score]
        if actual_value != expected_value:
            mismatches.append(
                f"satisfaction.score_counts.{score}: esperado {expected_value}, obtenido {actual_value}"
            )
    if report["satisfaction"]["average_score"] != REFERENCE["average_score"]:
        mismatches.append(
            "satisfaction.average_score: esperado "
            f"{REFERENCE['average_score']:.2f}, obtenido "
            f"{report['satisfaction']['average_score']}"
        )

    return mismatches


def render_report(report, source_name):
    totals = report["totals"]
    satisfaction = report["satisfaction"]
    lines = [
        "=" * 64,
        "  TRACKFLOW — INFORME DE INCIDENCIAS",
        f"  Archivo: {Path(source_name).name}",
        "=" * 64,
        "",
        f"REGISTROS TOTALES ............... {totals['total_records']:>5}",
        f"  Válidos ....................... {totals['valid_records']:>5}",
        f"  Inválidos / incompletos ....... {totals['invalid_records']:>5}",
        "",
        "DESGLOSE DE REGISTROS INVÁLIDOS",
    ]

    for rule, label in RULE_LABELS.items():
        lines.append(f"  {label:<42} {report['invalid_by_rule'][rule]:>4}")

    lines.extend(["", "DETALLE DE REGISTROS INVÁLIDOS"])
    if report["invalid_records"]:
        for record in report["invalid_records"]:
            labels = ", ".join(RULE_LABELS[issue] for issue in record["issues"])
            lines.append(f"  Fila {record['row']}: {labels}")
    else:
        lines.append("  Ninguno")

    lines.extend(["", "INCIDENCIAS POR CATEGORÍA (registros válidos)"])
    for category, count in report["by_category"].items():
        lines.append(f"  {category:<28} {count:>4} ({_percentage(count, totals['valid_records'])})")

    lines.extend(["", "INCIDENCIAS POR ESTADO (registros válidos)"])
    for status, count in report["by_status"].items():
        lines.append(f"  {status:<28} {count:>4} ({_percentage(count, totals['valid_records'])})")

    lines.extend(["", "INCIDENCIAS POR PAÍS (registros válidos)"])
    for country, count in report["by_country"].items():
        lines.append(f"  {country:<28} {count:>4} ({_percentage(count, totals['valid_records'])})")

    average = satisfaction["average_score"]
    lines.extend(
        [
            "",
            "ÍNDICE DE SATISFACCIÓN (incidencias cerradas válidas)",
            f"  Incidencias puntuadas: {satisfaction['scored_records']} de {satisfaction['closed_records']}",
            f"  Puntuación media: {average:.2f} / 5.00" if average is not None else "  Puntuación media: N/D",
        ]
    )
    for score, count in satisfaction["score_counts"].items():
        lines.append(f"  Puntuación {score} .................... {count}")
    lines.extend(["", "=" * 64])
    return "\n".join(lines)


def _percentage(value, denominator):
    return f"{(value / denominator * 100):.1f}%" if denominator else "0.0%"


def results_rows(report):
    rows = [
        ("total_records", report["totals"]["total_records"]),
        ("valid_records", report["totals"]["valid_records"]),
        ("invalid_records", report["totals"]["invalid_records"]),
    ]
    rows.extend(
        (f"invalid_rule:{rule}", count)
        for rule, count in report["invalid_by_rule"].items()
    )
    rows.extend((f"category:{key}", value) for key, value in report["by_category"].items())
    rows.extend((f"status:{key}", value) for key, value in report["by_status"].items())
    rows.extend((f"country:{key}", value) for key, value in report["by_country"].items())
    rows.extend(
        [
            ("closed_records", report["satisfaction"]["closed_records"]),
            ("scored_closed_records", report["satisfaction"]["scored_records"]),
            ("average_satisfaction_score", report["satisfaction"]["average_score"]),
        ]
    )
    rows.extend(
        (f"satisfaction_score:{score}", count)
        for score, count in report["satisfaction"]["score_counts"].items()
    )
    return rows


def write_results_csv(report, destination):
    with open(destination, "w", encoding="utf-8", newline="") as output:
        output.write(results_csv_text(report))


def results_csv_text(report):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("metric", "value"))
    writer.writerows(results_rows(report))
    return output.getvalue()


def _worker():
    payload = json.load(sys.stdin)
    report = analyze_csv_text(payload["csv"])
    json.dump({"report": report, "results_csv": results_csv_text(report)}, sys.stdout)


if __name__ == "__main__" and "--json-stdin" in sys.argv:
    try:
        _worker()
    except (CSVFormatError, KeyError, json.JSONDecodeError) as error:
        json.dump({"error": str(error)}, sys.stdout)
        raise SystemExit(2) from error