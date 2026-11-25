"""Fase 1 del RAG de TrackFlow: chunking, embeddings e indexación en Qdrant.

Corpus: ``docs/company-knowledge-base/`` (copiado de
``00-general-contexts/trackflow/`` según CONTEXT.md, sección 2):
``trackflow-sla-delivery.es.md``, ``trackflow-returns-policy.es.md``,
``trackflow-carrier-coverage.es.md`` y ``trackflow-storage-pricing.es.md``.

Colección Qdrant: ``trackflow_knowledge`` (nombre exacto del CONTEXT.md).

Configuración (variables de entorno o ``.env``; nunca se hardcodean claves):

Embeddings — solo ``embed()``, para indexar y para consultar:

* ``EMBEDDING_PROVIDER`` ``fastembed`` (por defecto: modelo local ONNX en CPU,
  sin clave ni coste) u ``openai`` (cualquier API OpenAI-compatible).
* ``EMBEDDING_MODEL``    por defecto
  ``sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`` (384 dim).
* ``EMBEDDING_API_URL`` / ``EMBEDDING_API_KEY`` solo con ``openai`` (si faltan
  se usan ``LLM_API_URL`` / ``LLM_API_KEY``).
* ``EMBEDDING_CACHE_DIR`` caché del modelo local (por defecto ``.cache/fastembed``).

Generación — solo ``generate_answer()``:

* ``LLM_API_URL``      URL base de una API OpenAI-compatible de chat
                       (OpenAI, OpenRouter, Ollama, LiteLLM...).
* ``LLM_API_KEY``      clave de esa API.
* ``GENERATION_MODEL`` ID del modelo de chat. Debe ser distinto de
  ``EMBEDDING_MODEL``: nunca se reutiliza el modelo de generación para embeddings.

Qdrant: ``QDRANT_URL`` (por defecto ``http://localhost:6333``) y
``QDRANT_API_KEY`` opcional (Qdrant Cloud).

Uso:

    uv run python -m mock_data.process.rag      # (re)indexa la base de conocimiento
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[2]
from mock_data.process.model_budget import BUDGET

KNOWLEDGE_BASE_DIR = ROOT / "docs" / "company-knowledge-base"
COLLECTION_NAME = "trackflow_knowledge"
COMPANY = "trackflow"
SOURCE_DOCUMENTS = ("sla-delivery", "returns-policy", "carrier-coverage", "storage-pricing")
DEFAULT_EMBEDDING_PROVIDER = "fastembed"
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_PROVIDERS = ("fastembed", "openai")
DEFAULT_QDRANT_URL = "http://localhost:6333"
# Espacio de nombres fijo para IDs deterministas (uuid5) de cada chunk.
CHUNK_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://trackflow.example/knowledge")

_FILENAME = re.compile(r"^trackflow-(?P<source>[a-z-]+)\.(?P<language>[a-z]{2})\.md$")
_BULLET = re.compile(r"^-\s+")
_NUMBERED = re.compile(r"^\d+\.\s+")
_SECTION_LABEL = re.compile(r"^(?:-\s+|\d+\.\s+)?(?P<label>[^:.]{3,60}):\s")


# --------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class EmbeddingSettings:
    provider: str
    model: str
    api_url: str | None
    api_key: str | None
    cache_dir: str


@dataclass(frozen=True)
class GenerationSettings:
    api_url: str
    api_key: str
    model: str


def _load_dotenv() -> None:
    """Carga ``.env`` de la raíz si existe, sin pisar variables ya definidas."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dependencia declarada en pyproject
        return
    load_dotenv(ROOT / ".env", override=False)


