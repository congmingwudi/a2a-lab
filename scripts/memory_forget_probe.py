"""Forget probe: how long does a DELETED memory keep appearing, per surface?

The dcunited-dc1 estate saw Get Context keep serving deleted memories for 40+
minutes while the Memory API list dropped them in 1-4 minutes (2026-11-13). This
measures the same thing on our tenant, for one synthetic owner:

  1. open a conversation for the owner (so Get Context has one to read)
  2. create one sys_PROFILE_FACT and one sys_PROFILE_PREFERENCES marker
  3. poll the Memory API list and Get Context until both markers are on both
  4. DELETE both, then poll both surfaces every --tick seconds for --watch seconds

It writes `traces/governance/forget-<run>.json` (gitignored: raw tenant wire) and
prints, per surface, the first tick each marker stopped appearing. Writes to
Data 360: two synthetic memories (deleted in step 4) and one probe conversation.
DNS failures are retried, because a flapping resolver must not end the timeline.

    PYTHONPATH=src uv run python scripts/memory_forget_probe.py [--agent A2ALab_Memory_Case]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from interop.memory import MemoryClient  # noqa: E402
from interop.models import new_trace_id  # noqa: E402

OUT = ROOT / "traces" / "governance"
CATEGORIES = ("sys_PROFILE_FACT", "sys_PROFILE_PREFERENCES")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%SZ")


async def retry(fn, *a, **kw):
    """Retry only connection-level failures (DNS/VPN flaps); API errors propagate."""
    for attempt in range(12):
        try:
            return await fn(*a, **kw)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
            if attempt == 11:
                raise
            await asyncio.sleep(10)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="A2ALab_Memory_Case")
    ap.add_argument("--watch", type=int, default=2700, help="seconds to watch after delete")
    ap.add_argument("--tick", type=int, default=60, help="seconds between post-delete polls")
    ap.add_argument("--settle", type=int, default=600, help="max seconds to wait for markers")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")

    run = uuid.uuid4().hex[:10]
    owner = f"a2alab-forget-{run}"
    markers = {c: f"Forget probe {run}: {c} marker" for c in CATEGORIES}
    mem, t = MemoryClient(source="forget-probe"), new_trace_id()
    res: dict = {"run": run, "owner": owner, "agent": args.agent, "trace_id": t, "ticks": []}

    async def observe() -> dict:
        listed = {}
        for c in CATEGORIES:
            listing = await retry(mem.list_memories, t, agent_id=args.agent, owner_id=owner,
                                  category=c)  # fmt: skip
            listed[c] = any(markers[c] in e.content for e in listing.entries)
        ctx = json.dumps(await retry(mem.get_context, t, agent_id=args.agent, owner_id=owner))
        return {"t": stamp(), "list": listed, "context": {c: markers[c] in ctx for c in CATEGORIES}}

    try:
        await retry(mem.start_conversation, t, agent_id=args.agent, owner_id=owner)
        ids: dict[str, list[str]] = {}
        for c in CATEGORIES:
            saved = await retry(mem.create_memories, t, agent_id=args.agent, owner_id=owner,
                                category=c, contents=[markers[c]])  # fmt: skip
            ids[c] = [e.memory_id for e in saved.entries if e.memory_id]
        res["created"] = {"t": stamp(), "ids": ids}
        print(stamp(), "created", json.dumps(ids), flush=True)

        start = time.monotonic()
        while True:
            o = await observe()
            print(o["t"], "settle", json.dumps(o), flush=True)
            if all(o["list"].values()) and all(o["context"].values()):
                res["visible_everywhere"] = o
                break
            if time.monotonic() - start > args.settle:
                res["visible_everywhere"] = None
                print("markers never reached both surfaces; deleting anyway", flush=True)
                break
            await asyncio.sleep(20)
    finally:
        for c, mids in locals().get("ids", {}).items():
            gone = await retry(mem.delete_memories, t, agent_id=args.agent, owner_id=owner,
                               category=c, memory_ids=mids)  # fmt: skip
            print(stamp(), "deleted", c, gone, flush=True)
        res["deleted_at"] = stamp()

    t0 = time.monotonic()
    first_gone: dict[str, str | None] = {f"{s}:{c}": None for s in ("list", "context")
                                         for c in CATEGORIES}  # fmt: skip
    while time.monotonic() - t0 <= args.watch:
        o = await observe()
        o["since_delete_s"] = round(time.monotonic() - t0)
        res["ticks"].append(o)
        for s in ("list", "context"):
            for c in CATEGORIES:
                k = f"{s}:{c}"
                if first_gone[k] is None and not o[s][c]:
                    first_gone[k] = f"{o['since_delete_s']}s"
        print(o["t"], f"+{o['since_delete_s']}s", json.dumps({"list": o["list"],
              "context": o["context"]}), flush=True)  # fmt: skip
        if all(first_gone.values()):
            break
        await asyncio.sleep(args.tick)
    res["first_gone"] = first_gone
    await mem.aclose()
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"forget-{run}.json"
    out.write_text(json.dumps(res, indent=1, default=str))
    print("first absent after delete (null = still present at end of watch):",
          json.dumps(first_gone))  # fmt: skip
    print("wrote", out)


if __name__ == "__main__":
    asyncio.run(main())
