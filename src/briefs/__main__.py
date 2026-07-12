"""Brief worker CLI.

    uv run python -m briefs --run-now "Omega, Inc."   # fire the daily job now
    uv run python -m briefs --watch                    # service scheduled runs

--watch is the lab-host half of the scheduled-deployment pattern: Anthropic's
cron fires sessions autonomously; this loop finds each deployment run,
attaches to its session, executes the save_account_brief custom tool
host-side (Salesforce delivery), and records the trace. Sessions fired while
the lab host was down simply idle awaiting the tool result — they are picked
up and completed on the next poll, nothing is lost.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from briefs.runner import BriefRunner, load_brief_ids, run_brief
from interop.models import new_trace_id

WATCH_STATE = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "brief_state.json"
POLL_S = float(os.environ.get("A2ALAB_BRIEF_POLL_S", "60"))


def _load_serviced() -> set[str]:
    if WATCH_STATE.exists():
        return set(json.loads(WATCH_STATE.read_text()).get("serviced_sessions", []))
    return set()


def _save_serviced(serviced: set[str]) -> None:
    WATCH_STATE.parent.mkdir(parents=True, exist_ok=True)
    # Keep the file bounded; old sessions can't reappear in recent runs.
    WATCH_STATE.write_text(json.dumps({"serviced_sessions": sorted(serviced)[-500:]}, indent=2))


async def watch() -> None:
    from anthropic import AsyncAnthropic

    ids = load_brief_ids()
    deployment_id = ids.get("deployment_id")
    if not deployment_id:
        print("[briefs] no deployment_id in .a2alab/brief.json — nothing to watch")
        return
    client = AsyncAnthropic()
    serviced = _load_serviced()
    print(f"[briefs] watching deployment {deployment_id} every {POLL_S:.0f}s", flush=True)

    while True:
        try:
            runs = client.beta.deployment_runs.list(deployment_id=deployment_id)
            async for run in runs:
                session_id = getattr(run, "session_id", None)
                if not session_id or session_id in serviced:
                    continue
                print(f"[briefs] servicing scheduled session {session_id}", flush=True)
                trace_id = new_trace_id()
                runner = BriefRunner(client)
                try:
                    result = await runner.service_scheduled_session(session_id, trace_id)
                    print(
                        f"[briefs] session {session_id} done: "
                        f"{len(result['deliveries'])} brief(s), "
                        f"{result['web_lookups']} web lookups, "
                        f"{result['elapsed_s']}s (trace {trace_id})",
                        flush=True,
                    )
                except Exception as exc:
                    print(f"[briefs] session {session_id} failed: {exc}", flush=True)
                finally:
                    await runner.aclose()
                serviced.add(session_id)
                _save_serviced(serviced)
        except Exception as exc:
            print(f"[briefs] poll error (retrying): {exc}", flush=True)
        await asyncio.sleep(POLL_S)


async def run_now(accounts: str) -> None:
    trace_id = new_trace_id()
    print(f"[briefs] running now for: {accounts} (trace {trace_id})", flush=True)
    result = await run_brief(accounts, trace_id)
    print(json.dumps({k: v for k, v in result.items() if k != "text"}, indent=2))
    print(result["text"])


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--watch", action="store_true", help="service scheduled runs")
    group.add_argument("--run-now", metavar="ACCOUNTS", help="fire the job immediately")
    args = parser.parse_args()
    if args.watch:
        asyncio.run(watch())
    else:
        asyncio.run(run_now(args.run_now))


if __name__ == "__main__":
    main()
