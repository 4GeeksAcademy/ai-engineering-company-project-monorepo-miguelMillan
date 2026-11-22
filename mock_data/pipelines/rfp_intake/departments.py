"""Departamentos de TrackFlow que participan en una propuesta (BRIEFING del Hito 9, §2.1).

Los identificadores son EXACTAMENTE los del BRIEFING: ``warehouse``, ``lastmile`` y ``reverse``. No hay otros:
un servicio que la RFP pida y que ninguno cubra (p. ej. integración ERP, aduanas) no crea un departamento
nuevo, se reporta a Ventas como ``out_of_scope_requests``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CURRENCY_BY_COUNTRY = {"US": "USD", "ES": "EUR"}  # BRIEFING §5: USD para EE. UU., EUR para España


@dataclass(frozen=True)
class Department:
    department_id: str
    name: str
    owner: str
    contributes: str                       # qué aporta a la propuesta (BRIEFING §2.1)
    asks: tuple[str, ...]                  # qué le pide Ventas (synthesizer)
    service: str                           # valor de ``services_requested`` que lo activa
    keywords: re.Pattern = field(compare=False)


DEPARTMENTS: dict[str, Department] = {
    "warehouse": Department(
        "warehouse", "Warehouse Operations", "Ana Whitfield",
        "Capacidad de almacenamiento, costo por pallet/SKU, tiempo de onboarding",
        ("Capacidad de almacenamiento disponible para el volumen/SKU del cliente",
         "Costo por pallet / por SKU",
         "Tiempo de onboarding hasta operar"),
        "warehousing",
        re.compile(r"\b(almacen\w*|warehous\w*|storage|fulfil+ment|sku|skus|pallets?|inventario|inventory|stock|restock|"
                   r"centro (logistico|de distribucion)|integracion|erp|api|onboarding|up and running|garage)\b", re.I)),
    "lastmile": Department(
        "lastmile", "Last Mile and Carrier Management", "Carlos Vega",
        "Costo por envío, transportistas disponibles según destino, SLA de entrega",
        ("Costo por envío según destino y volumen",
         "Transportistas disponibles para los destinos del cliente",
         "SLA de entrega a tiempo (%) comprometido"),
        "last_mile",
        re.compile(r"\b(ultima milla|last mile|shipping|ship|envios?|reparto|entregas?|delivery|carriers?|transportistas?|"
                   r"paqueteria|orders? a month|pedidos?( al|/)? mes|across the us|destinos?)\b", re.I)),
    "reverse": Department(
        "reverse", "Reverse Logistics", "Sofía Ramos",
        "Costo y tiempo de procesamiento de devoluciones (si el cliente lo solicita)",
        ("Costo de procesamiento por devolución",
         "Tiempo de procesamiento de devoluciones (nunca menos de 48 h, BRIEFING §5)",
         "Proceso de inspección / reacondicionamiento / restock"),
        "reverse_logistics",
        re.compile(r"\b(devoluci\w*|returns?|reverse logistics|logistica inversa|reacondicion\w*|restock\w*|inspecci\w*|"
                   r"refurbish\w*|rma)\b", re.I)),
}
DEPARTMENT_BY_SERVICE = {d.service: d.department_id for d in DEPARTMENTS.values()}
