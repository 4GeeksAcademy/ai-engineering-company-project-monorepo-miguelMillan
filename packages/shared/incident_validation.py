import csv
import io
import re
from datetime import date


COUNTRIES = {"US", "ES"}
CUSTOMER_TYPES = {"B2B", "B2C"}
CARRIERS_BY_COUNTRY = {
    "US": {"UPS", "FEDEX", "DHL_US"},
    "ES": {"MRW", "SEUR", "DHL_ES", "LOCAL_ES"},
}
CATEGORIES = (
    "LOST_PARCEL", "DELAYED_DELIVERY", "WRONG_ADDRESS", "RETURN_REQUEST", "DAMAGE",
)
STATUSES = ("OPEN", "CLOSED", "DISCARDED")
REQUIRED_FIELDS = (
    "incident_id", "date", "country", "customer_type", "tracking_number", "carrier",
    "category", "description", "status", "customer_email",
)
RULE_LABELS = {
    "incident_id": "Invalid or missing incident ID",
    "date": "Invalid or missing date",
    "country": "Invalid or missing country",
    "customer_type": "Invalid or missing customer type",
    "tracking_number": "Invalid tracking number",
    "carrier_country": "Carrier/country mismatch",
    "category": "Invalid or missing category",
    "description": "Invalid or missing description",
    "status": "Invalid or missing status",
    "customer_email": "Invalid or missing email",
    "closed_without_score": "Closed incident, no score",
    "score_out_of_range": "Satisfaction score outside 1-5",
    "duplicate_incident_id": "Duplicate incident ID",
}


class CSVFormatError(ValueError):
    pass


def csv_value(row, field):
    value = row.get(field)
    return value.strip() if isinstance(value, str) else ""


def record_issues(row, seen_ids, *, require_incident_id=True):
    issues = []
    values = {field: csv_value(row, field) for field in REQUIRED_FIELDS}
    incident_id = values["incident_id"]
    if incident_id or require_incident_id:
        if not re.fullmatch(r"TRF-\d{6}", incident_id):
            issues.append("incident_id")
        elif incident_id in seen_ids:
            issues.append("duplicate_incident_id")
        else:
            seen_ids.add(incident_id)

    incident_date = values["date"]
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", incident_date):
            raise ValueError
        date.fromisoformat(incident_date)
    except ValueError:
        issues.append("date")

    country = values["country"]
    if country not in COUNTRIES:
        issues.append("country")
    if values["customer_type"] not in CUSTOMER_TYPES:
        issues.append("customer_type")
    if len(values["tracking_number"]) < 8:
        issues.append("tracking_number")
    if country not in CARRIERS_BY_COUNTRY or values["carrier"] not in CARRIERS_BY_COUNTRY[country]:
        issues.append("carrier_country")
    if values["category"] not in CATEGORIES:
        issues.append("category")
    if len(values["description"]) < 5:
        issues.append("description")
    status = values["status"]
    if status not in STATUSES:
        issues.append("status")
    if "@" not in values["customer_email"]:
        issues.append("customer_email")

    score_text = csv_value(row, "satisfaction_score")
    score = None
    if score_text:
        try:
            score = int(score_text)
        except ValueError:
            issues.append("score_out_of_range")
        else:
            if score < 1 or score > 5:
                issues.append("score_out_of_range")
    if status == "CLOSED" and score is None and "score_out_of_range" not in issues:
        issues.append("closed_without_score")
    return issues, score


def validated_csv_rows(csv_text, *, require_incident_id=True):
    if not csv_text or not csv_text.strip():
        raise CSVFormatError("El archivo CSV est\u00e1 vac\u00edo.")
    try:
        reader = csv.DictReader(io.StringIO(csv_text, newline=""))
        headers = reader.fieldnames or []
        required = [field for field in REQUIRED_FIELDS if require_incident_id or field != "incident_id"]
        missing = [field for field in required if field not in headers]
        if missing:
            raise CSVFormatError("Faltan encabezados obligatorios: " + ", ".join(missing))
        seen_ids = set()
        for row_number, row in enumerate(reader, start=2):
            if row is not None:
                issues, score = record_issues(row, seen_ids, require_incident_id=require_incident_id)
                yield row_number, row, issues, score
    except csv.Error as error:
        raise CSVFormatError(f"No se pudo interpretar el CSV: {error}") from error