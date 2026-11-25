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

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from rate_limit import limited_user
from security import is_staff, require_staff

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from services.agent.action_log import action_log  # noqa: E402
from services.agent.guardrails import SessionContext  # noqa: E402
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
    # Conversación: permite que el siguiente mensaje responda a la propuesta de memoria pendiente.
    conversation_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


class MemoryProposalOut(BaseModel):
    """Propuesta de memoria pendiente: NO está guardada hasta que un usuario la confirme."""

    id: str
    tipo: str
    accion: str
    hecho: str
    motivo: str
    carrier: str | None = None
    pais: str | None = None
    zona: str | None = None
    ruta: str | None = None
    cliente_b2b: str | None = None


class MemoryResolutionOut(BaseModel):
    """Cómo se resolvió en este turno la propuesta pendiente de la conversación."""

    proposal_id: str
    label: str
    outcome: str


class AgentAnswer(BaseModel):
    answer: str
    memory_proposal: MemoryProposalOut | None = None
    memory_resolution: MemoryResolutionOut | None = None


@router.post("/agent/query", response_model=AgentAnswer, response_model_exclude_none=True)
def ask_agent(payload: AgentQuestion, request: Request, response: Response, user=Depends(limited_user)) -> AgentAnswer:
    # Las tools del agente van por el servidor MCP de TrackFlow con la identidad OAuth del propio
    # agente (cliente trackflow-agent, scopes de solo lectura). El JWT de quien pregunta solo
    # autoriza esta petición: no se reenvía (su audiencia es el API, no el servidor MCP).
    try:
        session = f"{getattr(user, 'id', 'u')}:{payload.conversation_id}" if payload.conversation_id else None
        result = get_agent().run(payload.question, session_id=session, user=_user_label(user),
                                 session=build_session_context(request, user))
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
    proposal = MemoryProposalOut.model_validate(result.memory_proposal) if result.memory_proposal else None
    resolution = MemoryResolutionOut.model_validate(result.memory_resolution) if result.memory_resolution else None
    return AgentAnswer(answer=result.answer, memory_proposal=proposal, memory_resolution=resolution)


# ----------------------------------------------------------------------------
# Memoria del agente: el usuario confirma o rechaza lo que el agente propuso.
# Confirmar es la ÚNICA escritura en memoria (vuelve a pasar los guardrails).
# ----------------------------------------------------------------------------
def _memory_store():
    store = get_agent().memory_store
    if store is None:
        raise HTTPException(status_code=503, detail="La memoria del agente no está habilitada.")
    return store


def _user_label(user) -> str:
    return str(getattr(user, "email", None) or getattr(user, "id", "usuario"))


@router.post("/agent/memory/proposals/{proposal_id}/confirm")
def confirm_memory_proposal(proposal_id: str, user=Depends(require_staff)):
    from services.agent.memory import MemoryWriteRejected

    store = _memory_store()
    try:
        entry = store.confirm(proposal_id, approved_by=_user_label(user))
    except KeyError as error:
        raise HTTPException(status_code=404, detail="La propuesta no existe o ya caducó.") from error
    except MemoryWriteRejected as error:
        raise HTTPException(status_code=422, detail={"code": "MEMORIA_PROHIBIDA",
                                                     "violations": [v.model_dump() for v in error.violations]}) from error
    except Exception as error:  # noqa: BLE001 — Redis caído
        logger.exception("memory confirm failed")
        raise HTTPException(status_code=503, detail="La memoria del agente no está disponible.") from error
    return {"status": "confirmed", "entry": entry.model_dump()}


@router.post("/agent/memory/proposals/{proposal_id}/reject")
def reject_memory_proposal(proposal_id: str, user=Depends(require_staff)):
    store = _memory_store()
    try:
        store.reject(proposal_id, rejected_by=_user_label(user))
    except KeyError as error:
        raise HTTPException(status_code=404, detail="La propuesta no existe o ya caducó.") from error
    return {"status": "rejected", "id": proposal_id}


# ----------------------------------------------------------------------------
# Sesión autenticada para los guardrails: qué pedidos puede consultar quien pregunta
# ----------------------------------------------------------------------------
def build_session_context(request: Request, user) -> SessionContext:
    """Qué puede hacer el agente en nombre de ESTA cuenta: pedidos, marcas y tools internas (deny by default)."""
    from services.agent.session import session_for

    return session_for(getattr(request.app.state, "engine", None), getattr(user, "user_uuid", None), _user_label(user),
                       staff=is_staff(user), client_ids=getattr(user, "client_ids", ()))


@router.get("/agent/guardrails/summary")
def guardrails_summary(conversation_id: str | None = None, since: str | None = None, user=Depends(require_staff)):
    """Cuántas veces se activó cada guardrail (opcionalmente en una conversación del usuario o desde una fecha)."""
    session = f"{getattr(user, 'id', 'u')}:{conversation_id}" if conversation_id else None
    return get_agent().traces.guardrail_summary(session_id=session, since=since)


@router.get("/agent/runs/{run_id}/actions")
def agent_run_actions(run_id: str, _user=Depends(require_staff)):
    """Qué hizo el agente en una corrida, en orden, y por qué."""
    traces = get_agent().traces
    trace = traces.get_trace(run_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="No existe esa corrida.")
    return {"run_id": run_id, "actor": trace.get("actor"), "status": trace["status"], "started_at": trace["started_at"],
            "sources": trace["sources_used"], "actions": action_log(trace, traces.guardrail_events(run_id))}


@router.get("/agent/memory")
def list_agent_memory(_user=Depends(require_staff)):
    return {"entries": [entry.model_dump() for entry in _memory_store().entries()]}


@router.get("/agent/memory/audit")
def agent_memory_audit(_user=Depends(require_staff)):
    """Registro auditable de cada decisión: propuesta, resultado, mensajes, usuario y marca de tiempo."""
    return {"decisions": _memory_store().audit_log()}
