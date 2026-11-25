"""Freno de coste, parada de emergencia y validación de las llamadas a tools."""

import pytest

from mock_data.process import model_budget, rag as rag_process
from mock_data.process.model_budget import KillSwitchActive, ModelBudget, ModelBudgetExceeded
from services.agent import tools
from services.agent.mcp_client import AGENT_ALLOWED_TOOLS, McpCallResult, ReadOnlyViolation, TrackflowMcpTools
from services.agent.tool_policy import TOOL_SCHEMAS, ToolCallRejected, validate_tool_call

TICKET = "8a4f3f2e-6c1d-5b9a-9e2f-0123456789ab"


@pytest.fixture(autouse=True)
def clean_budget():
    model_budget.BUDGET.reset()
    yield
    model_budget.BUDGET.reset()


def test_a_runaway_loop_stops_at_the_hourly_budget_and_recovers(monkeypatch):
    monkeypatch.setenv("LLM_MAX_CALLS_PER_HOUR", "5")
    now = [0.0]
    budget, done = ModelBudget(clock=lambda: now[0]), 0
    with pytest.raises(ModelBudgetExceeded):
        while True:
            budget.charge()
            done += 1
            assert done < 100
    assert done == 5 == budget.used()
    now[0] = 3601
    budget.charge()


def test_every_model_client_is_charged(monkeypatch):
    monkeypatch.setenv("LLM_MAX_CALLS_PER_HOUR", "1")
    rag_process._openai_client("http://127.0.0.1:9/v1", "k")
    with pytest.raises(ModelBudgetExceeded):
        rag_process._openai_client("http://127.0.0.1:9/v1", "k")


def test_openai_clients_are_only_created_behind_the_budget():
    import re
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "*.py"], cwd=root, capture_output=True, text=True, check=True).stdout.split()
    creators = {f for f in files if not re.search(r"(^|/)tests/", f) and (root / f).exists()
                and re.search(r"\b(Async)?OpenAI\(|Anthropic\(|ChatOpenAI\(|init_chat_model\(", (root / f).read_text(encoding="utf-8"))}
    # streaming reutiliza la URL y la clave de un cliente ya cobrado.
    assert creators == {"mock_data/process/rag.py", "services/agent/streaming.py"}


@pytest.mark.parametrize("value", ["", "abc", "0", "-3"])
def test_a_bad_limit_falls_back_to_the_default_never_to_unlimited(monkeypatch, value):
    monkeypatch.setenv("LLM_MAX_CALLS_PER_HOUR", value)
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", value)
    assert model_budget._positive("LLM_MAX_CALLS_PER_HOUR", 600) == 600 and model_budget.max_output_tokens() == 800


def test_the_kill_switch_stops_every_call_without_a_restart(tmp_path, monkeypatch):
    switch = tmp_path / "stop"
    monkeypatch.setenv("AI_KILL_SWITCH_FILE", str(switch))
    rag_process._openai_client("http://127.0.0.1:9/v1", "k")
    switch.touch()
    with pytest.raises(KillSwitchActive):
        rag_process._openai_client("http://127.0.0.1:9/v1", "k")
    switch.unlink()
    rag_process._openai_client("http://127.0.0.1:9/v1", "k")
    monkeypatch.setenv("AI_KILL_SWITCH", "1")
    with pytest.raises(KillSwitchActive):
        rag_process._openai_client("http://127.0.0.1:9/v1", "k")


# ---------------------------------------------------------------- validación de llamadas a tools
def test_the_policy_covers_exactly_the_tools_of_the_agent():
    assert set(TOOL_SCHEMAS) == AGENT_ALLOWED_TOOLS


@pytest.mark.parametrize("tool,arguments", [
    ("incidents_update_ticket_status", {"incident_id": TICKET, "status": "resolved"}), ("incidents_create_ticket", {"title": "x"}),
    ("approve_return", {"id": 1}), ("set_carrier", {"carrier": "FedEx"}), ("", {}),
    ("incidents_get_ticket", {"incident_id": "1 OR 1=1"}), ("incidents_get_ticket", {"incident_id": "../../users/1"}),
    ("incidents_get_ticket", {}), ("incidents_get_ticket", {"incident_id": TICKET, "status": "resolved"}), ("incidents_get_ticket", None),
    ("incidents_list_tickets", {"limit": 100000}), ("incidents_list_tickets", {"limit": "50"}), ("incidents_list_tickets", {"limit": 5, "status": "todos"}),
    ("inventory_query_products", {"operation": "update", "sku": "A-1"}), ("inventory_query_products", {"operation": "list", "quantity": 5}),
    ("inventory_query_products", {"operation": "list", "sku": "A-1'; DROP TABLE sku;--"}), ("inventory_query_products", {"operation": "list", "warehouse": "MAD"}),
])
def test_invalid_tool_calls_are_rejected_before_they_leave(tool, arguments):
    with pytest.raises(ToolCallRejected):
        validate_tool_call(tool, arguments)


