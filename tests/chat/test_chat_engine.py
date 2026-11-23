"""Contrato de eventos, pub/sub por sesión, interrupción y rehidratación del chat (sin WebSocket ni LLM)."""

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from services.agent.chat import events
from services.agent.chat.broker import ChannelBroker
from services.agent.chat.engine import ChatEngine
from services.agent.chat.store import ChatStore
from services.agent.streaming import GenerationAborted

ANSWERS = {"rastreo": "Tu pedido salió ayer y llega mañana por la tarde".split(), "devolución": "Claro, tienes 30 días para devolverlo".split()}


class FakeAgent:
    """Habla el mismo contrato que KnowledgeAgent.stream; responde según el tema del mensaje."""

    def __init__(self, delay=0.02, error=None, replaced=None):
        self.delay, self.error, self.replaced = delay, error, replaced
        self.calls, self.discarded, self.controls = [], [], []

    def stream(self, question, *, on_token, control, run_id, session_id, user, session):
        self.controls.append(control)
        self.calls.append(SimpleNamespace(question=question, run_id=run_id, session_id=session_id, user=user, session=session))
        words = ANSWERS["devolución" if "devoluc" in question else "rastreo"]
        text = ""
        for index, word in enumerate(words):
            time.sleep(self.delay)
            token = word if index == 0 else f" {word}"
            on_token(token)
            text += token
            if self.error and index == 2:
                raise self.error
        return SimpleNamespace(answer=self.replaced or text, replaced=bool(self.replaced))

    def discard_unseen_proposal(self, session_id, run_id):
        self.discarded.append((session_id, run_id))


@pytest.fixture
def store():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return ChatStore(engine)


class Client:
    def __init__(self, subscription, snapshot):
        self.subscription, self.snapshot, self.events = subscription, snapshot, []

    async def until(self, name, count=1, timeout=5):
        async def read():
            while sum(e["event"] == name for e in self.events) < count:
                self.events.append(await self.subscription.get())
        await asyncio.wait_for(read(), timeout)
        return self.events[-1]["data"]

    async def drain(self, seconds):
        try:
            while True:
                self.events.append(await asyncio.wait_for(self.subscription.get(), seconds))
        except TimeoutError:
            return

    def names(self):
        out = []
        for e in self.events:
            if not out or out[-1] != e["event"]:
                out.append(e["event"])
        return out

    def text(self, start=0, stop=None):
        return "".join(e["data"]["token"] for e in self.events[start:stop] if e["event"] == "token_chunk")


async def connect(engine, session_id):
    return Client(*await engine.connect(session_id))


def scenario(store, agent, body):
    chat = store.create_session("user-1")
    return asyncio.run(body(ChatEngine(store, lambda: agent), chat.session_id))


# ---------------------------------------------------------------- contrato
def test_server_events_match_the_briefing_shapes():
    assert events.token_chunk("chat_0219", "Tu", 7) == {"event": "token_chunk", "data": {"session_id": "chat_0219", "token": "Tu", "sequence": 7}}
    assert events.generation_interrupted("chat_0219", "msg_0449") == {
        "event": "generation_interrupted", "data": {"session_id": "chat_0219", "message_id": "msg_0449", "status": "interrupted"}}
    assert events.generation_completed("chat_0219", "msg_0450") == {
        "event": "generation_completed", "data": {"session_id": "chat_0219", "message_id": "msg_0450"}}
    assert events.session_snapshot("chat_0219", []) == {"event": "session_snapshot", "data": {"session_id": "chat_0219", "messages": []}}
    assert events.user_message("chat_0219", "...") == {"event": "user_message", "data": {"session_id": "chat_0219", "text": "..."}}
    assert events.channel("chat_0219") == "chat.chat_0219"


def test_client_events_accept_the_briefing_frames_and_reject_everything_else():
    frame = '{"event": "interrupt_requested", "data": {"session_id": "chat_0219", "new_input": "espera, quiero hacer una devolución, no rastrear el pedido"}}'
    assert events.parse_client_event(frame, "chat_0219").data.new_input.startswith("espera")
    assert events.parse_client_event('{"event": "user_message", "data": {"session_id": "chat_0219", "text": "hola"}}', "chat_0219").data.text == "hola"
    good = {"event": "user_message", "data": {"session_id": "s", "text": "hola"}}
    for bad in ("no json", "[]", "{}", {**good, "event": "message"}, {**good, "event": "token_chunk"}, {**good, "event": "rfp_ticket_created"},
                {"event": "user_message", "data": {"session_id": "s", "text": ""}}, {"event": "user_message", "data": {"session_id": "s", "text": "x" * 4001}},
                {"event": "user_message", "data": {"text": "hola"}}, {"event": "user_message", "data": {"session_id": "otra", "text": "hola"}},
                {"event": "user_message", "data": {"session_id": "s", "text": "hola", "ticket_id": "t"}}):
        with pytest.raises(events.ContractError):
            events.parse_client_event(bad if isinstance(bad, str) else json.dumps(bad), "s")


