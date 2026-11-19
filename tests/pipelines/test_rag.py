"""Pruebas unitarias del RAG de TrackFlow (sin Qdrant en vivo ni LLM real).

* ``retrieve()``: cliente Qdrant simulado (stub) y ``embed`` simulado.
* ``query()``: ``retrieve()`` y el LLM de generación simulados.
* ``setup()``: Qdrant en memoria del propio SDK (``QdrantClient(":memory:")``).
"""

from types import SimpleNamespace

import pytest

import mock_data.pipelines.rag as pipeline
import mock_data.process.rag as process

CHUNKS = [
    {"company": "trackflow", "source_document": "returns-policy", "section": "Política de Devoluciones > Ventana de devolución estándar",
     "language": "es", "chunk_index": 1, "text": "Política de Devoluciones\nVentana de devolución estándar: 30 días desde la entrega."},
    {"company": "trackflow", "source_document": "carrier-coverage", "section": "Cobertura de Transportistas > España (Zaragoza)",
     "language": "es", "chunk_index": 1, "text": "Cobertura de Transportistas\n- SEUR: mejor cobertura en zonas rurales de Aragón."},
    {"company": "trackflow", "source_document": "sla-delivery", "section": "SLA de Entrega > Envío estándar",
     "language": "es", "chunk_index": 0, "text": "SLA de Entrega\n- Envío estándar: 3 a 5 días hábiles dentro del mismo país."},
    {"company": "trackflow", "source_document": "storage-pricing", "section": "Tarifas de Almacenamiento > Tarifa estándar",
     "language": "es", "chunk_index": 0, "text": "Tarifas de Almacenamiento\n- Tarifa estándar: 16 EUR por metro cúbico al mes (Zaragoza)."},
]


class StubQdrant:
    """Imita ``QdrantClient.query_points``: devuelve hasta ``limit`` puntos ordenados por score."""

    def __init__(self, scored_payloads):
        self.scored_payloads = scored_payloads
        self.calls = []

    def query_points(self, *, collection_name, query, limit, with_payload, query_filter):
        self.calls.append({"collection_name": collection_name, "query": query, "limit": limit, "query_filter": query_filter})
        ranked = sorted(self.scored_payloads, key=lambda item: item[0], reverse=True)[:limit]
        return SimpleNamespace(points=[SimpleNamespace(id=f"p{i}", score=score, payload=dict(payload), vector=None)
                                       for i, (score, payload) in enumerate(ranked)])


@pytest.fixture
def fake_embed(monkeypatch):
    calls = []

    def embed(text):
        calls.append(text)
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(pipeline, "embed", embed)
    return calls


@pytest.fixture
def llm_env(monkeypatch):
    monkeypatch.setenv("LLM_API_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fastembed")
    monkeypatch.setenv("EMBEDDING_MODEL", "test-embedding-model")
    monkeypatch.setenv("GENERATION_MODEL", "test-generation-model")
    monkeypatch.setattr(process, "_load_dotenv", lambda: None)