def load_embedding_settings() -> EmbeddingSettings:
    """Configuración del modelo de EMBEDDINGS (independiente del de generación)."""
    _load_dotenv()
    provider = os.environ.get("EMBEDDING_PROVIDER", DEFAULT_EMBEDDING_PROVIDER).strip().lower()
    if provider not in EMBEDDING_PROVIDERS:
        raise RuntimeError(f"EMBEDDING_PROVIDER debe ser uno de {EMBEDDING_PROVIDERS}.")
    model = os.environ.get("EMBEDDING_MODEL", "").strip() or (DEFAULT_EMBEDDING_MODEL if provider == "fastembed" else "")
    api_url = (os.environ.get("EMBEDDING_API_URL") or os.environ.get("LLM_API_URL") or "").rstrip("/") or None
    api_key = os.environ.get("EMBEDDING_API_KEY") or os.environ.get("LLM_API_KEY") or None
    if provider == "openai":
        missing = [name for name, value in (("EMBEDDING_MODEL", model), ("EMBEDDING_API_URL o LLM_API_URL", api_url),
                                            ("EMBEDDING_API_KEY o LLM_API_KEY", api_key)) if not value]
        if missing:
            raise RuntimeError(f"Con EMBEDDING_PROVIDER=openai configura {', '.join(missing)}.")
    cache_dir = os.environ.get("EMBEDDING_CACHE_DIR") or str(ROOT / ".cache" / "fastembed")
    return EmbeddingSettings(provider=provider, model=model, api_url=api_url, api_key=api_key, cache_dir=cache_dir)


def load_generation_settings() -> GenerationSettings:
    """Configuración del LLM de GENERACIÓN; falla si reutiliza el modelo de embeddings."""
    _load_dotenv()
    settings = GenerationSettings(
        api_url=os.environ.get("LLM_API_URL", "").strip().rstrip("/"),
        api_key=os.environ.get("LLM_API_KEY", "").strip(),
        model=os.environ.get("GENERATION_MODEL", "").strip(),
    )
    missing = [name for name, value in (("LLM_API_URL", settings.api_url), ("LLM_API_KEY", settings.api_key),
                                        ("GENERATION_MODEL", settings.model)) if not value]
    if missing:
        raise RuntimeError(f"Configura {', '.join(missing)} (LLM de generación) en el entorno o en .env.")
    if settings.model == load_embedding_settings().model:
        raise RuntimeError(
            "EMBEDDING_MODEL y GENERATION_MODEL deben ser modelos distintos: "
            "nunca se reutiliza el modelo de generación para embeddings."
        )
    return settings


def qdrant_client():
    from qdrant_client import QdrantClient

    _load_dotenv()
    return QdrantClient(
        url=os.environ.get("QDRANT_URL", DEFAULT_QDRANT_URL),
        api_key=os.environ.get("QDRANT_API_KEY") or None,
    )


# --------------------------------------------------------------------------
# Parseo y chunking semántico
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Chunk:
    source_document: str
    section: str
    language: str
    chunk_index: int
    text: str

    @property
    def point_id(self) -> str:
        """ID determinista: el mismo chunk siempre produce el mismo punto."""
        return str(uuid.uuid5(CHUNK_ID_NAMESPACE, f"{COMPANY}/{self.source_document}/{self.language}/{self.chunk_index}"))

    def payload(self) -> dict[str, Any]:
        return {
            "company": COMPANY,
            "source_document": self.source_document,
            "section": self.section,
            "language": self.language,
            "chunk_index": self.chunk_index,
            "text": self.text,
        }


def _unwrap(lines: Iterable[str]) -> str:
    """Une líneas cortadas por el ancho del Markdown fuente en una sola frase."""
    return re.sub(r"\s+", " ", " ".join(line.strip() for line in lines)).strip()


def _split_items(lines: list[str]) -> list[str]:
    """Agrupa líneas de lista: cada ítem incluye sus líneas de continuación."""
    items: list[list[str]] = []
    for line in lines:
        if _BULLET.match(line) or _NUMBERED.match(line) or not items:
            items.append([line])
        else:
            items[-1].append(line)
    return [_unwrap(item) for item in items]


def _section_label(text: str) -> str:
    match = _SECTION_LABEL.match(text)
    if match:
        return match.group("label").strip()
    first_sentence = re.split(r"(?<=[.;])\s", re.sub(r"^(?:-\s+|\d+\.\s+)", "", text), maxsplit=1)[0]
    return first_sentence if len(first_sentence) <= 70 else first_sentence[:67].rstrip(" ,") + "…"