def test_chat_session_has_exactly_the_briefing_fields(store):
    chat = store.create_session("user-1", "glowlab_cosmetics").public()
    assert set(chat) == {"session_id", "agent_id", "user_id", "client_id", "status", "created_at"}
    assert (chat["agent_id"], chat["status"], chat["client_id"]) == ("first_line_cx", "active", "glowlab_cosmetics")


# ---------------------------------------------------------------- pub/sub
def test_broker_delivers_in_order_to_every_subscriber_of_the_channel_only():
    async def body():
        broker = ChannelBroker()
        a, b, other = broker.subscribe("chat.x"), broker.subscribe("chat.x"), broker.subscribe("chat.y")
        threads = [threading.Thread(target=lambda: [broker.publish("chat.x", (threading.get_ident(), i)) for i in range(200)]) for _ in range(4)]
        [t.start() for t in threads]; [t.join() for t in threads]
        got = [[await s.get() for _ in range(800)] for s in (a, b)]
        assert got[0] == got[1]                                                        # un único orden para todos
        for ident in {g[0] for g in got[0]}:
            assert [i for t, i in got[0] if t == ident] == list(range(200))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(other.get(), 0.05)
        broker.unsubscribe("chat.x", a)
        assert broker.subscribers("chat.x") == 1 and broker.publish("chat.nadie", 1) == 0
    asyncio.run(body())


def test_a_consumer_that_cannot_keep_up_is_dropped_without_affecting_the_others():
    async def body():
        broker = ChannelBroker(max_pending=10)
        slow, fast = broker.subscribe("c"), broker.subscribe("c")
        seen = []
        for i in range(30):
            broker.publish("c", i)
            seen.append(await fast.get())
        assert seen == list(range(30)) and not fast.overflowed
        assert [await slow.get() for _ in range(11)] == [*range(10), None]
    asyncio.run(body())


# ---------------------------------------------------------------- turnos
def test_a_turn_streams_token_by_token_and_completes(store):
    async def body(engine, sid):
        client = await connect(engine, sid)
        assert client.snapshot["data"]["messages"] == []
        await engine.submit(sid, "rastreo de mi pedido", user="ana", context=lambda: "ctx")
        done = await client.until("generation_completed")
        assert client.names() == ["user_message", "token_chunk", "generation_completed"]
        chunks = [e["data"] for e in client.events if e["event"] == "token_chunk"]
        assert [c["sequence"] for c in chunks] == list(range(1, len(ANSWERS["rastreo"]) + 1))
        assert all(set(c) == {"session_id", "token", "sequence"} for c in chunks)
        question, answer = store.messages(sid)
        assert (question.role, question.text) == ("user", "rastreo de mi pedido")
        assert (answer.message_id, answer.status, answer.text) == (done["message_id"], "completed", client.text())
        return engine, answer
    agent = FakeAgent()
    _, answer = scenario(store, agent, body)
    call = agent.calls[0]
    assert (call.run_id, call.session_id, call.session) == (answer.message_id, answer.session_id, "ctx")


