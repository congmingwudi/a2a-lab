"""WS8 fan-out dispatch — parallelism, the delegation guard, partial failure."""

from __future__ import annotations

import asyncio
import time

import pytest

from interop import delegation
from interop.models import AgentResponse
from interop.registry import Registry, Target
from orchestration.runner import dispatch
from orchestration.legs import LEGS


class FakeClient:
    """Records what it was asked, answers however the test says."""

    def __init__(self, name, answer=None, error=None, delay=0.0, sink=None):
        self.name = name
        self._answer = answer
        self._error = error
        self._delay = delay
        self._sink = sink if sink is not None else []
        self.closed = False

    async def ask(self, req):
        self._sink.append((self.name, req))
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error:
            raise self._error
        return AgentResponse(text=self._answer, session_id=req.session_id)

    async def aclose(self):
        self.closed = True


def _registry(clients: dict, protocol="a2a"):
    """A Registry whose client_for returns our fakes."""
    targets = {
        leg.target: Target(name=leg.target, platform=leg.platform, protocol=protocol)
        for leg in LEGS
    }
    reg = Registry(targets)
    reg.client_for = lambda name, exact=False: clients[name]  # type: ignore[method-assign]
    return reg


def _all_ok(sink):
    return {
        leg.target: FakeClient(leg.target, answer=f"{leg.role} answer", sink=sink) for leg in LEGS
    }


async def test_all_legs_answer_and_every_leg_is_rendered(tmp_path):
    sink = []
    result = await dispatch(
        "A port strike has halted traffic.",
        caller="a2alab-supply-orchestrator",
        caller_platform="claude",
        registry=_registry(_all_ok(sink)),
    )
    assert result.complete
    assert result.ok_count == 3
    rendered = result.render()
    for leg in LEGS:
        assert leg.business_unit in rendered
    assert "[fan-out coverage: 3/3 legs answered]" in rendered


async def test_partial_failure_is_reported_never_omitted():
    """The core contract: a dropped leg must be visible in the output."""
    sink = []
    clients = _all_ok(sink)
    broken = LEGS[1]
    clients[broken.target] = FakeClient(
        broken.target, error=RuntimeError("gateway exploded"), sink=sink
    )

    result = await dispatch(
        "A port strike has halted traffic.",
        caller="a2alab-supply-orchestrator",
        caller_platform="claude",
        registry=_registry(clients),
    )

    assert not result.complete
    assert result.ok_count == 2
    rendered = result.render()
    # the failed leg still has a section, and it names what went wrong
    assert f"[leg unavailable: {broken.platform}" in rendered
    assert "gateway exploded" in rendered
    # and the coverage line makes a degraded run unmistakable
    assert "[fan-out coverage: 2/3 legs answered]" in rendered


async def test_empty_answer_counts_as_a_failed_leg():
    """A completed call with no text is a failure, not a success.

    Same rule the A2A client learned the hard way, and the same shape as the
    Agentforce timeout measured 2026-07-25: a well-formed response carrying
    nothing.
    """
    sink = []
    clients = _all_ok(sink)
    empty = LEGS[0]
    clients[empty.target] = FakeClient(empty.target, answer="   ", sink=sink)

    result = await dispatch(
        "task", caller="orc", caller_platform="claude", registry=_registry(clients)
    )
    assert result.ok_count == 2
    assert "empty answer" in result.render()


async def test_legs_run_concurrently_not_serially():
    """Wall time must track the slowest leg, not the sum — that IS the point."""
    sink = []
    clients = {
        leg.target: FakeClient(leg.target, answer="ok", delay=0.30, sink=sink) for leg in LEGS
    }
    start = time.perf_counter()
    result = await dispatch(
        "task", caller="orc", caller_platform="claude", registry=_registry(clients)
    )
    elapsed = time.perf_counter() - start
    assert result.complete
    # three 0.30s legs: ~0.30s concurrent vs ~0.90s serial
    assert elapsed < 0.60, f"legs appear to run serially ({elapsed:.2f}s)"


async def test_every_leg_carries_the_delegation_rider_at_depth_one():
    """D27 under fan-out: three parallel delegations, each depth 1, each
    told not to delegate onward."""
    sink = []
    await dispatch(
        "task",
        caller="a2alab-supply-orchestrator",
        caller_platform="claude",
        trace_id="trace-fanout-1",
        registry=_registry(_all_ok(sink)),
    )
    assert len(sink) == 3
    for _name, req in sink:
        assert delegation.MARKER in req.message
        assert "delegation-depth: 1" in req.message
        assert "caller-agent: a2alab-supply-orchestrator" in req.message
        # D34 text-level trace propagation rides along
        assert "lab-trace: trace-fanout-1" in req.message
        # machine-readable twin
        assert req.metadata["delegation"]["depth"] == 1
        # and every leg shares the orchestrator's trace id
        assert req.trace_id == "trace-fanout-1"


