"""Agente generador de Warehouse Operations (Ana Whitfield): capacidad, costo por pallet/SKU y onboarding (§2.1)."""

from __future__ import annotations

from mock_data.pipelines.rfp_intake import rate_card as rc
from mock_data.pipelines.rfp_intake.generation import ConceptRow, DepartmentGenerator, SectionInput, pending


class WarehouseGenerator(DepartmentGenerator):
    department_id = "warehouse"
    scope = (
        "- Almacenamiento: capacidad para el catálogo y el volumen del cliente, y tiempo de onboarding.\n"
        "- TrackFlow factura el almacenamiento por m³ ocupado al mes, NO por pallet ni por SKU: no digas «X por pallet» ni «X por SKU».\n"
        "- No hables de devoluciones, transporte ni última milla: los redactan Reverse Logistics y Last Mile."
    )

    def knowledge(self, inp: SectionInput) -> list[str]:
        rate = rc.STORAGE_RATE.get(inp.currency or "")
        return [f"almacenamiento {rate} {inp.currency} por m³ al mes (por volumen ocupado, no por unidad)" if rate else
                "tarifa de almacenamiento pendiente: moneda sin confirmar",
                f"primeros {rc.STORAGE_GRACE_DAYS} días sin cargo; desde el día {rc.LONG_TERM_DAYS + 1} sin movimiento, "
                f"{rc.LONG_TERM_SURCHARGE_PERCENT} % de la tarifa"]

    def concept_rows(self, inp: SectionInput) -> list[ConceptRow]:
        es, cur, m, owner = inp.language == "es", inp.currency or "—", inp.metadata, inp.owner
        rate, sku = rc.STORAGE_RATE.get(cur), m.get("sku_count")
        deadline = m.get("deadline") or m.get("deadline_text")
        volume_note = ((f"Volumen de la RFP: {m['monthly_volume']} pedidos/mes." if es else
                        f"RFP volume: {m['monthly_volume']} orders/month.") if m.get("monthly_volume") else
                       ("Volumen mensual no indicado en la RFP." if es else "Monthly volume not stated in the RFP."))
        deadline_note = ((f"Fecha límite de la RFP: {deadline}." if es else f"RFP deadline: {deadline}.") if deadline else
                         ("Sin fecha límite en la RFP." if es else "No deadline in the RFP."))
        return [
            ("Almacenamiento" if es else "Storage",
             (f"{rate} {cur} por m³ al mes" if es else f"{rate} {cur} per m³ per month") if rate else pending(inp.language, owner),
             f"Por volumen ocupado, no por unidad. Primeros {rc.STORAGE_GRACE_DAYS} días sin cargo." if es else
             f"Billed by occupied volume, not per unit. First {rc.STORAGE_GRACE_DAYS} days free of charge."),
            ("Inventario de larga duración" if es else "Long-term inventory",
             f"{rc.LONG_TERM_SURCHARGE_PERCENT} % de la tarifa estándar" if es else f"{rc.LONG_TERM_SURCHARGE_PERCENT} % of the standard rate",
             f"Desde el día {rc.LONG_TERM_DAYS + 1} sin movimiento." if es else f"From day {rc.LONG_TERM_DAYS + 1} without movement."),
            ("Costo por pallet / SKU" if es else "Cost per pallet / SKU",
             "Sin tarifa por unidad: se factura por m³" if es else "No per-unit rate: billed per m³",
             (f"Catálogo de {sku} SKU: {owner} estima los m³ a partir del surtido." if es else
              f"Catalog of {sku} SKUs: {owner} estimates the m³ from the assortment.") if sku else
             ("El cliente no indicó el número de SKU." if es else "The client did not state the SKU count.")),
            ("Capacidad y onboarding" if es else "Capacity and onboarding",
             f"Pendiente de confirmar por {owner}" if es else f"To be confirmed by {owner}",
             f"{volume_note} {deadline_note}"),
        ]
