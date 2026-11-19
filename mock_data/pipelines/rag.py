"""Fase 2 del RAG de TrackFlow: recuperación en Qdrant y generación de la respuesta.

``query()`` es la ÚNICA función que deben llamar consumidores externos (API,
UI). Es literalmente ``generate_answer(question, retrieve(question, ...))``;
el futuro agente LangGraph podrá llamar a ``retrieve()`` y ``generate_answer()``
como pasos separados sin repetir la recuperación.

Variables de entorno adicionales a las de ``mock_data/process/rag.py``:

* ``RAG_MIN_SCORE`` umbral mínimo de similitud coseno (por defecto 0.45, calibrado).
* ``RAG_TOP_K``     vecinos que se piden a Qdrant en ``query()`` (por defecto 5).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from mock_data.process.rag import (
    COLLECTION_NAME,
    COMPANY,
    _openai_client,
    embed,
    load_generation_settings,
    qdrant_client,
)

logger = logging.getLogger("trackflow.rag")

DEFAULT_TOP_K = 5
# Umbral calibrado con scripts/evaluate_rag_retrieval.py sobre
# mock_data/eval/test-queries.json y el modelo de embeddings por defecto
# (paraphrase-multilingual-MiniLM-L12-v2): es el valor más alto que conserva
# todos los aciertos de Recall@3 (82 %); con 0.50 bajaría al 76 %, por debajo
# del KPI del 80 %. Si se cambia EMBEDDING_MODEL hay que recalibrarlo.
DEFAULT_MIN_SCORE = 0.45

PAYLOAD_FIELDS = ("company", "source_document", "section", "language", "chunk_index", "text")

NO_CONTEXT_ANSWER = (
    "No tengo información suficiente en la base de conocimiento de TrackFlow para responder "
    "a esa pregunta con datos exactos. Para no comprometer condiciones que no están en los "
    "acuerdos estándar, consúltalo con el área responsable antes de responder al cliente."
)

SYSTEM_PROMPT = """Eres el asistente comercial interno de TrackFlow, empresa de logística de última milla y \
almacenaje con operación en Estados Unidos (Los Ángeles) y España (Zaragoza).

Audiencia: account managers y equipo de business development de Miguel Torres (Director Comercial). \
Usan tus respuestas en llamadas con marcas prospecto y clientes actuales. Responde como lo haría un \
vendedor de TrackFlow en esa llamada: en español, claro, breve y con datos exactos.

Reglas obligatorias:
1. Usa SOLO la información del CONTEXTO. Si el contexto no responde la pregunta, dilo explícitamente \
("no tengo información suficiente en la base de conocimiento") y no completes con conocimiento general.
2. Copia porcentajes, tarifas, monedas y plazos exactamente como aparecen en el contexto; no los \
redondees, conviertas ni estimes.
3. Nunca prometas condiciones fuera de los acuerdos estándar. Cualquier descuento de almacenamiento \
requiere aprobación de Miguel Torres, y cualquier excepción de transportista requiere aprobación de \
Carlos Vega (Head of Carrier Operations): dilo siempre en lugar de ofrecerlos.
4. Nunca garantices un SLA de entrega en fechas de alta demanda declaradas (Black Friday, Navidad, \
Rebajas de enero en España): indica que los tiempos pueden extenderse según el documento y que debe \
comunicarse al cliente con anticipación.
5. Las devoluciones internacionales nunca son "automáticas": remítelas siempre a gestión manual del \
equipo de Sofía Ramos (Gerente de Devoluciones).
6. Termina con una línea "Fuente:" que cite el documento y la sección de cada fragmento usado, con el \
formato [n] source_document — section."""


def retrieve(
    query: str, *, k: int = DEFAULT_TOP_K, min_score: float, client=None, embed_fn=None
) -> list[dict[str, Any]]:
    """Payloads de los k chunks más cercanos a ``query`` con score ≥ ``min_score``.

    Puede devolver menos de k elementos, o ninguno: el umbral nunca se relaja
    para "rellenar" el contexto. Devuelve diccionarios con los campos del
    payload (``source_document``, ``section``, ``text``…) más ``score``; nunca
    objetos del SDK de Qdrant.

    ``client`` y ``embed_fn`` solo se inyectan en pruebas y evaluación; por
    defecto se usan el cliente configurado y la misma ``embed()`` del indexado.
    """
    if not query or not query.strip():
        return []
    if k < 1:
        raise ValueError("k debe ser al menos 1.")
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    client = client or qdrant_client()
    response = client.query_points(
        collection_name=COLLECTION_NAME,
        query=(embed_fn or embed)(query),
        limit=k,
        with_payload=True,
        query_filter=Filter(must=[FieldCondition(key="company", match=MatchValue(value=COMPANY))]),
    )
    results = []
    for point in response.points:
        if point.score is None or point.score < min_score:
            continue
        payload = point.payload or {}
        item = {field: payload.get(field) for field in PAYLOAD_FIELDS}
        item["score"] = float(point.score)
        results.append(item)
    # Registro solo en servidor (para depurar el umbral); nunca se devuelve al cliente.
    logger.info(
        "retrieve k=%s min_score=%.3f kept=%s scores=%s",
        k, min_score, len(results), [round(float(p.score), 3) for p in response.points],
    )
    return results


def build_context(context: list[dict[str, Any]]) -> str:
    """Contexto numerado y trazable a su documento/sección de origen."""
    return "\n\n".join(
        f"[{n}] source_document: {chunk['source_document']} — section: {chunk['section']}\n{chunk['text']}"
        for n, chunk in enumerate(context, start=1)
    )


def generate_answer(question: str, context: list[dict[str, Any]]) -> str:
    """Arma el prompt y llama al LLM de GENERACIÓN (``GENERATION_MODEL``).

    Sin contexto por encima del umbral no se llama al LLM: se devuelve una
    respuesta honesta fija. Así es imposible que el modelo invente un SLA,
    una tarifa o una política de TrackFlow cuando no hay fuente.
    """
    if not context:
        return NO_CONTEXT_ANSWER
    settings = load_generation_settings()
    completion = _openai_client(settings.api_url, settings.api_key).chat.completions.create(
        model=settings.model,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"CONTEXTO:\n{build_context(context)}\n\nPREGUNTA DEL ACCOUNT MANAGER:\n{question.strip()}",
            },
        ],
    )
    answer = (completion.choices[0].message.content or "").strip()
    if not answer:
        raise RuntimeError(f"El modelo {settings.model} devolvió una respuesta vacía.")
    return answer


def _min_score() -> float:
    return float(os.environ.get("RAG_MIN_SCORE", DEFAULT_MIN_SCORE))


def _top_k() -> int:
    return int(os.environ.get("RAG_TOP_K", DEFAULT_TOP_K))


def query(question: str) -> str:
    """Única entrada pública: recupera contexto y genera la respuesta final."""
    return generate_answer(question, retrieve(question, k=_top_k(), min_score=_min_score()))
