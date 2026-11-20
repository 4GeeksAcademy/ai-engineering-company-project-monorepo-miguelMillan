"""Endpoint del agente LangGraph de TrackFlow: POST /agent/query (RAG + tools MCP de incidencias e inventario).

Convive con ``POST /knowledge/query`` (RAG directo). No contiene lógica de
negocio: solo invoca el grafo compilado de ``services/agent/graph.py`` y
traduce su resultado a HTTP. El ``run_id`` de cada corrida viaja en la
cabecera ``X-Agent-Run-Id`` para poder consultar su trace y sus checkpoints
(``uv run python -m services.agent.inspect <run_id>``).
"""

import logging
import sys
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from security import get_current_user

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from services.agent.graph import (  # noqa: E402
    AgentGraphStructureError,
    AgentRunError,
    KnowledgeAgent,
    build_agent_graph,
    compile_agent_graph,
)

logger = logging.getLogger("trackflow.agent")
router = APIRouter(tags=["agent"])

# Compilación al arrancar: un error estructural del grafo impide que el API
# arranque, en lugar de aparecer en la primera petición de producción.
compile_agent_graph(build_agent_graph())


@lru_cache(maxsize=1)
def get_agent() -> KnowledgeAgent:
    """Grafo compilado con checkpointer y trace store persistentes (uno por proceso)."""
    return KnowledgeAgent()


class AgentQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Solo límite de transporte; la validez de la pregunta la decide el grafo.
    question: str = Field(max_length=4000)


class AgentAnswer(BaseModel):
    answer: str


@router.post("/agent/query", response_model=AgentAnswer)
def ask_agent(payload: AgentQuestion, response: Response, _user=Depends(get_current_user)) -> AgentAnswer:
    # Las tools del agente van por el servidor MCP de TrackFlow con la identidad OAuth del propio
    # agente (cliente trackflow-agent, scopes de solo lectura). El JWT de quien pregunta solo
    # autoriza esta petición: no se reenvía (su audiencia es el API, no el servidor MCP).
    try:
        result = get_agent().run(payload.question)
    except AgentRunError as error:
        logger.error("agent run %s failed at node %s: %r", error.run_id, error.node, error.cause)
        headers = {"X-Agent-Run-Id": error.run_id}
        if isinstance(error.cause, RuntimeError) and not isinstance(error.cause, AgentGraphStructureError):
            raise HTTPException(status_code=503, detail="El agente de conocimiento no está disponible en este momento.",
                                headers=headers) from error
        raise HTTPException(status_code=502, detail="El agente no pudo completar la respuesta. Inténtalo de nuevo en unos minutos.",
                            headers=headers) from error
    except Exception as error:  # fallo antes de crear la corrida (p. ej. almacenamiento de traces)
        logger.exception("agent unavailable")
        raise HTTPException(status_code=503, detail="El agente de conocimiento no está disponible en este momento.") from error

    response.headers["X-Agent-Run-Id"] = result.run_id
    if result.error:
        raise HTTPException(status_code=422, detail=result.answer, headers={"X-Agent-Run-Id": result.run_id})
    return AgentAnswer(answer=result.answer)
