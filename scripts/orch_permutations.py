"""Permutation harness for the three supplier-disruption ORCHESTRATORS.

Runs every radio-button permutation of each orchestrator through the SAME seams
the console `/api/run` path uses, captures the final response that would reach
the operator, and classifies it PASS / GAP / FAIL. This is the "does every
permutation actually produce a real orchestration answer?" check — the thing a
green unit suite cannot tell you, because these hops cross four live platforms.

  CMA  (Anthropic Managed Agents host-side / remote-MCP fan-out)
        variant ∈ {tool, mcp} × dispatch ∈ {sync, async}   (async only under tool)
  ADK  (Vertex AI Agent Engine, SequentialAgent[ParallelAgent→synthesiser])
        dispatch ∈ {sync, async}   — driven fire-then-poll (submit + poll), never
        a blocking ask() (a blocking message:send runs the graph inline and 400s
        past ~105s; WS11)
  AF   (Agentforce orchestrator, Agent Script)
        topology ∈ {delegated, serial} × dispatch ∈ {sync, async}
        (dispatch only meaningful under delegated)

LIVE: hits real platforms. Slow (~2 min per ADK run). Operator-run, not part of
`uv run pytest`. Full per-run briefs are written to tmp-docs/orch_perm_out/ (a
gitignored scratch dir, never surfaced) so a failing permutation's brief and
trace id are there to investigate.

    PYTHONPATH=src uv run python scripts/orch_permutations.py [all|cma|adk|af]
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from interop import af_channel  # noqa: E402
from interop.models import AgentRequest, new_trace_id  # noqa: E402
from interop.registry import Registry  # noqa: E402

QUESTION = (
    "A port strike has halted all container traffic through Rotterdam as of "
    "06:00 today. Assess the impact for our EU manufacturing customers."
)

# Where the ADK submit+poll loop gives up. Sync was measured ~134s against WARM
# leg targets; since defaulting each leg to its own DEDICATED agent
# (adk-logistics-a2a, foundry-commercial-a2a — commit f554954) those targets are
# exercised far less often than the shared research agents they replaced, so
# they run cold far more often — and foundry-commercial-a2a has no `warmup: true`
# path in the operator panel. A cold orchestrator container stacked with 2-3
# concurrently-cold legs can legitimately clear 210s on plain SYNC leg dispatch.
# 300s keeps real margin over that chain; it does not help async-internal, which
# may never terminate regardless of budget. Keep equal to the console's pollRun
# deadlineMs (src/console/static/index.html).
ADK_POLL_DEADLINE_S = 300.0
ADK_POLL_INTERVAL_S = 4.0

OUT_DIR = Path(__file__).resolve().parent.parent / "tmp-docs" / "orch_perm_out"


def _classify(text: str) -> str:
    """PASS = a real brief; GAP = a brief that reports missing legs (a valid
    partial-failure outcome, not a harness failure); FAIL = empty/error."""
    t = (text or "").strip()
    if not t or t.startswith("(empty"):
        return "FAIL"
    low = t.lower()
    if "[leg unavailable" in low or "gap" in low.split("\n")[0].lower():
        return "GAP"
    return "PASS"


def _coverage_line(brief: str) -> str:
    gaps = (brief or "").count("[leg unavailable")
    return f"{max(0, 3 - gaps)}/3 answered"


async def run_cma(variant: str, dispatch: str) -> dict:
    from orchestration.cma import CmaOrchestrator, OrchestratorNotProvisioned

    trace_id = new_trace_id()
    try:
        orch = CmaOrchestrator(variant=variant, dispatch_mode=dispatch)
    except OrchestratorNotProvisioned as exc:
        return {"skip": f"not provisioned: {exc}", "trace_id": trace_id}
    result = await orch.run(QUESTION, trace_id=trace_id)
    brief = result.get("brief") or ""
    fan = result.get("fanout")
    cov = f"{fan.ok_count}/{len(fan.results)} answered" if fan else "(no fan-out result)"
    return {
        "text": brief,
        "trace_id": result.get("trace_id", trace_id),
        "coverage": cov,
        "wall_ms": result.get("wall_ms"),
    }


async def run_adk(dispatch: str) -> dict:
    reg = Registry.load("config/targets.yaml")
    client = reg.client_for("adk-orchestrator-a2a", exact=True)
    # Submit must absorb a cold-start (Agent Engine scales to zero); the console
    # runs it under the 110s target timeout / 120s ALB. A WARM submit returns in
    # ~1-2s (return_immediately honored) — the log below tells us which we got.
    client.timeout = 115.0
    trace_id = new_trace_id()
    req = AgentRequest(message=QUESTION, trace_id=trace_id)
    req.metadata["dispatch_mode"] = dispatch
    t0 = time.perf_counter()
    handle = await client.submit(req)
    print(
        f"    submit returned in {handle.submit_ms} ms · state={handle.state} "
        f"· answered_immediately={handle.answered_immediately}",
        flush=True,
    )
    if handle.answered_immediately and handle.text.strip():
        return {
            "text": handle.text,
            "trace_id": trace_id,
            "coverage": _coverage_line(handle.text),
            "wall_ms": int((time.perf_counter() - t0) * 1000),
        }
    # Fire-then-poll, riding the eventually-consistent 404s (WS11).
    while time.perf_counter() - t0 < ADK_POLL_DEADLINE_S:
        await asyncio.sleep(ADK_POLL_INTERVAL_S)
        try:
            snap = await client.poll(handle.task_id, trace_id=trace_id, expect_transient=True)
        except Exception:
            continue  # flapping 404 — keep polling
        if snap.done:
            if snap.state == "TASK_STATE_FAILED":
                return {
                    "text": "",
                    "trace_id": trace_id,
                    "error": snap.detail or "task failed",
                    "wall_ms": int((time.perf_counter() - t0) * 1000),
                }
            return {
                "text": snap.text,
                "trace_id": trace_id,
                "coverage": _coverage_line(snap.text),
                "wall_ms": int((time.perf_counter() - t0) * 1000),
            }
    return {
        "text": "",
        "trace_id": trace_id,
        "error": f"no terminal task within {ADK_POLL_DEADLINE_S:.0f}s "
        "(async internal dispatch overruns Agent Engine's task budget)",
        "wall_ms": int((time.perf_counter() - t0) * 1000),
    }


async def run_af(topology: str, dispatch: str) -> dict:
    reg = Registry.load("config/targets.yaml")
    client = reg.client_for("agentforce-orchestrator-rest")
    trace_id = new_trace_id()
    message = QUESTION + af_channel.topology_block(topology)
    if topology == "delegated":
        message += af_channel.dispatch_block(dispatch)
    req = AgentRequest(message=message, trace_id=trace_id)
    resp = await client.ask(req)
    return {
        "text": resp.text,
        "trace_id": trace_id,
        "coverage": _coverage_line(resp.text),
        "wall_ms": resp.latency_ms,
    }


# (label, coroutine factory) for each permutation.
def permutations(which: str):
    perms: list[tuple[str, str, callable]] = []
    if which in ("all", "cma"):
        perms += [
            ("cma", "tool · sync", lambda: run_cma("tool", "sync")),
            ("cma", "tool · async", lambda: run_cma("tool", "async")),
            ("cma", "mcp", lambda: run_cma("mcp", "sync")),
        ]
    if which in ("all", "adk"):
        perms += [
            ("adk", "sync", lambda: run_adk("sync")),
            ("adk", "async", lambda: run_adk("async")),
        ]
    if which in ("all", "af"):
        perms += [
            ("af", "delegated · sync", lambda: run_af("delegated", "sync")),
            ("af", "delegated · async", lambda: run_af("delegated", "async")),
            ("af", "serial", lambda: run_af("serial", "sync")),
        ]
    return perms


async def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    reg = Registry.load("config/targets.yaml")
    print(f"=== orchestrator permutation harness · scope={which} · mode={reg.mode} ===\n")
    rows = []
    for orch, label, factory in permutations(which):
        tag = f"{orch}/{label}"
        print(f"--- {tag} ---", flush=True)
        t0 = time.perf_counter()
        try:
            out = await factory()
        except Exception as exc:
            dt = time.perf_counter() - t0
            traceback.print_exc()
            rows.append((tag, "FAIL", f"{type(exc).__name__}: {str(exc)[:80]}", "", f"{dt:.0f}s"))
            continue
        dt = time.perf_counter() - t0
        if out.get("skip"):
            rows.append((tag, "SKIP", out["skip"][:80], out.get("trace_id", "")[:12], f"{dt:.0f}s"))
            print(f"  SKIP: {out['skip']}\n")
            continue
        if out.get("error"):
            rows.append(
                (tag, "FAIL", out["error"][:80], out.get("trace_id", "")[:12], f"{dt:.0f}s")
            )
            print(f"  FAIL in {dt:.0f}s: {out['error']}\n")
            continue
        verdict = _classify(out["text"])
        cov = out.get("coverage", "")
        tid = out.get("trace_id", "")
        rows.append((tag, verdict, cov, tid[:12], f"{dt:.0f}s"))
        # Dump the full brief for investigation (scratch, never surfaced).
        (OUT_DIR / f"{orch}_{label.replace(' · ', '_').replace(' ', '')}.md").write_text(
            f"# {tag}\ntrace={tid} coverage={cov} wall={dt:.0f}s\n\n{out['text']}\n"
        )
        print(f"  {verdict} in {dt:.0f}s · {cov} · trace {tid[:12]}")
        print(f"  {out['text'].strip()[:200].replace(chr(10), ' ')}…\n", flush=True)

    print("\n===== SUMMARY =====")
    print(f"{'permutation':<26}{'verdict':<8}{'coverage / note':<48}{'trace':<14}{'wall'}")
    for tag, verdict, note, tid, wall in rows:
        print(f"{tag:<26}{verdict:<8}{note[:46]:<48}{tid:<14}{wall}")
    bad = [r for r in rows if r[1] == "FAIL"]
    print(f"\n{len(rows)} permutations · {len(bad)} FAIL")
    print(f"full briefs: {OUT_DIR}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
