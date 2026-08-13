"""The fan-out MCP server (WS7 item 4) — fake leg runner, no platforms.

What matters here is the CONTRACT the orchestrating model sees: one tool per
business unit, evidence shaped identically to the host-side variant, and a
run_id that actually threads three separate tool calls into one run. The
transport itself is tested in test_mcp_http.py.
"""

from __future__ import annotations

import json

from fanout_mcp.tools import FanOutTools, build_registry, roster, tool_name
from mcp_http.core import handle_message
from orchestration.agents import LEG_AGENTS, by_role
from orchestration.legs import legs_for
from orchestration.runner import LegResult


class FakeRunner:
    """Stands in for orchestration.run_one. Records what each tool asked for."""

    def __init__(self, text="three bullets", ok=True, error=""):
        self.text = text
        self.ok = ok
        self.error = error
        self.calls: list[dict] = []

    async def __call__(self, role, task, *, caller, caller_platform, trace_id, **kw):
        self.calls.append(
            {
                "role": role,
                "task": task,
                "caller": caller,
                "caller_platform": caller_platform,
                "trace_id": trace_id,
            }
        )
        leg = next(leg for leg in legs_for() if leg.role == role)
        return LegResult(leg, self.ok, text=self.text, error=self.error, latency_ms=1234)


def call(registry, name, args):
    reply = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        },
        registry,
    )
    return reply["result"]


# ---- shape ----------------------------------------------------------------


def test_one_tool_per_business_unit():
    """The premise of the whole variant: three tools, so the MODEL schedules
    them. One aggregate tool would put the ordering back in host code."""
    registry = build_registry(FanOutTools(runner=FakeRunner()))
    consult = {t.name for t in registry.all() if t.name.startswith("consult_")}
    assert consult == {"consult_logistics", "consult_commercial", "consult_customer_operations"}
    assert len(consult) == len(LEG_AGENTS)


def test_tool_names_follow_the_org_chart_not_the_deployment():
    """A unit that gets replatformed must not change the tool the model calls."""
    for agent in LEG_AGENTS:
        assert agent.platform not in tool_name(agent)
        assert tool_name(agent).startswith("consult_")


def test_descriptions_tell_the_model_the_tools_are_independent():
    """Without this the model has no basis for issuing them in one turn, and
    the parallelism being measured is unmeasurable."""
    registry = build_registry(FanOutTools(runner=FakeRunner()))
    per_unit = [t for t in registry.all() if t.name.startswith(("consult_", "submit_"))]
    assert per_unit  # the check_task poll tool is shared, so it is exempt
    for tool in per_unit:
        assert "any order" in tool.description
        assert "one turn" in tool.description


def test_roster_is_generated_from_the_same_source_as_the_tools():
    text = roster()
    for agent in LEG_AGENTS:
        assert tool_name(agent) in text
        assert agent.business_unit in text


# ---- dispatch -------------------------------------------------------------


def test_each_tool_runs_only_its_own_leg():
    runner = FakeRunner()
    registry = build_registry(FanOutTools(runner=runner))
    for agent in LEG_AGENTS:
        call(registry, tool_name(agent), {"situation": "port strike", "run_id": "t-1"})
    assert [c["role"] for c in runner.calls] == [a.role for a in LEG_AGENTS]


def test_tools_bind_their_own_agent_not_the_last_one_in_the_loop():
    """Late-binding closures over the loop variable would give every tool the
    final agent — and every call would still SUCCEED, just consulting the wrong
    unit three times."""
    runner = FakeRunner()
    registry = build_registry(FanOutTools(runner=runner))
    call(registry, "consult_logistics", {"situation": "s", "run_id": "t-1"})
    assert runner.calls[-1]["role"] == "exposure"


def test_the_same_run_id_threads_every_call_into_one_run():
    runner = FakeRunner()
    registry = build_registry(FanOutTools(runner=runner))
    for agent in LEG_AGENTS:
        call(registry, tool_name(agent), {"situation": "s", "run_id": "trace-abc"})
    assert {c["trace_id"] for c in runner.calls} == {"trace-abc"}


