"""Fire one fan-out run and report what came back (WS8).

    uv run python scripts/run_fanout.py
    uv run python scripts/run_fanout.py --task "A typhoon has closed Kaohsiung."
    uv run python scripts/run_fanout.py --legs exposure,commercial

The orchestrator's own model is not involved here — this drives the dispatch
layer directly so the FAN-OUT can be measured without waiting on a managed
session. The two orchestrator variants (CMA and ADK) call the same
`orchestration.dispatch`, so what this proves about the legs holds for both.

Every leg is a real delegation: D27 rider at depth 1, D34 lab-trace line, one
Hop per leg under a single trace id. That trace id is printed at the end — feed
it to the console or the obs harvest to measure how many of the legs can be
joined back to this run from the platforms' own logs, which is the number WS8
exists to produce.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

from interop.models import new_trace_id  # noqa: E402
from orchestration import dispatch, legs_for  # noqa: E402

DEFAULT_TASK = (
    "A port strike has halted all container traffic through Rotterdam as of "
    "06:00 today. Assess the impact for our EU manufacturing customers."
)


async def _run_cma(args, variant: str = "tool") -> int:
    """The Managed Agents orchestrator variants: the MODEL decides to fan out.

    Everything the legs do is identical to the direct path — same dispatch,
    same rider, same partial-failure contract. What is added is the platform's
    own orchestration layer on top, which is the thing being compared.

    `tool` runs the fan-out host-side behind one custom tool; `mcp` gives the
    model three separate remote tools and lets it schedule them. The call path
    printed at the end is the measurement that separates the two.
    """
    from orchestration.cma import CmaOrchestrator

    trace_id = new_trace_id()
    label = (
        "Anthropic Managed Agents (custom tool, host-side fan-out)"
        if variant == "tool"
        else "Anthropic Managed Agents (remote MCP, model-scheduled fan-out)"
    )
    print(f"orchestrator  {label}")
    print(f"trace         {trace_id}")
    print(f"task          {args.task}\n")

    result = await CmaOrchestrator(variant=variant).run(args.task, trace_id=trace_id)
    fan = result["fanout"]
    path = result["call_path"]

    if variant == "mcp":
        # There is no host-side FanOutResult here — the legs ran inside the
        # Lambda. What the model DID is the observable, so print it.
        print(f"call path {path.render()}")
        print(f"units     {len(path.calls)} tool call(s) over {path.turns} model turn(s)")
    elif fan is None:
        print("!! the orchestrator never called consult_business_units")
        print("   (that is a finding, not a crash — the model chose not to fan out)")
    else:
        for leg_result in fan.results:
            status = "ok  " if leg_result.ok else "FAIL"
            print(f"[{status}] {leg_result.leg.role:<15} {leg_result.latency_ms:>6} ms")
        print(f"\ncoverage  {fan.ok_count}/{len(fan.results)} legs answered")

    print(f"wall      {result['wall_ms']} ms")
    print(f"session   {result['session_id']}")
    print(f"trace     {result['trace_id']}")
    print("\n--- orchestrator's brief ---\n")
    print(result["brief"] or "(empty)")

    # Same rule as the direct path: a short run must not exit 0. For the MCP
    # variant "short" means the model did not consult every unit — which is
    # exactly the failure mode being measured, so it must be visible in the
    # exit code rather than only in the prose.
    if variant == "mcp":
        return 0 if len({c.name for c in path.calls}) == 3 else 1
    return 0 if (fan and fan.complete) else 1


async def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default=DEFAULT_TASK)
    ap.add_argument(
        "--legs",
        default="",
        help="comma-separated leg roles to run (default: all)",
    )
    ap.add_argument("--caller", default="a2alab-supply-orchestrator")
    ap.add_argument("--caller-platform", default="claude")
    ap.add_argument(
        "--orchestrator",
        default="none",
        choices=("none", "cma", "cma-mcp"),
        help=(
            "none = drive the dispatch layer directly (measures the legs). "
            "cma = the Managed Agents orchestrator with one host-side custom "
            "tool (the control). cma-mcp = the same agent with three remote MCP "
            "tools, where the MODEL schedules the units."
        ),
    )
    args = ap.parse_args()

    if args.orchestrator == "cma":
        return await _run_cma(args, variant="tool")
    if args.orchestrator == "cma-mcp":
        return await _run_cma(args, variant="mcp")

    all_legs = legs_for()  # resolved now, so .env overrides apply
    wanted = {r.strip() for r in args.legs.split(",") if r.strip()}
    if wanted:
        unknown = wanted - {leg.role for leg in all_legs}
        if unknown:
            print(f"unknown leg(s): {', '.join(sorted(unknown))}")
            return 2

    trace_id = new_trace_id()
    print(f"fan-out  trace {trace_id}")
    print(f"task     {args.task}\n")
    for leg in all_legs:
        marker = " " if not wanted or leg.role in wanted else "-"
        print(f"  [{marker}] {leg.role:<15} {leg.business_unit:<22} -> {leg.target}")
    print()

    result = await dispatch(
        args.task,
        caller=args.caller,
        caller_platform=args.caller_platform,
        trace_id=trace_id,
        roles=wanted or None,
    )

    for leg_result in result.results:
        status = "ok  " if leg_result.ok else "FAIL"
        print(
            f"[{status}] {leg_result.leg.role:<15} {leg_result.latency_ms:>6} ms  "
            f"{leg_result.leg.target}"
        )
        body = leg_result.text if leg_result.ok else leg_result.error
        for line in (body or "").strip().splitlines()[:6]:
            print(f"         {line[:110]}")
        print()

    print("-" * 72)
    print(f"coverage  {result.ok_count}/{len(result.results)} legs answered")
    print(f"wall      {result.total_ms} ms")
    print(f"trace     {trace_id}")
    print("\nSynthesised hand-off to the orchestrator model:\n")
    print(result.render())
    # A partial fan-out is a real outcome, not a crash — but it must not exit 0,
    # or a scheduled run would report success while silently short.
    return 0 if result.complete else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
