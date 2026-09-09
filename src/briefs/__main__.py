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
import datetime as _dt
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from briefs.runner import BriefRunner, load_brief_ids, run_brief
from interop.models import new_trace_id

WATCH_STATE = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "brief_state.json"
POLL_S = float(os.environ.get("A2ALAB_BRIEF_POLL_S", "60"))

# How many times a session with a FAILED Salesforce delivery is retried before
# it is recorded terminal `failed` and stops being serviced (A4/F07). A cap, not
# forever: a persistently-broken write must not re-attempt on every poll.
MAX_ATTEMPTS = int(os.environ.get("A2ALAB_BRIEF_MAX_ATTEMPTS", "3"))

# lab_state row key. Kept from the pre-b3 id-only list so a hosted store written
# by the old watcher is found and MIGRATED on read, not orphaned.
STATE_KEY = "brief_serviced_sessions"
LEDGER_CAP = 500

# Terminal statuses: a session in one of these is never serviced again.
_TERMINAL = ("delivered", "failed")


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


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _ledger_from_payload(payload: dict) -> dict[str, dict]:
    """The persisted payload -> in-memory ledger (session_id -> entry).

    New shape: `{"sessions": {session_id: entry}}`. Legacy shape (pre-b3): an
    id-only list `{"serviced_sessions": [...]}`, which MIGRATES deterministically
    — each id becomes a terminal `delivered` entry (attempts=1). Legacy entries
    have no `at`, so an ascending `ordinal` from list position gives eviction a
    defined order (oldest = lowest)."""
    if "sessions" in payload:
        return dict(payload["sessions"])
    ledger: dict[str, dict] = {}
    for i, sid in enumerate(payload.get("serviced_sessions") or []):
        ledger[sid] = {
            "status": "delivered",
            "attempts": 1,
            "at": None,
            "ordinal": i,
            "last_error": None,
            "calls": {},
        }
    return ledger


def _evict_key(entry: dict):
    """Sort key for the 500-bound. Legacy entries (no `at`) rank below every
    timestamped one, so a freshly-serviced session is never evicted in favour of
    a migrated id; within each class, by `at`/`ordinal` (oldest lowest)."""
    at = entry.get("at")
    if at:
        return (1, at)
    return (0, entry.get("ordinal", 0))


def _bound(ledger: dict[str, dict], cap: int = LEDGER_CAP) -> dict[str, dict]:
    if len(ledger) <= cap:
        return dict(ledger)
    ordered = sorted(ledger.items(), key=lambda kv: _evict_key(kv[1]))
    return dict(ordered[-cap:])


def _is_terminal(entry: dict) -> bool:
    return entry.get("status") in _TERMINAL


def _session_status(entry: dict, max_attempts: int = MAX_ATTEMPTS) -> str:
    """The per-tick outcome. `delivered` ONLY when every observed save call is
    `ok` (and no poll error); `pending` while a call failed / none was observed
    and attempts remain; `failed` (terminal) once attempts hit the cap. A
    zero-save session with no error is NOT `delivered` — it retries, then goes
    terminal, rather than being silently serviced (the pre-b3 bug)."""
    calls = entry.get("calls") or {}
    statuses = [c.get("status") for c in calls.values()]
    has_failed = "failed" in statuses
    delivered_any = "ok" in statuses
    if not entry.get("last_error") and delivered_any and not has_failed:
        return "delivered"
    if entry.get("attempts", 0) >= max_attempts:
        return "failed"
    return "pending"


def _load_ledger() -> dict[str, dict]:
    store = _state_store()
    if store is not None:
        try:
            payload = store.get_state(STATE_KEY) or {}
            return _ledger_from_payload(payload)
        finally:
            store.close()
    if WATCH_STATE.exists():
        return _ledger_from_payload(json.loads(WATCH_STATE.read_text()))
    return {}


def _save_ledger(ledger: dict[str, dict]) -> None:
    payload = {"sessions": _bound(ledger)}
    store = _state_store()
    if store is not None:
        try:
            # Not soft-failed: if this write is lost the next poll re-delivers
            # briefs that already landed in Salesforce.
            store.put_state(STATE_KEY, payload)
        finally:
            store.close()
        return
    WATCH_STATE.parent.mkdir(parents=True, exist_ok=True)
    WATCH_STATE.write_text(json.dumps(payload, indent=2))