def test_a_missing_run_id_is_refused_rather_than_invented():
    """Three legs under three generated trace ids look exactly like a healthy
    fan-out until someone tries to join them."""
    runner = FakeRunner()
    registry = build_registry(FanOutTools(runner=runner))
    result = call(registry, "consult_logistics", {"situation": "s"})
    assert json.loads(result["content"][0]["text"])["error"].startswith("run_id is required")
    assert runner.calls == []


def test_a_missing_situation_is_refused():
    runner = FakeRunner()
    registry = build_registry(FanOutTools(runner=runner))
    result = call(registry, "consult_logistics", {"run_id": "t-1"})
    assert "situation is required" in result["content"][0]["text"]
    assert runner.calls == []


def test_delegation_is_stamped_as_the_orchestrator_not_the_mcp_server():
    """The D34 join convention attributes a platform session by caller, and the
    unit was consulted on the orchestrator's behalf."""
    runner = FakeRunner()
    registry = build_registry(FanOutTools(runner=runner))
    call(registry, "consult_commercial", {"situation": "s", "run_id": "t-1"})
    assert runner.calls[0]["caller"] == "a2alab-supply-orchestrator"
    assert runner.calls[0]["caller_platform"] == "claude"


# ---- evidence shape -------------------------------------------------------


def test_a_tool_returns_the_same_rendered_section_as_the_host_side_variant():
    """Attribution changed what the orchestrator wrote (7d50dd9). If the two
    variants handed back differently-shaped evidence, every difference in the
    brief would be confounded by the input rather than by the topology."""
    registry = build_registry(FanOutTools(runner=FakeRunner(text="- exposure is high")))
    text = call(registry, "consult_logistics", {"situation": "s", "run_id": "t-1"})["content"][0][
        "text"
    ]
    agent = by_role("exposure")
    assert agent.agent_name in text
    assert f"Cite this unit as [{agent.business_unit}]" in text
    assert "- exposure is high" in text


def test_a_failed_leg_is_reported_not_omitted():
    """Same partial-failure contract as dispatch: the model must be able to see
    that a unit is missing, which is the 2026-07-25 finding in tool form."""
    registry = build_registry(FanOutTools(runner=FakeRunner(ok=False, error="timed out")))
    result = call(registry, "consult_customer_operations", {"situation": "s", "run_id": "t-1"})
    assert "[leg unavailable:" in result["content"][0]["text"]
    assert "timed out" in result["content"][0]["text"]
    # a reported failure is a successful TOOL call — isError would tell the
    # model the tool is broken rather than that the unit is unreachable
    assert result["isError"] is False


# ---- orchestrator variant selection ---------------------------------------


def test_mcp_variant_swaps_the_tool_inventory_not_the_agent():
    """One agent, two inventories (D41). Two agents would drift, and the whole
    claim of the comparison is that only the topology differs."""
    from orchestration.cma import CmaOrchestrator

    state = {
        "agent_id": "agent_1",
        "environment_id": "env_1",
        "vault_id": "vlt_1",
        "mcp_url": "https://example.test",
        "system": "mcp prompt",
    }
    mcp = CmaOrchestrator(client=object(), state=state, variant="mcp")._session_kwargs()
    tool = CmaOrchestrator(client=object(), state=state, variant="tool")._session_kwargs()

    assert tool["agent"] == "agent_1"
    assert mcp["agent"]["type"] == "agent_with_overrides"
    assert mcp["agent"]["id"] == "agent_1"  # same agent, not a second one
    assert mcp["vault_ids"] == ["vlt_1"]
    # Overrides REPLACE rather than merge, so the host-side tool must be absent
    # — its presence would let the model satisfy the task without ever reaching
    # the MCP server, and the run would look like a success.
    names = [t.get("type") for t in mcp["agent"]["tools"]]
    assert names == ["mcp_toolset"]
    # An unattended run has nobody to confirm a tool prompt; "ask" would idle
    # forever, which is the laptop dependency this variant exists to remove.
    policy = mcp["agent"]["tools"][0]["default_config"]["permission_policy"]
    assert policy == {"type": "always_allow"}


