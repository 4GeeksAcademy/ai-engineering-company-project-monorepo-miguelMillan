"""Evaluación de recuperación del RAG de TrackFlow: Recall@3 y calibración de min_score.

Requiere la colección ``trackflow_knowledge`` indexada (``uv run python -m
mock_data.process.rag``) y usa la MISMA ``embed()`` y ``retrieve()`` que
producción. Ejecutar desde la raíz:

    uv run python scripts/evaluate_rag_retrieval.py

Entrada:  ``mock_data/eval/test-queries.json`` (el ``data/eval/`` del CONTEXT).
Salida:   ``mock_data/eval/rag-retrieval-eval.json``.

Métricas
--------
* **Recall@3 sin umbral**: el chunk esperado está entre los 3 primeros por
  similitud coseno. Mide la calidad del ranking (embeddings + chunking).
* **Recall@3 con umbral**: además su score ≥ ``min_score``; es lo que de verdad
  llega al prompt en producción. KPI del CONTEXT: ≥ 80 %.
* **Rechazo fuera de alcance**: proporción de preguntas ``out_of_scope`` cuyo
  mejor resultado queda por debajo de ``min_score`` (→ "no hay información").

Calibración
-----------
Se recorren umbrales de 0,00 a 1,00. El umbral recomendado es el MÁS ALTO que
conserva todos los aciertos de Recall@3 sin umbral (no se pierde ningún chunk
correcto). Así se rechaza todo el ruido posible sin coste en recall; el
rechazo de preguntas fuera de alcance que logra se reporta tal cual.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mock_data.pipelines.rag import retrieve  # noqa: E402
from mock_data.process.rag import COLLECTION_NAME, load_embedding_settings  # noqa: E402

QUERIES_PATH = ROOT / "mock_data" / "eval" / "test-queries.json"
OUTPUT_PATH = ROOT / "mock_data" / "eval" / "rag-retrieval-eval.json"
RANKING_DEPTH = 5  # se piden 5 para ver la posición aunque caiga fuera del top-3


def load_test_queries(path: Path = QUERIES_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if len(data["in_scope"]) < 8:
        raise ValueError("test-queries.json debe tener al menos 8 preguntas in_scope.")
    return data


def rank_of_expected(results: list[dict[str, Any]], expected: dict[str, Any]) -> tuple[int | None, float | None]:
    """Posición (1-based) y score del chunk esperado dentro de ``results``."""
    for position, item in enumerate(results, start=1):
        if item["source_document"] == expected["source_document"] and item["chunk_index"] == expected["chunk_index"]:
            return position, item["score"]
    return None, None


def recall_at_k(evaluations: list[dict[str, Any]], *, k: int = 3, min_score: float | None = None) -> float:
    """Proporción de preguntas cuyo chunk esperado está en el top-k (y ≥ min_score si se indica)."""
    hits = 0
    for item in evaluations:
        rank, score = item["rank"], item["expected_score"]
        if rank is not None and rank <= k and (min_score is None or score >= min_score):
            hits += 1
    return hits / len(evaluations) if evaluations else 0.0


def threshold_sweep(in_scope: list[dict[str, Any]], out_of_scope: list[dict[str, Any]], *, k: int = 3) -> list[dict[str, float]]:
    rows = []
    for step in range(0, 101):
        threshold = step / 100
        rejected = sum(1 for item in out_of_scope if item["top_score"] is None or item["top_score"] < threshold)
        rows.append({
            "min_score": threshold,
            "recall_at_k": recall_at_k(in_scope, k=k, min_score=threshold),
            "out_of_scope_rejection": rejected / len(out_of_scope) if out_of_scope else 1.0,
        })
    return rows


def recommend_threshold(in_scope: list[dict[str, Any]], *, k: int = 3) -> float:
    """Umbral más alto (a 2 decimales, redondeando hacia abajo) que no pierde ningún acierto del top-k."""
    hit_scores = [item["expected_score"] for item in in_scope if item["rank"] is not None and item["rank"] <= k]
    if not hit_scores:
        return 0.0
    return math.floor(min(hit_scores) * 100) / 100


def evaluate(
    queries: dict[str, Any],
    *,
    retrieve_fn: Callable[..., list[dict[str, Any]]] = retrieve,
    k: int = 3,
) -> dict[str, Any]:
    in_scope = []
    for case in queries["in_scope"]:
        results = retrieve_fn(case["question"], k=RANKING_DEPTH, min_score=-1.0)
        rank, score = rank_of_expected(results, case["expected"])
        in_scope.append({
            "id": case["id"],
            "question": case["question"],
            "expected": f"{case['expected']['source_document']}#{case['expected']['chunk_index']}",
            "rank": rank,
            "expected_score": score,
            "hit_at_k": rank is not None and rank <= k,
            f"top_{k}": [
                {"chunk": f"{r['source_document']}#{r['chunk_index']}", "section": r["section"], "score": round(r["score"], 4)}
                for r in results[:k]
            ],
        })
    out_of_scope = []
    for case in queries.get("out_of_scope", []):
        results = retrieve_fn(case["question"], k=1, min_score=-1.0)
        out_of_scope.append({
            "id": case["id"],
            "question": case["question"],
            "top_score": results[0]["score"] if results else None,
            "top_chunk": f"{results[0]['source_document']}#{results[0]['chunk_index']}" if results else None,
        })

    sweep = threshold_sweep(in_scope, out_of_scope, k=k)
    recommended = recommend_threshold(in_scope, k=k)
    at_recommended = next(row for row in sweep if math.isclose(row["min_score"], recommended))
    coverage: dict[str, int] = {}
    for case in queries["in_scope"]:
        coverage[case["expected"]["source_document"]] = coverage.get(case["expected"]["source_document"], 0) + 1
    return {
        "collection": COLLECTION_NAME,
        "k": k,
        "questions_in_scope": len(in_scope),
        "questions_out_of_scope": len(out_of_scope),
        "coverage_by_document": coverage,
        "recall_at_k_without_threshold": recall_at_k(in_scope, k=k),
        "recommended_min_score": recommended,
        "recall_at_k_with_recommended_min_score": at_recommended["recall_at_k"],
        "out_of_scope_rejection_with_recommended_min_score": at_recommended["out_of_scope_rejection"],
        "kpi_recall_threshold": queries.get("recall_threshold", 0.8),
        "in_scope": in_scope,
        "out_of_scope": out_of_scope,
        "threshold_sweep": sweep,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--queries", type=Path, default=QUERIES_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()

    settings = load_embedding_settings()
    report = evaluate(load_test_queries(args.queries))
    report["embedding_provider"] = settings.provider
    report["embedding_model"] = settings.model
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Modelo de embeddings: {settings.provider} / {settings.model}")
    print(f"Recall@{report['k']} sin umbral: {report['recall_at_k_without_threshold']:.0%} "
          f"({report['questions_in_scope']} preguntas, cobertura {report['coverage_by_document']})")
    print(f"min_score recomendado: {report['recommended_min_score']:.2f} → Recall@{report['k']} "
          f"{report['recall_at_k_with_recommended_min_score']:.0%} | rechazo fuera de alcance "
          f"{report['out_of_scope_rejection_with_recommended_min_score']:.0%}")
    for item in report["in_scope"]:
        mark = "OK " if item["hit_at_k"] else "MISS"
        score = f"{item['expected_score']:.3f}" if item["expected_score"] is not None else "  -  "
        print(f"  {mark} {item['id']:11} rank={item['rank']} score={score} top1={item['top_3'][0]['chunk']}")
    for item in report["out_of_scope"]:
        print(f"  OOS  {item['id']:11} top_score={item['top_score']:.3f} ({item['top_chunk']})")
    passed = report["recall_at_k_with_recommended_min_score"] >= report["kpi_recall_threshold"]
    print("KPI Recall@3 ≥ 80 %:", "CUMPLE" if passed else "NO CUMPLE")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