def chunk_markdown(markdown: str, *, source_document: str, language: str) -> list[Chunk]:
    """Divide un documento de política en unidades semánticas autocontenidas.

    Reglas (los documentos fuente no tienen subtítulos, solo ``# Título``,
    párrafos y listas):

    * Un párrafo independiente es un chunk (p. ej. la regla de compensación
      del 5 % o la advertencia de alta demanda): nunca se parte por tamaño.
    * Un párrafo que termina en ``:`` es una *introducción* de la lista que le
      sigue; se antepone a cada chunk de esa lista para que no pierda su
      condición (p. ej. "según volumen ocupado, no según número de unidades").
    * Lista con viñetas ``-``: un chunk por viñeta (cada tarifa o tipo de
      envío es una regla completa).
    * Lista numerada: UN chunk con todos los pasos (es un procedimiento; separar
      los pasos rompería el orden).
    * Bloque "encabezado + viñetas" (p. ej. "España (Zaragoza): MRW, SEUR…"
      seguido de sus transportistas): un chunk por bloque, para comparar
      transportistas del mismo país en un único contexto.

    Cada chunk empieza con el título del documento para que sea comprensible
    fuera de contexto y su ``section`` es "Título > etiqueta".
    """
    lines = markdown.splitlines()
    title = next((line[2:].strip() for line in lines if line.startswith("# ")), source_document)
    body = [line for line in lines if not line.startswith("# ")]
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in body:
        if line.strip():
            current.append(line.rstrip())
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)

    units: list[tuple[str, str]] = []  # (etiqueta de sección, texto)
    lead: str | None = None
    for block in blocks:
        first_item = next((i for i, line in enumerate(block) if _BULLET.match(line) or _NUMBERED.match(line)), None)
        if first_item is None:
            paragraph = _unwrap(block)
            if paragraph.endswith(":"):
                lead = paragraph  # introduce la lista o grupos siguientes
                continue
            units.append((_section_label(paragraph), paragraph))
            lead = None
            continue

        header = _unwrap(block[:first_item]) if first_item > 0 else ""
        items = _split_items(block[first_item:])
        prefix = " ".join(part for part in (lead, header) if part)
        if header:
            # Grupo con encabezado propio: el grupo completo es la unidad.
            units.append((_section_label(header), "\n".join([prefix, *items])))
        elif _NUMBERED.match(block[first_item]):
            units.append((_section_label(lead or items[0]), "\n".join([p for p in (prefix,) if p] + items)))
            lead = None
        else:
            for item in items:
                units.append((_section_label(item), f"{prefix}\n{item}" if prefix else item))
            lead = None

    return [
        Chunk(
            source_document=source_document,
            section=f"{title} > {label}",
            language=language,
            chunk_index=index,
            text=f"{title}\n{text}",
        )
        for index, (label, text) in enumerate(units)
    ]


def load_chunks(knowledge_base_dir: Path = KNOWLEDGE_BASE_DIR) -> list[Chunk]:
    """Lee y trocea todos los documentos de la base de conocimiento."""
    paths = sorted(knowledge_base_dir.glob("trackflow-*.md"))
    chunks: list[Chunk] = []
    found: set[str] = set()
    for path in paths:
        match = _FILENAME.match(path.name)
        if not match:
            continue
        source, language = match.group("source"), match.group("language")
        if source not in SOURCE_DOCUMENTS:
            raise ValueError(f"Documento fuera del corpus del CONTEXT.md: {path.name}")
        found.add(source)
        chunks.extend(chunk_markdown(path.read_text(encoding="utf-8"), source_document=source, language=language))
    missing = set(SOURCE_DOCUMENTS) - found
    if missing:
        raise FileNotFoundError(f"Faltan documentos fuente en {knowledge_base_dir}: {sorted(missing)}")
    for source in SOURCE_DOCUMENTS:
        count = sum(chunk.source_document == source for chunk in chunks)
        if count < 3:
            raise ValueError(f"{source} produjo {count} chunks; el CONTEXT.md exige al menos 3.")
    return chunks


