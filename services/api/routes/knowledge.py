"""Endpoint del asistente comercial de TrackFlow (RAG, Hito 7).

El router es solo transporte HTTP: valida la pregunta, llama a ``query()`` de
``mock_data/pipelines/rag.py`` (el ``data/pipelines/`` del enunciado) y
devuelve únicamente ``{"answer": ...}``. No hay recuperación ni generación
aquí; nunca se devuelven chunks, payloads de Qdrant ni puntuaciones (se
registran solo en el log del servidor desde ``retrieve()``).
"""

import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from rate_limit import limited_user
from security import is_staff

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from mock_data.pipelines.rag import SYSTEM_PROMPT, query as knowledge_query  # noqa: E402
from services.agent.guardrails import (  # noqa: E402
    SAFE_FALLBACK,
    SessionContext,
    check_input,
    check_output,
    log_event,
)


logger = logging.getLogger("trackflow.knowledge")
router = APIRouter(tags=["knowledge"])


class KnowledgeQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=3, max_length=1000)


class KnowledgeAnswer(BaseModel):
    answer: str


@router.post("/knowledge/query", response_model=KnowledgeAnswer)
def ask_knowledge_base(payload: KnowledgeQuestion, user=Depends(limited_user)) -> KnowledgeAnswer:
    """Responde con el texto generado por el modelo a partir de la base de conocimiento.

    Mismas guardas de entrada y de salida que el agente: esta ruta llamaba al modelo sin ninguna."""
    session = SessionContext(user=str(user.email), staff=is_staff(user))
    decision = check_input(payload.question, session)
    if decision.response:
        log_event({"route": "/knowledge/query", "guard": decision.guard, "code": decision.code, "action": decision.action})
        return KnowledgeAnswer(answer=decision.response)
    question = decision.rewritten_question or payload.question
    try:
        answer = knowledge_query(question)
    except RuntimeError as error:
        # Configuración ausente del LLM de generación o respuesta vacía del modelo.
        logger.error("knowledge_query no disponible: %s", error)
        raise HTTPException(status_code=503, detail="El asistente de conocimiento no está disponible en este momento.") from error
    except Exception as error:
        logger.exception("knowledge_query falló")
        raise HTTPException(status_code=502, detail="No se pudo generar la respuesta. Inténtalo de nuevo en unos minutos.") from error
    check = check_output(answer, session, SYSTEM_PROMPT)
    if not check.ok:
        log_event({"route": "/knowledge/query", "guard": "output_validation", "code": ",".join(check.codes), "action": "replace"})
        answer = SAFE_FALLBACK
    return KnowledgeAnswer(answer=answer)
