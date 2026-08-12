"""Parallel dispatch with an honest partial-failure contract (WS8).

The whole point of fan-out is that the legs are independent, so they run
concurrently and the turn costs the slowest leg rather than the sum. That also
means **partial failure is the normal case**, and the policy here is deliberate:

    A missing leg is reported, never omitted.

Every leg that fails contributes a visible `[leg unavailable: <platform> —
<reason>]` line to the synthesised answer. This is a direct response to what the
lab measured on 2026-07-25: Agentforce abandoning a slow action still returned
HTTP 200, with its section heading present and the delegated content silently
gone, at full cost. A fan-out that quietly drops one of three legs is the same
failure with three times the surface — and it is undetectable downstream unless
the orchestrator says so.

Each leg is its own `Hop` under the orchestrator's trace_id, so the console
groups the whole fan-out as one run and the obs harvest can later measure how
many legs join back to it from the platforms' own logs.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field

from a2a.utils.errors import (
    MethodNotFoundError,
    TaskNotFoundError,
    VersionNotSupportedError,
)

from interop import delegation
from interop.models import AgentRequest, new_trace_id
from interop.registry import Registry
from interop.trace import Hop
from orchestration.legs import Leg, legs_for

# Per-leg wall clock. A fan-out is only as fast as its slowest leg, so one
# wedged platform must not hold the turn open indefinitely — it becomes an
# unavailable leg instead. Generous by default because these are async
# missions, not sync conversation turns.
#
# Overridable because the two orchestrator topologies do not get the same
# budget, and pretending otherwise would hide the cost of moving the tool.
# Host-side, a leg is bounded only by our own patience. Through the remote MCP
# server the leg is inside an HTTP request/response, so it inherits API
# Gateway's integration timeout — 29s in this account, and NOT raisable for
# HTTP APIs (AWS's >29s support covers Regional and private REST APIs only).
# The Lambda therefore sets ~25s, leaving margin for the JSON-RPC round trip.
#
# The consequence is worth stating plainly rather than tuning away: a warm leg
# measures ~17s and fits, while a cold platform (AgentCore 31-56s, Agent Engine
# ~34s p95) does not and is reported as unavailable. Moving a tool from the
# host to the orchestration layer imposes a request budget on work that
# previously had none.
LEG_TIMEOUT_DEFAULT_S = 120.0

# Cadence for the async (fire-then-poll) dispatch mode: how long to wait between
# `tasks/get` calls while a leg's task is still WORKING. Host-side this is a
# free choice — the work advances on the platform regardless of whether we poll
# (unlike the Lambda path D47, where polling is what advances it). A modest
# interval keeps the poll count — the WS11 finding — legible without hammering.
POLL_INTERVAL_DEFAULT_S = 1.0

# How long a task we have NEVER successfully read may keep coming back
# unretrievable before we give up on the async lifecycle. Measured live against
# Vertex AI Agent Engine 2026-08-11: submit returns a task id, but the first
# `tasks/get` calls can still 404 (the task store is eventually consistent right
# after submit) and a later poll succeeds with the completed answer — a WARM
# logistics deployment took 2 transient 404s, a COLD one takes many more while
# the ~34s p95 cold start runs. So a 404 on a task not yet seen is transient, not
# "submit-only" — ride through it until the task appears or this grace elapses.
#
# TIME, not count, is the signal: at a 1s cadence a cold start would exhaust any
# small retry count long before the task exists, so the window must be wide
# enough to cover a cold start. The distinction from a permanent failure is
# POSITION — the same MethodNotFoundError means "not visible yet" right after
# submit and "wrong method, never will be" once we have already read the task
# once (see `_run_leg_async`). Bounded by the leg deadline regardless, so the
# worst case is a cold leg that never appears waiting this long, then degrading
# honestly to a blocking call.
POLL_NOT_FOUND_GRACE_S = 45.0

# The three dispatch modes a caller may ask for. "sync" is today's blocking
# `ask()`; "async" is A2A submit + poll. Kept as strings, not a bool, because
# the honest outcomes are richer than two: a leg can be asked for async and
# fall back to sync because its protocol has no task lifecycle at all (an
# AgentCore or Agent API leg), which is a first-class WS11 finding, not an
# error — so the mode a leg ACTUALLY ran in is recorded per leg, below.
DISPATCH_MODES = ("sync", "async")


def leg_timeout_s() -> float:
    """Read at CALL time, never at import.

    `legs.py` documents why in detail: scripts import this module at the top of
    the file and call load_dotenv() inside main(), so an import-time read sees
    every .env override as absent and the run quietly uses the default. That
    bug cost a whole fan-out run that looked perfect and used the wrong
    targets. Anything env-dependent here has to be resolved when it is USED.
    """
    return float(os.environ.get("A2ALAB_LEG_TIMEOUT_S") or LEG_TIMEOUT_DEFAULT_S)


def poll_interval_s() -> float:
    """Async poll cadence, read at call time for the same reason as above."""
    return float(os.environ.get("A2ALAB_ASYNC_POLL_INTERVAL_S") or POLL_INTERVAL_DEFAULT_S)


def poll_not_found_grace_s() -> float:
    """How long to ride through a not-yet-visible task, read at call time.

    Overridable so tests can shrink the 45s cold-start window to milliseconds —
    the fallback tests would otherwise spin for the full window before degrading.
    """
    return float(os.environ.get("A2ALAB_ASYNC_NOT_FOUND_GRACE_S") or POLL_NOT_FOUND_GRACE_S)


@dataclass
class LegResult:
    leg: Leg
    ok: bool
    text: str = ""
    error: str = ""
    latency_ms: int = 0
    # How this leg ACTUALLY ran, which is not always how it was asked to run.
    # "sync" = blocking ask(); "async" = A2A submit + poll to a terminal state;
    # "async→sync" = async was requested but this leg's protocol has no task
    # lifecycle, so it fell back to a blocking call. The distinction is a WS11
    # measurement, not bookkeeping: it is the per-platform answer to "who
    # actually implements the asynchronous half of A2A".
    dispatch_mode: str = "sync"
    polls: int = 0  # how many tasks/get calls it took to reach terminal (async only)

    def render(self) -> str:
        """One section of the synthesised answer — present either way.

        The header names the unit, the platform, the specific agent and the
        latency, because the orchestrator can only attribute what it was told.
        A bare "Logistics:" gives a model nothing to cite and quietly invites
        it to blend three sources into one voice.
        """
        from orchestration.agents import source_header

        header = source_header(self.leg.role, target=self.leg.target, latency_ms=self.latency_ms)
        if self.ok:
            return f"{header}\n{self.text.strip()}"
        return f"{header}\n[leg unavailable: {self.leg.platform} — {self.error}]"


@dataclass
class FanOutResult:
    task: str
    trace_id: str
    results: list[LegResult] = field(default_factory=list)
    total_ms: int = 0

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def complete(self) -> bool:
        return self.ok_count == len(self.results)

    @property
    def dispatch_summary(self) -> str:
        """One phrase describing how the legs were dispatched — for the coverage
        line and the console badge. Only interesting once any leg ran async, so
        a plain sync run says nothing extra.

        Names WHICH legs ran each way, not just the counts: "which platform
        implements the asynchronous half of A2A" is the WS11 finding, and a bare
        "2 fell back to sync" makes the reader guess which two. Each leg is named
        by its business unit and its target (`config/targets.yaml`), matching the
        section headers in the brief above."""
        modes = [r.dispatch_mode for r in self.results]
        if all(m == "sync" for m in modes):
            return ""

        def _name(r: "LegResult") -> str:
            return f"{r.leg.business_unit} ({r.leg.target})"

        async_legs = [r for r in self.results if r.dispatch_mode == "async"]
        fallback_legs = [r for r in self.results if r.dispatch_mode == "async→sync"]
        total_polls = sum(r.polls for r in self.results)
        parts = [
            f"{len(async_legs)}/{len(modes)} legs async (submit+poll, {total_polls} polls)"
        ]
        if async_legs:
            parts[0] += ": " + ", ".join(_name(r) for r in async_legs)
        if fallback_legs:
            # The honest WS11 finding: an async request a protocol could not
            # serve. Naming the legs stops the run reading as fully asynchronous
            # and says exactly which remotes lack the task lifecycle.
            parts.append(
                f"{len(fallback_legs)} fell back to sync (no A2A task lifecycle): "
                + ", ".join(_name(r) for r in fallback_legs)
            )
        return " · ".join(parts)

    def render(self) -> str:
        """The text handed back to the orchestrating model to synthesise from.

        Deliberately includes the coverage line even when everything worked:
        the orchestrator should always know how many legs it is reasoning over,
        so a degraded run cannot read like a complete one.
        """
        sections = "\n\n".join(r.render() for r in self.results)
        coverage = f"[fan-out coverage: {self.ok_count}/{len(self.results)} legs answered]"
        if self.dispatch_summary:
            coverage += f"\n[dispatch: {self.dispatch_summary}]"
        return f"{sections}\n\n{coverage}"


class AsyncLifecycleUnsupported(Exception):
    """Raised inside `_run_leg_async` when the REMOTE endpoint accepts an async
    submit but will not give the task back through the poll, so there is nothing
    to wait on. Distinct from a leg failure: it means "this platform can't serve
    the asynchronous half", which is a WS11 finding, and the caller responds by
    falling back to a blocking `ask()` (recorded async→sync) rather than
    reporting the leg unavailable.

    Crucially this is NOT "ADK cannot poll." Google documents a get-task method,
    and as of 2026-08-11 fire-then-poll WORKS end-to-end against the managed
    Agent Engine endpoint: the WS11 probe's original "submit-only, cause
    unresolved" verdict was a CLIENT bug, not a platform gap — we sent no
    A2A-Version header, so the 1.0-only handler read the request as 0.3 and
    400'd every poll (plan/03-results.md). Pinning `protocol_version: 1.0` on the
    target (config/targets.yaml) fixed it. This exception is now the residual
    honest-degradation path: it still fires if a DIFFERENT remote takes a submit
    and then genuinely will not serve the task, and it is the SAME degradation
    the AgentCore leg (no task lifecycle at all) already makes up front. The
    client-capability check below cannot catch that: `A2AClient` HAS submit+poll,
    so the leg looks async-capable until the poll comes back unretrievable."""


# The ways a poll can come back "took my submit but won't return the task",
# every one an a2a REST-transport mapping (a2a.utils.errors): a 404 maps to
# MethodNotFoundError (Agent Engine returns its structured TASK_NOT_FOUND inside
# a stringified message the SDK can't structure-match, so it falls to the generic
# 404 → MethodNotFoundError path — this is also the shape a transient
# not-yet-visible task takes right after submit, ridden through in
# `_run_leg_async`), a missing/old A2A-Version 400s as VersionNotSupportedError,
# and a structured task-not-found is TaskNotFoundError. Kept an explicit, NARROW
# tuple on purpose: a genuine TASK_STATE_FAILED, a 500, or an auth error must
# still fail the leg — swallowing those as "unsupported" would hide real
# breakage behind a sync answer. With protocol_version pinned (2026-08-11) the
# Agent Engine legs no longer hit this at all; it remains the honest fallback for
# any remote that takes a submit and then cannot serve the task.
_POLL_UNRETRIEVABLE = (MethodNotFoundError, VersionNotSupportedError, TaskNotFoundError)


def _async_capable(client) -> bool:
    """Does this client implement the A2A asynchronous half (submit + poll)?

    Only `A2AClient` does (WS11). The base contract is `ask()` alone, and the
    rest/mcp/agentcore/agentforce clients have no task lifecycle — so an async
    request on one of those legs is not an error, it is a leg the protocol
    cannot serve asynchronously, and the honest thing is to fall back to a
    blocking call and SAY so. Detected by capability, not isinstance, so a
    future non-A2A client that grows the pair is picked up for free.

    Note this is a CLIENT-shape check, not a platform-behaviour one: a client
    can carry submit+poll and still be pointed at a remote that only honours the
    submit (Agent Engine). That case cannot be known until the poll is tried, so
    it is handled at runtime via `AsyncLifecycleUnsupported`, not here."""
    return callable(getattr(client, "submit", None)) and callable(getattr(client, "poll", None))


async def _run_leg_async(
    client,
    leg: Leg,
    req: AgentRequest,
    *,
    trace_id: str,
    timeout_s: float,
) -> tuple[str, int]:
    """Submit the leg, then poll `tasks/get` until it reaches a terminal state.

    Returns (answer_text, poll_count). Raises on failure/timeout, so the caller's
    existing except-blocks turn it into an unavailable leg with the same
    contract as the sync path. Each submit and each poll is its own trace Hop
    (A2AClient records them), so the console shows the fire, then one hop per
    check-back — and the poll count is the WS11 finding made visible.

    The submit/poll pair is what dissolves the gateway ceiling: the SendMessage
    returns in ~1s carrying only a task id, and no single request is ever held
    open across the agent's actual work (WS11, D47)."""
    try:
        handle = await asyncio.wait_for(client.submit(req), timeout=timeout_s)
    except _POLL_UNRETRIEVABLE as exc:
        # The endpoint has no async submit route at all — nothing was created,
        # so degrade to a blocking call. (Distinct from the poll failure below,
        # a submit-only shape where the remote took the submit but won't serve
        # the task read.)
        raise AsyncLifecycleUnsupported(str(exc)) from exc
    if handle.answered_immediately:
        # The server ignored return_immediately and blocked to completion — a
        # per-platform finding in its own right (it advertises the async half
        # but does not implement it). The answer is already in hand.
        return handle.text, 0

    interval = poll_interval_s()
    grace_s = poll_not_found_grace_s()
    start = time.monotonic()
    deadline = start + timeout_s
    polls = 0
    seen_task = False  # have we ever successfully read this task?
    while True:
        if time.monotonic() >= deadline:
            raise asyncio.TimeoutError(f"async leg not terminal after {timeout_s:.0f}s")
        try:
            # Until we have read the task once, a 404 is the eventually-consistent
            # window, not a failure — tell poll() so the hop records `pending`,
            # not a red ✗ error, for a leg that in fact completes (WS11).
            snapshot = await client.poll(
                handle.task_id, trace_id=trace_id, expect_transient=not seen_task
            )
        except _POLL_UNRETRIEVABLE as exc:
            # A task we HAVE already read, now coming back unretrievable, is the
            # real "submit-only" shape (a remote took the submit but will not
            # serve the task): the endpoint 404s, or 400s with a version
            # mismatch, or reports the task unknown. Degrade to a blocking call
            # so the leg still answers, recorded async→sync.
            #
            # But a task we have NEVER read yet can 404 transiently right after
            # submit — the store is eventually consistent, and a later poll
            # succeeds (measured live 2026-08-11). That is not submit-only, so
            # ride through it while the grace window lasts. The two are the SAME
            # exception class; only the position (never-seen + still early)
            # distinguishes them.
            if not seen_task and (time.monotonic() - start) < grace_s:
                await asyncio.sleep(interval)
                continue
            raise AsyncLifecycleUnsupported(str(exc)) from exc
        seen_task = True
        polls += 1
        if snapshot.done:
            if snapshot.state != "TASK_STATE_COMPLETED":
                raise RuntimeError(snapshot.detail or f"task {snapshot.state}")
            return snapshot.text, polls
        if snapshot.interrupted:
            # INPUT_REQUIRED / AUTH_REQUIRED: the task is parked waiting on a
            # human/credential the fan-out cannot supply, so it will never
            # complete on its own. Report it rather than poll to the deadline.
            raise RuntimeError(f"task interrupted ({snapshot.state}) — needs input we cannot give")
        await asyncio.sleep(interval)