class FakeLLM:
    """Cliente OpenAI-compatible simulado: registra la llamada de chat y devuelve ``reply``."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))])


# --------------------------------------------------------------------------
# retrieve()
# --------------------------------------------------------------------------
def test_retrieve_excludes_results_below_min_score(fake_embed):
    client = StubQdrant([(0.82, CHUNKS[0]), (0.61, CHUNKS[2]), (0.49, CHUNKS[1]), (0.12, CHUNKS[3])])

    results = pipeline.retrieve("¿Cuál es la ventana de devolución estándar?", k=5, min_score=0.5, client=client)

    assert [r["source_document"] for r in results] == ["returns-policy", "sla-delivery"]
    assert all(r["score"] >= 0.5 for r in results)
    assert "carrier-coverage" not in {r["source_document"] for r in results}  # 0.49 < 0.5 queda fuera


def test_retrieve_can_return_fewer_than_k(fake_embed):
    client = StubQdrant([(0.9, CHUNKS[0]), (0.3, CHUNKS[1]), (0.2, CHUNKS[2]), (0.1, CHUNKS[3])])

    results = pipeline.retrieve("ventana de devolución", k=3, min_score=0.5, client=client)

    assert client.calls[0]["limit"] == 3  # se piden k vecinos…
    assert len(results) == 1  # …pero el umbral no se relaja para rellenar


def test_retrieve_returns_nothing_when_no_chunk_passes_threshold(fake_embed):
    client = StubQdrant([(0.31, CHUNKS[0]), (0.2, CHUNKS[1])])

    assert pipeline.retrieve("receta de paella", k=5, min_score=0.5, client=client) == []


def test_retrieve_returns_plain_payload_dicts_from_trackflow_collection(fake_embed):
    client = StubQdrant([(0.75, CHUNKS[1])])

    results = pipeline.retrieve("¿Qué transportista cubre mejor Aragón rural?", k=5, min_score=0.5, client=client)

    assert fake_embed == ["¿Qué transportista cubre mejor Aragón rural?"]  # misma embed() que al indexar
    assert client.calls[0]["collection_name"] == "trackflow_knowledge"
    condition = client.calls[0]["query_filter"].must[0]
    assert (condition.key, condition.match.value) == ("company", "trackflow")
    assert type(results[0]) is dict
    assert set(results[0]) == {"company", "source_document", "section", "language", "chunk_index", "text", "score"}
    assert results[0]["section"] == "Cobertura de Transportistas > España (Zaragoza)"


def test_retrieve_rejects_invalid_k_and_skips_blank_query(fake_embed):
    client = StubQdrant([(0.9, CHUNKS[0])])
    with pytest.raises(ValueError):
        pipeline.retrieve("SLA", k=0, min_score=0.5, client=client)
    assert pipeline.retrieve("   ", k=5, min_score=0.5, client=client) == []
    assert fake_embed == []


# --------------------------------------------------------------------------
# query() / generate_answer()
# --------------------------------------------------------------------------
def test_query_returns_generation_model_output(monkeypatch, llm_env):
    model_answer = "La ventana estándar es de 30 días desde la entrega.\nFuente: [1] returns-policy — Ventana de devolución estándar"
    llm = FakeLLM(model_answer)
    retrieve_calls = []
    monkeypatch.setattr(pipeline, "retrieve", lambda q, *, k, min_score: retrieve_calls.append((q, k, min_score)) or [CHUNKS[0]])
    monkeypatch.setattr(pipeline, "_openai_client", lambda url, key: llm)

    answer = pipeline.query("¿Cuál es la ventana de devolución estándar?")

    assert answer == model_answer
    assert retrieve_calls == [("¿Cuál es la ventana de devolución estándar?", 5, pipeline.DEFAULT_MIN_SCORE)]
    call = llm.calls[0]
    assert call["model"] == "test-generation-model"  # LLM de generación, no el de embeddings
    assert call["temperature"] == 0
    prompt = call["messages"][1]["content"]
    assert CHUNKS[0]["text"] in prompt and "source_document: returns-policy" in prompt
    assert "Miguel Torres" in call["messages"][0]["content"]  # voz/audiencia del CONTEXT.md


def test_query_never_returns_raw_chunk_text(monkeypatch, llm_env):
    llm = FakeLLM("SEUR es el transportista con mejor cobertura en zonas rurales de Aragón.")
    monkeypatch.setattr(pipeline, "retrieve", lambda q, *, k, min_score: [CHUNKS[1]])
    monkeypatch.setattr(pipeline, "_openai_client", lambda url, key: llm)

    answer = pipeline.query("¿Qué transportista cubre mejor Aragón rural?")

    assert len(llm.calls) == 1  # siempre pasa por generación
    assert answer != CHUNKS[1]["text"] and CHUNKS[1]["text"] not in answer


def test_query_fails_instead_of_falling_back_to_chunks_when_model_is_empty(monkeypatch, llm_env):
    monkeypatch.setattr(pipeline, "retrieve", lambda q, *, k, min_score: [CHUNKS[3]])
    monkeypatch.setattr(pipeline, "_openai_client", lambda url, key: FakeLLM("   "))

    with pytest.raises(RuntimeError, match="respuesta vacía"):
        pipeline.query("¿Cuánto cuesta el almacenamiento en Zaragoza?")


def test_query_without_context_answers_honestly_without_calling_llm(monkeypatch, llm_env):
    llm = FakeLLM("no debería llamarse")
    monkeypatch.setattr(pipeline, "retrieve", lambda q, *, k, min_score: [])
    monkeypatch.setattr(pipeline, "_openai_client", lambda url, key: llm)

    answer = pipeline.query("¿Cuál es la capital de Australia?")

    assert answer == pipeline.NO_CONTEXT_ANSWER
    assert "información suficiente" in answer
    assert llm.calls == []


def test_query_respects_configured_threshold_and_k(monkeypatch, llm_env):
    monkeypatch.setenv("RAG_MIN_SCORE", "0.62")
    monkeypatch.setenv("RAG_TOP_K", "3")
    seen = []
    monkeypatch.setattr(pipeline, "retrieve", lambda q, *, k, min_score: seen.append((k, min_score)) or [])

    pipeline.query("¿SLA en Black Friday?")

    assert seen == [(3, 0.62)]


def test_embedding_and_generation_models_must_differ(monkeypatch, llm_env):
    monkeypatch.setenv("GENERATION_MODEL", "test-embedding-model")
    with pytest.raises(RuntimeError, match="distintos"):
        process.load_generation_settings()


def test_local_embeddings_need_no_api_key_but_generation_does(monkeypatch):
    monkeypatch.setattr(process, "_load_dotenv", lambda: None)
    for name in ("EMBEDDING_PROVIDER", "EMBEDDING_MODEL", "LLM_API_URL", "LLM_API_KEY", "GENERATION_MODEL"):
        monkeypatch.delenv(name, raising=False)

    embedding = process.load_embedding_settings()
    assert (embedding.provider, embedding.model) == ("fastembed", process.DEFAULT_EMBEDDING_MODEL)
    with pytest.raises(RuntimeError, match="LLM_API_URL, LLM_API_KEY, GENERATION_MODEL"):
        process.load_generation_settings()


def test_remote_embedding_provider_requires_its_own_configuration(monkeypatch):
    monkeypatch.setattr(process, "_load_dotenv", lambda: None)
    for name in ("EMBEDDING_MODEL", "EMBEDDING_API_URL", "EMBEDDING_API_KEY", "LLM_API_URL", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    with pytest.raises(RuntimeError, match="EMBEDDING_MODEL"):
        process.load_embedding_settings()


def test_embed_uses_same_local_model_and_normalization(monkeypatch):
    monkeypatch.setattr(process, "_load_dotenv", lambda: None)
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    seen = []

    class FakeModel:
        def embed(self, texts):
            seen.extend(texts)
            return iter([SimpleNamespace(tolist=lambda: [0.5, 0.25])])

    monkeypatch.setattr(process, "_local_embedding_model", lambda model, cache_dir: seen.append(model) or FakeModel())
    assert process.embed("  ¿SLA   en\nBlack Friday? ") == [0.5, 0.25]
    assert seen == [process.DEFAULT_EMBEDDING_MODEL, "¿SLA en Black Friday?"]
    with pytest.raises(ValueError):
        process.embed("   ")


# --------------------------------------------------------------------------
# setup(): chunking + indexación idempotente (Qdrant en memoria)
# --------------------------------------------------------------------------
def deterministic_embed(text):
    total = sum(ord(ch) for ch in text)
    return [float(len(text) % 7 + 1), float(total % 11 + 1), float(text.count(" ") % 5 + 1)]


def test_setup_indexes_every_document_with_context_payload_and_is_idempotent():
    from qdrant_client import QdrantClient

    client = QdrantClient(":memory:")
    first = process.setup(client=client, embed_fn=deterministic_embed)
    ids_first = sorted(str(p.id) for p in client.scroll("trackflow_knowledge", limit=100)[0])
    second = process.setup(client=client, embed_fn=deterministic_embed)
    points = client.scroll("trackflow_knowledge", limit=100, with_payload=True)[0]

    assert first["collection"] == "trackflow_knowledge"
    assert second["points"] == first["points"] == len(points)  # sin duplicados al re-ejecutar
    assert sorted(str(p.id) for p in points) == ids_first  # IDs deterministas
    assert all(count >= 3 for count in first["chunks_per_document"].values())
    assert set(first["chunks_per_document"]) == {"sla-delivery", "returns-policy", "carrier-coverage", "storage-pricing"}
    for point in points:
        assert set(point.payload) == {"company", "source_document", "section", "language", "chunk_index", "text"}
        assert point.payload["company"] == "trackflow" and point.payload["language"] == "es"


def test_chunks_keep_business_rules_whole():
    chunks = {(c.source_document, c.section): c.text for c in process.load_chunks()}
    texts = list(chunks.values())

    # La compensación del 5 % no se separa de su condición de dos meses consecutivos.
    assert any("5%" in t and "dos meses consecutivos" in t for t in texts)
    # La advertencia de alta demanda va entera (fechas + 40 % adicional).
    assert any("Black Friday" in t and "40%" in t and "Rebajas de enero" in t for t in texts)
    # El descuento de almacenamiento conserva la aprobación de Miguel Torres.
    assert any("tarifa preferencial" in t and "Miguel Torres" in t for t in texts)
    # Devoluciones internacionales: gestión manual del equipo de Sofía Ramos.
    assert any("internacionales" in t and "gestión manual" in t and "Sofía Ramos" in t for t in texts)
    # Cada viñeta de tarifas conserva la condición "según volumen ocupado".
    storage = [t for (doc, _), t in chunks.items() if doc == "storage-pricing" and "- " in t]
    assert storage and all("volumen ocupado" in t for t in storage)
