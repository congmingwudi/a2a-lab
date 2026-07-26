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
    names = {t.name for t in registry.all()}
    assert names == {"consult_logistics", "consult_commercial", "consult_customer_operations"}
    assert len(names) == len(LEG_AGENTS)


def test_tool_names_follow_the_org_chart_not_the_deployment():
    """A unit that gets replatformed must not change the tool the model calls."""
    for agent in LEG_AGENTS:
        assert agent.platform not in tool_name(agent)
        assert tool_name(agent).startswith("consult_")


def test_descriptions_tell_the_model_the_tools_are_independent():
    """Without this the model has no basis for issuing them in one turn, and
    the parallelism being measured is unmeasurable."""
    registry = build_registry(FanOutTools(runner=FakeRunner()))
    for tool in registry.all():
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
