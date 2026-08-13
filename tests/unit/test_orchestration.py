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


async def test_run_one_timeout_override_beats_the_env_budget(monkeypatch):
    """A leg slower than the (tight, gateway-bound) env budget still completes
    when `run_one` is given an explicit larger `timeout_s`. This is what lets the
    async fire-then-poll worker outlast the 25s sync ceiling it would otherwise
    inherit — the fix for the cold-Foundry timeout (run 7ef510e2, WS11)."""
    from orchestration.runner import run_one

    monkeypatch.setenv("A2ALAB_LEG_TIMEOUT_S", "0.02")  # the sync gateway budget
    sink = []
    clients = _all_ok(sink)
    role = LEGS[0].role
    clients[LEGS[0].target] = FakeClient(
        LEGS[0].target, answer="slow but fine", delay=0.15, sink=sink
    )

    # With the env budget it would time out; the explicit override rescues it.
    slow = await run_one(
        role,
        "task",
        caller="orc",
        caller_platform="claude",
        trace_id="t1",
        registry=_registry(clients),
    )
    assert not slow.ok and "timed out" in slow.error

    ok = await run_one(
        role,
        "task",
        caller="orc",
        caller_platform="claude",
        trace_id="t2",
        registry=_registry(clients),
        timeout_s=5.0,
    )
    assert ok.ok and "slow but fine" in ok.text


def test_async_leg_budget_is_read_at_call_time_and_defaults_generously(monkeypatch):
    """The async worker's budget is its own env key, defaulting to the full
    (non-gateway) leg default — not the tight sync `A2ALAB_LEG_TIMEOUT_S`."""
    from orchestration.runner import LEG_TIMEOUT_DEFAULT_S, async_leg_timeout_s

    monkeypatch.delenv("A2ALAB_ASYNC_LEG_TIMEOUT_S", raising=False)
    monkeypatch.setenv("A2ALAB_LEG_TIMEOUT_S", "25")  # sync budget must NOT bleed in
    assert async_leg_timeout_s() == LEG_TIMEOUT_DEFAULT_S
    monkeypatch.setenv("A2ALAB_ASYNC_LEG_TIMEOUT_S", "90")
    assert async_leg_timeout_s() == 90.0


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
    # The default is the DEDICATED leg agent, not the shared researcher — an
    # env-less caller (the remote fan-out Lambda) must still reach the agent
    # built for the leg. Pointing this at google-adk-a2a made the Logistics leg
    # refuse as a researcher with no shipment data (2026-08-12).
    assert {leg.role: leg.target for leg in legs_for()}["exposure"] == "adk-logistics-a2a"


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


# ── WS11: async (fire-then-poll) dispatch mode ──────────────────────────────
#
# The point of these is the COMPARISON the workstream set out to take, expressed
# as assertions: a leg whose protocol implements the async half runs submit +
# poll (and the poll count is recorded), while a leg whose protocol does not
# falls back to a blocking call and SAYS so — never silently reads as async.


class AsyncFakeClient:
    """An A2A-capable fake: submit() returns a handle, poll() walks WORKING →
    COMPLETED after `poll_to_done` checks, so the poll count is deterministic."""

    def __init__(self, name, answer, *, poll_to_done=2, sink=None):
        self.name = name
        self._answer = answer
        self._poll_to_done = poll_to_done
        self._polls = 0
        self._sink = sink if sink is not None else []
        self.closed = False
        self.asked = False

    async def submit(self, req):
        self._sink.append((self.name, req))

        class _Handle:
            task_id = f"task-{self.name}"
            answered_immediately = False
            text = ""

        return _Handle()

    async def poll(self, task_id, *, trace_id=None, expect_transient=False):
        self._polls += 1
        done = self._polls >= self._poll_to_done

        class _Snap:
            state = "TASK_STATE_COMPLETED" if done else "TASK_STATE_WORKING"
            text = self._answer if done else ""
            detail = ""

            def __init__(inner):
                inner.done = done
                inner.interrupted = False

        return _Snap()

    async def ask(self, req):
        # Present so a capability check that only looked at ask() would be wrong;
        # the async path must NOT call it.
        self.asked = True
        return AgentResponse(text="SHOULD NOT BE CALLED", session_id=req.session_id)

    async def aclose(self):
        self.closed = True


