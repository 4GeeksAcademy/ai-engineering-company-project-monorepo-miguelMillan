"""Agente generador de Reverse Logistics (Sofía Ramos): costo y tiempo de procesamiento de devoluciones (§2.1)."""

from __future__ import annotations

from mock_data.pipelines.rfp_intake import rate_card as rc
from mock_data.pipelines.rfp_intake.generation import ConceptRow, DepartmentGenerator, SectionInput, pending


class ReverseGenerator(DepartmentGenerator):
    department_id = "reverse"
    scope = (
        "- Devoluciones: proceso de inspección y clasificación (reventa, reacondicionamiento o descarte), costo y tiempo de procesamiento.\n"
        "- El procesamiento de devoluciones NUNCA es inferior a 48 horas: no prometas plazos más cortos.\n"
        "- No hables de almacenamiento ni de transporte: los redactan Warehouse Operations y Last Mile."
    )

    def knowledge(self, inp: SectionInput) -> list[str]:
        return [f"ventana de devolución de {rc.RETURN_WINDOW_DAYS} días desde la entrega, salvo contrato",
                "el envío de vuelta lo paga la marca, salvo error de TrackFlow",
                f"procesamiento nunca en menos de {rc.MIN_RETURNS_PROCESSING_HOURS} horas",
                "devoluciones internacionales: gestión manual del equipo de Sofía Ramos"]

    def concept_rows(self, inp: SectionInput) -> list[ConceptRow]:
        es, owner = inp.language == "es", inp.owner
        return [
            ("Costo por devolución procesada" if es else "Cost per processed return", pending(inp.language, owner),
             "Inspección y clasificación: reventa, reacondicionamiento o descarte." if es else
             "Inspection and grading: resale, refurbishment or disposal."),
            ("Tiempo de procesamiento" if es else "Processing time",
             f"Nunca inferior a {rc.MIN_RETURNS_PROCESSING_HOURS} horas" if es else f"Never below {rc.MIN_RETURNS_PROCESSING_HOURS} hours",
             f"Plazo exacto a confirmar por {owner}." if es else f"Exact turnaround to be confirmed by {owner}."),
            ("Envío de vuelta" if es else "Return shipping", "A cargo de la marca" if es else "Paid by the brand",
             "Salvo error de TrackFlow (daño en tránsito o entrega equivocada)." if es else
             "Unless TrackFlow is at fault (transit damage or wrong delivery)."),
            ("Ventana de devolución" if es else "Return window",
             f"{rc.RETURN_WINDOW_DAYS} días desde la entrega" if es else f"{rc.RETURN_WINDOW_DAYS} days from delivery",
             "Salvo otra ventana en el contrato." if es else "Unless the contract sets another window."),
        ]

    def extra_service_lines(self, inp: SectionInput) -> list[str]:
        return [f"Procesamiento de devoluciones: nunca en menos de {rc.MIN_RETURNS_PROCESSING_HOURS} horas."
                if inp.language == "es" else
                f"Returns processing: never in less than {rc.MIN_RETURNS_PROCESSING_HOURS} hours."]