async def _run_leg(
    registry: Registry,
    leg: Leg,
    task: str,
    *,
    caller: str,
    caller_platform: str,
    trace_id: str,
    inbound_depth: int,
    dispatch_mode: str = "sync",
) -> LegResult:
    start = time.perf_counter()
    client = None
    timeout_s = leg_timeout_s()
    # The mode this leg will actually run in, decided once the client is known.
    # Requested-async on a client with no async half degrades to sync, recorded
    # as "async→sync" so the coverage line does not overclaim.
    ran_mode = "sync"
    polls = 0
    try:
        client = registry.client_for(leg.target)
        want_async = dispatch_mode == "async"
        if want_async:
            ran_mode = "async" if _async_capable(client) else "async→sync"
        message, meta = delegation.delegate(
            leg.prompt(task),
            caller=caller,
            platform=caller_platform,
            inbound_depth=inbound_depth,
            trace_id=trace_id,
        )
        req = AgentRequest(message=message, trace_id=trace_id, metadata=meta)
        text = None
        if ran_mode == "async":
            # submit() and poll() emit their own hops, so no wrapping Hop here —
            # a fan-out leg Hop around them would double-count the leg.
            try:
                text, polls = await _run_leg_async(
                    client, leg, req, trace_id=trace_id, timeout_s=timeout_s
                )
                text = (text or "").strip()
            except AsyncLifecycleUnsupported:
                # The remote took the submit but will not serve the poll — a
                # genuinely submit-only endpoint (WS11). NB the Agent Engine legs
                # no longer reach here: their poll failure was our missing
                # A2A-Version header, fixed by pinning protocol_version on the
                # target (2026-08-11). This is the SAME honest finding the
                # AgentCore/Agent-API legs make up front — the only difference is
                # it can't be known until the poll is tried — so record it
                # identically (async→sync) and drop through to the blocking path
                # below, which answers the leg with `ask()`.
                ran_mode = "async→sync"
                polls = 0
        if text is None:
            with Hop(
                trace_id,
                source=caller,
                target=leg.target,
                protocol=registry.get(registry.resolve_name(leg.target)).protocol,
                transport_detail=f"fan-out leg: {leg.role}"
                + (" (async→sync fallback)" if ran_mode == "async→sync" else ""),
                request_payload={"role": leg.role, "message": message},
            ) as hop:
                resp = await asyncio.wait_for(client.ask(req), timeout=timeout_s)
                text = (resp.text or "").strip()
                hop.response_payload = {"role": leg.role, "text": text}
        latency = int((time.perf_counter() - start) * 1000)
        if not text:
            # A completed call with no content is a failure, not a success —
            # the same rule the A2A client learned the hard way.
            return LegResult(
                leg, False, error="empty answer", latency_ms=latency, dispatch_mode=ran_mode
            )
        return LegResult(
            leg, True, text=text, latency_ms=latency, dispatch_mode=ran_mode, polls=polls
        )
    except asyncio.TimeoutError:
        return LegResult(
            leg,
            False,
            error=f"timed out after {timeout_s:.0f}s",
            latency_ms=int((time.perf_counter() - start) * 1000),
            dispatch_mode=ran_mode,
            polls=polls,
        )
    except Exception as exc:
        return LegResult(
            leg,
            False,
            error=f"{type(exc).__name__}: {exc}"[:160],
            latency_ms=int((time.perf_counter() - start) * 1000),
            dispatch_mode=ran_mode,
            polls=polls,
        )
    finally:
        if client is not None:
            await client.aclose()