def test_interrupt_stops_the_original_generation_and_starts_a_new_turn_with_new_input(store):
    new_input = "espera, quiero hacer una devolución, no rastrear el pedido"

    async def body(engine, sid):
        client = await connect(engine, sid)
        await engine.submit(sid, "rastreo", user=None, context=None)
        await client.until("token_chunk", 3)
        await engine.interrupt(sid, new_input, user=None, context=None)
        await client.until("generation_completed")
        await client.drain(0.4)                                                        # nada tardío de la primera

        cut = next(i for i, e in enumerate(client.events) if e["event"] == "generation_interrupted")
        interrupted = client.events[cut]["data"]
        assert client.names() == ["user_message", "token_chunk", "generation_interrupted", "user_message", "token_chunk", "generation_completed"]
        assert client.events[cut + 1]["data"]["text"] == new_input
        original, redirected = client.text(0, cut), client.text(cut)
        assert 0 < len(original) < len(" ".join(ANSWERS["rastreo"])) and " ".join(ANSWERS["rastreo"]).startswith(original)
        assert redirected == " ".join(ANSWERS["devolución"])                           # refleja new_input, sin restos del rastreo
        rows = store.messages(sid)
        assert [(m.role, m.status) for m in rows] == [("user", "completed"), ("assistant", "interrupted"), ("user", "completed"), ("assistant", "completed")]
        assert (rows[1].message_id, rows[1].text) == (interrupted["message_id"], original)   # parcial conservado tal como se vio
        assert rows[3].message_id != rows[1].message_id and rows[2].text == new_input
        assert store.get_session(sid, "user-1").status == "active"
        return rows
    agent = FakeAgent(delay=0.03)
    rows = scenario(store, agent, body)
    assert agent.calls[1].question == new_input
    assert [c.cancelled for c in agent.controls] == [True, False]                      # se detiene el modelo, no solo lo que se ve
    assert (rows[1].session_id, rows[1].message_id) in agent.discarded


def test_stop_without_new_input_leaves_the_session_interrupted_until_the_next_message(store):
    async def body(engine, sid):
        client = await connect(engine, sid)
        await engine.submit(sid, "rastreo", user=None, context=None)
        await client.until("token_chunk", 2)
        await engine.interrupt(sid, "   ", user=None, context=None)
        assert store.get_session(sid, "user-1").status == "interrupted" and len(store.messages(sid)) == 2
        await engine.submit(sid, "devolución", user=None, context=None)
        await client.until("generation_completed")
        assert store.get_session(sid, "user-1").status == "active"
    scenario(store, FakeAgent(), body)


def test_interrupting_before_the_first_token_or_after_the_end(store):
    async def body(engine, sid):
        client = await connect(engine, sid)
        await engine.submit(sid, "rastreo", user=None, context=None)
        await engine.interrupt(sid, "devolución", user=None, context=None)             # aún pensando
        await client.until("generation_completed")
        assert [(m.status, m.text) for m in store.messages(sid)][1] == ("interrupted", "")
        before = len(client.events)
        await engine.interrupt(sid, "rastreo otra vez", user=None, context=None)       # ya había terminado: es un turno normal
        await client.until("generation_completed", 2)
        assert "generation_interrupted" not in [e["event"] for e in client.events[before:]]
        assert store.messages(sid)[3].status == "completed"
    scenario(store, FakeAgent(), body)


def test_interrupting_at_any_moment_never_lets_a_late_token_through(store):
    """Muchas carreras reales entre el hilo del agente y la interrupción."""
    async def body(engine, sid):
        client = await connect(engine, sid)
        for round_ in range(40):
            await engine.submit(sid, f"rastreo {round_}", user=None, context=None)
            await asyncio.sleep(0.001 * (round_ % 12))
            await engine.interrupt(sid, "", user=None, context=None)
        await client.drain(0.3)
        turns, open_ = [], False
        for e in client.events:
            if e["event"] == "user_message":
                open_, turns = True, turns + [[]]
            elif e["event"] == "token_chunk":
                assert open_, "token después de generation_interrupted"
                turns[-1].append(e["data"])
            elif e["event"] in ("generation_interrupted", "generation_completed"):
                open_ = False
        assert len(turns) == 40
        assert all([c["sequence"] for c in t] == list(range(1, len(t) + 1)) for t in turns)
        answers = [m for m in store.messages(sid) if m.role == "assistant"]
        assert [m.text for m in answers] == ["".join(c["token"] for c in t) for t in turns]   # se guarda justo lo mostrado
        assert all(m.status in ("interrupted", "completed") for m in answers)
    scenario(store, FakeAgent(delay=0.001), body)


def test_a_new_message_while_answering_redirects_and_two_sessions_do_not_interfere(store):
    async def body(engine, sid):
        other = store.create_session("user-2").session_id
        client, neighbour = await connect(engine, sid), await connect(engine, other)
        await engine.submit(sid, "rastreo", user=None, context=None)
        await engine.submit(other, "rastreo", user=None, context=None)
        await client.until("token_chunk", 2)
        await engine.submit(sid, "devolución", user=None, context=None)
        await asyncio.gather(client.until("generation_completed"), neighbour.until("generation_completed"))
        assert "generation_interrupted" in client.names() and neighbour.names() == ["user_message", "token_chunk", "generation_completed"]
        assert all(e["data"]["session_id"] == other for e in neighbour.events)
    scenario(store, FakeAgent(), body)


