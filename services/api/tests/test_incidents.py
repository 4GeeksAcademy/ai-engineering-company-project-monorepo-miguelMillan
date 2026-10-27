import uuid


def incident_payload(**changes):
    return {
        "title": "Paquete retrasado",
        "description": "El paquete no se ha entregado dentro del plazo esperado.",
        "category": "lost_parcel",
        "origin": "branch",
        "branch": "la_warehouse",
        **changes,
    }


def create_incident(client, **changes):
    return client.post("/api/incidents", json=incident_payload(**changes))


def test_incident_lifecycle_filters_summary_and_allowed_transition(authenticated_client):
    first = create_incident(authenticated_client)
    second = create_incident(
        authenticated_client,
        title="Incidencia del carrier",
        category="carrier_issue",
        branch="zaragoza_warehouse",
    )

    assert first.status_code == 201
    assert second.status_code == 201
    first_id = first.json()["id"]
    first_path = f"/api/incidents/{first_id}"
    assert authenticated_client.get(first_path).json()["title"] == "Paquete retrasado"
    assert len(authenticated_client.get("/api/incidents").json()) == 2
    assert len(authenticated_client.get("/api/incidents?category=lost_parcel&branch=la_warehouse").json()) == 1
    assert authenticated_client.get("/api/incidents?category=other").json() == []

    summary = authenticated_client.get("/api/incidents/summary").json()
    assert summary["total"] == 2
    assert summary["by_category"]["lost_parcel"] == 1
    assert summary["by_status"]["open"] == 2

    changed = authenticated_client.patch(f"{first_path}/status", json={"status": "in_progress"})
    assert changed.status_code == 200
    assert authenticated_client.get(first_path).json()["status"] == "in_progress"


def test_incident_empty_list_and_summary_are_valid_boundaries(authenticated_client):
    assert authenticated_client.get("/api/incidents").json() == []
    summary = authenticated_client.get("/api/incidents/summary").json()
    assert summary["total"] == 0
    assert summary["by_status"]["open"] == 0
    assert summary["by_category"]["lost_parcel"] == 0


def test_incident_rejects_invalid_payload_uuid_transition_and_missing_resource(authenticated_client):
    assert create_incident(authenticated_client, title="   ").status_code == 400
    assert create_incident(authenticated_client, category="unknown").status_code == 400
    assert authenticated_client.get("/api/incidents/not-a-uuid").status_code == 400

    unknown_path = f"/api/incidents/{uuid.uuid4()}"
    assert authenticated_client.get(unknown_path).status_code == 404
    assert authenticated_client.patch(f"{unknown_path}/status", json={"status": "in_progress"}).status_code == 404

    created = create_incident(authenticated_client)
    incident_path = f"/api/incidents/{created.json()['id']}"
    assert authenticated_client.patch(f"{incident_path}/status", json={"status": "resolved"}).status_code == 400
    assert authenticated_client.patch(f"{incident_path}/status", json={"status": "open", "extra": True}).status_code == 400


def test_incident_endpoints_require_authentication(api_client):
    assert api_client.get("/api/incidents").status_code == 401
    assert api_client.post("/api/incidents", json=incident_payload()).status_code == 401