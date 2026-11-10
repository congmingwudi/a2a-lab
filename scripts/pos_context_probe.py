#!/usr/bin/env python3
"""WS32: how soon does a POS order reach an agent through Data 360 Agent Memory?

Rings up ONE in-store order for a demo customer through the Ingestion API
(`interop.pos`), then polls GetContext through a memory registration whose
attached real-time graph carries the POS node, and reports two times:

  in_context   the order id first appears anywhere in the GetContext body
  in_prompt    it first survives `render_context` (the faces' prompt renderer and
               its graph budget), i.e. what Claude/OpenAI would actually be shown

The owner is the customer's CRM Contact id (D117), resolved through
`A2ALAB_MEMORY_CUSTOMER_MAP` exactly as the faces resolve it; the conversation
is the owner's D94 conversation. Writes one POS row; reads only from memory.
Raw final bodies go to traces/graph-probe/ (gitignored: CRM profile data).

    uv run python scripts/pos_context_probe.py [--customer maya] [--agent A2ALab_Memory_Case_POS]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from interop.memory import MemoryClient, owner_conversation, render_context  # noqa: E402
from interop.models import new_trace_id  # noqa: E402
from interop.pos import PosClient, new_order  # noqa: E402

OUT = ROOT / "traces" / "graph-probe"


async def run(customer: str, agent: str, timeout: float, interval: float) -> dict:
    owner = json.loads(os.environ["A2ALAB_MEMORY_CUSTOMER_MAP"])[customer]
    trace_id = new_trace_id()
    conv = owner_conversation(owner)
    mem, pos = MemoryClient(source="pos-probe"), PosClient(source="pos-probe")
    try:
        # Baseline first: the registration must answer before the clock starts.
        await mem.get_context(trace_id, agent_id=agent, owner_id=owner, conversation_id=conv)
        row = new_order(owner, product_sku="SKU-LAMP-05", product_name="Camp lantern",
                        category="Camping", quantity=1, amount=29.0)  # fmt: skip
        t0 = time.monotonic()
        ack = await pos.send(trace_id, [row])
        out: dict = {"customer": customer, "agent": agent, "order_id": row["order_id"],
                     "trace_id": trace_id, "ingest_s": ack["accepted_s"], "reads": 0}  # fmt: skip
        print(f"{time.strftime('%H:%M:%S')} ingest {ack['status']} {ack['accepted_s']}s "
              f"{row['order_id']}", flush=True)  # fmt: skip
        body: dict = {}
        while time.monotonic() - t0 < timeout:
            body = await mem.get_context(
                trace_id, agent_id=agent, owner_id=owner, conversation_id=conv
            )
            out["reads"] += 1
            el = round(time.monotonic() - t0, 1)
            if "in_context_s" not in out and row["order_id"] in json.dumps(body):
                out["in_context_s"] = el
                print(f"{time.strftime('%H:%M:%S')} in GetContext after {el}s", flush=True)
            if "in_context_s" in out and "in_prompt_s" not in out:
                if row["order_id"] in render_context(body):
                    out["in_prompt_s"] = el
                    print(f"{time.strftime('%H:%M:%S')} in rendered prompt after {el}s", flush=True)
            if "in_prompt_s" in out:
                break
            await asyncio.sleep(interval)
        out["rendered_chars"] = len(render_context(body))
        OUT.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        (OUT / f"{stamp}-pos-{customer}.json").write_text(json.dumps(body, indent=1))
        return out
    finally:
        await mem.aclose()
        await pos.aclose()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--customer", default="maya")
    ap.add_argument("--agent", default="A2ALab_Memory_Case_POS")
    ap.add_argument("--timeout", type=float, default=2400, help="seconds")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between reads")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    res = asyncio.run(run(args.customer, args.agent, args.timeout, args.interval))
    print(json.dumps(res, indent=1))
    return 0 if "in_context_s" in res else 1


if __name__ == "__main__":
    sys.exit(main())
