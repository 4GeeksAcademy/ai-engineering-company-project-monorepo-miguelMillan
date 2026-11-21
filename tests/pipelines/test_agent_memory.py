"""Memoria del agente (Hito 8, Parte 1): criterio de auto-evaluación, guardrails del CONTEXT, interfaz
de lectura/escritura sobre Redis (fakeredis) y comportamiento del grafo con dobles del LLM."""

from __future__ import annotations

import json
from pathlib import Path

import fakeredis
import pytest

from services.agent.graph import KnowledgeAgent, sqlite_checkpointer
from services.agent.memory import (
    MemoryProposal,
    MemoryWriteRejected,
    RedisMemoryStore,
    check_proposal,
    evaluate_memory_gate,
)
from services.agent.tools import IncidentToolResult
from services.agent.tracing import TraceStore, executed_nodes, node_output

ROOT = Path(__file__).resolve().parents[2]

# Ejemplos literales del CONTEXT del hito
SHOULD_PROPOSE = [
    "En realidad SEUR ya no cubre esa zona rural de Zaragoza, hay que usar el carrier local desde el mes pasado.",
    "Esos retrasos reportados en incidencias de Los Ángeles esta semana son por la huelga portuaria, no por un problema "
    "nuestro — ya van tres tickets sobre lo mismo.",
    "El cliente de cosméticos siempre quiere su reporte mensual con el desglose de devoluciones primero, antes que el "
    "volumen de envíos.",
]
NOTHING_TO_REMEMBER = [
    "¿Dónde está el paquete con tracking XJ4471?",
    "Perfecto, ya quedó resuelto.",
    "Tradúceme esto al inglés para el cliente.",
    # y consultas normales de la Parte 1/2
    "¿Qué transportista cubre mejor Aragón rural?",
    "¿En qué estado está el ticket d32a9b66-b81b-5dce-b90f-c4e8bfc27161?",
    "¿Cuál es la ventana de devolución estándar?",
    "Gracias, eso es todo.",
    "Prepara el reporte mensual del cliente de cosméticos: ¿qué métricas destaco?",
]

SEUR_RULE = MemoryProposal(tipo="regla_carrier", accion="corregir", carrier="SEUR", pais="ES", zona="zona rural de Zaragoza",
                           hecho="SEUR ya no cubre la zona rural de Zaragoza; se usa el transportista local desde el mes pasado",
                           motivo="corrige la regla de asignación de carrier para esa zona")
STRIKE = MemoryProposal(tipo="contexto_incidente_recurrente", pais="US", ruta="Puerto de Los Ángeles", tickets_relacionados=3,
                        vigencia_dias=7, hecho="Los retrasos de esta semana en Los Ángeles se deben a la huelga portuaria",
                        motivo="evita re-escalar la misma alerta; ya van tres tickets")
COSMETICS = MemoryProposal(tipo="preferencia_reporte_b2b", cliente_b2b="cliente de cosméticos",
                           hecho="Quiere el reporte mensual con el desglose de devoluciones primero, antes que el volumen de envíos",
                           motivo="preferencia recurrente del cliente B2B sobre su reporte")


@pytest.fixture
def store():
    return RedisMemoryStore(fakeredis.FakeRedis(decode_responses=True))


# --------------------------------------------------------------------------
# 1. Criterio explícito de auto-evaluación (no "siempre")
# --------------------------------------------------------------------------
@pytest.mark.parametrize("question", SHOULD_PROPOSE)
def test_memorable_interactions_open_the_gate(question):
    gate = evaluate_memory_gate(question)
    assert gate.open and gate.signals, gate


@pytest.mark.parametrize("question", NOTHING_TO_REMEMBER)
def test_most_interactions_are_nothing_to_remember(question):
    gate = evaluate_memory_gate(question)
    assert not gate.open and gate.reason, gate


# --------------------------------------------------------------------------
# 2. Guardrails: exactamente lo que el CONTEXT permite y prohíbe
# --------------------------------------------------------------------------
@pytest.mark.parametrize("proposal,question", [(SEUR_RULE, SHOULD_PROPOSE[0]), (STRIKE, SHOULD_PROPOSE[1]), (COSMETICS, SHOULD_PROPOSE[2])])
def test_the_three_memorable_categories_pass(proposal, question):
    assert check_proposal(proposal, question) == []


