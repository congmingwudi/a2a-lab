"""Measure the fan-out join rate (WS8's deliverable number).

    uv run python scripts/fanout_join_rate.py <trace_id>
    uv run python scripts/fanout_join_rate.py --latest

For one fan-out run, answer: of the platforms this task actually touched, how
many can be joined back to the run FROM THEIR OWN execution logs — not from the
lab's wire trace, which of course has all of them.

That is the number the `orchestration-topology` insight is really about. A 1:1
cell spans at most two platforms' logs; a fan-out spans four, each with its own
retention, ingestion lag and join key. The two mechanisms the lab has:

- **D34 `lab-trace:` rider** — the trace id rides in the message TEXT, so any
  platform that logs the utterance can be joined by a text match. Foundry logs
  spans, not prompts, so it cannot be joined this way.
- **`platform_ref`** — the lab records the platform's own session/response id
  on the hop at emit time, so a platform that returns one can be joined by id
  even when it logs no text.

Where neither applies, the platform is genuinely unjoinable, and saying so is
the point.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from dotenv import load_dotenv  # noqa: E402

from observability.store import ObsStore  # noqa: E402


def _trace_hops(trace_id: str) -> list[dict]:
    hops = []
    for path in sorted((REPO / "traces").glob("*.jsonl")):
        for line in path.read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("trace_id") == trace_id:
                hops.append(event)
    return sorted(hops, key=lambda e: e.get("hop_seq", 0))


def _latest_fanout_trace() -> str | None:
    """Most recent trace containing a fan-out leg hop."""
    best = (0.0, None)
    for path in sorted((REPO / "traces").glob("*.jsonl")):
        for line in path.read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if "fan-out leg" in str(event.get("transport_detail", "")):
                if event.get("ts", 0) > best[0]:
                    best = (event["ts"], event["trace_id"])
    return best[1]


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("trace_id", nargs="?")
    ap.add_argument("--latest", action="store_true")
    args = ap.parse_args()

    trace_id = args.trace_id or (_latest_fanout_trace() if args.latest else None)
    if not trace_id:
        print("give a trace id or --latest")
        return 2

    hops = _trace_hops(trace_id)
    if not hops:
        print(f"no hops for trace {trace_id}")
        return 2

    # The platforms this run actually touched, from the lab's own record.
    touched: dict[str, dict] = {}
    for hop in hops:
        target = str(hop.get("target", ""))
        platform = {
            "adk-logistics-a2a": "adk",
            "google-adk-a2a": "adk",
            "adk-a2a": "adk",
            "foundry-commercial-a2a": "foundry",
            "foundry-a2a": "foundry",
            "openai-agentcore": "openai",
            "a2alab-supply-orchestrator": "claude",
        }.get(target)
        if not platform:
            continue
        entry = touched.setdefault(platform, {"target": target, "platform_ref": None})
        if hop.get("platform_ref"):
            entry["platform_ref"] = hop["platform_ref"]

    store = ObsStore()
    try:
        by_text = store.session_lab_traces() if hasattr(store, "session_lab_traces") else {}
        rows = {
            f"{s['platform']}:{s['native_id']}": s
            for p in set(touched)
            for s in store.list_sessions(p)
        }
    finally:
        store.close()

    print(f"fan-out join rate — trace {trace_id}")
    print(f"platforms touched: {len(touched)}\n")
    joined = 0
    for platform, info in sorted(touched.items()):
        text_hits = [
            k for k, v in by_text.items() if v == trace_id and k.startswith(f"{platform}:")
        ]
        ref = info["platform_ref"]
        ref_hit = f"{platform}:{ref}" in rows if ref else False
        how = []
        if text_hits:
            how.append(f"lab-trace rider ({len(text_hits)} session)")
        if ref_hit:
            how.append(f"platform_ref {ref}")
        ok = bool(how)
        joined += ok
        mark = "JOINED" if ok else "  --  "
        print(f"  [{mark}] {platform:<10} via {info['target']}")
        print(f"            {' + '.join(how) if how else 'no join path'}")

    print(f"\njoin rate: {joined}/{len(touched)} platforms joinable from their OWN logs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
