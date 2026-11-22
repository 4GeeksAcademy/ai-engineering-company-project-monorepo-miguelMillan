"""Datos comerciales de TrackFlow que los generadores pueden usar en una propuesta económica (Parte 2).

Fuente: la base de conocimiento de la empresa (``docs/company-knowledge-base/*.es.md``, la misma que indexa el
RAG del asistente comercial). Cada dato lleva su fuente. **Lo que la base de conocimiento no fija no se inventa**:
queda como «pendiente» con el responsable que debe cotizarlo (BRIEFING §2.1), y el responsable lo completa al
aprobar la sección en la Parte 3. Así un generador nunca escribe una tarifa que TrackFlow no haya publicado.
"""

from __future__ import annotations

KB = "docs/company-knowledge-base"
SOURCES = {
    "storage": f"{KB}/trackflow-storage-pricing.es.md",
    "sla": f"{KB}/trackflow-sla-delivery.es.md",
    "carriers": f"{KB}/trackflow-carrier-coverage.es.md",
    "returns": f"{KB}/trackflow-returns-policy.es.md",
}

# SLA contractual de entrega a tiempo (BRIEFING §5: toda propuesta debe indicarlo).
ON_TIME_SLA_PERCENT = 90
SLA_COMPENSATION_PERCENT = 5          # compensación si el país cae < 90 % dos meses seguidos
PEAK_EXTENSION_PERCENT = 40           # días de alta demanda: tiempos hasta +40 %, sin SLA garantizado

# Almacenamiento: por volumen ocupado (m³), no por unidad.
STORAGE_RATE = {"USD": 18, "EUR": 16}  # por m³ al mes: Los Ángeles (USD) / Zaragoza (EUR)
STORAGE_GRACE_DAYS = 30
LONG_TERM_DAYS = 180                   # desde el día 181 sin movimiento
LONG_TERM_SURCHARGE_PERCENT = 150      # % sobre la tarifa estándar
PREFERENTIAL_RATE_M3 = 50              # > 50 m³ de media mensual: tarifa preferencial negociable (solo Miguel Torres)

# Devoluciones (BRIEFING §5: nunca prometer procesamiento en menos de 48 h).
MIN_RETURNS_PROCESSING_HOURS = 48
RETURN_WINDOW_DAYS = 30

CARRIERS = {
    "US": "UPS, FedEx y DHL (Los Ángeles)",
    "ES": "MRW, SEUR, DHL y transportistas locales del área metropolitana de Zaragoza",
}
DELIVERY_TIMES = {
    "es": "estándar de 3 a 5 días hábiles en el mismo país; express de 24 a 48 horas en zonas metropolitanas "
          "(Los Ángeles y Zaragoza-Madrid)",
    "en": "standard 3 to 5 business days within the same country; express 24 to 48 hours in metro areas "
          "(Los Angeles and Zaragoza-Madrid)",
}

# Tabla de descuentos por volumen (BRIEFING §5). Los tramos son los que Ventas usa para dimensionar; el porcentaje
# de cada tramo NO está publicado: según la base de conocimiento, cualquier descuento lo aprueba Miguel Torres.
VOLUME_DISCOUNT_TIERS = {
    "es": ["Hasta 2.000 pedidos/mes", "De 2.001 a 10.000 pedidos/mes", "Más de 10.000 pedidos/mes"],
    "en": ["Up to 2,000 orders/month", "2,001 to 10,000 orders/month", "Over 10,000 orders/month"],
}
DISCOUNT_APPROVER = "Miguel Torres"


def numbers_in_rate_card() -> set[str]:
    """Cifras que un generador puede citar sin que cuenten como inventadas."""
    values = {ON_TIME_SLA_PERCENT, SLA_COMPENSATION_PERCENT, PEAK_EXTENSION_PERCENT, STORAGE_GRACE_DAYS, LONG_TERM_DAYS,
              LONG_TERM_DAYS + 1, LONG_TERM_SURCHARGE_PERCENT, PREFERENTIAL_RATE_M3, MIN_RETURNS_PROCESSING_HOURS,
              RETURN_WINDOW_DAYS, *STORAGE_RATE.values(), 3, 5, 24, 7, 12, 2000, 2001, 10000}
    return {str(v) for v in values}
