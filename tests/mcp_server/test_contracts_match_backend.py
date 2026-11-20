"""Los contratos del servidor MCP usan los MISMOS campos, IDs y valores de dominio que las APIs ya construidas.

Se comparan con los modelos reales de ``services/api`` (``models.py`` y
``schemas.py``). Si el backend cambia un campo o un valor, este test falla en
lugar de dejar que el servidor MCP diverja en silencio.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import get_args

import pytest

from mcps.trackflow_mcp import contracts

API_DIR = Path(__file__).resolve().parents[2] / "services" / "api"


@pytest.fixture(scope="module")
def backend_models():
    sys.path.insert(0, str(API_DIR))
    try:
        import models
        import schemas
    finally:
        sys.path.remove(str(API_DIR))
    return models, schemas


def fields(model) -> set[str]:
    return set(model.model_fields)


def test_incident_domain_values_match(backend_models):
    models, _ = backend_models
    assert list(get_args(contracts.IncidentStatus)) == [s.value for s in models.IncidentStatus]
    assert get_args(contracts.IncidentCategory) == get_args(models.IncidentCategory)
    assert get_args(contracts.IncidentOrigin) == get_args(models.IncidentOrigin)
    assert get_args(contracts.IncidentBranch) == get_args(models.IncidentBranch)


def test_incident_payloads_match(backend_models):
    models, _ = backend_models
    assert fields(contracts.IncidentSummary) == fields(models.IncidentListItemResponse)
    assert fields(contracts.IncidentDetail) == fields(models.IncidentDetailResponse)
    assert fields(contracts.IncidentCreated) == fields(models.IncidentCreatedResponse)
    assert fields(contracts.IncidentStatusChanged) - {"previous_status"} == fields(models.IncidentStatusResponse)
    # Entrada de creación: exactamente IncidentCreate
    from mcps.trackflow_mcp.server import create_mcp
    from mcps.trackflow_mcp.backend import TrackflowBackend
    import asyncio

    tools = {t.name: t for t in asyncio.run(create_mcp(TrackflowBackend.__new__(TrackflowBackend)).list_tools(run_middleware=False))}
    assert set(tools["incidents_create_ticket"].parameters["properties"]) == fields(models.IncidentCreate)
    assert set(tools["incidents_update_ticket_status"].parameters["properties"]) == {"incident_id"} | fields(models.IncidentStatusUpdate)


def test_inventory_payloads_and_values_match(backend_models):
    _, schemas = backend_models
    assert fields(contracts.Product) - {"below_minimum"} == fields(schemas.SKUResponse)
    assert get_args(contracts.Warehouse) == get_args(schemas.Warehouse)
    assert get_args(contracts.ProductCategory) == get_args(schemas.Category)


def test_status_transitions_match_the_incidents_manager_lifecycle():
    """``TRANSITIONS`` de services/api/routes/incidents.py (leído con ast: no hace falta tinydb aquí)."""
    tree = ast.parse((API_DIR / "routes" / "incidents.py").read_text(encoding="utf-8"))
    node = next(n.value for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "TRANSITIONS")
    backend = {key.attr: sorted(elt.attr for elt in (value.elts if isinstance(value, ast.Set) else []))
               for key, value in zip(node.keys, node.values)}
    assert {k: sorted(v) for k, v in contracts.STATUS_TRANSITIONS.items()} == backend
