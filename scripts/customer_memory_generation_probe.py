#!/usr/bin/env python3
"""D107 go/no-go for the custom customer-memory generation category.

Dry-run by default. ``--apply`` uses a controlled ``a2alab-*`` profile to
list, create, read back, and delete one opaque generation marker. It also
checks whether the marker leaks into GetContext or the saved fact/preference
prompt material. The created marker is deleted in ``finally`` when possible.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

from interop.memory import (  # noqa: E402
    GENERATION_CATEGORY,
    MemoryClient,
    new_generation_marker,
    render_context,
    saved_memories,
)
from interop.memory_adapter import CASE_MEMORY_AGENT  # noqa: E402
from interop.models import new_trace_id  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    trace_id = new_trace_id()
    marker = new_generation_marker()
    print(json.dumps({
        "trace_id": trace_id,
        "owner": args.owner,
        "agent_id": CASE_MEMORY_AGENT,
        "category": args.category,
        "apply": args.apply,
    }, sort_keys=True))
    if not args.apply:
        print("DRY RUN — add --apply to exercise the tenant and clean up one marker")
        return 0

    mem = MemoryClient(source="customer-memory-generation-probe")
    created_ids: list[str] = []
    result: dict[str, object] = {}
    try:
        before = await mem.list_memories(
            trace_id, agent_id=CASE_MEMORY_AGENT, owner_id=args.owner,
            category=args.category,
        )
        result["empty_read_ok"] = before.complete
        created = await mem.create_memories(
            trace_id, agent_id=CASE_MEMORY_AGENT, owner_id=args.owner,
            category=args.category, contents=[marker],
        )
        created_ids = [entry.memory_id for entry in created.entries if entry.memory_id]
        after_create = await mem.list_memories(
            trace_id, agent_id=CASE_MEMORY_AGENT, owner_id=args.owner,
            category=args.category,
        )
        matching = [entry for entry in after_create.entries if entry.content == marker]
        created_ids = [entry.memory_id for entry in matching if entry.memory_id] or created_ids
        result["create_ok"] = bool(created_ids)
        result["read_back_ok"] = len(matching) == 1 and after_create.complete

        context = await mem.get_context(
            trace_id, agent_id=CASE_MEMORY_AGENT, owner_id=args.owner,
        )
        saved, saved_errors = await saved_memories(
            mem, trace_id, agent_id=CASE_MEMORY_AGENT, owner_id=args.owner,
        )
        result["marker_in_context"] = marker in json.dumps(context)
        result["marker_in_rendered_context"] = marker in render_context(context)
        result["marker_in_saved_prompt"] = marker in saved
        result["saved_read_errors"] = saved_errors
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            if created_ids:
                await mem.delete_memories(
                    trace_id, agent_id=CASE_MEMORY_AGENT, owner_id=args.owner,
                    category=args.category, memory_ids=created_ids,
                )
            cleanup = await mem.list_memories(
                trace_id, agent_id=CASE_MEMORY_AGENT, owner_id=args.owner,
                category=args.category,
            )
            result["cleanup_ok"] = not any(
                entry.content == marker for entry in cleanup.entries
            )
        except Exception as exc:
            result["cleanup_ok"] = False
            result["cleanup_error"] = f"{type(exc).__name__}: {exc}"
        await mem.aclose()

    result["ok"] = all(result.get(key) is True for key in (
        "empty_read_ok", "create_ok", "read_back_ok", "cleanup_ok"
    ))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", default="a2alab-memprobe")
    parser.add_argument("--category", default=GENERATION_CATEGORY)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.owner.startswith("a2alab-"):
        parser.error("--owner must use the controlled a2alab-* namespace")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
