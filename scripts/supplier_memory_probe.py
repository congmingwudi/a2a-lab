#!/usr/bin/env python3
"""D102 live go/no-go: do provenance labels survive Data 360 case recall?

Dry-run by default. With --apply, write one unique source-labelled observation
through the Logistics registration, close its session, then poll GetContext
through the Coordinator registration. Exit 0 only when the exact observation
id and source survive the cross-agent Data 360 read; exit 1 when context becomes
available without them, and 2 on invalid input/timeout.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

from interop.memory import case_conversation, open_and_read, write_turn  # noqa: E402
from interop.models import new_trace_id  # noqa: E402
from orchestration.memory import AGENT_IDS, SCENARIO, CaseMemory, MemoryRun  # noqa: E402


async def run(args) -> int:
    trace_id = new_trace_id()
    observation_id = f"obs-probe-{uuid.uuid4().hex[:12]}"
    source = "Logistics provenance probe"
    run_spec = MemoryRun(
        mode="seed", owner_id=args.owner, case_key=args.case, reader="coordinator"
    )
    cid = case_conversation(args.owner, SCENARIO, args.case)
    print(f"trace          {trace_id}")
    print(f"conversation   {cid}")
    print(f"observation    {observation_id}")
    print(f"write agent    {AGENT_IDS['exposure']}")
    print(f"read agent     {AGENT_IDS['coordinator']}")
    if not args.apply:
        print("DRY RUN — add --apply to write and poll the Data 360 tenant")
        return 0

    case_memory = CaseMemory()
    mem = case_memory.client
    sid = await mem.start_conversation(
        trace_id,
        agent_id=AGENT_IDS["exposure"],
        owner_id=args.owner,
        conversation_id=run_spec.conversation_id,
    )
    text = (
        f"[observation_id={observation_id}; source={source}; seed_trace={trace_id}] "
        "Rotterdam alternate-route capacity is the probe fact."
    )
    errors = await write_turn(
        mem,
        trace_id,
        agent_id=AGENT_IDS["exposure"],
        owner_id=args.owner,
        session_id=sid,
        conversation_id=run_spec.conversation_id,
        user_message="D102 provenance survival probe",
        agent_response=text,
    )
    if errors:
        print("write failed: " + "; ".join(errors), file=sys.stderr)
        return 2

    deadline = time.monotonic() + args.timeout
    attempt = 0
    last = ""
    while time.monotonic() < deadline:
        attempt += 1
        _, context, read_errors = await open_and_read(
            mem,
            trace_id,
            agent_id=AGENT_IDS["coordinator"],
            owner_id=args.owner,
            session_id="case-read-only",
            start=False,
            conversation_id=run_spec.conversation_id,
            include_saved_memories=False,
        )
        if read_errors:
            print(f"attempt {attempt}: read error: {'; '.join(read_errors)}")
        elif context:
            last = context
            id_ok = observation_id in context
            source_ok = source in context
            print(
                f"attempt {attempt}: context={len(context)} chars "
                f"id_survived={id_ok} source_survived={source_ok}"
            )
            if id_ok and source_ok:
                print("GO — exact provenance survived the cross-agent Data 360 read")
                return 0
            print("NO-GO — content is available but exact provenance did not survive")
            print(last[:1000])
            return 1
        else:
            print(f"attempt {attempt}: no context yet")
        await asyncio.sleep(args.interval)
    print("TIMEOUT — no non-empty context became available", file=sys.stderr)
    return 2


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", required=True, help="controlled a2alab-* probe owner")
    parser.add_argument("--case", default="d102-provenance-probe")
    parser.add_argument("--timeout", type=float, default=420.0)
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.owner.startswith("a2alab-"):
        parser.error("--owner must use the a2alab-* namespace")
    if args.timeout <= 0 or args.interval <= 0:
        parser.error("--timeout and --interval must be positive")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