async def run_one(
    role: str,
    task: str,
    *,
    caller: str,
    caller_platform: str,
    scenario: str = "supplier-disruption",
    trace_id: str,
    registry: Registry | None = None,
    inbound_depth: int = 0,
    dispatch_mode: str = "sync",
) -> LegResult:
    """One leg, on its own. The seam the remote MCP fan-out server calls.

    `dispatch` runs the fan-out HERE and owns the concurrency; this runs a
    single leg and lets someone else decide what runs beside it. That someone
    is the orchestrating MODEL: as three separate MCP tools, the legs are
    scheduled on the orchestration layer rather than by a host-side
    `asyncio.gather`, which is the whole question WS7 item 4 exists to answer —
    does the model actually fan out, and in what order.

    `trace_id` is REQUIRED, not defaulted. Each tool call is a separate process
    (a separate Lambda invocation, even), so nothing ambient connects them; the
    id has to be passed in or the run fragments into three unrelated traces and
    the join-rate measurement is meaningless. Making it a keyword with no
    default means that mistake is a TypeError rather than a silently useless
    trace.

    Never raises for a leg failure — same contract as `dispatch`, so a failed
    leg comes back as a LegResult whose `render()` names what is missing.
    """
    legs = {leg.role: leg for leg in legs_for(scenario)}
    if role not in legs:
        raise KeyError(f"unknown leg role '{role}' — known: {', '.join(sorted(legs))}")
    return await _run_leg(
        registry or Registry.load(),
        legs[role],
        task,
        caller=caller,
        caller_platform=caller_platform,
        trace_id=trace_id,
        inbound_depth=inbound_depth,
        dispatch_mode=dispatch_mode,
    )


