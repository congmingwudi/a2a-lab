"""Managed vs self-hosted Claude latency (WS1 item 5 / plan/03-results.md).

The lab runs ONE Claude adapter behind three different hostings, which is the
whole point of the comparison — nothing but the hosting changes:

  managed        Anthropic Managed Agents (CLAUDE_BACKEND=managed), the
                 default; sessions are provisioned per conversation, so the
                 first turn pays for container start and later turns do not.
  sdk-local      claude-agent-sdk in a long-running local server — the warm
                 self-hosted floor, and the baseline the other two are read
                 against.
  sdk-agentcore  the same sdk backend containerized on Bedrock AgentCore
                 (D26), i.e. self-hosted latency once a network and a
                 serverless runtime are in the path.

    uv run python scripts/probe_backend_latency.py            # 5 runs each
    uv run python scripts/probe_backend_latency.py --runs 3

`managed` is measured twice on purpose: `first (cold session)` uses a fresh
session id per run so every run pays provisioning, and `follow-up (warm
session)` reuses one session so none do. That gap is the number Path A's
timeout budget actually has to survive.

The sdk-local column needs a server this script starts itself (the stack's
:8001 runs whatever CLAUDE_BACKEND says, usually managed), so it binds a
throwaway port rather than disturbing the running stack.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from interop.clients.rest import RestClient  # noqa: E402
from interop.models import AgentRequest, new_trace_id  # noqa: E402
from interop.registry import Registry  # noqa: E402

SDK_PORT = 8051
QUESTION = (
    "In two sentences: what is the difference between the MCP and A2A "
    "protocols for agent interoperability?"
)


def _p95(values: list[int]) -> int:
    return sorted(values)[min(len(values) - 1, math.ceil(len(values) * 0.95) - 1)]


def _listeners(port: int) -> list[str]:
    out = subprocess.run(
        ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True
    )
    return [p for p in out.stdout.split() if p]


def _start_sdk_server() -> subprocess.Popen:
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "CLAUDE_BACKEND": "sdk",
        "A2ALAB_MODE": "local",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "platforms.claude", "--protocol", "rest", "--port", str(SDK_PORT)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(80):
        if _listeners(SDK_PORT):
            time.sleep(1.0)
            return proc
        time.sleep(0.25)
    raise RuntimeError(f"sdk server did not bind on :{SDK_PORT}")


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    for pid in _listeners(SDK_PORT):
        try:
            os.kill(int(pid), signal.SIGKILL)
        except (ProcessLookupError, ValueError):
            pass


async def _time_runs(make_client, runs: int, session_of) -> tuple[list[int], str]:
    """Latencies for `runs` asks. `session_of(i)` decides cold vs warm."""
    latencies: list[int] = []
    note = ""
    client = make_client()
    try:
        for i in range(runs):
            req = AgentRequest(message=QUESTION, session_id=session_of(i), trace_id=new_trace_id())
            start = time.perf_counter()
            try:
                resp = await client.ask(req)
                latencies.append(int((time.perf_counter() - start) * 1000))
                if not (resp.text or "").strip():
                    note = "empty answer on at least one run"
            except Exception as exc:
                note = f"{type(exc).__name__}: {exc}"[:120]
                break
    finally:
        await client.aclose()
    return latencies, note


def _row(backend: str, turn: str, latencies: list[int], note: str) -> dict:
    return {
        "backend": backend,
        "turn": turn,
        "p50": int(statistics.median(latencies)) if latencies else None,
        "p95": _p95(latencies) if latencies else None,
        "n": len(latencies),
        "note": note,
    }


async def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()
    runs = args.runs
    registry = Registry.load()
    stamp = int(time.time())
    rows = []

    # --- managed, cold: a new session id per run pays provisioning every time
    print(f"managed / cold session ({runs} runs, new session each)...", flush=True)
    lat, note = await _time_runs(
        lambda: RestClient(
            "http://localhost:8001",
            auth={"header_name": "x-lab-token", "header_value": os.environ.get("A2ALAB_TOKEN", "")},
            target_name="claude-rest",
            timeout=180,
        ),
        runs,
        lambda i: f"lat-cold-{stamp}-{i}",
    )
    rows.append(_row("managed", "first (cold session)", lat, note))
    print(f"  {rows[-1]['p50']} / {rows[-1]['p95']} ms  {note}", flush=True)

    # --- managed, warm: one session, so only run 0 provisions; drop it
    print(f"managed / warm session ({runs} follow-ups in one session)...", flush=True)
    warm_session = f"lat-warm-{stamp}"
    lat, note = await _time_runs(
        lambda: RestClient(
            "http://localhost:8001",
            auth={"header_name": "x-lab-token", "header_value": os.environ.get("A2ALAB_TOKEN", "")},
            target_name="claude-rest",
            timeout=180,
        ),
        runs + 1,
        lambda i: warm_session,
    )
    rows.append(_row("managed", "follow-up (warm session)", lat[1:], note))
    print(f"  {rows[-1]['p50']} / {rows[-1]['p95']} ms  {note}", flush=True)

    # --- sdk on a warm local server
    print(f"sdk-local / warm server ({runs} runs, :{SDK_PORT})...", flush=True)
    proc = _start_sdk_server()
    try:
        lat, note = await _time_runs(
            lambda: RestClient(
                f"http://localhost:{SDK_PORT}",
                auth={
                    "header_name": "x-lab-token",
                    "header_value": os.environ.get("A2ALAB_TOKEN", ""),
                },
                target_name="claude-rest",
                timeout=180,
            ),
            runs,
            lambda i: f"lat-sdk-{stamp}-{i}",
        )
    finally:
        _stop(proc)
    rows.append(_row("sdk", "first (warm server)", lat, note))
    print(f"  {rows[-1]['p50']} / {rows[-1]['p95']} ms  {note}", flush=True)

    # --- the same sdk backend, hosted on AgentCore
    print(f"sdk-agentcore / warm runtime ({runs} runs)...", flush=True)
    lat, note = await _time_runs(
        lambda: registry.client_for("claude-agentcore", exact=True), runs, lambda i: None
    )
    rows.append(_row("sdk-agentcore", "warm runtime", lat, note))
    print(f"  {rows[-1]['p50']} / {rows[-1]['p95']} ms  {note}", flush=True)

    print("\n| Backend | Turn | p50 | p95 | n | Notes |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        p50 = f"{r['p50'] / 1000:.1f}s" if r["p50"] else "—"
        p95 = f"{r['p95'] / 1000:.1f}s" if r["p95"] else "—"
        print(f"| {r['backend']} | {r['turn']} | {p50} | {p95} | {r['n']} | {r['note']} |")


if __name__ == "__main__":
    asyncio.run(main())
