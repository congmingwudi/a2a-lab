"""Protocol matrix harness: run every runnable cell in config/targets.yaml,
record pass/fail + p50/p95 latency + a transcript snippet, print the table,
and append results to plan/03-results.md.

    uv run python scripts/matrix.py                 # all targets
    uv run python scripts/matrix.py claude-rest     # one cell
    uv run python scripts/matrix.py --runs 5        # latency percentiles
"""

from __future__ import annotations

import argparse
import asyncio
import math
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

from interop.models import AgentRequest, new_trace_id
from interop.registry import Registry

QUESTION = (
    "In two sentences: what is the difference between the MCP and A2A "
    "protocols for agent interoperability?"
)


def _p95(latencies: list[int]) -> int:
    # Nearest-rank percentile: the tail outlier must survive small run counts.
    return sorted(latencies)[min(len(latencies) - 1, math.ceil(len(latencies) * 0.95) - 1)]


async def run_cell(registry: Registry, name: str, runs: int) -> dict:
    target = registry.get(name)
    latencies: list[int] = []
    snippet = ""
    error = ""
    for _ in range(runs):
        # An unconfigured target (e.g. missing SF_* env vars) must FAIL its
        # own cell, not crash the whole run.
        try:
            client = registry.client_for(name, exact=True)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            break
        try:
            start = time.perf_counter()
            resp = await client.ask(AgentRequest(message=QUESTION, trace_id=new_trace_id()))
            latencies.append(int((time.perf_counter() - start) * 1000))
            snippet = (resp.text or "").replace("\n", " ")[:120]
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            break
        finally:
            await client.aclose()
    ok = bool(latencies) and not error
    return {
        "target": name,
        "platform": target.platform,
        "protocol": target.protocol,
        "status": target.status,
        "ok": ok,
        "p50": int(statistics.median(latencies)) if latencies else None,
        "p95": _p95(latencies) if latencies else None,
        "snippet": snippet,
        "error": error[:200],
    }


def print_table(rows: list[dict]) -> str:
    header = f"{'target':<18} {'platform':<11} {'protocol':<15} {'status':<12} {'result':<7} {'p50ms':>6} {'p95ms':>6}  detail"
    lines = [header, "-" * len(header)]
    for r in rows:
        result = "PASS" if r["ok"] else "FAIL"
        detail = r["snippet"] if r["ok"] else r["error"]
        lines.append(
            f"{r['target']:<18} {r['platform']:<11} {r['protocol']:<15} {r['status']:<12} "
            f"{result:<7} {str(r['p50'] or '-'):>6} {str(r['p95'] or '-'):>6}  {detail}"
        )
    return "\n".join(lines)


def append_results(table: str) -> None:
    results = Path("plan/03-results.md")
    if results.exists():
        stamp = time.strftime("%Y-%m-%d %H:%M:%S %Z")
        with results.open("a") as f:
            f.write(f"\n## Matrix run — {stamp}\n\n```\n{table}\n```\n")


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("targets", nargs="*", help="target names (default: all)")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--no-record", action="store_true")
    args = parser.parse_args()

    registry = Registry.load()
    names = args.targets or list(registry.targets)
    rows = [await run_cell(registry, name, args.runs) for name in names]
    table = print_table(rows)
    print(table)
    if not args.no_record:
        append_results(table)
    if any(not r["ok"] for r in rows):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