@pytest.mark.parametrize("hecho,code", [
    # Ubicación exacta de clientes B2C (destinatarios finales)
    ("La destinataria de Zaragoza vive en Calle Mayor 12, 3º B; entregar siempre por la tarde", "DIRECCION_EXACTA_CLIENTE"),
    ("El cliente final del pedido de LA está en 1234 Sunset Blvd, CA 90028", "DIRECCION_EXACTA_CLIENTE"),
    ("El destinatario vive en el código postal 50012 y pide entregas en su casa", "DIRECCION_EXACTA_CLIENTE"),
    # Ubicación de clientes B2B (marcas)
    ("El almacén de la marca de cosméticos está en Polígono Plaza nave 14, Zaragoza", "DIRECCION_EXACTA_CLIENTE"),
    ("Las oficinas de PureStep: sus oficinas estan en el centro de Los Ángeles, recoger allí", "UBICACION_CLIENTE"),
    # Rutas internas del almacén
    ("En el almacén de Zaragoza la ruta interna de picking pasa por el pasillo 4 y el muelle 2", "RUTA_INTERNA_ALMACEN"),
    ("El código de acceso de la puerta de servicio de LA cambió", "RUTA_INTERNA_ALMACEN"),
    # Contratos comerciales activos / en negociación
    ("Estamos negociando con GlowLab un contrato con 12% de descuento negociado", "CONTRATO_EN_NEGOCIACION"),
    ("La renovación del contrato de PureStep incluye una tarifa pactada nueva", "CONTRATO_EN_NEGOCIACION"),
])
def test_prohibited_information_is_rejected_for_b2c_and_b2b(hecho, code):
    proposal = MemoryProposal(tipo="preferencia_reporte_b2b", cliente_b2b="GlowLab", hecho=hecho, motivo="lo pidió el usuario")
    assert code in {v.code for v in check_proposal(proposal)}


def test_single_package_incident_without_pattern_is_rejected():
    isolated = MemoryProposal(tipo="contexto_incidente_recurrente", pais="ES", ruta="Zaragoza-Huesca", tickets_relacionados=1,
                              hecho="El paquete XJ4471 llegó con la caja rota", motivo="queja del cliente")
    assert "INCIDENCIA_PUNTUAL_SIN_PATRON" in {v.code for v in check_proposal(isolated, "el paquete XJ4471 llegó roto")}
    # Sin tickets relacionados ni señal de recurrencia en el mensaje, no hay patrón que recordar
    no_evidence = STRIKE.model_copy(update={"tickets_relacionados": None})
    assert "INCIDENCIA_PUNTUAL_SIN_PATRON" in {v.code for v in check_proposal(no_evidence, "un retraso en LA")}


@pytest.mark.parametrize("carrier,country", [("Correos", "ES"), ("SEUR", "US"), ("UPS", "ES"), ("Amazon Logistics", "US")])
def test_carrier_rules_only_for_the_eight_trackflow_carrier_country_pairs(carrier, country):
    proposal = SEUR_RULE.model_copy(update={"carrier": carrier, "pais": country})
    assert "CARRIER_DESCONOCIDO" in {v.code for v in check_proposal(proposal)}


# --------------------------------------------------------------------------
# 3. Interfaz explícita de lectura / escritura (Redis, namespace propio)
# --------------------------------------------------------------------------
def test_proposing_never_writes_memory(store):
    pending = store.save_pending(SEUR_RULE, SHOULD_PROPOSE[0], run_id="r1")
    assert store.entries() == []
    assert store.recall("¿Qué carrier uso para la zona rural de Zaragoza con SEUR?") == []
    assert store.get_pending(pending.id).proposal == SEUR_RULE


