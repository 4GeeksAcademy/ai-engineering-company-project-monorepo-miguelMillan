"""Ingesta: PDF → Markdown (MarkItDown), metadatos del documento y métricas de legibilidad.

Ningún agente lee el PDF: todos leen el Markdown que produce MarkItDown (``convert_pdf``).

Metadatos (BRIEFING §2.2/§2.3): extracción DETERMINISTA y verificable. Cada campo guarda el fragmento del
documento del que sale (``evidence``); si un dato no está en la RFP queda ``None`` y se convierte en pregunta
abierta. Nunca se inventa un volumen, una fecha ni un presupuesto.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from mock_data.pipelines.rfp_intake.departments import CURRENCY_BY_COUNTRY, DEPARTMENT_BY_SERVICE


# ============================================================================
# PDF → Markdown
# ============================================================================
def convert_pdf(path: Path | str) -> str:
    """Convierte el PDF con MarkItDown y normaliza artefactos de pdfminer (viñetas ``(cid:127)``)."""
    from markitdown import MarkItDown

    text = MarkItDown().convert(str(path)).text_content or ""
    text = re.sub(r"\(cid:\d+\)\s*", "- ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def normalize(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (text or "").lower()) if unicodedata.category(c) != "Mn")


# ============================================================================
# Metadatos
# ============================================================================
@dataclass
class RfpMetadata:
    rfp_reference: str | None = None
    client_name: str | None = None
    client_country: str | None = None          # "US" | "ES"
    currency: str | None = None                # derivada de client_country (USD / EUR)
    services_requested: list[str] = field(default_factory=list)   # warehousing | last_mile | reverse_logistics
    excluded_services: list[str] = field(default_factory=list)    # pedidos explícitamente fuera de alcance
    monthly_volume: int | None = None          # pedidos/mes, solo si la RFP lo dice
    sku_count: int | None = None
    deadline: str | None = None                # ISO, solo si la fecha está completa en la RFP
    deadline_text: str | None = None           # el texto tal cual (p. ej. «around August 24th»)
    budget_range: str | None = None
    contact: str | None = None
    departments_needed: list[str] = field(default_factory=list)
    out_of_scope_requests: list[str] = field(default_factory=list)  # pedidos sin departamento en el BRIEFING
    evidence: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SPAIN = re.compile(r"\b(espana|spain|espanol\w*|zaragoza|madrid|barcelona|aragon|s\.l\.|s\.a\.|\.es\b|eur\b|€)")
USA = re.compile(r"\b(usa|u\.s\.|united states|estados unidos|ee\.? ?uu|los angeles|california|, ca\b|new york|texas|"
                 r"inc\.|llc|across the us|the us\b|usd|\$)")
SERVICE_PATTERNS = {
    "warehousing": re.compile(r"\b(almacenamiento|almacenaje|almacen\w*|warehousing|warehouse|storage|fulfil+ment)\b"),
    "last_mile": re.compile(r"\b(ultima milla|last[- ]mile|shipping|envios? (a|al|para) (los )?client\w*|reparto|"
                            r"carrier selection|seleccion de transportistas?|gestion de transportistas)\b"),
    "reverse_logistics": re.compile(r"\b(devoluciones|gestion de devoluciones|returns? (management|processing|handling)|"
                                    r"reverse logistics|logistica inversa)\b"),
}
EXCLUSIONS = {
    "last_mile": re.compile(r"(no se requiere[^.\n]{0,60}(ultima milla|transportistas?)|"
                            r"(mantenemos|mantiene|tenemos|usamos|utilizamos) (nuestro |su )?propio transportista|"
                            r"(our|their) own (carrier|shipping|transport)|we (already )?(ship|handle shipping) ourselves|"
                            r"(sin|without) (ultima milla|last[- ]mile))"),
    "warehousing": re.compile(r"(no (se requiere|necesitamos) (almacenamiento|almacen)|(our|their) own warehouse|"
                              r"(nuestro|su) propio almacen)"),
    "reverse_logistics": re.compile(r"(no (se requiere|necesitamos) (gestion de )?devoluciones|"
                                    r"(we|they) (handle|manage) returns (ourselves|in-house)|no returns)"),
}
# Pedidos que no cubre ningún departamento del BRIEFING (§2.1): se informan a Ventas, no se inventan workers.
UNKNOWN_SCOPE = [
    (re.compile(r"\b(integracion con (nuestro )?erp|erp (integration|via api)|integracion\w* via api|api integration)\b"),
     "Integración tecnológica (ERP/API)"),
    (re.compile(r"\b(aduanas?|customs( brokerage| clearance)?|despacho aduanero)\b"), "Gestión aduanera"),
    (re.compile(r"\b(transporte maritimo|ocean freight|air freight|carga aerea|freight forwarding)\b"), "Transporte internacional de carga"),
    (re.compile(r"\b(atencion al cliente|customer service|call center)\b"), "Atención al cliente del e-commerce"),
    (re.compile(r"\b(fotografia de producto|product photography|marketing)\b"), "Servicios de marketing/contenido"),
]
VOLUME = re.compile(r"(\d{1,3}(?:[.,]\d{3})+|\d+)\s*(?:\+\s*)?(orders|pedidos|envios|shipments|packages|paquetes)"
                    r"\s*(?:a|per|al|por|/|cada)\s*(?:month|mes)\b")
SKUS = re.compile(r"(\d{1,3}(?:[.,]\d{3})+|\d+)\s*(?:skus?|referencias)\b")
BUDGET = re.compile(r"(presupuesto|budget)[^.\n]{0,60}?((?:€|\$|usd|eur)\s?\d[\d.,]*(?:\s?[-–a]\s?(?:€|\$|usd|eur)?\s?\d[\d.,]*)?(?:\s?(?:€|eur|usd|k|mil))?)")
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september",
                                       "october", "november", "december"], 1)}
MONTHS.update({m: i for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                            "septiembre", "octubre", "noviembre", "diciembre"], 1)})
DEADLINE_LABELED = re.compile(r"(proposal due date|due date|fecha limite|deadline|plazo de (entrega|presentacion))\s*:?\s*([^\n]{3,40})")
DATE_EN = re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?")
DATE_ES = re.compile(r"\b(\d{1,2})\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)(?:\s+de\s+(\d{4}))?")


def _number(raw: str) -> int:
    return int(re.sub(r"[.,]", "", raw))


def _parse_date(text: str) -> tuple[str | None, str | None]:
    """(fecha ISO si el año está explícito, texto original). Nunca se inventa el año."""
    norm = normalize(text)
    for match in (DATE_EN.search(norm), DATE_ES.search(norm)):
        if not match:
            continue
        if match.re is DATE_EN:
            month, day, year = MONTHS[match.group(1)], int(match.group(2)), match.group(3)
        else:
            day, month, year = int(match.group(1)), MONTHS[match.group(2)], match.group(3)
        raw = text[match.start():match.end()]
        return (date(int(year), month, day).isoformat() if year else None), raw
    return None, None


def _first_line_client(markdown: str) -> str | None:
    for line in markdown.splitlines()[:5]:
        match = re.match(r"^\s*([A-Z][\w&.' -]{2,60}?)\s+[—-]\s+[A-Z]", line)
        if match:
            return match.group(1).strip()
    return None


def extract_metadata(markdown: str) -> RfpMetadata:
    meta = RfpMetadata()
    norm = normalize(markdown)
    ev = meta.evidence

    ref = re.search(r"(rfp reference no\.?|reference no\.?|referencia( rfp)?|n\.?o de referencia)\s*:?\s*([A-Za-z0-9][\w./-]{2,40})", markdown, re.I)
    if ref:
        meta.rfp_reference, ev["rfp_reference"] = ref.group(3), ref.group(0).strip()

    for pattern in (r"issuing organization\s*:\s*([^\n]+)", r"(?:organizacion|empresa|cliente)\s*:\s*([^\n]+)",
                    r"\bwe(?:'re| are)\s+([A-Z][\w&' -]{2,40}?)(?:,| a\b| an\b)", r"\bsomos\s+([A-Z][\w&' -]{2,40}?)(?:,| una\b| un\b)"):
        match = re.search(pattern, markdown, re.I if "issuing" in pattern or "organizacion" in pattern else 0)
        if match:
            meta.client_name, ev["client_name"] = match.group(1).strip(), match.group(0).strip()
            break
    if not meta.client_name:
        first = _first_line_client(markdown)
        if first:
            meta.client_name, ev["client_name"] = first, markdown.splitlines()[0].strip()

    spain, usa = SPAIN.search(norm), USA.search(norm)
    if spain and not usa:
        meta.client_country = "ES"
        ev["client_country"] = spain.group(0)
    elif usa and not spain:
        meta.client_country = "US"
        ev["client_country"] = usa.group(0)
    elif spain and usa:
        # Ambos aparecen: decide el que identifica al cliente (cabecera/organización) o el primero en el texto.
        meta.client_country = "ES" if spain.start() < usa.start() else "US"
        ev["client_country"] = f"ambiguo: «{spain.group(0)}» y «{usa.group(0)}»; se toma el primero"
    meta.currency = CURRENCY_BY_COUNTRY.get(meta.client_country or "")

    for service, pattern in SERVICE_PATTERNS.items():
        hit = pattern.search(norm)
        excluded = EXCLUSIONS[service].search(norm)
        if excluded:
            meta.excluded_services.append(service)
            ev[f"excluded:{service}"] = excluded.group(0)
        elif hit:
            meta.services_requested.append(service)
            ev[f"service:{service}"] = hit.group(0)
    meta.departments_needed = [DEPARTMENT_BY_SERVICE[s] for s in meta.services_requested]

    for pattern, label in UNKNOWN_SCOPE:
        hit = pattern.search(norm)
        if hit:
            meta.out_of_scope_requests.append(label)
            ev[f"out_of_scope:{label}"] = hit.group(0)

    volume = VOLUME.search(norm)
    if volume:
        meta.monthly_volume, ev["monthly_volume"] = _number(volume.group(1)), volume.group(0)
    skus = SKUS.search(norm)
    if skus:
        meta.sku_count, ev["sku_count"] = _number(skus.group(1)), skus.group(0)

    labeled = DEADLINE_LABELED.search(norm)
    if labeled:
        start = labeled.start(3)
        meta.deadline, meta.deadline_text = _parse_date(markdown[start:start + 40])
        ev["deadline"] = markdown[labeled.start():labeled.end()].strip()
    else:
        timing = re.search(r"(within the next[^.]{0,80}|antes del?[^.\n]{0,40}|around [a-z]+ \d{1,2}(st|nd|rd|th)?)", norm)
        if timing:
            phrase = " ".join(markdown[timing.start():timing.end()].split())
            iso, raw = _parse_date(phrase)
            # Sin año explícito no se fija una fecha ISO: se guarda el texto tal cual (nunca se inventa el año).
            meta.deadline, meta.deadline_text = iso, raw or phrase
            ev["deadline"] = phrase

    budget = BUDGET.search(norm)
    if budget:
        meta.budget_range, ev["budget_range"] = budget.group(2).strip(), budget.group(0)

    contact = re.search(r"(?:^|\n)(?:\d+\.\s*)?(?i:best,|regards,|saludos,|atentamente,|contact|contacto)\s*\n+([A-Z][^\n]{2,60})(?:\n([^\n]{2,80}))?", markdown)
    if contact:
        meta.contact = " — ".join(x.strip() for x in contact.groups() if x)
    return meta


# ============================================================================
# Legibilidad y costo de procesamiento
# ============================================================================
SENTENCE_SPLIT = re.compile(r"(?<=[.!?¿¡])\s+|\n{2,}")


def _sentences(text: str) -> list[str]:
    return [s for s in (part.strip() for part in SENTENCE_SPLIT.split(text)) if s]


def _spanish_syllables(word: str) -> int:
    return max(1, len(re.findall(r"[aeiouáéíóúü]+", word.lower())))


def readability_metrics(markdown: str) -> dict[str, Any]:
    """Métricas que anticipan el costo de procesamiento del documento.

    * ``py-readability-metrics`` (Flesch, Flesch-Kincaid, Gunning Fog, ARI, Coleman-Liau) con su tokenizador
      de oraciones sustituido por uno por regex: el de NLTK necesita descargar ``punkt`` en tiempo de ejecución
      (no reproducible en CI). La librería exige ≥ 100 palabras: por debajo, esas métricas quedan ``None`` con
      el motivo, sin fallar.
    * Fernández-Huerta (adaptación de Flesch al español), calculada aquí, porque las RFP llegan en español.
    * Proxies de costo: palabras, oraciones, tablas/listas y tokens estimados (≈ caracteres / 4).
    """
    import readability.text.analyzer as analyzer
    from readability import Readability
    from readability.exceptions import ReadabilityException

    analyzer.sent_tokenize = _sentences  # tokenizador determinista, sin datos de NLTK
    words = re.findall(r"[A-Za-zÀ-ÿ0-9]+", markdown)
    sentences = _sentences(markdown)
    metrics: dict[str, Any] = {
        "words": len(words), "sentences": len(sentences), "characters": len(markdown),
        "estimated_tokens": max(1, len(markdown) // 4),
        "list_items": len(re.findall(r"(?m)^\s*[-*•]\s", markdown)),
        "fernandez_huerta": None, "flesch": None, "flesch_kincaid_grade": None, "gunning_fog": None, "ari": None,
        "coleman_liau": None, "readability_note": None,
    }
    if words and sentences:
        syllables = sum(_spanish_syllables(w) for w in words)
        metrics["fernandez_huerta"] = round(206.84 - 60 * (syllables / len(words)) - 1.02 * (len(words) / len(sentences)), 1)
    try:
        scorer = Readability(markdown)
        metrics.update({
            "flesch": round(scorer.flesch().score, 1),
            "flesch_kincaid_grade": round(scorer.flesch_kincaid().score, 1),
            "gunning_fog": round(scorer.gunning_fog().score, 1),
            "ari": round(scorer.ari().score, 1),
            "coleman_liau": round(scorer.coleman_liau().score, 1),
        })
    except ReadabilityException as error:
        metrics["readability_note"] = f"py-readability-metrics: {error}"
    tokens, fog = metrics["estimated_tokens"], metrics["gunning_fog"] or 12
    metrics["processing_cost"] = "alto" if tokens > 6000 or fog > 18 else "medio" if tokens > 1500 or fog > 14 else "bajo"
    return metrics


def utcnow() -> datetime:
    from datetime import timezone

    return datetime.now(timezone.utc)