# ---------------------------------------------------------------- desacoplo y rehidratación
def test_disconnecting_does_not_stop_the_producer_and_reconnecting_restores_the_thread(store):
    async def body(engine, sid):
        client = await connect(engine, sid)
        await engine.submit(sid, "rastreo", user=None, context=None)
        await client.until("token_chunk", 2)
        await engine.disconnect(sid, client.subscription)
        assert engine.broker.subscribers(events.channel(sid)) == 0
        await asyncio.sleep(0.5)                                                       # nadie mira; el agente termina
        back = await connect(engine, sid)
        messages = back.snapshot["data"]["messages"]
        assert [(m["role"], m["status"]) for m in messages] == [("user", "completed"), ("assistant", "completed")]
        assert messages[1]["text"] == " ".join(ANSWERS["rastreo"])
        await engine.submit(sid, "devolución", user=None, context=None)                # sigue el mismo hilo
        await back.until("generation_completed")
        assert [m.role for m in store.messages(sid)] == ["user", "assistant", "user", "assistant"]
        assert {c.session_id for c in engine.agent().calls} == {sid}
    agent = FakeAgent()
    scenario(store, agent, body)


def test_reconnecting_mid_answer_continues_exactly_where_the_snapshot_ends(store):
    async def body(engine, sid):
        first = await connect(engine, sid)
        await engine.submit(sid, "rastreo", user=None, context=None)
        late = []
        for count in (1, 3, 5):
            await first.until("token_chunk", count)
            late.append(await connect(engine, sid))
        await asyncio.gather(*(c.until("generation_completed") for c in [first, *late]))
        for client in late:
            partial = client.snapshot["data"]["messages"][1]
            assert partial["status"] == "streaming"
            chunks = [e["data"]["sequence"] for e in client.events if e["event"] == "token_chunk"]
            assert chunks == list(range(partial["sequence"] + 1, len(ANSWERS["rastreo"]) + 1))
            assert partial["text"] + client.text() == " ".join(ANSWERS["rastreo"])     # ni repetido ni perdido
    scenario(store, FakeAgent(), body)


def test_after_a_server_restart_the_orphan_answer_is_kept_as_interrupted(store):
    sid = store.create_session("user-1").session_id
    _, answer = store.start_turn(sid, "rastreo")
    store.update_message(answer.message_id, text_="Tu pedido salió", status="streaming")

    async def body():
        client = await connect(ChatEngine(store, lambda: FakeAgent()), sid)            # proceso nuevo
        assert [(m["text"], m["status"]) for m in client.snapshot["data"]["messages"]] == [("rastreo", "completed"), ("Tu pedido salió", "interrupted")]
    asyncio.run(body())
    store.update_message(answer.message_id, text_="otro texto", status="completed")    # lo cerrado no se reescribe
    assert store.messages(sid)[1].text == "Tu pedido salió"


def test_agent_failure_is_reported_keeps_the_partial_text_and_the_session_survives(store):
    async def body(engine, sid):
        client = await connect(engine, sid)
        await engine.submit(sid, "rastreo", user=None, context=None)
        failure = await client.until("error")
        assert failure["code"] == "agent_unavailable" and "clave-secreta" not in json.dumps(failure)
        assert [(m.status, m.text) for m in store.messages(sid)][1] == ("failed", "Tu pedido salió")
        engine.agent().error = None
        await engine.submit(sid, "devolución", user=None, context=None)
        await client.until("generation_completed")
    scenario(store, FakeAgent(error=RuntimeError("clave-secreta")), body)


def test_an_answer_replaced_by_the_output_guard_is_corrected_with_a_snapshot(store):
    async def body(engine, sid):
        client = await connect(engine, sid)
        await engine.submit(sid, "rastreo", user=None, context=None)
        await client.until("session_snapshot")
        assert client.names()[-2:] == ["generation_completed", "session_snapshot"]
        assert client.events[-1]["data"]["messages"][1]["text"] == "No puedo darte esa respuesta." == store.messages(sid)[1].text
    scenario(store, FakeAgent(replaced="No puedo darte esa respuesta."), body)