def test_confirm_is_the_only_write_and_consolidates_by_carrier_and_country(store):
    first = store.save_pending(SEUR_RULE, SHOULD_PROPOSE[0], "r1")
    store.confirm(first.id, approved_by="valentina.cruz@trackflow.example")
    other_zone = SEUR_RULE.model_copy(update={"zona": "Teruel capital", "hecho": "SEUR entrega en Teruel capital en 48 h"})
    store.confirm(store.save_pending(other_zone, "en realidad SEUR ya no tarda 24h en Teruel", "r2").id, approved_by="agente-es-04")
    correction = SEUR_RULE.model_copy(update={"hecho": "SEUR vuelve a cubrir la zona rural de Zaragoza desde hoy"})
    store.confirm(store.save_pending(correction, "en realidad SEUR ha vuelto a cubrir esa zona", "r3").id, approved_by="agente-es-02")

    entries = store.entries()
    assert [e.key for e in entries] == ["carrier:SEUR:ES"]  # una sola entrada por carrier + país, no una por ticket
    facts = entries[0].hechos
    assert set(facts) == {"zona-rural-de-zaragoza", "teruel-capital"}
    assert facts["zona-rural-de-zaragoza"]["hecho"].startswith("SEUR vuelve")  # la corrección sustituye
    assert facts["zona-rural-de-zaragoza"]["aprobado_por"] == "agente-es-02"
    assert store.get_pending(first.id) is None


def test_incident_context_expires(store):
    store.confirm(store.save_pending(STRIKE, SHOULD_PROPOSE[1], "r1").id, approved_by="agente-us-01")
    key = "trackflow:agent_memory:entry:incident:US:puerto-de-los-angeles"
    assert 0 < store.r.ttl(key) <= 7 * 24 * 3600


def test_confirm_reapplies_guardrails_even_if_the_pending_was_tampered(store):
    pending = store.save_pending(COSMETICS, SHOULD_PROPOSE[2], "r1")
    tampered = pending.model_copy(update={"proposal": COSMETICS.model_copy(update={"hecho": "Su almacén está en Calle Alfonso 5, 50003"})})
    store.r.set(f"trackflow:agent_memory:pending:{pending.id}", tampered.model_dump_json())
    with pytest.raises(MemoryWriteRejected) as error:
        store.confirm(pending.id, approved_by="x")
    assert "DIRECCION_EXACTA_CLIENTE" in str(error.value)
    assert store.entries() == []


def test_reject_discards_the_proposal_and_is_audited(store):
    pending = store.save_pending(COSMETICS, SHOULD_PROPOSE[2], "r1")
    store.reject(pending.id, rejected_by="agente-es-01")
    assert store.get_pending(pending.id) is None and store.entries() == []
    assert [r["outcome"] for r in store.audit_log()] == ["propuesta", "rechazada"]


def test_recall_returns_only_relevant_entries(store):
    for proposal, question in ((SEUR_RULE, SHOULD_PROPOSE[0]), (STRIKE, SHOULD_PROPOSE[1]), (COSMETICS, SHOULD_PROPOSE[2])):
        store.confirm(store.save_pending(proposal, question, None).id, approved_by="valentina")
    assert [e.key for e in store.recall("¿Qué carrier uso para un envío a un pueblo de Zaragoza?")] == ["carrier:SEUR:ES"]
    assert [e.key for e in store.recall("¿SEUR sigue cubriendo Huesca?")] == ["carrier:SEUR:ES"]
    assert [e.key for e in store.recall("Hay retrasos en Los Ángeles, ¿escalo la incidencia?")] == ["incident:US:puerto-de-los-angeles"]
    assert [e.key for e in store.recall("Prepara el reporte mensual del cliente de cosméticos")] == ["b2b_report:cliente-de-cosmeticos"]
    assert store.recall("¿Cuál es la ventana de devolución estándar?") == []
    assert store.recall("¿Qué tarifa tiene UPS en Los Ángeles?") == []  # no hay memoria de UPS


def test_memory_never_touches_the_rag_knowledge_collection(store):
    store.confirm(store.save_pending(SEUR_RULE, SHOULD_PROPOSE[0], None).id, approved_by="valentina")
    assert all(k.startswith("trackflow:agent_memory:") for k in store.r.keys("*"))
    for module in ("memory.py", "memory_llm.py"):
        code = (ROOT / "services/agent" / module).read_text(encoding="utf-8")
        assert "qdrant_client" not in code and "QdrantClient" not in code and "upsert" not in code, module