def test_unknown_variant_is_rejected_at_construction():
    from orchestration.cma import CmaOrchestrator

    import pytest

    with pytest.raises(ValueError):
        CmaOrchestrator(client=object(), state={}, variant="parallel")


def test_missing_state_raises_a_catchable_error_not_systemexit(monkeypatch, tmp_path):
    """A host with no .a2alab/ and no env override must fail with a type the
    console can catch — SystemExit escaped /api/run as a plaintext 500 the
    browser then tried to JSON.parse ("Unexpected token 'I', Internal S...")."""
    import pytest

    from orchestration import cma

    monkeypatch.delenv(cma.STATE_ENV, raising=False)
    monkeypatch.delenv(cma.MCP_STATE_ENV, raising=False)
    monkeypatch.setattr(cma, "STATE_FILE", tmp_path / "absent.json")
    monkeypatch.setattr(cma, "MCP_STATE_FILE", tmp_path / "absent_mcp.json")

    with pytest.raises(cma.OrchestratorNotProvisioned):
        cma.load_state()
    with pytest.raises(cma.OrchestratorNotProvisioned):
        cma.load_mcp_state()
    # Not a SystemExit — that is the whole point of the distinct type.
    assert not issubclass(cma.OrchestratorNotProvisioned, SystemExit)


def test_env_override_carries_state_where_no_file_exists(monkeypatch, tmp_path):
    """The container path: the ids ride the task-definition env as whole JSON,
    exactly as CLAUDE_MANAGED_* do (deploy/console/deploy_console.sh)."""
    from orchestration import cma

    monkeypatch.setattr(cma, "STATE_FILE", tmp_path / "absent.json")
    monkeypatch.setattr(cma, "MCP_STATE_FILE", tmp_path / "absent_mcp.json")
    monkeypatch.setenv(
        cma.STATE_ENV, json.dumps({"agent_id": "agent_x", "environment_id": "env_x"})
    )
    monkeypatch.setenv(
        cma.MCP_STATE_ENV,
        json.dumps(
            {
                "agent_id": "agent_y",
                "environment_id": "env_y",
                "system": "s",
                "mcp_url": "u",
                "vault_id": "v",
            }
        ),
    )

    assert cma.load_state()["agent_id"] == "agent_x"
    assert cma.load_mcp_state()["mcp_url"] == "u"


def test_call_path_distinguishes_parallel_from_serial():
    """The measurement WS7 item 4 exists to produce: did the model issue the
    units together, or walk them one at a time?"""
    from orchestration.cma import CallPath, ToolCall

    together = CallPath(
        calls=[ToolCall("consult_logistics", 1, 10), ToolCall("consult_commercial", 1, 12)],
        turns=1,
    )
    apart = CallPath(
        calls=[ToolCall("consult_logistics", 1, 10), ToolCall("consult_commercial", 2, 900)],
        turns=2,
    )
    assert together.parallel
    assert "parallel" in together.render()
    assert not apart.parallel
    assert "serial" in apart.render()
    assert not CallPath().parallel  # no calls at all is not "parallel"


# ---- async fire-then-poll tools (WS11 items 6-7) --------------------------

from fanout_mcp.tasks import STATE_COMPLETED, TaskStore, run_task  # noqa: E402
from fanout_mcp.tools import (  # noqa: E402
    CHECK_TOOL,
    AsyncFanOutTools,
    submit_tool_name,
    worker_runner,
)