async def dispatch(
    task: str,
    *,
    caller: str,
    caller_platform: str,
    scenario: str = "supplier-disruption",
    trace_id: str | None = None,
    registry: Registry | None = None,
    inbound_depth: int = 0,
    roles: set[str] | None = None,
    dispatch_mode: str = "sync",
) -> FanOutResult:
    """Run every leg of `scenario` concurrently and collect the results.

    `dispatch_mode` selects how each leg's remote call is made: "sync" is the
    blocking `ask()` every run used before WS11; "async" fires the A2A
    submit + poll lifecycle on legs whose protocol implements it, and falls
    back to sync (recorded per leg) on legs that do not. The two A2A legs
    (adk, foundry) run the real lifecycle; the AgentCore comms leg falls back —
    which is itself the per-platform finding WS11 set out to take.

    Never raises for a leg failure — a failed leg is data, and the caller gets
    a result whose `render()` names what is missing. Raises only if the
    scenario itself is unknown, which is a programming error.
    """
    legs = legs_for(scenario)
    if roles:
        # Subsetting is a caller convenience (scripts/run_fanout.py --legs), not
        # a behaviour change: the production path passes nothing and always runs
        # every leg, so the thing under test is the thing that ships.
        legs = tuple(leg for leg in legs if leg.role in roles)
    registry = registry or Registry.load()
    trace_id = trace_id or new_trace_id()
    start = time.perf_counter()

    # gather with return_exceptions so one leg blowing up in an unexpected way
    # still cannot take the fan-out with it; _run_leg already catches the
    # expected cases and turns them into LegResults.
    raw = await asyncio.gather(
        *(
            _run_leg(
                registry,
                leg,
                task,
                caller=caller,
                caller_platform=caller_platform,
                trace_id=trace_id,
                inbound_depth=inbound_depth,
                dispatch_mode=dispatch_mode,
            )
            for leg in legs
        ),
        return_exceptions=True,
    )
    results: list[LegResult] = []
    for leg, item in zip(legs, raw):
        if isinstance(item, LegResult):
            results.append(item)
        else:
            results.append(LegResult(leg, False, error=f"{type(item).__name__}: {item}"[:160]))

    return FanOutResult(
        task=task,
        trace_id=trace_id,
        results=results,
        total_ms=int((time.perf_counter() - start) * 1000),
    )