async def test_async_dispatch_submits_and_polls_a2a_legs(monkeypatch):
    """Every leg is A2A-capable here, so an async run submits + polls each and
    never blocks — and the poll count lands on the result."""
    monkeypatch.setenv("A2ALAB_ASYNC_POLL_INTERVAL_S", "0.001")
    sink = []
    clients = {
        leg.target: AsyncFakeClient(leg.target, f"{leg.role} answer", poll_to_done=2, sink=sink)
        for leg in LEGS
    }
    result = await dispatch(
        "A port strike has halted traffic.",
        caller="a2alab-supply-orchestrator",
        caller_platform="claude",
        registry=_registry(clients),
        dispatch_mode="async",
    )
    assert result.complete and result.ok_count == 3
    # every leg genuinely ran the async lifecycle, and none fell through to ask()
    assert all(r.dispatch_mode == "async" for r in result.results)
    assert all(r.polls >= 2 for r in result.results)
    assert not any(c.asked for c in clients.values())
    # the coverage line surfaces the async dimension, so a fire-then-poll run
    # cannot read like a blocking one
    rendered = result.render()
    assert "3/3 legs async" in rendered
    assert "dispatch:" in rendered
    # the async legs are NAMED (business unit + target), not just counted, so
    # the reader can see WHICH platforms served the asynchronous half (WS11)
    for leg in LEGS:
        assert f"{leg.business_unit} ({leg.target})" in rendered


async def test_async_falls_back_to_sync_when_a_leg_has_no_task_lifecycle():
    """A leg whose client has no submit/poll (an AgentCore / Agent API leg) is
    not an error under async — it runs sync and is recorded async→sync, the WS11
    per-platform finding. The base-class FakeClient has ask() only."""
    sink = []
    clients = {
        leg.target: AsyncFakeClient(leg.target, f"{leg.role} answer", sink=sink) for leg in LEGS
    }
    no_async = LEGS[2]  # customer_comms — the AgentCore leg in the real config
    clients[no_async.target] = FakeClient(no_async.target, answer="sync answer", sink=sink)

    result = await dispatch(
        "task",
        caller="orc",
        caller_platform="claude",
        registry=_registry(clients),
        dispatch_mode="async",
    )
    assert result.ok_count == 3
    by_role = {r.leg.role: r for r in result.results}
    assert by_role[no_async.role].dispatch_mode == "async→sync"
    assert by_role["exposure"].dispatch_mode == "async"
    # the fallback is named in the output, never hidden — and it names the
    # SPECIFIC leg that fell back (business unit + target), not just a count
    rendered = result.render()
    assert "fell back to sync" in rendered
    assert f"{no_async.business_unit} ({no_async.target})" in rendered


class SubmitOnlyFakeClient(AsyncFakeClient):
    """An A2A-capable fake whose remote is submit-only: submit() hands back a
    task id, but EVERY poll raises. The runner must treat that as "no async
    lifecycle reachable here" and fall back — NOT fail the leg.

    Note this is now a hypothetical shape, not Agent Engine: as of 2026-08-11,
    pinning A2A-Version: 1.0 made Agent Engine serve fire-then-poll end-to-end
    (the original "submit-only" verdict was our missing header, plan/03-results
    .md). The fallback path still has to exist and stay correct for any remote
    that genuinely takes a submit and then will not return the task, so this
    exercises it — a poll that fails FROM THE FIRST ATTEMPT AND NEVER RECOVERS,
    distinct from the transient-then-success case
    (test_async_rides_through_a_transient_not_found_after_submit).

    `poll_error_cls` selects which failure shape to raise, each an a2a REST
    mapping: a 404 (MethodNotFoundError), a 400 version mismatch
    (VersionNotSupportedError), and a structured task-not-found
    (TaskNotFoundError) — all three must degrade identically.

    ask() returns a REAL answer, because the whole point of the fallback is that
    the leg still gets answered — a blocking call to a submit-only endpoint
    works fine, it is only the task READ that is unreachable."""

    def __init__(self, name, answer, *, poll_error_cls=None, sink=None):
        super().__init__(name, answer, sink=sink)
        self._sync_answer = answer
        if poll_error_cls is None:
            from a2a.utils.errors import MethodNotFoundError

            poll_error_cls = MethodNotFoundError
        self._poll_error_cls = poll_error_cls

    async def poll(self, task_id, *, trace_id=None, expect_transient=False):
        raise self._poll_error_cls(f"unretrievable task https://.../a2a/tasks/{task_id}")

    async def ask(self, req):
        self.asked = True
        return AgentResponse(text=self._sync_answer, session_id=req.session_id)


