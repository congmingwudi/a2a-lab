"""The ADK researcher's search-source switch (ADK_REAL_SEARCH): synthetic
deterministic signals by default, live Google Search grounding opt-in —
prompt guidance and tool selection must move together."""

import pytest

from platforms.adk import core


def test_synthetic_default(monkeypatch):
    monkeypatch.delenv("ADK_REAL_SEARCH", raising=False)
    assert not core.real_search_enabled()
    assert "search_industry_news" in core.research_instruction()


def test_real_search_flag(monkeypatch):
    monkeypatch.setenv("ADK_REAL_SEARCH", "1")
    assert core.real_search_enabled()
    assert "google_search" in core.research_instruction()
    monkeypatch.setenv("ADK_REAL_SEARCH", "0")
    assert not core.real_search_enabled()


def test_synthetic_news_is_deterministic_and_labeled():
    a = core.search_industry_news("Omega, Inc.")
    assert a == core.search_industry_news("Omega, Inc.")
    assert "synthetic" in a.lower()


def test_agent_tools_follow_flag(monkeypatch):
    pytest.importorskip("google.adk")
    from platforms.adk.agent import build_llm_agent

    monkeypatch.delenv("ADK_REAL_SEARCH", raising=False)
    names = [getattr(t, "__name__", type(t).__name__) for t in build_llm_agent().tools]
    assert "search_industry_news" in names

    monkeypatch.setenv("ADK_REAL_SEARCH", "1")
    names = [getattr(t, "__name__", type(t).__name__) for t in build_llm_agent().tools]
    assert "GoogleSearchTool" in names and "search_industry_news" not in names


# --- WS11: the ADK fan-out leg tool routes through the shared runner ---------
# _leg_tool (unlike build_fanout_orchestrator) pulls in no google.adk, so it is
# testable without the ADK runtime. What we pin: the operator's per-run async
# choice reaches run_one, and the async budget only applies to async.


class _FakeLegResult:
    def __init__(self, ok=True):
        self.ok = ok

    def render(self) -> str:
        return "## Section\nrendered leg"


async def test_leg_tool_async_threads_dispatch_mode_and_budget_into_run_one(monkeypatch):
    from orchestration import legs_for
    from platforms.adk.agent import _leg_tool

    captured = {}

    async def fake_run_one(role, task, **kwargs):
        captured["role"] = role
        captured["task"] = task
        captured.update(kwargs)
        return _FakeLegResult(ok=True)

    monkeypatch.setattr("orchestration.runner.run_one", fake_run_one)
    monkeypatch.setattr("orchestration.runner.async_leg_timeout_s", lambda: 120.0)

    leg = legs_for("supplier-disruption")[0]
    tool = _leg_tool(leg, dispatch_mode="async", trace_id="trace-xyz")
    out = await tool("A port strike halts traffic.")

    assert out == "## Section\nrendered leg"
    assert captured["role"] == leg.role
    assert captured["dispatch_mode"] == "async"
    assert captured["trace_id"] == "trace-xyz"
    # ADK legs run inside the ParallelAgent branch, off any gateway path, so
    # async gets the full off-request per-leg budget (not the small sync env).
    assert captured["timeout_s"] == 120.0
    # The run is attributed to the ADK orchestrator identity for the join measure.
    assert captured["caller_platform"] == "adk"


async def test_leg_tool_sync_passes_no_timeout_override(monkeypatch):
    from orchestration import legs_for
    from platforms.adk.agent import _leg_tool

    captured = {}

    async def fake_run_one(role, task, **kwargs):
        captured.update(kwargs)
        return _FakeLegResult(ok=True)

    monkeypatch.setattr("orchestration.runner.run_one", fake_run_one)

    leg = legs_for("supplier-disruption")[0]
    tool = _leg_tool(leg, dispatch_mode="sync", trace_id="t")
    await tool("situation")
    # sync leaves timeout_s None so run_one reads the (small, gateway-safe) env.
    assert captured["dispatch_mode"] == "sync"
    assert captured["timeout_s"] is None


async def test_leg_tool_mints_a_trace_id_when_none_is_passed(monkeypatch):
    from orchestration import legs_for
    from platforms.adk.agent import _leg_tool

    captured = {}

    async def fake_run_one(role, task, **kwargs):
        captured.update(kwargs)
        return _FakeLegResult(ok=True)

    monkeypatch.setattr("orchestration.runner.run_one", fake_run_one)

    leg = legs_for("supplier-disruption")[0]
    tool = _leg_tool(leg)  # no trace_id
    await tool("situation")
    # run_one requires a trace_id; the leg must mint one rather than fragment.
    assert captured["trace_id"]


# --- BUG 1 regression: the synthesiser's REQUIRED {unit_<role>} template vars
# crash the whole run (KeyError: Context variable not found: unit_exposure.) if
# a branch ends without a final TEXT event. The ParallelAgent seeds every unit
# state key with an attributable placeholder first; a completing branch's
# output_key overwrites it, so the "never omit a gap" contract still holds.


def _fanout_before_callback(orch):
    """The seeding callback ADK stored on the ParallelAgent (it may normalize a
    single callback into a list)."""
    fan_out = orch.sub_agents[0]
    cbs = getattr(fan_out, "canonical_before_agent_callbacks", None)
    if not cbs:
        cbs = fan_out.before_agent_callback
    if not isinstance(cbs, list):
        cbs = [cbs]
    return cbs[0]


class _FakeCbCtx:
    def __init__(self, state):
        self.state = state


def test_fanout_seeds_every_unit_state_key():
    pytest.importorskip("google.adk")
    from orchestration import legs_for
    from platforms.adk.agent import build_fanout_orchestrator

    cb = _fanout_before_callback(build_fanout_orchestrator())
    assert cb is not None
    state: dict = {}
    cb(_FakeCbCtx(state))
    for leg in legs_for():
        val = state[f"unit_{leg.role}"]
        assert val.startswith("[leg unavailable:") and leg.platform in val


def test_fanout_seed_never_overwrites_a_real_answer():
    pytest.importorskip("google.adk")
    from platforms.adk.agent import build_fanout_orchestrator

    cb = _fanout_before_callback(build_fanout_orchestrator())
    state = {"unit_exposure": "REAL EXPOSURE ANSWER"}
    cb(_FakeCbCtx(state))
    assert state["unit_exposure"] == "REAL EXPOSURE ANSWER"  # branch output wins
    assert state["unit_commercial"].startswith("[leg unavailable:")  # gap seeded
