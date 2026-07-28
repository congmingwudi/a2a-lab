"""Drive the weekly cost sentinel (WS12) from the shell.

    uv run python scripts/cost_sentinel.py run      # fire one brief now
    uv run python scripts/cost_sentinel.py status   # deployment + recent runs
    uv run python scripts/cost_sentinel.py latest   # print the newest cost brief
    uv run python scripts/cost_sentinel.py reconcile # link briefs -> billed sessions
    uv run python scripts/cost_sentinel.py pause    # suppress the weekly cron
    uv run python scripts/cost_sentinel.py resume   # enable the weekly cron

The deployment is created PAUSED (setup_cost_sentinel.py); `run` works while
paused — that is the documented way to test a schedule without waiting for it.
The console's Coding Agents Telemetry section has the same Run button.

Sibling of scripts/obs_analysis.py, deliberately: same deployment shape, same
MCP server, same briefs table. The only thing that differs is `kind='cost'` on
the read, which is what keeps two analysts out of each other's feed.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

STATE_FILE = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "cost_sentinel.json"
BRIEF_KIND = "cost"

# How far after a run's start its brief may land and still be considered its
# output. Generous because the session does the work — several MCP round trips
# and a written brief — before save_brief fires; the first real firing took
# ~3 minutes. Tight enough that two firings in the same window would have to be
# deliberate, and the claimed-once rule catches that case anyway.
MATCH_WINDOW_S = 3600


def _state() -> dict:
    if not STATE_FILE.exists():
        print("sentinel not provisioned — run scripts/setup_cost_sentinel.py")
        raise SystemExit(1)
    return json.loads(STATE_FILE.read_text())


def cmd_run(client, state) -> None:
    client.beta.deployments.run(state["deployment_id"])
    print("fired — polling for the session id...")
    for _ in range(30):
        time.sleep(5)
        for dr in client.beta.deployment_runs.list(deployment_id=state["deployment_id"]):
            if dr.session_id:
                print(f"session: {dr.session_id}")
                print(
                    "watch: https://platform.claude.com/workspaces/default/sessions/"
                    f"{dr.session_id}"
                )
                print("brief lands in lab.obs_briefs (kind='cost') — cost_sentinel.py latest")
                return
            if getattr(dr, "error", None):
                print(f"run failed: {dr.error.type}: {dr.error.message}")
                return
    print("no session recorded yet — try cost_sentinel.py status")


def cmd_status(client, state) -> None:
    print(f"deployment: {state['deployment_id']} (agent {state['agent_id']})")
    print(f"schedule:   '{state.get('cron')}' {state.get('timezone')}")
    for i, dr in enumerate(client.beta.deployment_runs.list(deployment_id=state["deployment_id"])):
        if i >= 5:
            break
        outcome = dr.session_id or (
            f"{dr.error.type}: {dr.error.message}" if getattr(dr, "error", None) else "pending"
        )
        trigger = getattr(getattr(dr, "trigger_context", None), "type", "?")
        print(f"  {dr.created_at}  [{trigger}]  {outcome}")
    # Opportunistic: `status` is the command a person runs after a scheduled
    # firing, and it already holds both halves of the join. Best-effort — a
    # store that is unreachable must not break the status readout.
    try:
        cmd_reconcile(client, state)
    except Exception as exc:  # noqa: BLE001
        print(f"  (reconcile skipped: {type(exc).__name__})")


def cmd_latest(_client, _state) -> None:
    from observability.pg import PgObsStore

    briefs = PgObsStore().list_briefs(limit=1, kind=BRIEF_KIND)
    if not briefs:
        print("no cost briefs yet — run: uv run python scripts/cost_sentinel.py run")
        return
    b = briefs[0]
    print(f"# cost brief {b['brief_date']} (session {b['session_id']}, {b['queries_run']} queries)")
    print()
    print(b["brief_md"])


def _writer_client():
    """Same reader/writer split as the obs MCP server and pg_backfill: the
    standard env is `lab_reader`, and an UPDATE needs the writer."""
    from observability.pg import PgClient

    cluster = os.environ.get("A2ALAB_PG_CLUSTER_ARN")
    writer_secret = os.environ.get("A2ALAB_PG_WRITER_SECRET_ARN")
    if cluster and writer_secret:
        return PgClient(cluster_arn=cluster, secret_arn=writer_secret)
    return PgClient.from_env()


def _as_utc(value) -> "datetime | None":
    """Data API hands back timestamps as naive strings (UTC); the Anthropic SDK
    hands back aware datetimes. Normalise both to aware UTC before comparing —
    a naive/aware mix raises, and a silent local-time read would mis-match by
    the UTC offset, which is exactly the size of the matching window."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace(" ", "T").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def cmd_reconcile(client, state) -> None:
    """Fill `obs_briefs.session_id` by matching briefs to deployment runs.

    **Why this is reconciliation and not a fix at the write site.** `save_brief`
    takes `session_id` as a tool argument, and the agent does not know its own
    session id — nothing in the Managed Agents runtime tells it. So the column
    was null for every brief either analyst ever wrote. Stamping it from `run`
    would cover manual firings only; the weekly cron has no local runner at all,
    which is the whole point of scheduling it. The session id exists on the
    deployment RUN, so the join has to happen after the fact, from either side.

    Match rule: the newest run whose `created_at` is at or before the brief's,
    within MATCH_WINDOW_S. A session is claimed at most once — one firing writes
    one brief, so a second brief matching the same session means the guess is
    wrong and it is left null rather than duplicated onto both."""
    pg = _writer_client()
    pending = pg.execute(
        "SELECT id, brief_date, created_at FROM lab.obs_briefs "
        "WHERE kind = :kind AND session_id IS NULL ORDER BY id",
        {"kind": BRIEF_KIND},
    )
    if not pending:
        print("every cost brief already names its session")
        return

    runs = []
    for dr in client.beta.deployment_runs.list(deployment_id=state["deployment_id"]):
        started = _as_utc(getattr(dr, "created_at", None))
        if dr.session_id and started:
            runs.append((started, dr.session_id))
    runs.sort(reverse=True)
    if not runs:
        print(f"{len(pending)} brief(s) unlinked, but the deployment has no runs with a session")
        return

    claimed: set[str] = set()
    linked = 0
    for brief in pending:
        created = _as_utc(brief["created_at"])
        if created is None:
            print(f"  brief {brief['id']}: unreadable created_at — skipped")
            continue
        match = next(
            (
                (started, sid)
                for started, sid in runs
                if sid not in claimed
                and started <= created
                and (created - started).total_seconds() <= MATCH_WINDOW_S
            ),
            None,
        )
        if match is None:
            print(f"  brief {brief['id']} ({brief['brief_date']}): no run within the window")
            continue
        started, session_id = match
        claimed.add(session_id)
        pg.execute(
            "UPDATE lab.obs_briefs SET session_id = :sid WHERE id = :id AND session_id IS NULL",
            {"sid": session_id, "id": brief["id"]},
        )
        linked += 1
        gap = (created - started).total_seconds()
        print(f"  brief {brief['id']} ({brief['brief_date']}) -> {session_id}  (+{gap:.0f}s)")
    print(f"linked {linked}/{len(pending)}")


def cmd_pause(client, state) -> None:
    client.beta.deployments.pause(state["deployment_id"])
    print("paused — the weekly cron will not fire. Manual `run` still works.")


def cmd_resume(client, state) -> None:
    client.beta.deployments.unpause(state["deployment_id"])
    print(
        f"resumed — next firing on cron '{state.get('cron')}' {state.get('timezone')}. "
        "Missed occurrences are NOT backfilled."
    )


COMMANDS = {
    "run": cmd_run,
    "status": cmd_status,
    "latest": cmd_latest,
    "reconcile": cmd_reconcile,
    "pause": cmd_pause,
    "resume": cmd_resume,
}


def main() -> int:
    load_dotenv()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = COMMANDS.get(cmd)
    if not fn:
        print(f"usage: cost_sentinel.py [{'|'.join(COMMANDS)}]")
        return 2

    state = _state()
    # `latest` reads the store directly, so it must not need an Anthropic
    # client — the brief is readable whether or not the agent is reachable.
    if cmd == "latest":
        fn(None, state)
        return 0

    from anthropic import Anthropic

    fn(Anthropic(), state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