@pytest.mark.parametrize(
    "error_name", ["MethodNotFoundError", "VersionNotSupportedError", "TaskNotFoundError"]
)
async def test_async_falls_back_to_sync_when_the_remote_will_not_serve_the_poll(
    monkeypatch, error_name
):
    """The runtime half of the fallback: a client HAS submit + poll, so it looks
    async-capable, but the remote accepts the submit and then will not return the
    task (Agent Engine is submit-only, WS11). The leg must degrade to a blocking
    ask() — recorded async→sync and answered — not be reported unavailable.

    Parametrised over ALL THREE measured poll-failure shapes: the 2026-08-11 bug
    was the 404 (MethodNotFoundError), but the probe also recorded a 400 version
    mismatch and a task-not-found, and each must fall back identically — catching
    only the 404 would leave the run failing under a different status code.
    """
    import a2a.utils.errors as a2a_errors

    error_cls = getattr(a2a_errors, error_name)
    monkeypatch.setenv("A2ALAB_ASYNC_POLL_INTERVAL_S", "0.001")
    # No grace: this client's poll NEVER recovers, so the not-yet-visible window
    # would only delay the inevitable fallback. The transient case is covered
    # separately below.
    monkeypatch.setenv("A2ALAB_ASYNC_NOT_FOUND_GRACE_S", "0")
    sink = []
    clients = {
        leg.target: AsyncFakeClient(leg.target, f"{leg.role} answer", sink=sink) for leg in LEGS
    }
    submit_only = LEGS[0]  # exposure — the ADK/Agent Engine leg in the real config
    clients[submit_only.target] = SubmitOnlyFakeClient(
        submit_only.target, "logistics answer via sync", poll_error_cls=error_cls, sink=sink
    )

    result = await dispatch(
        "task",
        caller="orc",
        caller_platform="claude",
        registry=_registry(clients),
        dispatch_mode="async",
    )
    # the submit-only leg ANSWERED — the run is complete, not degraded
    assert result.complete and result.ok_count == 3
    by_role = {r.leg.role: r for r in result.results}
    assert by_role[submit_only.role].dispatch_mode == "async→sync"
    assert by_role[submit_only.role].polls == 0  # no successful polls happened
    assert by_role[submit_only.role].ok
    assert "logistics answer via sync" in by_role[submit_only.role].text
    # the fallback client's blocking path was actually used
    assert clients[submit_only.target].asked
    # the other legs still ran the real async lifecycle
    assert by_role["commercial"].dispatch_mode == "async"
    # and the output names the fallback rather than hiding it
    assert "fell back to sync" in result.render()


class TransientNotFoundClient(AsyncFakeClient):
    """A2A-capable, and the remote DOES serve the task — but the first few polls
    for a just-submitted task 404 while the task store catches up, then it
    resolves. This is Vertex AI Agent Engine's real behaviour measured live
    2026-08-11: submit returns a task id, poll[0..n] 404, a later poll returns
    COMPLETED. The runner must ride through the early not-found window and finish
    async — NOT fall back to sync, because the async lifecycle genuinely works
    here. The 404 shape is MethodNotFoundError (the SDK maps a bare 404 there).

    `not_found_first` polls raise before the task becomes visible; after that it
    behaves like the base AsyncFakeClient and walks WORKING → COMPLETED."""

    def __init__(self, name, answer, *, not_found_first=2, sink=None):
        super().__init__(name, answer, poll_to_done=1, sink=sink)
        self._not_found_first = not_found_first
        self._attempts = 0

    async def poll(self, task_id, *, trace_id=None, expect_transient=False):
        self._attempts += 1
        if self._attempts <= self._not_found_first:
            from a2a.utils.errors import MethodNotFoundError

            raise MethodNotFoundError(f"Resource not found: https://.../a2a/tasks/{task_id}")
        return await super().poll(task_id, trace_id=trace_id, expect_transient=expect_transient)