# --------------------------------------------------------------------------
# 4. Grafo: una sola llamada al modelo con campo propuesta_memoria; propone, nunca escribe
# --------------------------------------------------------------------------
CHUNK = {"company": "trackflow", "source_document": "carrier-coverage", "section": "España", "language": "es", "chunk_index": 0,
         "text": "SEUR: mejor cobertura en zonas rurales de Aragón.", "score": 0.62}
TICKET = "d32a9b66-b81b-5dce-b90f-c4e8bfc27161"


class FakeLLM:
    def __init__(self, proposal=None):
        self.proposal = proposal
        self.calls = []

    def generate(self, question, context):  # generate_answer del RAG (sin campo de memoria)
        self.calls.append(("generate_answer", question))
        return "SEUR tiene la mejor cobertura en zonas rurales de Aragón."

    def generate_with_memory(self, question, context, memories):
        self.calls.append(("generate_with_memory", [m.key for m in memories]))
        return "Según la memoria aprobada, SEUR ya no cubre esa zona: se usa el transportista local."

    def reflect(self, question, answer, memories, gate=None):
        self.calls.append(("reflect", question))
        return self.proposal


def agent_for(llm, store, chunks=(CHUNK,), incidents=None):
    def incidents_fn(query, *, mcp=None):
        return incidents or IncidentToolResult(ok=True, mode="list", query=query, incidents=[], total=3)

    return KnowledgeAgent(retrieve_fn=lambda q, *, k, min_score: list(chunks), generate_fn=llm.generate,
                          memory_generate_fn=llm.generate_with_memory, reflect_fn=llm.reflect, incidents_fn=incidents_fn,
                          memory_store=store, checkpointer=sqlite_checkpointer(":memory:"), traces=TraceStore(":memory:"))


def test_plain_question_makes_no_proposal_and_one_plain_model_call(store):
    llm = FakeLLM(proposal=SEUR_RULE.model_dump())
    result = agent_for(llm, store).run("¿Qué transportista cubre mejor Aragón rural?")
    assert llm.calls == [("generate_answer", "¿Qué transportista cubre mejor Aragón rural?")]  # sin auto-evaluación
    assert result.memory_proposal is None and "Propuesta de memoria" not in result.answer
    assert store.r.keys("*") == []


def test_memorable_rag_interaction_proposes_with_a_single_model_call_and_never_writes(store):
    llm = FakeLLM(proposal=SEUR_RULE.model_dump())
    agent = agent_for(llm, store)
    result = agent.run(SHOULD_PROPOSE[0])

    assert llm.calls == [("reflect", SHOULD_PROPOSE[0])]  # UNA llamada: la auto-evaluación con propuesta_memoria
    assert result.memory_proposal["tipo"] == "regla_carrier" and result.memory_proposal["id"].startswith("mem-")
    # No da por cierta la corrección: acusa recibo, muestra la base de conocimiento y pregunta
    assert result.answer.startswith("Tomo nota de lo que indicas. Todavía no está verificado")
    assert "Lo que dice hoy la base de conocimiento: «SEUR: mejor cobertura en zonas rurales de Aragón.»" in result.answer
    assert "¿Quieres que lo recuerde" in result.answer
    assert store.entries() == []  # proponer no es escribir
    assert store.get_pending(result.memory_proposal["id"]) is not None
    trace = agent.traces.get_trace(result.run_id)
    assert executed_nodes(trace)[:3] == ["resolve_pending_memory", "receive_question", "recall_memory"] and executed_nodes(trace)[-1] == "propose_memory"
    assert trace["final_node"] == "propose_memory"


def test_memorable_tool_interaction_uses_a_single_reflection_call(store):
    llm = FakeLLM(proposal=STRIKE.model_dump())
    result = agent_for(llm, store, chunks=()).run(SHOULD_PROPOSE[1])
    assert result.sources == ["incidents"]
    assert llm.calls == [("reflect", SHOULD_PROPOSE[1])]  # la respuesta fue determinista: una sola llamada, solo propuesta
    assert result.memory_proposal["tipo"] == "contexto_incidente_recurrente"