async def test_legs_refuse_to_go_deeper_than_the_guard_allows(monkeypatch):
    """An orchestrator that is ITSELF a delegate must not fan out further.

    dispatch() stamps depth = inbound + 1, so an inbound depth already at the
    limit produces requests the receiving seams will refuse — the guard holds
    at fan-out exactly as it does in a chain.
    """
    sink = []
    await dispatch(
        "task",
        caller="orc",
        caller_platform="claude",
        inbound_depth=1,
        registry=_registry(_all_ok(sink)),
    )
    for _name, req in sink:
        assert req.metadata["delegation"]["depth"] == 2
        assert not delegation.allowed(req)


async def test_slow_leg_times_out_instead_of_hanging_the_turn(monkeypatch):
    monkeypatch.setenv("A2ALAB_LEG_TIMEOUT_S", "0.05")
    sink = []
    clients = _all_ok(sink)
    slow = LEGS[2]
    clients[slow.target] = FakeClient(slow.target, answer="late", delay=5.0, sink=sink)

    result = await asyncio.wait_for(
        dispatch("task", caller="orc", caller_platform="claude", registry=_registry(clients)),
        timeout=3.0,
    )
    assert result.ok_count == 2
    assert "timed out" in result.render()


async def test_clients_are_closed_even_when_a_leg_fails():
    sink = []
    clients = _all_ok(sink)
    clients[LEGS[0].target] = FakeClient(LEGS[0].target, error=RuntimeError("boom"), sink=sink)
    await dispatch("task", caller="orc", caller_platform="claude", registry=_registry(clients))
    assert all(c.closed for c in clients.values())


def test_unknown_scenario_is_a_programming_error():
    with pytest.raises(KeyError):
        asyncio.run(
            dispatch("task", caller="orc", caller_platform="claude", scenario="does-not-exist")
        )


def test_leg_prompt_pins_the_answer_shape():
    """Determinism: every leg is asked for the same shape so runs compare."""
    for leg in LEGS:
        prompt = leg.prompt("a port strike")
        assert "at most 3 short bullets" in prompt
        assert "Do not call any other agent" in prompt
        assert "a port strike" in prompt


def test_leg_targets_resolve_lazily_not_at_import(monkeypatch):
    """Regression: leg targets were baked into a module-level tuple at import.

    scripts/run_fanout.py imports this module at the top and calls load_dotenv()
    inside main(), so every .env override was read as absent and each run
    silently used the default targets. Nothing failed — the run looked perfect,
    and the only way it surfaced was reading the recorded hops and finding the
    wrong target names. Anything env-dependent must resolve when it is USED.
    """
    from orchestration.legs import legs_for

    monkeypatch.setenv("A2ALAB_LEG_EXPOSURE_TARGET", "some-other-agent")
    assert {leg.role: leg.target for leg in legs_for()}["exposure"] == "some-other-agent"

    monkeypatch.delenv("A2ALAB_LEG_EXPOSURE_TARGET")
    assert {leg.role: leg.target for leg in legs_for()}["exposure"] == "google-adk-a2a"


async def test_dispatch_can_subset_roles_without_changing_behaviour():
    """--legs is a caller convenience; the default path still runs every leg."""
    sink = []
    clients = _all_ok(sink)
    result = await dispatch(
        "task",
        caller="orc",
        caller_platform="claude",
        registry=_registry(clients),
        roles={"commercial"},
    )
    assert [r.leg.role for r in result.results] == ["commercial"]
    assert len(sink) == 1


async def test_leg_timeout_is_read_at_call_time_not_import_time(monkeypatch):
    """The remote MCP server runs legs inside an API Gateway request and must
    cut them shorter than the host-side path does. Reading the override at
    import would repeat the bug legs.py documents: scripts import this module
    before load_dotenv() runs, so every .env value reads as absent and the run
    silently uses the default while looking perfect."""
    from orchestration.runner import LEG_TIMEOUT_DEFAULT_S, leg_timeout_s

    assert leg_timeout_s() == LEG_TIMEOUT_DEFAULT_S
    monkeypatch.setenv("A2ALAB_LEG_TIMEOUT_S", "25")
    assert leg_timeout_s() == 25.0
