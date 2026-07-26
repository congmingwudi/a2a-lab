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
LEG_TIMEOUT_S = 120.0


@dataclass
class LegResult:
    leg: Leg
    ok: bool
    text: str = ""
    error: str = ""
    latency_ms: int = 0

    def render(self) -> str:
        """One section of the synthesised answer — present either way."""
        header = f"{self.leg.business_unit} ({self.leg.platform}):"
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
            resp = await asyncio.wait_for(client.ask(req), timeout=LEG_TIMEOUT_S)
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
            error=f"timed out after {LEG_TIMEOUT_S:.0f}s",
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


async def dispatch(
    task: str,
    *,
    caller: str,
    caller_platform: str,
    scenario: str = "supplier-disruption",
    trace_id: str | None = None,
    registry: Registry | None = None,
    inbound_depth: int = 0,
) -> FanOutResult:
    """Run every leg of `scenario` concurrently and collect the results.

    Never raises for a leg failure — a failed leg is data, and the caller gets
    a result whose `render()` names what is missing. Raises only if the
    scenario itself is unknown, which is a programming error.
    """
    legs = legs_for(scenario)
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
