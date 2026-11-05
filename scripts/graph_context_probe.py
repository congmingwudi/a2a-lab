#!/usr/bin/env python3
"""D105 follow-up: what does an attached real-time data graph add to GetContext?

Read-only A/B. For each demo customer, resolve the conversation exactly as the
faces do (the D107 generation marker when one is listed, else the owner's D94
conversation), then read GetContext through two registrations that differ by
the attached graph:

  A  A2ALab_Memory_Case        the shared registration the faces call (D105)
  B  A2ALab_Memory_Case_Graph  same profile DMO + A2ALab_Memory_Customer360

and report, per arm: the response keys, item counts, raw bytes per key,
latency, and what `render_context` (the faces' prompt renderer, 4000-char cap)
would hand Claude/OpenAI — including whether the graph crowds memory out of
the cap. Raw bodies are written under traces/graph-probe/ (gitignored: they
hold CRM profile data). Nothing is written to Data 360.

    uv run python scripts/graph_context_probe.py [--customers maya jordan] [--reads 3]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from interop.memory import (  # noqa: E402
    GENERATION_CATEGORY,
    MemoryClient,
    MemoryListing,
    generation_conversation,
    owner_conversation,
    render_context,
    select_generation,
)
from interop.models import new_trace_id  # noqa: E402

ARMS = {"A": "A2ALab_Memory_Case", "B": "A2ALab_Memory_Case_Graph"}
OUT = ROOT / "traces" / "graph-probe"


def nbytes(v) -> int:
    return len(json.dumps(v, ensure_ascii=False)) if v not in (None, "", [], {}) else 0


async def conversation_for(mem: MemoryClient, trace_id: str, owner: str) -> tuple[str, bool]:
    """The faces' derivation (memory_adapter.MemoryAdapter.handle)."""
    listing = await mem.list_memories(
        trace_id, agent_id=ARMS["A"], owner_id=owner, category=GENERATION_CATEGORY
    )
    if isinstance(listing, MemoryListing):
        generation, malformed = select_generation(listing)
        if generation and not malformed:
            return generation_conversation(owner, generation), True
    return owner_conversation(owner), False


async def probe(customer: str, owner: str, reads: int, stamp: str) -> dict:
    mem = MemoryClient(source="graph-probe")
    trace_id = new_trace_id()
    try:
        conv, has_gen = await conversation_for(mem, trace_id, owner)
        out: dict = {"customer": customer, "generation": has_gen, "trace_id": trace_id}
        for arm, agent in ARMS.items():
            times, body = [], {}
            for _ in range(reads):
                t0 = time.perf_counter()
                body = await mem.get_context(
                    trace_id, agent_id=agent, owner_id=owner, conversation_id=conv
                )
                times.append(int((time.perf_counter() - t0) * 1000))
            (OUT / f"{stamp}-{customer}-{arm}.json").write_text(json.dumps(body, indent=1))
            uncapped = render_context(body, limit=10**9, include_profile=not has_gen)
            capped = render_context(body, include_profile=not has_gen)
            mem_lines = [ln for ln in uncapped.splitlines() if ln.startswith("- userContext")]
            kept = sum(1 for ln in mem_lines if ln in capped)
            out[arm] = {
                "agent": agent,
                "keys": sorted(body),
                "items": {k: len(v) for k, v in body.items() if isinstance(v, list)},
                "bytes": {k: nbytes(v) for k, v in body.items()},
                "ms": times,
                "ms_median": int(statistics.median(times)),
                "rendered_chars_uncapped": len(uncapped),
                "rendered_chars_sent": len(capped),
                "memory_lines": len(mem_lines),
                "memory_lines_surviving_cap": kept,
            }
        return out
    finally:
        await mem.aclose()


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--customers", nargs="+", default=["maya", "jordan"])
    ap.add_argument("--reads", type=int, default=3)
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    cmap = json.loads(os.environ["A2ALAB_MEMORY_CUSTOMER_MAP"])
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    for c in args.customers:
        print(json.dumps(await probe(c, cmap[c], args.reads, stamp), indent=1))
    print(f"raw bodies: {OUT.relative_to(ROOT)}/{stamp}-*.json")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
