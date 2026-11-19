"""Pruebas del set de evaluación (test-queries.json) y del cálculo de Recall@3 / umbral."""

from scripts.evaluate_rag_retrieval import (
    evaluate,
    load_test_queries,
    recall_at_k,
    recommend_threshold,
)
from mock_data.process.rag import SOURCE_DOCUMENTS, load_chunks


def test_test_queries_cover_every_source_document_and_peak_demand():
    queries = load_test_queries()
    in_scope = queries["in_scope"]

    assert queries["collection"] == "trackflow_knowledge"
    assert len(in_scope) >= 8
    assert {case["expected"]["source_document"] for case in in_scope} == set(SOURCE_DOCUMENTS)
    assert any("Black Friday" in case["question"] or "Rebajas" in case["question"] for case in in_scope)
    assert len({case["id"] for case in in_scope + queries["out_of_scope"]}) == len(in_scope) + len(queries["out_of_scope"])
    assert queries["out_of_scope"], "hacen falta preguntas fuera de alcance para calibrar min_score"


def test_every_expected_chunk_exists_with_its_section():
    chunks = {(c.source_document, c.chunk_index): c.section for c in load_chunks()}
    for case in load_test_queries()["in_scope"]:
        key = (case["expected"]["source_document"], case["expected"]["chunk_index"])
        assert key in chunks, f"{case['id']}: el chunk esperado {key} no existe (¿cambió el chunking?)"
        assert chunks[key] == case["expected"]["section"], case["id"]


def test_recall_at_k_counts_rank_and_optional_threshold():
    evaluations = [
        {"rank": 1, "expected_score": 0.9},
        {"rank": 3, "expected_score": 0.5},
        {"rank": 4, "expected_score": 0.8},  # fuera del top-3
        {"rank": None, "expected_score": None},
    ]
    assert recall_at_k(evaluations, k=3) == 0.5
    assert recall_at_k(evaluations, k=3, min_score=0.6) == 0.25


def test_recommended_threshold_keeps_every_top_k_hit():
    evaluations = [{"rank": 1, "expected_score": 0.734}, {"rank": 2, "expected_score": 0.458}, {"rank": 5, "expected_score": 0.2}]
    threshold = recommend_threshold(evaluations, k=3)

    assert threshold == 0.45
    assert recall_at_k(evaluations, k=3, min_score=threshold) == recall_at_k(evaluations, k=3)


def test_evaluate_with_stub_retrieve_reports_recall_and_rejection():
    queries = {
        "in_scope": [
            {"id": "a", "question": "ventana", "expected": {"source_document": "returns-policy", "chunk_index": 1, "section": "s"}},
            {"id": "b", "question": "aragón", "expected": {"source_document": "carrier-coverage", "chunk_index": 1, "section": "s"}},
        ],
        "out_of_scope": [{"id": "x", "question": "paella"}],
    }
    ranked = {
        "ventana": [("returns-policy", 1, 0.8), ("sla-delivery", 0, 0.4)],
        "aragón": [("sla-delivery", 1, 0.5), ("storage-pricing", 0, 0.45), ("carrier-coverage", 2, 0.44), ("carrier-coverage", 1, 0.43)],
        "paella": [("carrier-coverage", 1, 0.2)],
    }

    def stub_retrieve(question, *, k, min_score):
        return [{"source_document": d, "chunk_index": i, "section": f"{d}#{i}", "score": s} for d, i, s in ranked[question][:k]]

    report = evaluate(queries, retrieve_fn=stub_retrieve)

    assert report["recall_at_k_without_threshold"] == 0.5  # 'aragón' quedó en la posición 4
    assert report["recommended_min_score"] == 0.8
    assert report["out_of_scope_rejection_with_recommended_min_score"] == 1.0
    assert report["coverage_by_document"] == {"returns-policy": 1, "carrier-coverage": 1}