async def watch() -> None:
    from anthropic import AsyncAnthropic

    ids = load_brief_ids()
    deployment_id = ids.get("deployment_id")
    if not deployment_id:
        print("[briefs] no deployment_id in .a2alab/brief.json — nothing to watch")
        return
    client = AsyncAnthropic()
    ledger = _load_ledger()
    print(f"[briefs] watching deployment {deployment_id} every {POLL_S:.0f}s", flush=True)

    while True:
        try:
            runs = client.beta.deployment_runs.list(deployment_id=deployment_id)
            async for run in runs:
                session_id = getattr(run, "session_id", None)
                if not session_id:
                    continue
                prior = ledger.get(session_id)
                if prior and _is_terminal(prior):
                    continue  # delivered, or failed at the attempt cap
                candidate = await _service_once(client, session_id, prior)
                # Persist BEFORE mutating the live ledger. If the state write is
                # lost, `_save_ledger`'s invariant is that the next poll
                # re-delivers — but only if the in-memory ledger still shows the
                # session as non-terminal. Committing the (possibly terminal)
                # entry first and saving second would strand a failed write:
                # the poll would skip a session that never persisted (F07 rd1).
                _save_ledger({**ledger, session_id: candidate})
                ledger[session_id] = candidate
        except Exception as exc:
            print(f"[briefs] poll error (retrying): {exc}", flush=True)
        await asyncio.sleep(POLL_S)


async def _service_once(client, session_id: str, prior: dict | None) -> dict:
    """Service one scheduled session and fold its per-call result into a ledger
    entry. `prior['calls']` (tool_use_id -> record) is replayed so an already-
    `ok` call is not re-written and a `failed` one is retried (A4/F07)."""
    prior_calls = dict((prior or {}).get("calls") or {})
    attempts = (prior or {}).get("attempts", 0) + 1
    print(f"[briefs] servicing scheduled session {session_id} (attempt {attempts})", flush=True)
    trace_id = new_trace_id()
    runner = BriefRunner(client)
    last_error: str | None = None
    calls = prior_calls
    try:
        result = await runner.service_scheduled_session(
            session_id, trace_id, prior_calls=prior_calls
        )
        # Merge: keep any prior call the tick did not re-observe, overlay fresh.
        calls = {**prior_calls, **{c["tool_use_id"]: c for c in result["calls"]}}
        last_error = result.get("error")
        print(
            f"[briefs] session {session_id}: {result['delivered']} delivered, "
            f"{result['failed']} failed, {result['web_lookups']} web lookups, "
            f"{result['elapsed_s']}s (trace {trace_id})",
            flush=True,
        )
    except Exception as exc:
        last_error = str(exc)
        print(f"[briefs] session {session_id} tick errored: {exc}", flush=True)
    finally:
        await runner.aclose()
    entry = {
        "status": "pending",
        "attempts": attempts,
        "at": _now_iso(),
        "last_error": last_error,
        "calls": calls,
    }
    entry["status"] = _session_status(entry)
    if entry["status"] == "failed":
        print(
            f"[briefs] session {session_id} TERMINAL failed after {attempts} attempts: "
            f"{last_error}",
            flush=True,
        )
    return entry


def push_state() -> None:
    """Seed the hosted outcome ledger from the local file, as a UNION.

    A union rather than a replace: by the time this runs the hosted watcher may
    already have serviced sessions the local file has never heard of, and
    dropping those would re-run them. Both sides are migrated through
    `_ledger_from_payload`, so a legacy id-only local file merges cleanly with a
    hosted ledger. When both carry the same session, the more-progressed entry
    wins (a terminal `delivered`/`failed` over a `pending`; higher attempts
    otherwise) so a merge never resurrects a completed session.
    """
    store = _state_store()
    if store is None:
        raise SystemExit(
            "no hosted store configured — set A2ALAB_PG_CLUSTER_ARN and "
            "A2ALAB_PG_SECRET_ARN in .env (with no Aurora there is nothing to seed)"
        )
    try:
        hosted = _ledger_from_payload(store.get_state(STATE_KEY) or {})
        local: dict[str, dict] = {}
        if WATCH_STATE.exists():
            local = _ledger_from_payload(json.loads(WATCH_STATE.read_text()))
        merged: dict[str, dict] = dict(hosted)
        for sid, entry in local.items():
            if sid not in merged or _more_progressed(entry, merged[sid]):
                merged[sid] = entry
        store.put_state(STATE_KEY, {"sessions": _bound(merged)})
    finally:
        store.close()
    print(
        f"[briefs] seeded hosted ledger: {len(local)} local + {len(hosted)} hosted "
        f"-> {len(_bound(merged))} session(s)"
    )


def _more_progressed(a: dict, b: dict) -> bool:
    """Does entry `a` represent more delivery progress than `b`? Terminal beats
    non-terminal; between two of the same class, more attempts wins."""
    rank = {"pending": 0, "failed": 1, "delivered": 2}
    ra, rb = rank.get(a.get("status"), 0), rank.get(b.get("status"), 0)
    if ra != rb:
        return ra > rb
    return a.get("attempts", 0) > b.get("attempts", 0)


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
