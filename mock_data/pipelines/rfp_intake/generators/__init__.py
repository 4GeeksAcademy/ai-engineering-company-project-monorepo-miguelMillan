"""Un agente generador por departamento del BRIEFING (§2.1), cada uno en su módulo."""

from __future__ import annotations

from mock_data.pipelines.rfp_intake.generation import DepartmentGenerator
from mock_data.pipelines.rfp_intake.generators.lastmile import LastMileGenerator
from mock_data.pipelines.rfp_intake.generators.reverse import ReverseGenerator
from mock_data.pipelines.rfp_intake.generators.warehouse import WarehouseGenerator

GENERATORS: dict[str, DepartmentGenerator] = {
    "warehouse": WarehouseGenerator(),
    "lastmile": LastMileGenerator(),
    "reverse": ReverseGenerator(),
}


def generator_for(department_id: str) -> DepartmentGenerator:
    try:
        return GENERATORS[department_id]
    except KeyError as error:
        raise ValueError(f"No hay agente generador para el departamento {department_id!r}") from error