# --------------------------------------------------------------------------
# Embeddings
# --------------------------------------------------------------------------
def normalize_text(text: str) -> str:
    """Preprocesado idéntico al indexar y al consultar: espacios colapsados."""
    return re.sub(r"\s+", " ", text).strip()


def _openai_client(api_url: str, api_key: str):
    """Único punto donde se crea un cliente de modelo: cobra la llamada (presupuesto y parada de emergencia)."""
    from openai import OpenAI

    BUDGET.charge()
    return OpenAI(base_url=api_url, api_key=api_key, timeout=30.0, max_retries=2)


@lru_cache(maxsize=2)
def _local_embedding_model(model: str, cache_dir: str):
    """Carga (una vez por proceso) el modelo ONNX local de fastembed."""
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=model, cache_dir=cache_dir)


def embed(text: str) -> list[float]:
    """Vector de ``text`` con el modelo de EMBEDDINGS dedicado (``EMBEDDING_MODEL``).

    Es la única vía de embedding: la usan ``setup()`` para cada chunk y
    ``retrieve()`` para la pregunta, con el mismo modelo y el mismo
    preprocesado (``normalize_text``). Nunca usa el modelo de generación.
    """
    cleaned = normalize_text(text)
    if not cleaned:
        raise ValueError("No se puede generar el embedding de un texto vacío.")
    settings = load_embedding_settings()
    if settings.provider == "fastembed":
        vector = next(iter(_local_embedding_model(settings.model, settings.cache_dir).embed([cleaned]))).tolist()
    else:
        response = _openai_client(settings.api_url, settings.api_key).embeddings.create(model=settings.model, input=cleaned)
        vector = list(response.data[0].embedding)
    if not vector:
        raise RuntimeError(f"El modelo {settings.model} devolvió un embedding vacío.")
    return [float(value) for value in vector]


# --------------------------------------------------------------------------
# Indexación
# --------------------------------------------------------------------------
def setup(
    knowledge_base_dir: Path = KNOWLEDGE_BASE_DIR,
    *,
    client=None,
    embed_fn: Callable[[str], list[float]] = embed,
) -> dict[str, Any]:
    """(Re)crea ``trackflow_knowledge`` e indexa todo el corpus.

    Idempotencia — estrategia **limpiar y recargar** con IDs deterministas:
    la colección se elimina y se recrea en cada ejecución (el corpus son 4
    documentos breves, recargar cuesta segundos), de modo que nunca quedan
    puntos duplicados ni chunks obsoletos de una versión anterior de un
    documento. Además cada punto usa un ``uuid5`` derivado de
    ``company/source_document/language/chunk_index``, así que los IDs son
    estables entre ejecuciones y trazables.
    """
    from qdrant_client.models import Distance, PointStruct, VectorParams

    chunks = load_chunks(knowledge_base_dir)
    vectors = [embed_fn(chunk.text) for chunk in chunks]
    dimension = len(vectors[0])
    if any(len(vector) != dimension for vector in vectors):
        raise RuntimeError("El modelo de embeddings devolvió vectores de distinta dimensión.")

    client = client or qdrant_client()
    if client.collection_exists(COLLECTION_NAME):
        client.delete_collection(COLLECTION_NAME)
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
    )
    client.upsert(
        collection_name=COLLECTION_NAME,
        points=[
            PointStruct(id=chunk.point_id, vector=vector, payload=chunk.payload())
            for chunk, vector in zip(chunks, vectors)
        ],
        wait=True,
    )
    per_document = {source: sum(c.source_document == source for c in chunks) for source in SOURCE_DOCUMENTS}
    return {
        "collection": COLLECTION_NAME,
        "points": client.count(COLLECTION_NAME, exact=True).count,
        "chunks_per_document": per_document,
        "vector_size": dimension,
        "distance": "cosine",
    }


def main() -> int:
    summary = setup()
    print(
        f"Colección {summary['collection']}: {summary['points']} puntos "
        f"(dimensión {summary['vector_size']}, {summary['distance']}) — {summary['chunks_per_document']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