class FakeTaskClient:
    """A dict standing in for the Aurora fanout_tasks table (mirrors the one in
    test_fanout_tasks). Enough SQL to hold rows and read them back."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def execute(self, sql: str, params: dict | None = None):
        params = params or {}
        head = sql.strip().split()[0].upper()
        if head == "INSERT":
            self.rows[params["task_id"]] = {
                "task_id": params["task_id"],
                "run_id": params["run_id"],
                "unit": params["unit"],
                "state": params["state"],
                "situation": params.get("situation", ""),
                "result": "",
                "error": "",
            }
            return []
        if head == "UPDATE":
            row = self.rows[params["task_id"]]
            row["state"] = params["state"]
            if "result" in params:
                row["result"] = params["result"] or ""
            if "error" in params:
                row["error"] = params["error"] or ""
            return []
        if "WHERE task_id" in sql and "situation" in sql:
            row = self.rows.get(params["task_id"])
            return [{"situation": row["situation"]}] if row else []
        if "WHERE task_id" in sql:
            row = self.rows.get(params["task_id"])
            keys = ("task_id", "run_id", "unit", "state", "result", "error")
            return [{k: row[k] for k in keys}] if row else []
        if "WHERE run_id" in sql:
            keys = ("task_id", "run_id", "unit", "state", "result", "error")
            return [
                {k: r[k] for k in keys}
                for r in self.rows.values()
                if r["run_id"] == params["run_id"]
            ]
        return []


def _async_tools(dispatcher):
    return AsyncFanOutTools(store=TaskStore(FakeTaskClient()), dispatcher=dispatcher)


def test_registry_exposes_submit_tools_and_one_shared_check_tool():
    """The async half of the MCP variant: a submit_<unit> per unit plus one
    shared check_task poll tool — the A2A submit/poll lifecycle in MCP."""
    registry = build_registry(async_tools=_async_tools(lambda t: None))
    names = {t.name for t in registry.all()}
    assert {"submit_logistics", "submit_commercial", "submit_customer_operations"} <= names
    assert CHECK_TOOL in names
    # both topologies live on the ONE server — no second deploy to switch
    assert {"consult_logistics", "consult_commercial", "consult_customer_operations"} <= names


def test_submit_returns_a_task_id_without_running_the_leg():
    """Fire-then-poll's whole point: the accepting call returns with the work
    not started (D47), so it does not re-hit the gateway ceiling D41 measured."""
    dispatched: list[str] = []
    tools = _async_tools(lambda tid: dispatched.append(tid))
    registry = build_registry(async_tools=tools)

    out = json.loads(
        call(registry, "submit_logistics", {"situation": "port strike", "run_id": "run-1"})[
            "content"
        ][0]["text"]
    )
    assert out["state"] == "SUBMITTED"
    assert out["task_id"]
    assert out["poll_with"] == CHECK_TOOL
    assert dispatched == [out["task_id"]]  # a worker was requested...
    # ...but nothing ran: check still shows SUBMITTED, no result present
    state = json.loads(
        call(registry, CHECK_TOOL, {"task_id": out["task_id"]})["content"][0]["text"]
    )
    assert state["state"] == "SUBMITTED"
    assert "result" not in state


def test_submit_refuses_a_missing_run_id_and_does_not_dispatch():
    dispatched: list[str] = []
    registry = build_registry(async_tools=_async_tools(lambda tid: dispatched.append(tid)))
    out = json.loads(call(registry, "submit_logistics", {"situation": "s"})["content"][0]["text"])
    assert out["error"].startswith("run_id is required")
    assert dispatched == []


def test_fire_then_poll_loop_completes_every_unit_and_check_carries_results():
    """Submit all three; a worker (here inline) runs each leg; check by run_id
    shows them all terminal and check by task_id carries the unit's section."""
    store = TaskStore(FakeTaskClient())

    def dispatcher(task_id):
        # The worker's own execution window, faked inline: run the leg to done.
        run_task(task_id, store, lambda unit, situation: f"- {unit} answered")

    tools = AsyncFanOutTools(store=store, dispatcher=dispatcher)
    registry = build_registry(async_tools=tools)

    task_ids = []
    for agent in LEG_AGENTS:
        out = json.loads(
            call(registry, submit_tool_name(agent), {"situation": "s", "run_id": "run-9"})[
                "content"
            ][0]["text"]
        )
        task_ids.append(out["task_id"])

    everything = json.loads(call(registry, CHECK_TOOL, {"run_id": "run-9"})["content"][0]["text"])
    assert len(everything["tasks"]) == 3
    assert all(t["state"] == STATE_COMPLETED for t in everything["tasks"])

    one = json.loads(call(registry, CHECK_TOOL, {"task_id": task_ids[0]})["content"][0]["text"])
    assert one["result"].endswith("answered")