async def test_async_rides_through_a_transient_not_found_after_submit(monkeypatch):
    """The other half of the 404 story: a task that is not visible YET (the store
    is eventually consistent right after submit) must not be mistaken for a
    submit-only remote. As long as we have never successfully read the task and
    the grace window has not elapsed, an unretrievable poll is retried — and once
    the task appears the leg completes async, with the transient polls NOT
    counted as successful reads.

    Distinguishes position, not exception class: the SAME MethodNotFoundError is
    ridden through here (never-seen, still early) and falls back immediately in
    the submit-only test above (grace 0). This is the regression guard for the
    2026-08-11 fix — without the grace window the ADK leg fell back to sync on
    the first transient 404 even though async worked."""
    monkeypatch.setenv("A2ALAB_ASYNC_POLL_INTERVAL_S", "0.001")
    monkeypatch.setenv("A2ALAB_ASYNC_NOT_FOUND_GRACE_S", "30")  # ample; polls are ~instant
    sink = []
    clients = {
        leg.target: AsyncFakeClient(leg.target, f"{leg.role} answer", sink=sink) for leg in LEGS
    }
    adk_leg = LEGS[0]  # exposure — the Agent Engine leg in the real config
    clients[adk_leg.target] = TransientNotFoundClient(
        adk_leg.target, "logistics answer via async", not_found_first=2, sink=sink
    )

    result = await dispatch(
        "task",
        caller="orc",
        caller_platform="claude",
        registry=_registry(clients),
        dispatch_mode="async",
    )
    assert result.complete and result.ok_count == 3
    by_role = {r.leg.role: r for r in result.results}
    # rode through the transient 404s and finished ASYNC, not async→sync
    assert by_role[adk_leg.role].dispatch_mode == "async"
    assert by_role[adk_leg.role].ok
    assert "logistics answer via async" in by_role[adk_leg.role].text
    # the transient not-founds were not counted as reads; exactly one real poll
    # returned the completed task
    assert by_role[adk_leg.role].polls == 1
    # and the blocking path was never touched — this leg did NOT fall back
    assert not clients[adk_leg.target].asked
    assert "fell back to sync" not in result.render()


class FlapAfterReadClient(AsyncFakeClient):
    """A2A-capable, the remote serves the task — but the eventually-consistent
    store 404s AGAIN on a poll AFTER a successful read. This is the real Agent
    Engine shape from session edcb844…, 2026-08-12: poll returns WORKING, the
    very next poll 404s, a later poll returns COMPLETED. The 404 is still the
    store flapping, not a submit-only endpoint — once a read has succeeded the
    endpoint is PROVEN to serve tasks, so the leg must ride through and finish
    async, NOT fall back to sync.

    Poll script: WORKING (seen) → 404 → COMPLETED."""

    def __init__(self, name, answer, *, sink=None):
        super().__init__(name, answer, sink=sink)
        self._attempts = 0

    async def poll(self, task_id, *, trace_id=None, expect_transient=False):
        self._attempts += 1
        if self._attempts == 2:
            from a2a.utils.errors import MethodNotFoundError

            raise MethodNotFoundError(f"Resource not found: https://.../a2a/tasks/{task_id}")

        class _Snap:
            done = self._attempts >= 3
            state = "TASK_STATE_COMPLETED" if done else "TASK_STATE_WORKING"
            text = self._answer if done else ""
            detail = ""
            interrupted = False

        return _Snap()