def test_model_saying_nothing_to_remember_is_respected(store):
    llm = FakeLLM(proposal=None)
    result = agent_for(llm, store).run(SHOULD_PROPOSE[0])
    assert result.memory_proposal is None and "Propuesta de memoria" not in result.answer and store.r.keys("*") == []


@pytest.mark.parametrize("bad", [
    {"tipo": "preferencia_reporte_b2b", "cliente_b2b": "GlowLab", "hecho": "Su almacén está en Calle Alfonso 5, 50003",
     "motivo": "para recogidas"},
    {"tipo": "regla_carrier", "carrier": "SEUR", "pais": "ES", "zona": "pasillo 4", "hecho": "La ruta interna de picking pasa por el pasillo 4",
     "motivo": "x"},
    {"tipo": "preferencia_reporte_b2b", "cliente_b2b": "GlowLab", "hecho": "Estamos negociando un contrato con 12% de descuento",
     "motivo": "x"},
])
def test_prohibited_model_proposals_are_dropped_and_never_offered(store, bad):
    llm = FakeLLM(proposal=bad)
    agent = agent_for(llm, store)
    result = agent.run(SHOULD_PROPOSE[0])
    assert result.memory_proposal is None and "Propuesta de memoria" not in result.answer
    rejection = node_output(agent.traces.get_trace(result.run_id), "propose_memory")["memory_rejection"]
    assert rejection["codes"]
    assert store.r.keys("*") == []


def test_malformed_proposal_is_dropped_with_a_reason(store):
    llm = FakeLLM(proposal={"tipo": "opinion", "hecho": "x"})
    agent = agent_for(llm, store)
    result = agent.run(SHOULD_PROPOSE[0])
    assert result.memory_proposal is None
    assert node_output(agent.traces.get_trace(result.run_id), "propose_memory")["memory_rejection"]["codes"] == ["PROPUESTA_MAL_FORMADA"]


def test_confirmed_memory_is_recalled_next_session_without_growing_a_prompt(store):
    store.confirm(store.save_pending(SEUR_RULE, SHOULD_PROPOSE[0], None).id, approved_by="valentina")
    store.confirm(store.save_pending(COSMETICS, SHOULD_PROPOSE[2], None).id, approved_by="valentina")
    llm = FakeLLM()
    agent = agent_for(llm, store)
    result = agent.run("¿Qué carrier usamos para un envío a un pueblo de Zaragoza?")
    # Solo la entrada relevante (SEUR/ES), no toda la memoria ni el historial de conversaciones
    assert llm.calls == [("generate_with_memory", ["carrier:SEUR:ES"])]
    recalled = node_output(agent.traces.get_trace(result.run_id), "recall_memory")["memories"]
    assert [m["key"] for m in recalled] == ["carrier:SEUR:ES"]


def test_tool_answer_includes_recalled_incident_context(store):
    store.confirm(store.save_pending(STRIKE, SHOULD_PROPOSE[1], None).id, approved_by="valentina")
    result = agent_for(FakeLLM(), store, chunks=()).run("¿Cuántas incidencias abiertas hay en Los Ángeles por retrasos?")
    assert "Contexto recordado" in result.answer and "huelga portuaria" in result.answer


def test_memory_store_down_never_breaks_the_answer():
    class Down:
        def recall(self, *a, **k):
            raise ConnectionError("redis caído")

        def save_pending(self, *a, **k):
            raise ConnectionError("redis caído")

    llm = FakeLLM(proposal=SEUR_RULE.model_dump())
    agent = agent_for(llm, Down())
    result = agent.run(SHOULD_PROPOSE[0])
    assert result.answer.startswith("Tomo nota") and result.memory_proposal is None
    assert node_output(agent.traces.get_trace(result.run_id), "propose_memory")["memory_rejection"]["codes"] == ["MEMORIA_NO_DISPONIBLE"]


def test_state_has_no_conversation_history_key():
    from typing import get_type_hints

    from services.agent.graph import AgentState

    assert not {"messages", "chat_history", "history", "system_prompt"} & set(get_type_hints(AgentState))


