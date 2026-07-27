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


def leg_timeout_s() -> float:
    """Read at CALL time, never at import.

    `legs.py` documents why in detail: scripts import this module at the top of
    the file and call load_dotenv() inside main(), so an import-time read sees
    every .env override as absent and the run quietly uses the default. That
    bug cost a whole fan-out run that looked perfect and used the wrong
    targets. Anything env-dependent here has to be resolved when it is USED.
    """
    return float(os.environ.get("A2ALAB_LEG_TIMEOUT_S") or LEG_TIMEOUT_DEFAULT_S)


@dataclass
class LegResult:
    leg: Leg
    ok: bool
    text: str = ""
    error: str = ""
    latency_ms: int = 0

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

    def render(self) -> str:
        """The text handed back to the orchestrating model to synthesise from.

        Deliberately includes the coverage line even when everything worked:
        the orchestrator should always know how many legs it is reasoning over,
        so a degraded run cannot read like a complete one.
        """
        sections = "\n\n".join(r.render() for r in self.results)
        coverage = f"[fan-out coverage: {self.ok_count}/{len(self.results)} legs answered]"
        return f"{sections}\n\n{coverage}"


async def _run_leg(
    registry: Registry,
    leg: Leg,
    task: str,
    *,
    caller: str,
    caller_platform: str,
    trace_id: str,
    inbound_depth: int,
) -> LegResult:
    start = time.perf_counter()
    client = None
    timeout_s = leg_timeout_s()
    try:
        client = registry.client_for(leg.target)
        message, meta = delegation.delegate(
            leg.prompt(task),
            caller=caller,
            platform=caller_platform,
            inbound_depth=inbound_depth,
            trace_id=trace_id,
        )
        req = AgentRequest(message=message, trace_id=trace_id, metadata=meta)
        with Hop(
            trace_id,
            source=caller,
            target=leg.target,
            protocol=registry.get(registry.resolve_name(leg.target)).protocol,
            transport_detail=f"fan-out leg: {leg.role}",
            request_payload={"role": leg.role, "message": message},
        ) as hop:
            resp = await asyncio.wait_for(client.ask(req), timeout=timeout_s)
            text = (resp.text or "").strip()
            hop.response_payload = {"role": leg.role, "text": text}
        latency = int((time.perf_counter() - start) * 1000)
        if not text:
            # A completed call with no content is a failure, not a success —
            # the same rule the A2A client learned the hard way.
            return LegResult(leg, False, error="empty answer", latency_ms=latency)
        return LegResult(leg, True, text=text, latency_ms=latency)
    except asyncio.TimeoutError:
        return LegResult(
            leg,
            False,
            error=f"timed out after {timeout_s:.0f}s",
            latency_ms=int((time.perf_counter() - start) * 1000),
        )
    except Exception as exc:
        return LegResult(
            leg,
            False,
            error=f"{type(exc).__name__}: {exc}"[:160],
            latency_ms=int((time.perf_counter() - start) * 1000),
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
) -> FanOutResult:
    """Run every leg of `scenario` concurrently and collect the results.

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
