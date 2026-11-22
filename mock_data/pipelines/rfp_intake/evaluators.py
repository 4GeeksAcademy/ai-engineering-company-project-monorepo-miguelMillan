"""Agentes evaluadores de la Parte 2 (corren en paralelo sobre cada sección generada).

Cada evaluador recibe **solo lo que necesita** y devuelve su propio bloque del ``EvaluationResult``:

* ``evaluate_readability(draft, language)``: el texto de la sección (sin tablas). py-readability-metrics +
  Fernández-Huerta para español; nada de la RFP.
* ``evaluate_relevance(draft, narrative, inp)``: la sección + lo que pidió la RFP según el handoff de la Parte 1
  (``ask`` del departamento, ``key_aspects`` con cifras, ``open_questions``, servicios excluidos). No el PDF.
* ``evaluate_compliance(draft, inp)``: la sección + moneda esperada del país. Reglas del BRIEFING §5, cada una con
  su ``rule_id``.

``combine`` une los tres bloques en el ``EvaluationResult`` y redacta un ``feedback_for_generator`` concreto: qué
regla, qué frase y cómo corregirla.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from mock_data.pipelines.rfp_intake import rate_card as rc
from mock_data.pipelines.rfp_intake.generation import SectionInput

# ============================================================================
# Utilidades de texto
# ============================================================================
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def prose_of(markdown: str) -> str:
    """Texto que se lee de corrido: sin tablas, encabezados ni negritas."""
    lines = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("|", "#")) or stripped.startswith("**") and "·" in stripped:
            continue
        lines.append(re.sub(r"\*\*|^[-*]\s+", "", stripped))
    return "\n".join(lines)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_SPLIT.split(text) if len(re.findall(r"\w+", s)) >= 2]


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-zÀ-ÿ0-9]+", text)


def _syllables_es(word: str) -> int:
    return max(1, len(re.findall(r"[aeiouáéíóúü]+", word.lower())))


def _syllables_en(word: str) -> int:
    w = word.lower()
    count = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and not w.endswith(("le", "ee")) and count > 1:
        count -= 1
    return max(1, count)


def numbers_in(text: str) -> set[str]:
    """Cifras normalizadas (``15.000`` → ``15000``); ignora los ordinales de sección tipo «5.3»."""
    found = set()
    for raw in re.findall(r"\d[\d.,]*\d|\d", text or ""):
        found.add(re.sub(r"[.,](?=\d{3}\b)", "", raw).rstrip(".,"))
    return found


def _snippet(text: str, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


# ============================================================================
# Legibilidad
# ============================================================================
READABILITY_THRESHOLDS = {"es": 55.0, "en": 40.0}  # Fernández-Huerta / Flesch: ≥ «normal» para un cliente B2B
MAX_SENTENCE_WORDS = 30                              # una oración más larga se señala para dividir


def evaluate_readability(draft: str, language: str) -> dict[str, Any]:
    import readability.text.analyzer as analyzer
    from readability import Readability
    from readability.exceptions import ReadabilityException

    text = prose_of(draft)
    sents = sentences(text)
    words = _words(text)
    details: dict[str, Any] = {"language": language, "words": len(words), "sentences": len(sents),
                               "threshold": READABILITY_THRESHOLDS[language]}
    if not words or not sents:
        return {"pass": False, "score": 0.0, "details": {**details, "reason": "La sección no tiene texto legible."}}
    wps = len(words) / len(sents)
    if language == "es":
        syll = sum(_syllables_es(w) for w in words) / len(words)
        score = round(206.84 - 60 * syll - 1.02 * wps, 1)
        details["metric"] = "fernandez_huerta"
    else:
        syll = sum(_syllables_en(w) for w in words) / len(words)
        score = round(206.835 - 1.015 * wps - 84.6 * syll, 1)
        details["metric"] = "flesch_reading_ease"
    # py-readability-metrics (≥ 100 palabras): Flesch-Kincaid y Gunning Fog como referencia adicional.
    analyzer.sent_tokenize = sentences
    try:
        scorer = Readability(text)
        details["py_readability"] = {"flesch": round(scorer.flesch().score, 1),
                                     "flesch_kincaid_grade": round(scorer.flesch_kincaid().score, 1),
                                     "gunning_fog": round(scorer.gunning_fog().score, 1)}
        if language == "en":
            score = details["py_readability"]["flesch"]
    except ReadabilityException as error:
        details["py_readability"] = {"note": str(error)}
    long_sentences = [{"words": len(_words(s)), "sentence": _snippet(s)} for s in sents if len(_words(s)) > MAX_SENTENCE_WORDS]
    details.update({"avg_words_per_sentence": round(wps, 1), "long_sentences": long_sentences,
                    "max_sentence_words": MAX_SENTENCE_WORDS})
    passed = score >= READABILITY_THRESHOLDS[language] and not long_sentences
    return {"pass": passed, "score": score, "details": details}


# ============================================================================
# Pertinencia (¿responde a lo que pide la RFP según el handoff?)
# ============================================================================
ASK_COVERAGE = {
    "warehouse": [
        ("Capacidad de almacenamiento", r"capacidad|capacity|m³|m3"),
        ("Costo por pallet / SKU", r"pallet|sku"),
        ("Tiempo de onboarding", r"onboarding|puesta en marcha"),
    ],
    "lastmile": [
        ("Costo por envío", r"costo por env[ií]o|cost per shipment"),
        ("Transportistas según destino", r"transportista|carrier"),
        ("SLA de entrega a tiempo", r"\bSLA\b"),
    ],
    "reverse": [
        ("Costo por devolución", r"costo por devoluci|cost per (processed )?return"),
        ("Tiempo de procesamiento de devoluciones", r"tiempo de procesamiento|processing time"),
        ("Proceso de inspección / reacondicionamiento", r"inspecci|inspection"),
    ],
}
EXCLUDED_SERVICE_PATTERNS = {
    "last_mile": r"[uú]ltima milla|last[- ]mile|reparto|delivery to (your )?customers",
    "warehousing": r"almacenamiento|warehous|storage",
    "reverse_logistics": r"devoluci|returns",
}
OFFER_VERBS = r"ofrecemos|incluimos|cotizamos|gestionaremos|nos encargaremos|we (will )?(offer|include|quote|handle|provide)"


def allowed_numbers(inp: SectionInput) -> set[str]:
    m = inp.metadata
    pieces = [str(v) for v in m.values() if v] + [str(inp.client.get("contact") or ""), inp.rfp_id]
    pieces += [f"{a.get('detalle')} {a.get('cita') or ''}" for a in inp.key_aspects] + inp.open_questions
    return numbers_in(" ".join(pieces)) | rc.numbers_in_rate_card() | {"1", "2", "3"}


def evaluate_relevance(draft: str, narrative: str, inp: SectionInput) -> dict[str, Any]:
    missing: list[str] = []
    for label, pattern in ASK_COVERAGE[inp.department_id]:
        if not re.search(pattern, draft, re.I):
            missing.append(f"No responde a «{label}», que la RFP pide a {inp.department}.")
    draft_numbers = numbers_in(draft)
    for aspect in inp.key_aspects:
        if aspect.get("origen") == "metadatos":
            for number in numbers_in(str(aspect.get("detalle"))) - draft_numbers:
                missing.append(f"Falta el dato de la RFP «{aspect.get('aspecto')}: {aspect.get('detalle')}» (cifra {number}).")
    for question in inp.open_questions:
        if question not in draft:
            missing.append(f"Falta la pregunta abierta de la Parte 1: «{question}».")
    if len(_words(narrative)) < 30:
        missing.append("El apartado de alcance y enfoque es demasiado corto (menos de 30 palabras) para responder a la RFP.")
    unsupported = sorted(numbers_in(narrative) - allowed_numbers(inp), key=lambda n: (len(n), n))
    off_scope = []
    for service in inp.metadata.get("excluded_services") or []:
        pattern = EXCLUDED_SERVICE_PATTERNS.get(service)
        for sentence in sentences(narrative):
            if pattern and re.search(pattern, sentence, re.I) and re.search(OFFER_VERBS, sentence, re.I):
                off_scope.append({"service": service, "sentence": _snippet(sentence)})
    passed = not missing and not unsupported and not off_scope
    return {"pass": passed, "missing_aspects": missing, "unsupported_figures": unsupported, "off_scope": off_scope}


# ============================================================================
# Cumplimiento de los lineamientos (BRIEFING §5)
# ============================================================================
COMPLIANCE_RULES = {
    "BRIEFING-5.1-currency": "Precio en USD para EE. UU. y en EUR para España (según client_country).",
    "BRIEFING-5.2-on-time-sla": "Indicar el SLA de entrega a tiempo (%) comprometido.",
    "BRIEFING-5.3-returns-min-48h": "Nunca prometer procesamiento de devoluciones en menos de 48 horas.",
    "BRIEFING-5.4-volume-discount-table": "Incluir una tabla de descuentos por volumen.",
    "BRIEFING-5.5-no-carrier-rates": "No revelar tarifas negociadas con transportistas concretos; solo el costo final al cliente.",
}
OTHER_CURRENCY = {
    "EUR": re.compile(r"\bUSD\b|US\$|\$\s?\d|\d\s?\$|d[oó]lar|dollar", re.I),
    "USD": re.compile(r"\bEUR\b|€|\beuros?\b", re.I),
}
SLA_PERCENT = re.compile(r"\bSLA\b[^\n]{0,80}?\d{2,3}(?:[.,]\d+)?\s?%|\d{2,3}(?:[.,]\d+)?\s?%\s*(?:de\s+)?(?:entregas a tiempo|on[- ]time)", re.I)
RETURNS_WORDS = re.compile(r"devoluci|\breturns?\b|reverse logistics|log[ií]stica inversa|\bRMA\b", re.I)
HOURS = re.compile(r"(\d+)\s*(?:h\b|hrs?\b|horas?\b|hours?\b)", re.I)
DAYS = re.compile(r"\b(\d+)\s*(?:d[ií]as?|days?)\b", re.I)
FAST_PHRASES = re.compile(r"mismo d[ií]a|same[- ]day|en el d[ií]a|al d[ií]a siguiente|next[- ]day|"
                          r"(?:menos de|under|less than)\s*48\s*(?:h|horas|hours)", re.I)
NEGATION = re.compile(r"\b(nunca|no|jam[aá]s|never|not|m[ií]nimo|al menos|at least|minimum|inferior)\b", re.I)
CARRIERS = re.compile(r"\b(UPS|FedEx|DHL|MRW|SEUR|USPS|GLS|Correos|OnTrac|LaserShip|Nacex|CTT|Amazon Shipping)\b", re.I)
MONEY = re.compile(r"\d[\d.,]*\s?(?:USD|EUR|€|\$)|[$€]\s?\d|tarifa negociada|negotiated rate|descuento (?:del|de) transportista|"
                   r"carrier discount|nuestro costo con|our cost with", re.I)


def _units(markdown: str) -> list[str]:
    """Oraciones y celdas de tabla: la unidad mínima en la que se evalúa una promesa."""
    units = []
    for line in markdown.splitlines():
        if line.strip().startswith("|"):
            units.append(" ".join(cell.strip() for cell in line.strip().strip("|").split("|")))
        else:
            units.extend(sentences(line))
    return [u for u in units if u.strip()]


def _negated(unit: str, start: int) -> bool:
    return bool(NEGATION.search(unit[max(0, start - 40):start]))


def returns_promises_under_48h(markdown: str) -> list[str]:
    offending = []
    for unit in _units(markdown):
        if not RETURNS_WORDS.search(unit):
            continue
        hits = [m for m in HOURS.finditer(unit) if int(m.group(1)) < rc.MIN_RETURNS_PROCESSING_HOURS]
        hits += [m for m in DAYS.finditer(unit) if int(m.group(1)) < 2]
        hits += list(FAST_PHRASES.finditer(unit))
        if any(not _negated(unit, m.start()) for m in hits):
            offending.append(_snippet(unit))
    return offending


def has_volume_discount_table(markdown: str) -> bool:
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if re.match(r"^#{1,6}\s.*(descuento|discount)", line.strip(), re.I):
            table = []
            for following in lines[index + 1:]:
                if following.strip().startswith("#"):
                    break
                if following.strip().startswith("|"):
                    table.append(following)
            rows = [row for row in table[2:] if not re.fullmatch(r"\|[\s|:-]+\|", row.strip())]
            if len(table) >= 2 and len(rows) >= 2:
                return True
    return False


def evaluate_compliance(draft: str, inp: SectionInput) -> dict[str, Any]:
    violations: list[dict[str, str]] = []
    expected = inp.currency
    if expected not in ("USD", "EUR"):
        violations.append({"rule_id": "BRIEFING-5.1-currency", "evidence": f"client_country={inp.client.get('country')!r}",
                           "message": "No se puede determinar la moneda: el país del cliente no está identificado.",
                           "fix": "Ventas debe confirmar el país del cliente (EE. UU. → USD, España → EUR)."})
    else:
        if not re.search(rf"\b{expected}\b", draft):
            violations.append({"rule_id": "BRIEFING-5.1-currency", "evidence": "sin moneda explícita",
                               "message": f"La sección no indica la moneda {expected}.",
                               "fix": f"Indica explícitamente que los precios están en {expected}."})
        for unit in _units(draft):
            if OTHER_CURRENCY[expected].search(unit):
                violations.append({"rule_id": "BRIEFING-5.1-currency", "evidence": _snippet(unit),
                                   "message": f"Aparece otra moneda: el cliente es de {inp.client.get('country')} y se cotiza en {expected}.",
                                   "fix": f"Expresa todos los importes en {expected} y elimina la otra moneda."})
    if not SLA_PERCENT.search(draft):
        violations.append({"rule_id": "BRIEFING-5.2-on-time-sla", "evidence": "sin porcentaje de SLA",
                           "message": "La sección no indica el SLA de entrega a tiempo (%).",
                           "fix": f"Añade «SLA de entrega a tiempo: {rc.ON_TIME_SLA_PERCENT} %» (SLA contractual de TrackFlow)."})
    for unit in returns_promises_under_48h(draft):
        violations.append({"rule_id": "BRIEFING-5.3-returns-min-48h", "evidence": unit,
                           "message": "Promete procesar devoluciones en menos de 48 horas.",
                           "fix": f"Elimina ese plazo: el procesamiento de devoluciones nunca es inferior a {rc.MIN_RETURNS_PROCESSING_HOURS} horas."})
    if not has_volume_discount_table(draft):
        violations.append({"rule_id": "BRIEFING-5.4-volume-discount-table", "evidence": "sin tabla",
                           "message": "Falta la tabla de descuentos por volumen.",
                           "fix": "Añade un apartado «Descuentos por volumen» con una tabla de al menos dos tramos."})
    for unit in _units(draft):
        if CARRIERS.search(unit) and MONEY.search(unit):
            violations.append({"rule_id": "BRIEFING-5.5-no-carrier-rates", "evidence": _snippet(unit),
                               "message": "Revela una tarifa o condición negociada con un transportista concreto.",
                               "fix": "Quita la tarifa del transportista; indica solo el costo final al cliente (o «pendiente de cotizar»)."})
    rule_ids = list(dict.fromkeys(v["rule_id"] for v in violations))
    return {"pass": not violations, "rule_ids": rule_ids, "violations": violations, "rules_checked": list(COMPLIANCE_RULES)}


# ============================================================================
# EvaluationResult
# ============================================================================
@dataclass
class EvaluationResult:
    section_id: str            # <ticket_id>:<department_id>
    department_id: str
    iteration: int
    readability: dict[str, Any]
    relevance: dict[str, Any]
    compliance: dict[str, Any]
    overall_pass: bool
    feedback_for_generator: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def feedback_for(readability: dict[str, Any], relevance: dict[str, Any], compliance: dict[str, Any]) -> str:
    items: list[str] = []
    for v in compliance.get("violations", []):
        items.append(f"[{v['rule_id']}] {v['message']} Texto: «{v['evidence']}». Cómo corregirlo: {v['fix']}")
    for missing in relevance.get("missing_aspects", []):
        items.append(f"[pertinencia] {missing}")
    if relevance.get("unsupported_figures"):
        items.append("[pertinencia] Cifras que no están en la RFP ni en el tarifario de TrackFlow: "
                     + ", ".join(relevance["unsupported_figures"]) + ". Elimínalas o escribe «pendiente de cotizar».")
    for off in relevance.get("off_scope", []):
        items.append(f"[pertinencia] Ofrece un servicio que el cliente excluyó ({off['service']}): «{off['sentence']}». Quita esa oferta.")
    if not readability.get("pass"):
        d = readability.get("details", {})
        item = (f"[legibilidad] {d.get('metric', 'legibilidad')} = {readability.get('score')} (mínimo {d.get('threshold')}); "
                f"media de {d.get('avg_words_per_sentence')} palabras por oración.")
        if d.get("long_sentences"):
            item += " Divide en oraciones de menos de 20 palabras: " + "; ".join(f"«{s['sentence']}»" for s in d["long_sentences"][:3])
        else:
            item += " Usa oraciones más cortas y palabras más simples."
        items.append(item)
    return "\n".join(f"- {item}" for item in items)


def combine(section_id: str, department_id: str, iteration: int, readability: dict[str, Any], relevance: dict[str, Any],
            compliance: dict[str, Any]) -> EvaluationResult:
    overall = bool(readability["pass"] and relevance["pass"] and compliance["pass"])
    return EvaluationResult(section_id, department_id, iteration, readability, relevance, compliance, overall,
                            "" if overall else feedback_for(readability, relevance, compliance))