def test_valid_calls_pass_unchanged():
    for tool, arguments in (("incidents_get_ticket", {"incident_id": TICKET}), ("incidents_list_tickets", {"status": "open", "limit": 50}),
                            ("inventory_query_products", {"operation": "list", "sku": "CSM-SRM-030", "warehouse": "ZGZ"})):
        assert validate_tool_call(tool, arguments) == arguments


def test_a_poisoned_plan_never_reaches_the_server():
    class Mcp:
        calls = []

        def call(self, name, arguments):
            self.calls.append((name, arguments))
            return McpCallResult(ok=True, data={"products": []})

    query = tools.InventoryQuery(sku="A'; DROP TABLE sku;--")
    result = tools.lookup_inventory(query, mcp=Mcp())
    assert Mcp.calls == [] and (result.ok, result.error_type) == (False, "invalid_query")


def test_the_client_validates_too_and_still_blocks_writes():
    client = TrackflowMcpTools("http://127.0.0.1:9/mcp", auth=None, timeout=1)
    assert client.call("incidents_get_ticket", {"incident_id": "x"}).error_code == "VALIDATION_ERROR"
    with pytest.raises(ReadOnlyViolation):
        client.call("incidents_update_ticket_status", {"incident_id": TICKET, "status": "resolved"})


def test_a_tool_that_returns_another_ticket_is_not_shown():
    class Mcp:
        def call(self, name, arguments):
            return McpCallResult(ok=True, data={"id": "11111111-1111-4111-8111-111111111111", "title": "De otra persona", "status": "open",
                                                "category": "other", "origin": "customer", "branch": "central"})

    result = tools.lookup_incidents(tools.IncidentQuery(ticket_id=TICKET), mcp=Mcp())
    assert (result.ok, result.error_type, result.incidents) == (False, "bad_response", [])


def test_old_traces_and_checkpoints_are_purged(tmp_path):
    from services.agent.graph import KnowledgeAgent
    from services.agent.guardrails import SessionContext
    from services.agent.purge import purge

    agent = KnowledgeAgent(data_dir=tmp_path, retrieve_fn=lambda q, *, k, min_score: [], generate_fn=lambda q, c: "x", mcp=object())
    old, new = (agent.run("¿Cuál es el SLA?", session=SessionContext(user="a@example.com")).run_id for _ in range(2))
    with agent.traces._lock, agent.traces._connection as db:
        db.execute("UPDATE agent_runs SET started_at = '2020-01-01T00:00:00.000+00:00' WHERE run_id = ?", (old,))
    assert agent.checkpoints(old) and purge(tmp_path, 90) == 1
    assert agent.traces.get_trace(old) is None and agent.traces.get_trace(new) is not None
    assert agent.checkpoints(old) == [] and agent.checkpoints(new)
    with pytest.raises(ValueError):
        purge(tmp_path, 0)


def test_purging_is_irreversible_so_it_asks_first(tmp_path, monkeypatch, capsys):
    from services.agent.graph import KnowledgeAgent
    from services.agent.purge import main

    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path))
    agent = KnowledgeAgent(data_dir=tmp_path, retrieve_fn=lambda q, *, k, min_score: [], generate_fn=lambda q, c: "x", mcp=object())
    run = agent.run("¿Cuál es el SLA?").run_id
    with agent.traces._lock, agent.traces._connection as db:
        db.execute("UPDATE agent_runs SET started_at = '2020-01-01T00:00:00.000+00:00'")
    assert main(["--days", "90"]) == 1 and agent.traces.get_trace(run) is not None and "Se borrarían 1" in capsys.readouterr().out
    assert main(["--days", "90", "--confirm"]) == 0 and agent.traces.get_trace(run) is None