def test_check_with_no_ids_asks_for_one():
    registry = build_registry(async_tools=_async_tools(lambda t: None))
    out = json.loads(call(registry, CHECK_TOOL, {})["content"][0]["text"])
    assert "task_id" in out["error"] and "run_id" in out["error"]


def test_worker_runner_maps_the_slug_to_its_leg_and_threads_the_run_id(monkeypatch):
    """The worker resolves the run id from the task row and hands it to the leg,
    so a task that ran in a separate invocation still correlates under the id the
    model threaded through submit."""
    seen: dict[str, str] = {}

    async def fake_run_one(role, task, *, caller, caller_platform, trace_id, **kw):
        seen["role"] = role
        seen["trace_id"] = trace_id
        leg = next(leg for leg in legs_for() if leg.role == role)
        return LegResult(leg, True, text="unit ok", error="", latency_ms=1)

    monkeypatch.setattr("fanout_mcp.tools.run_one", fake_run_one)
    out = worker_runner("run-xyz")("logistics", "a situation")
    assert seen["trace_id"] == "run-xyz"
    assert seen["role"] == "exposure"  # logistics slug -> exposure role
    assert "unit ok" in out


def test_worker_runner_uses_the_async_leg_budget_not_the_sync_one(monkeypatch):
    """The async worker is off the gateway path, so it must run the leg with the
    larger `async_leg_timeout_s()` budget — NOT the tight sync `A2ALAB_LEG_TIMEOUT_S`
    the deploy pins for the consult_* HTTP tools. This is the fix for run 7ef510e2:
    a cold Foundry leg (~26.5s) killed at the 25s sync budget though its task
    reached COMPLETED."""
    monkeypatch.setenv("A2ALAB_LEG_TIMEOUT_S", "25")  # the sync gateway budget
    monkeypatch.setenv("A2ALAB_ASYNC_LEG_TIMEOUT_S", "120")  # the worker's budget
    seen: dict[str, object] = {}

    async def fake_run_one(role, task, *, caller, caller_platform, trace_id, timeout_s=None, **kw):
        seen["timeout_s"] = timeout_s
        leg = next(leg for leg in legs_for() if leg.role == role)
        return LegResult(leg, True, text="unit ok", latency_ms=1)

    monkeypatch.setattr("fanout_mcp.tools.run_one", fake_run_one)
    worker_runner("run-xyz")("commercial", "a cold, slow situation")
    assert seen["timeout_s"] == 120.0  # the async budget, not 25


# ---- async prompt selection (cma) -----------------------------------------


def test_mcp_async_selects_the_fire_then_poll_system_prompt():
    from orchestration.cma import CmaOrchestrator

    state = {
        "agent_id": "agent_1",
        "environment_id": "env_1",
        "vault_id": "vlt_1",
        "mcp_url": "https://example.test",
        "system": "SYNC PROMPT",
        "system_async": "ASYNC PROMPT",
    }
    sync = CmaOrchestrator(client=object(), state=state, variant="mcp")._session_kwargs()
    asy = CmaOrchestrator(
        client=object(), state=state, variant="mcp", dispatch_mode="async"
    )._session_kwargs()
    assert sync["agent"]["system"] == "SYNC PROMPT"
    assert asy["agent"]["system"] == "ASYNC PROMPT"


def test_mcp_async_without_a_provisioned_prompt_is_a_catchable_error():
    """No system_async means the agent was provisioned before WS11 — fail with
    the console-catchable type, not a KeyError deep in _session_kwargs."""
    import pytest

    from orchestration.cma import CmaOrchestrator, OrchestratorNotProvisioned

    state = {
        "agent_id": "a",
        "environment_id": "e",
        "vault_id": "v",
        "mcp_url": "u",
        "system": "SYNC ONLY",
    }
    with pytest.raises(OrchestratorNotProvisioned):
        CmaOrchestrator(client=object(), state=state, variant="mcp", dispatch_mode="async")
