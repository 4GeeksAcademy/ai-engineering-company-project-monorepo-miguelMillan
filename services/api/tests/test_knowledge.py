"""Endpoint POST /knowledge/query: solo transporte, delega en query() del pipeline RAG."""


def test_knowledge_query_returns_only_generated_answer(authenticated_client, monkeypatch):
    calls = []

    def fake_query(question):
        calls.append(question)
        return "La ventana de devolución estándar es de 30 días desde la entrega.\nFuente: [1] returns-policy"

    monkeypatch.setattr("routes.knowledge.knowledge_query", fake_query)
    response = authenticated_client.post("/knowledge/query", json={"question": "  ¿Cuál es la ventana de devolución estándar?  "})

    assert response.status_code == 200
    assert response.json() == {"answer": "La ventana de devolución estándar es de 30 días desde la entrega.\nFuente: [1] returns-policy"}
    assert calls == ["¿Cuál es la ventana de devolución estándar?"]


def test_knowledge_query_never_exposes_chunks_or_scores(authenticated_client, monkeypatch):
    monkeypatch.setattr("routes.knowledge.knowledge_query", lambda question: "Respuesta generada.")
    body = authenticated_client.post("/knowledge/query", json={"question": "¿Qué transportista cubre Aragón rural?"}).json()

    assert set(body) == {"answer"}
    for forbidden in ("score", "chunks", "payload", "source_document", "context"):
        assert forbidden not in body


def test_knowledge_query_requires_authentication(api_client, monkeypatch):
    monkeypatch.setattr("routes.knowledge.knowledge_query", lambda question: "no debería llamarse")
    response = api_client.post("/knowledge/query", json={"question": "¿Cuál es la tarifa estándar?"})

    assert response.status_code == 401


def test_knowledge_query_validates_question(authenticated_client, monkeypatch):
    monkeypatch.setattr("routes.knowledge.knowledge_query", lambda question: "no debería llamarse")

    assert authenticated_client.post("/knowledge/query", json={"question": "  "}).status_code == 422
    assert authenticated_client.post("/knowledge/query", json={}).status_code == 422
    assert authenticated_client.post("/knowledge/query", json={"question": "x" * 1001}).status_code == 422
    assert authenticated_client.post("/knowledge/query", json={"question": "¿SLA?", "k": 10}).status_code == 422


def test_knowledge_query_maps_unavailable_pipeline_to_503(authenticated_client, monkeypatch):
    def missing_config(question):
        raise RuntimeError("Configura LLM_API_KEY")

    monkeypatch.setattr("routes.knowledge.knowledge_query", missing_config)
    response = authenticated_client.post("/knowledge/query", json={"question": "¿Cuál es el SLA estándar?"})

    assert response.status_code == 503
    assert "LLM_API_KEY" not in response.text


def test_knowledge_query_maps_upstream_failure_to_502(authenticated_client, monkeypatch):
    def qdrant_down(question):
        raise ConnectionError("qdrant:6333 refused")

    monkeypatch.setattr("routes.knowledge.knowledge_query", qdrant_down)
    response = authenticated_client.post("/knowledge/query", json={"question": "¿Cuál es el SLA estándar?"})

    assert response.status_code == 502
    assert "qdrant" not in response.text