# --------------------------------------------------------------------------
# 5. Confirmación del usuario: clasificación explícita de la respuesta a la propuesta pendiente
# --------------------------------------------------------------------------
from services.agent.memory import FACT_TTL_DAYS, MAX_B2B_CLIENTS, PendingAlreadyOpen, classify_pending_reply  # noqa: E402


@pytest.mark.parametrize("message,label,remainder", [
    ("Sí", "aprobar", ""),
    ("sí, guárdalo", "aprobar", ""),
    ("Vale, adelante.", "aprobar", ""),
    ("Sí, guárdalo. Y otra cosa: ¿cuál es la ventana de devolución estándar?", "aprobar", "¿cuál es la ventana de devolución estándar?"),
    ("No", "rechazar", ""),
    ("No, gracias", "rechazar", ""),
    ("no lo guardes", "rechazar", ""),
    ("No, no lo guardes. ¿Cuántos tickets abiertos hay?", "rechazar", "¿Cuántos tickets abiertos hay?"),
    ("Sí, pero desde el 1 de noviembre SEUR tampoco cubre Teruel rural", "editar", ""),
    ("guárdalo como: SEUR no cubre la zona rural de Zaragoza desde noviembre", "editar", ""),
    # Ambigüedad o cambio de tema: NUNCA se interpreta como aprobación
    ("No sé dónde está el paquete XJ4471", "otro_tema", "No sé dónde está el paquete XJ4471"),
    ("¿Cuál es la ventana de devolución estándar?", "otro_tema", "¿Cuál es la ventana de devolución estándar?"),
    ("sí hay retrasos en LA?", "otro_tema", "sí hay retrasos en LA?"),
    ("Perfecto, ya quedó resuelto.", "otro_tema", "Perfecto, ya quedó resuelto."),
    ("Tal vez, luego lo vemos", "otro_tema", "Tal vez, luego lo vemos"),
    ("La verdad es que sí lo guardaría", "otro_tema", "La verdad es que sí lo guardaría"),
])
def test_reply_to_pending_proposal_is_classified_explicitly(message, label, remainder):
    intent = classify_pending_reply(message)
    assert (intent.label, intent.remainder) == (label, remainder), intent
    assert intent.rationale


def test_edit_carries_the_corrected_fact():
    intent = classify_pending_reply("guárdalo como: SEUR no cubre la zona rural de Zaragoza desde noviembre")
    assert intent.edited_fact == "SEUR no cubre la zona rural de Zaragoza desde noviembre"


def test_only_one_pending_proposal_per_conversation(store):
    store.save_pending(SEUR_RULE, SHOULD_PROPOSE[0], "r1", session_id="conv-1")
    with pytest.raises(PendingAlreadyOpen):
        store.save_pending(COSMETICS, SHOULD_PROPOSE[2], "r2", session_id="conv-1")
    store.save_pending(COSMETICS, SHOULD_PROPOSE[2], "r3", session_id="conv-2")  # otra conversación sí puede


def session_agent(llm, store, chunks=(CHUNK,)):
    agent = agent_for(llm, store, chunks)
    return lambda message, session="conv-1", user="agente-es-01@trackflow.example": agent.run(message, session_id=session, user=user)


def test_approval_writes_memory_and_is_audited(store):
    ask = session_agent(FakeLLM(proposal=SEUR_RULE.model_dump()), store)
    first = ask(SHOULD_PROPOSE[0])
    assert first.memory_proposal and store.entries() == []
    reply = ask("Sí, guárdalo")
    assert reply.memory_resolution["outcome"] == "aprobada" and reply.final_node == "memory_acknowledge"
    assert reply.answer.startswith("✅ Memoria actualizada")
    assert [e.key for e in store.entries()] == ["carrier:SEUR:ES"]
    audit = store.audit_log()
    assert [r["outcome"] for r in audit] == ["propuesta", "aprobada"]
    approved = audit[-1]
    assert approved["user"] == "agente-es-01@trackflow.example" and approved["reply"] == "Sí, guárdalo"
    assert approved["mensaje_origen"] == SHOULD_PROPOSE[0] and approved["ts"] and approved["propuesta"]["hecho"]