def test_a_session_is_only_visible_to_its_owner(store):
    sid = store.create_session("user-1").session_id
    assert store.get_session(sid, "user-1") and store.get_session(sid, "user-2") is None and store.get_session("chat_x", "user-1") is None


def test_idle_sessions_do_not_stay_in_memory(store):
    async def body(engine, sid):
        client = await connect(engine, sid)
        await engine.submit(sid, "rastreo", user=None, context=None)
        await client.until("token_chunk", 1)
        await engine.disconnect(sid, client.subscription)
        await asyncio.sleep(0.5)
        assert engine._runtimes == {}
    scenario(store, FakeAgent(), body)


def test_shutting_down_the_api_keeps_the_partial_answer_as_interrupted(store):
    async def body(engine, sid):
        client = await connect(engine, sid)
        await engine.submit(sid, "rastreo", user=None, context=None)
        await client.until("token_chunk", 3)
        engine.shutdown()
        row = store.messages(sid)[1]
        assert row.status == "interrupted" and row.text and "Tu pedido salió".startswith(row.text[:15])
        assert store.get_session(sid, "user-1").status == "interrupted"
    scenario(store, FakeAgent(), body)


def test_the_periodic_save_never_undoes_an_interruption(store):
    """Escrituras simultáneas del hilo del agente y de la interrupción sobre el mismo mensaje."""
    for _ in range(30):
        _, answer = store.start_turn(store.create_session("user-1").session_id, "rastreo")
        flush = threading.Thread(target=lambda: [store.update_message(answer.message_id, text_=f"parcial {i}", status="streaming") for i in range(5)])
        flush.start()
        store.update_message(answer.message_id, text_="lo mostrado", status="interrupted")
        flush.join()
        row = store.messages(answer.session_id)[1]
        assert (row.status, row.text) == ("interrupted", "lo mostrado")


def test_many_watchers_of_one_session_see_the_same_events_with_a_single_agent_call(store):
    """Un supervisor (u otra pestaña) mira la misma conversación: se suscribe al canal, no vuelve a llamar al agente."""
    async def body(engine, sid):
        watchers = [await connect(engine, sid) for _ in range(20)]
        await engine.submit(sid, "rastreo", user=None, context=None)
        await watchers[0].until("token_chunk", 3)
        watchers.append(await connect(engine, sid))                                    # llega a mitad: foto + lo que falta
        await engine.interrupt(sid, "devolución", user=None, context=None)
        await asyncio.gather(*(w.until("generation_completed") for w in watchers))
        assert all(w.events == watchers[0].events for w in watchers[:20])
        late = watchers[-1]
        seen = late.snapshot["data"]["messages"][1]["text"] + late.text(0, next(i for i, e in enumerate(late.events) if e["event"] == "generation_interrupted"))
        assert seen == store.messages(sid)[1].text
    agent = FakeAgent()
    scenario(store, agent, body)
    assert [c.question for c in agent.calls] == ["rastreo", "devolución"]              # una llamada por turno, no por espectador


def test_many_subscribers_see_the_same_events_with_a_single_agent_call(store):
    """Doce conexiones a la misma sesión (dos llegan a mitad de respuesta), con una interrupción de por medio."""
    async def body(engine, sid):
        clients = [await connect(engine, sid) for _ in range(10)]
        await engine.submit(sid, "rastreo", user=None, context=None)
        await clients[0].until("token_chunk", 3)
        late = [await connect(engine, sid) for _ in range(2)]
        await engine.interrupt(sid, "quiero una devolución", user=None, context=None)
        await asyncio.gather(*(c.until("generation_completed") for c in [*clients, *late]))
        assert all(c.events == clients[0].events for c in clients)
        for client in late:                                                            # su snapshot + lo que vino después = lo mismo
            assert client.events == clients[0].events[-len(client.events):]
            cut = next(i for i, e in enumerate(client.events) if e["event"] == "generation_interrupted")
            assert client.snapshot["data"]["messages"][1]["text"] + client.text(0, cut) == store.messages(sid)[1].text
        assert [m.role for m in store.messages(sid)] == ["user", "assistant", "user", "assistant"]
    agent = FakeAgent(delay=0.03)
    scenario(store, agent, body)
    assert [c.question for c in agent.calls] == ["rastreo", "quiero una devolución"]   # una llamada por turno, no por cliente
