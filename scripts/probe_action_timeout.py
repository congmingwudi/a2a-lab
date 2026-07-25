"""M6: find the REAL Agentforce action timeout by injecting downstream delay.

The lab's Path A budget chain (plan/01-architecture.md) was built on a
*reported* ~60s Agentforce action timeout that nobody had measured. This
probe measures it, using the injection point the bridge already carries
(`A2ALAB_DELAY_S`, src/bridge/app.py): the bridge sleeps N seconds before
forwarding, so from Salesforce's side the custom action simply takes N + the
remote agent's own time to answer.

    uv run python scripts/probe_action_timeout.py                 # 10/30/60/90
    uv run python scripts/probe_action_timeout.py --delays 45 55  # bisect

Each probe drives the real twin through the real chain — Agent API -> the
Agentforce agent -> Apex -> Named Credential -> tunnel -> bridge -> Claude —
because a timeout measured anywhere else is a measurement of the harness
(D15: experiments enter through the real platform agent).

The script OWNS port 8100 while it runs: it stops whatever bridge is there,
runs its own with the delay set, and restores a plain bridge at the end. Run
it against an otherwise-idle stack.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from interop.models import AgentRequest, new_trace_id  # noqa: E402

BRIDGE_PORT = 8100
# The console's own DEFAULT_QUESTION (src/console/app.py), verbatim and for a
# reason: it is the phrasing proven to drive the twin down BOTH steps of its
# script — the Apex CRM action first, then ask_external_researcher, the
# delegating action whose timeout we are measuring. Improvised phrasings do
# not reliably get there; the first attempt at this probe used one and the
# agent answered from nothing at all (`"result":[]` — no actions invoked,
# both sections fabricated), which measures the model's willingness to
# confabulate rather than the platform's timeout.
QUESTION = (
    "Tell me what you know about account Omega, Inc. — a short summary of their current state."
)


def _listeners() -> list[str]:
    out = subprocess.run(
        ["lsof", "-ti", f"tcp:{BRIDGE_PORT}", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
    )
    return [p for p in out.stdout.split() if p]


def _stop_bridge() -> None:
    for pid in _listeners():
        try:
            os.kill(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
    for _ in range(20):
        if not _listeners():
            return
        time.sleep(0.25)
    for pid in _listeners():
        try:
            os.kill(int(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    time.sleep(0.5)


def _start_bridge(delay_s: float) -> subprocess.Popen:
    """A bridge whose forward is delayed by `delay_s` before it calls out.

    Pinned to A2ALAB_MODE=local on purpose: under `hosted` the bridge would
    resolve claude-rest to the AgentCore runtime, whose ~56s cold start is the
    same order as the timeout being measured and would confound every row.
    The probe wants the injected delay to be the only variable, so it talks to
    the warm local server.
    """
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "A2ALAB_DELAY_S": str(delay_s),
        "A2ALAB_MODE": "local",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "bridge", "--port", str(BRIDGE_PORT)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        if _listeners():
            time.sleep(0.5)  # let uvicorn finish binding before the first call
            return proc
        time.sleep(0.25)
    raise RuntimeError("bridge did not bind")


def _settle(window_start: float, delay_s: float) -> list[dict]:
    """Wait for the bridge leg to finish before tearing the bridge down.

    When Agentforce abandons a slow action it answers the user immediately,
    while the bridge is still mid-flight. Killing the bridge at that moment
    destroys the very evidence the probe needs — the first 90s run reported
    "action never fired" purely because teardown beat the hop to disk. So
    after the turn returns we wait out the remaining injected sleep plus
    headroom for the Claude leg, and only then stop the process.
    """
    deadline = window_start + delay_s + 60
    while time.time() < deadline:
        hops = _bridge_hops(window_start, time.time() + 1)
        if hops:
            return hops
        time.sleep(1)
    return _bridge_hops(window_start, time.time() + 1)


async def probe(delay_s: float) -> dict:
    from platforms.agentforce.client import AgentforceClient

    _stop_bridge()
    proc = _start_bridge(delay_s)
    client = AgentforceClient.from_env()
    trace_id = new_trace_id()
    window_start = time.time()
    start = time.perf_counter()
    result: dict
    try:
        resp = await client.ask(
            AgentRequest(
                message=QUESTION,
                session_id=f"m6-probe-{int(delay_s)}s",
                trace_id=trace_id,
            )
        )
        result = {
            "delay_s": delay_s,
            "wall_s": round(time.perf_counter() - start, 1),
            "outcome": "answered",
            "text": (resp.text or "").strip(),
            "trace_id": trace_id,
        }
    except Exception as exc:
        result = {
            "delay_s": delay_s,
            "wall_s": round(time.perf_counter() - start, 1),
            "outcome": f"{type(exc).__name__}",
            "text": str(exc)[:300],
            "trace_id": trace_id,
        }
    result["hops"] = _settle(window_start, delay_s)
    await client.aclose()
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    return result


def _bridge_hops(since: float, until: float) -> list[dict]:
    """Bridge hops recorded in a time window.

    The Apex invocable mints its OWN trace id per callout, so the Salesforce
    leg cannot be joined to the Agent API turn by id — the console correlates
    it by time window and so do we. This is the probe's ground truth: reply
    text can be fabricated (and was, on the first attempt), but a bridge hop
    is a recorded wire event that only exists if the action really fired.
    """
    path = ROOT / "traces" / f"{time.strftime('%Y-%m-%d')}.jsonl"
    if not path.exists():
        return []
    hops = []
    for line in path.read_text().splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("target") == "bridge" and since <= float(e.get("ts", 0)) <= until:
            hops.append(e)
    return hops


# When the action times out, the twin still writes its "External market
# research (from the Claude research agent):" heading and fills it with a
# graceful apology. So the HEADING proves nothing — an early version of this
# probe read its presence as success and scored two timeouts as passes. What
# distinguishes them is the section BODY.
ABSENT_MARKERS = (
    "temporarily unavailable",
    "not available",
    "unavailable",
    "try again",
    "could not",
    "couldn't",
    "no external",
)


def _external_section(text: str) -> str:
    i = text.lower().find("external market research")
    return "" if i < 0 else text[i:]


def _classify(r: dict) -> str:
    """Did the ACTION survive, or did the platform give up on it?

    The Agent API answers 200 either way — when the action times out the agent
    still replies, just without the delegated content — so the transport says
    nothing. Read the bridge hop first (did the action fire, and did it get an
    answer?), then the reply text for what the user actually saw.
    """
    if r["outcome"] != "answered":
        return f"transport error ({r['outcome']})"
    hops = r.get("hops") or []
    text = r["text"]
    section = _external_section(text)
    body = section.split(":", 1)[1] if ":" in section else section
    external = bool(section) and not any(m in body.lower() for m in ABSENT_MARKERS)
    if not hops:
        return "action never fired — no bridge hop (agent answered without delegating)"
    hop_status = hops[-1].get("status")
    # The bridge's Hop starts AFTER the injected sleep (src/bridge/app.py), so
    # the recorded latency is the Claude leg alone; the action's true duration
    # is the sleep plus that.
    leg_s = round((hops[-1].get("latency_ms") or 0) / 1000, 1)
    action_s = round(r["delay_s"] + leg_s, 1)
    if hop_status != "ok":
        return f"bridge leg failed after {leg_s}s ({hop_status})"
    if external:
        return f"action returned in ~{action_s}s — answer used"
    return f"action returned in ~{action_s}s but Agentforce had already given up — answer DROPPED"


async def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--delays", type=float, nargs="+", default=[10, 30, 60, 90])
    args = ap.parse_args()

    print(f"M6 action-timeout probe — delays {args.delays}\n")
    rows = []
    for d in args.delays:
        print(f"  injecting {d:.0f}s ...", flush=True)
        r = await probe(d)
        r["verdict"] = _classify(r)
        rows.append(r)
        print(f"    {r['wall_s']:>6.1f}s wall — {r['verdict']}")
        print(f"    trace {r['trace_id']}")
        snippet = r["text"].replace("\n", " ")[:160]
        print(f"    {snippet}\n", flush=True)

    print("\n| Injected delay | Wall time | Agentforce action outcome |")
    print("|---|---|---|")
    for r in rows:
        print(f"| {r['delay_s']:.0f}s | {r['wall_s']:.1f}s | {r['verdict']} |")

    # Leave the stack as we found it: a plain bridge, no injected delay.
    print("\nrestoring a clean bridge on :8100 ...")
    _stop_bridge()
    _start_bridge(0)
    print("done — bridge listening with no delay.")


if __name__ == "__main__":
    asyncio.run(main())
