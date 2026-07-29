"""Brief worker CLI.

    uv run python -m briefs --run-now "Omega, Inc."   # fire the daily job now
    uv run python -m briefs --watch                    # service scheduled runs
    uv run python -m briefs --push-state               # local serviced set -> Aurora

--watch is the lab-host half of the scheduled-deployment pattern: Anthropic's
cron fires sessions autonomously; this loop finds each deployment run,
attaches to its session, executes the save_account_brief custom tool
host-side (Salesforce delivery), and records the trace. Sessions fired while
the lab host was down simply idle awaiting the tool result — they are picked
up and completed on the next poll, nothing is lost.

--push-state is the MIGRATION step, and skipping it costs real money. The
serviced set moved from `.a2alab/brief_state.json` to Aurora when the watcher
moved to Fargate (WS13 item 3), and the hosted store starts EMPTY. A watcher
with no memory re-services every session still listed in recent deployment runs
— eight days of them, ten minutes of web research each — and would re-deliver
any whose tool call had not already been consumed. Run this once before starting
the hosted watcher, from a machine that has the local file.
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


def _state_store():
    """Aurora when configured, else the local file (WS13 item 3 / D50 pattern).

    This state is what stops a brief being delivered twice. On a laptop the file
    was fine; in a container it is a layer of the image, so every restart would
    forget which sessions had been serviced and re-deliver every brief still
    listed in recent runs — duplicate `A2ALab_Account_Brief__c` records in a
    production org, which is a worse failure than missing one.
    """
    try:
        from observability.pg import PgClient, PgObsStore

        if not PgClient.configured():
            return None
        return PgObsStore()
    except Exception:  # noqa: BLE001 - no AWS, no cluster: use the file
        return None


def _load_serviced() -> set[str]:
    store = _state_store()
    if store is not None:
        try:
            from observability.pg import STATE_BRIEF_SESSIONS

            payload = store.get_state(STATE_BRIEF_SESSIONS) or {}
            return set(payload.get("serviced_sessions") or [])
        finally:
            store.close()
    if WATCH_STATE.exists():
        return set(json.loads(WATCH_STATE.read_text()).get("serviced_sessions", []))
    return set()


def _save_serviced(serviced: set[str]) -> None:
    # Bounded either way; old sessions can't reappear in recent runs.
    bounded = sorted(serviced)[-500:]
    store = _state_store()
    if store is not None:
        from observability.pg import STATE_BRIEF_SESSIONS

        try:
            # Not soft-failed: if this write is lost the next poll re-delivers
            # briefs that already landed in Salesforce.
            store.put_state(STATE_BRIEF_SESSIONS, {"serviced_sessions": bounded})
        finally:
            store.close()
        return
    WATCH_STATE.parent.mkdir(parents=True, exist_ok=True)
    WATCH_STATE.write_text(json.dumps({"serviced_sessions": bounded}, indent=2))


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


def push_state() -> None:
    """Seed the hosted serviced-set from the local file, as a UNION.

    A union rather than a replace: by the time this runs the hosted watcher may
    already have serviced sessions the local file has never heard of, and
    dropping those would re-run them.
    """
    from observability.pg import STATE_BRIEF_SESSIONS

    # NOTE: this WRITES, and .env points A2ALAB_PG_SECRET_ARN at the lab_reader
    # secret because everything else the laptop does with Aurora is a read. Run
    # it as the writer or Postgres refuses with "cannot execute INSERT in a
    # read-only transaction":
    #   A2ALAB_PG_SECRET_ARN="$A2ALAB_PG_WRITER_SECRET_ARN" \
    #     uv run python -m briefs --push-state
    store = _state_store()
    if store is None:
        raise SystemExit(
            "no hosted store configured — set A2ALAB_PG_CLUSTER_ARN and "
            "A2ALAB_PG_SECRET_ARN in .env (with no Aurora there is nothing to seed)"
        )
    try:
        hosted = set((store.get_state(STATE_BRIEF_SESSIONS) or {}).get("serviced_sessions") or [])
        local: set[str] = set()
        if WATCH_STATE.exists():
            local = set(json.loads(WATCH_STATE.read_text()).get("serviced_sessions", []))
        merged = sorted(hosted | local)[-500:]
        store.put_state(STATE_BRIEF_SESSIONS, {"serviced_sessions": merged})
    finally:
        store.close()
    print(
        f"[briefs] seeded hosted state: {len(local)} local + {len(hosted)} hosted "
        f"-> {len(merged)} serviced session(s)"
    )


async def run_now(accounts: str) -> None:
    trace_id = new_trace_id()
    print(f"[briefs] running now for: {accounts} (trace {trace_id})", flush=True)
    result = await run_brief(accounts, trace_id)
    print(json.dumps({k: v for k, v in result.items() if k != "text"}, indent=2))
    print(result["text"])


def main() -> None:
    load_dotenv()
    # Hosted (WS13 item 3): ANTHROPIC_API_KEY and the Salesforce credentials
    # come from Secrets Manager, and must land before AsyncAnthropic() or
    # BriefWriter.from_env() read os.environ. A no-op locally, where .env holds
    # everything. Every other hosted seam does this; the watcher was the last
    # one that did not, because it had never run anywhere but a laptop.
    from interop.secret_env import load_secret_env_and_log

    load_secret_env_and_log("briefs")
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--watch", action="store_true", help="service scheduled runs")
    group.add_argument("--run-now", metavar="ACCOUNTS", help="fire the job immediately")
    group.add_argument(
        "--push-state",
        action="store_true",
        help="seed the hosted store from .a2alab/brief_state.json (run once, before --watch)",
    )
    args = parser.parse_args()
    if args.watch:
        asyncio.run(watch())
    elif args.push_state:
        push_state()
    else:
        asyncio.run(run_now(args.run_now))


if __name__ == "__main__":
    main()