def test_rejection_leaves_memory_unchanged_and_is_audited(store):
    ask = session_agent(FakeLLM(proposal=COSMETICS.model_dump()), store)
    ask(SHOULD_PROPOSE[2])
    before = store.entries()
    reply = ask("No, gracias")
    assert reply.memory_resolution["outcome"] == "rechazada" and "memoria queda sin cambios" in reply.answer
    assert store.entries() == before == []
    assert [r["outcome"] for r in store.audit_log()] == ["propuesta", "rechazada"]


def test_topic_change_discards_the_proposal_by_default_and_answers_normally(store):
    llm = FakeLLM(proposal=SEUR_RULE.model_dump())
    ask = session_agent(llm, store)
    first = ask(SHOULD_PROPOSE[0])
    reply = ask("¿Qué transportista cubre mejor Aragón rural?")
    assert reply.memory_resolution["label"] == "otro_tema"
    assert reply.memory_resolution["outcome"] == "descartada_sin_respuesta_clara"
    assert store.entries() == [] and store.get_pending(first.memory_proposal["id"]) is None
    assert "Descarté la propuesta" in reply.answer and "SEUR tiene la mejor cobertura" in reply.answer  # sigue con normalidad
    assert store.audit_log()[-1]["outcome"] == "descartada_sin_respuesta_clara"


def test_answer_and_follow_up_in_the_same_message_continues_normally(store):
    llm = FakeLLM(proposal=SEUR_RULE.model_dump())
    ask = session_agent(llm, store)
    ask(SHOULD_PROPOSE[0])
    llm.calls.clear()
    reply = ask("Sí, guárdalo. Y otra cosa: ¿qué transportista cubre mejor Aragón rural?")
    assert reply.memory_resolution["outcome"] == "aprobada"
    assert reply.answer.startswith("✅ Memoria actualizada")
    assert reply.final_node == "generate" and reply.sources == ["rag"]
    # la pregunta que sigue se responde con la memoria recién aprobada
    assert llm.calls == [("generate_with_memory", ["carrier:SEUR:ES"])]


def test_edit_writes_the_users_version(store):
    ask = session_agent(FakeLLM(proposal=SEUR_RULE.model_dump()), store)
    ask(SHOULD_PROPOSE[0])
    reply = ask("guárdalo como: SEUR no cubre la zona rural de Zaragoza desde el 1 de noviembre; usar el transportista local")
    assert reply.memory_resolution["outcome"] == "editada_y_aprobada"
    fact = store.entries()[0].hechos["zona-rural-de-zaragoza"]
    assert fact["hecho"].startswith("SEUR no cubre la zona rural de Zaragoza desde el 1 de noviembre")
    assert store.audit_log()[-1]["original"] == SEUR_RULE.hecho


def test_edit_that_breaks_the_rules_is_rejected_and_not_written(store):
    ask = session_agent(FakeLLM(proposal=COSMETICS.model_dump()), store)
    ask(SHOULD_PROPOSE[2])
    reply = ask("guárdalo como: enviar el reporte a su almacén de Calle Alfonso 5, 50003 Zaragoza")
    assert reply.memory_resolution["outcome"] == "rechazada_por_guardrails" and store.entries() == []
    assert store.audit_log()[-1]["outcome"] == "rechazada_por_guardrails"


def test_no_second_proposal_while_one_is_pending(store):
    # Sin conversación previa resuelta: si la pendiente sigue abierta (p. ej. otra pestaña), no se lanza otra.
    store.save_pending(COSMETICS, SHOULD_PROPOSE[2], "r0", session_id="conv-9")
    agent = agent_for(FakeLLM(proposal=SEUR_RULE.model_dump()), store)
    resolve_calls = []
    store_session_pending = store.session_pending
    store.session_pending = lambda sid: resolve_calls.append(sid) or (None if len(resolve_calls) == 1 else store_session_pending(sid))
    result = agent.run(SHOULD_PROPOSE[0], session_id="conv-9")
    assert result.memory_proposal is None and "Propuesta de memoria" not in result.answer
    rejection = node_output(agent.traces.get_trace(result.run_id), "propose_memory")["memory_rejection"]
    assert rejection["codes"] == ["PROPUESTA_PENDIENTE_ABIERTA"]


