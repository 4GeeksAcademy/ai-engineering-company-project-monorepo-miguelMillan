"""Agente generador de Last Mile and Carrier Management (Carlos Vega): costo por envío, transportistas por
destino y SLA de entrega (§2.1)."""

from __future__ import annotations

from mock_data.pipelines.rfp_intake import rate_card as rc
from mock_data.pipelines.rfp_intake.generation import ConceptRow, DepartmentGenerator, SectionInput, pending


class LastMileGenerator(DepartmentGenerator):
    department_id = "lastmile"
    scope = (
        "- Última milla: cómo se reparten los pedidos del cliente, qué transportistas cubren sus destinos y el SLA de entrega.\n"
        "- El costo por envío es el costo final al cliente y aún está pendiente de cotizar: nunca des tarifas de un transportista.\n"
        "- No hables de almacenamiento ni de devoluciones: los redactan Warehouse Operations y Reverse Logistics."
    )

    def knowledge(self, inp: SectionInput) -> list[str]:
        return [f"transportistas: {rc.CARRIERS.get(inp.client.get('country') or '', 'según destino')} "
                "(el sistema elige según destino, peso y urgencia)",
                f"tiempos: {rc.DELIVERY_TIMES[inp.language]}",
                f"SLA contractual: {rc.ON_TIME_SLA_PERCENT} % de entregas a tiempo al mes por país"]

    def concept_rows(self, inp: SectionInput) -> list[ConceptRow]:
        es, volume = inp.language == "es", inp.metadata.get("monthly_volume")
        return [
            ("Costo por envío" if es else "Cost per shipment",
             pending(inp.language, inp.owner, " (costo final al cliente)" if es else " (final cost to the client)"),
             (f"Según destino, peso y urgencia; volumen de {volume} pedidos/mes." if es else
              f"By destination, weight and urgency; volume of {volume} orders/month.") if volume else
             ("Según destino, peso y urgencia." if es else "By destination, weight and urgency.")),
            ("Transportistas disponibles" if es else "Available carriers",
             rc.CARRIERS.get(inp.client.get("country") or "", "Según destino" if es else "By destination"),
             "El sistema elige el transportista según destino, peso y urgencia." if es else
             "The system picks the carrier by destination, weight and urgency."),
            ("Tiempos de entrega" if es else "Delivery times", rc.DELIVERY_TIMES[inp.language], "Mismo país." if es else "Same country."),
        ]