async def test_async_rides_through_a_not_found_that_flaps_after_a_good_read(monkeypatch):
    """The session 47402230 regression: a 404 AFTER the task has been read once
    must be ridden through, not treated as a fatal submit-only shape. The old
    code gated the ride on `not seen_task`, so the first post-read 404 fell back
    to sync — defeating the async pattern on an endpoint that fully supports it.

    Here the ride-through is gated on `seen_task OR within grace`: even with the
    grace window set to 0 (so a NEVER-seen 404 would fall back immediately, per
    the submit-only test), a 404 that arrives after a successful read still rides
    through, because the read proved the endpoint serves tasks."""
    monkeypatch.setenv("A2ALAB_ASYNC_POLL_INTERVAL_S", "0.001")
    # Grace 0 on purpose: proves the ride-through here comes from having SEEN the
    # task, not from the post-submit grace window — the two are independent.
    monkeypatch.setenv("A2ALAB_ASYNC_NOT_FOUND_GRACE_S", "0")
    sink = []
    clients = {
        leg.target: AsyncFakeClient(leg.target, f"{leg.role} answer", sink=sink) for leg in LEGS
    }
    adk_leg = LEGS[0]  # exposure — the Agent Engine leg in the real config
    clients[adk_leg.target] = FlapAfterReadClient(
        adk_leg.target, "logistics answer via async", sink=sink
    )

    result = await dispatch(
        "task",
        caller="orc",
        caller_platform="claude",
        registry=_registry(clients),
        dispatch_mode="async",
    )
    assert result.complete and result.ok_count == 3
    by_role = {r.leg.role: r for r in result.results}
    # finished ASYNC despite the post-read 404 — did NOT fall back to sync
    assert by_role[adk_leg.role].dispatch_mode == "async"
    assert by_role[adk_leg.role].ok
    assert "logistics answer via async" in by_role[adk_leg.role].text
    # two successful reads counted (WORKING, then COMPLETED); the 404 in between
    # rode through and was not counted
    assert by_role[adk_leg.role].polls == 2
    assert not clients[adk_leg.target].asked
    assert "fell back to sync" not in result.render()


async def test_async_does_NOT_swallow_a_genuine_task_failure(monkeypatch):
    """The fallback is narrow on purpose: a task that reaches TASK_STATE_FAILED
    is a real failure of the remote agent, NOT an unreachable poll, so it must
    fail the leg — degrading it to a sync retry would hide broken agents behind a
    second attempt. Only "took the submit, won't return the task" degrades."""
    monkeypatch.setenv("A2ALAB_ASYNC_POLL_INTERVAL_S", "0.001")
    sink = []
    clients = {
        leg.target: AsyncFakeClient(leg.target, f"{leg.role} answer", sink=sink) for leg in LEGS
    }
    failing = LEGS[0]

    class _FailsClient(AsyncFakeClient):
        async def poll(self, task_id, *, trace_id=None, expect_transient=False):
            class _Snap:
                state = "TASK_STATE_FAILED"
                text = ""
                detail = "the agent crashed"
                done = True
                interrupted = False

            return _Snap()

        async def ask(self, req):
            self.asked = True
            return AgentResponse(text="should not be reached", session_id=req.session_id)

    clients[failing.target] = _FailsClient(failing.target, "x", sink=sink)
    result = await dispatch(
        "task",
        caller="orc",
        caller_platform="claude",
        registry=_registry(clients),
        dispatch_mode="async",
    )
    by_role = {r.leg.role: r for r in result.results}
    # the failing leg is UNAVAILABLE, not silently retried sync
    assert not by_role[failing.role].ok
    assert "the agent crashed" in by_role[failing.role].error
    assert not clients[failing.target].asked  # no sneaky sync retry


async def test_sync_mode_is_unchanged_and_records_sync():
    """The default path is exactly today's behaviour: blocking ask(), no polls,
    and no async wording leaks into the coverage line."""
    sink = []
    result = await dispatch(
        "task",
        caller="orc",
        caller_platform="claude",
        registry=_registry(_all_ok(sink)),
    )
    assert all(r.dispatch_mode == "sync" and r.polls == 0 for r in result.results)
    assert "dispatch:" not in result.render()
    assert "async" not in result.render()
