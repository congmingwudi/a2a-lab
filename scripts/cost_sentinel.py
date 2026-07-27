"""Drive the weekly cost sentinel (WS12) from the shell.

    uv run python scripts/cost_sentinel.py run      # fire one brief now
    uv run python scripts/cost_sentinel.py status   # deployment + recent runs
    uv run python scripts/cost_sentinel.py latest   # print the newest cost brief
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

STATE_FILE = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "cost_sentinel.json"
BRIEF_KIND = "cost"


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