def test_approval_cycle_is_reflected_in_a_future_session(store):
    llm = FakeLLM(proposal=SEUR_RULE.model_dump())
    ask = session_agent(llm, store)
    ask(SHOULD_PROPOSE[0])
    ask("Sí")
    llm.calls.clear()
    later = session_agent(llm, store)("¿Qué carrier usamos para un envío a un pueblo de Zaragoza?", session="conv-mañana")
    assert llm.calls == [("generate_with_memory", ["carrier:SEUR:ES"])] and later.memory_proposal is None


# --------------------------------------------------------------------------
# 6. Consolidación y limpieza
# --------------------------------------------------------------------------
def test_same_fact_is_deduplicated_not_duplicated(store):
    for run in ("r1", "r2"):
        store.confirm(store.save_pending(SEUR_RULE, SHOULD_PROPOSE[0], run).id, approved_by="valentina")
    entry = store.entries()[0]
    assert len(entry.hechos) == 1 and entry.hechos["zona-rural-de-zaragoza"]["confirmaciones"] == 2
    assert store.audit_log()[-1]["deduplicada"] is True


def test_zone_cap_evicts_the_oldest_rule(store, monkeypatch):
    monkeypatch.setitem(__import__("services.agent.memory", fromlist=["x"]).MAX_FACTS_PER_ENTRY, "regla_carrier", 3)
    for i in range(5):
        p = SEUR_RULE.model_copy(update={"zona": f"comarca {i}", "hecho": f"SEUR ya no cubre la comarca {chr(65 + i)} de Aragón"})
        store.confirm(store.save_pending(p, SHOULD_PROPOSE[0], f"r{i}").id, approved_by="valentina")
    entry = store.entries()[0]
    assert len(entry.hechos) == 3 and "comarca-0" not in entry.hechos and "comarca-4" in entry.hechos


def test_stale_carrier_rules_expire(store):
    store.confirm(store.save_pending(SEUR_RULE, SHOULD_PROPOSE[0], "r1").id, approved_by="valentina")
    entry = store.entries()[0]
    old = entry.model_copy(deep=True)
    old.hechos["zona-rural-de-zaragoza"]["aprobado_en"] = "2020-01-01T00:00:00+00:00"
    store.r.set("trackflow:agent_memory:entry:carrier:SEUR:ES", old.model_dump_json())
    assert store.cleanup()["hechos_caducados"] == 1
    assert store.entries() == [] and FACT_TTL_DAYS["regla_carrier"] == 180


def test_b2b_clients_are_capped_with_lru(store, monkeypatch):
    import services.agent.memory as memory

    monkeypatch.setattr(memory, "MAX_B2B_CLIENTS", 2)
    for i, name in enumerate(["GlowLab", "PureStep Footwear", "SoundWave Electronics"]):
        p = COSMETICS.model_copy(update={"cliente_b2b": name})
        store.confirm(store.save_pending(p, SHOULD_PROPOSE[2], f"r{i}").id, approved_by="valentina")
        store.r.zadd("trackflow:agent_memory:usage", {f"b2b_report:{memory.slug(name)}": i})
    store.cleanup()
    assert sorted(e.key for e in store.entries()) == ["b2b_report:purestep-footwear", "b2b_report:soundwave-electronics"]
    assert MAX_B2B_CLIENTS == 60


def test_unanswered_proposal_expires_as_discarded_and_is_audited(store):
    pending = store.save_pending(SEUR_RULE, SHOULD_PROPOSE[0], "r1", session_id="conv-x")
    store.r.delete(f"trackflow:agent_memory:pending:{pending.id}")  # simula que pasaron las 24 h sin respuesta
    assert store.session_pending("conv-x") is None
    assert store.audit_log()[-1]["outcome"] == "caducada_sin_respuesta" and store.entries() == []
    assert store.session_pending("conv-x") is None and len(store.audit_log()) == 2  # se audita una sola vez
