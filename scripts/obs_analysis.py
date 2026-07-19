"""Drive the hosted observability analyst (D23) from the shell.

    uv run python scripts/obs_analysis.py run       # fire one analysis now
    uv run python scripts/obs_analysis.py status    # deployment + recent runs
    uv run python scripts/obs_analysis.py latest    # print the newest brief
    uv run python scripts/obs_analysis.py pause     # suppress the nightly cron
    uv run python scripts/obs_analysis.py resume    # re-enable the nightly cron

The deployment is created PAUSED (setup_obs_analyst.py); `run` works while
paused. The console's Observability section has the same Run button.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

STATE_FILE = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "obs_analyst.json"


def _state() -> dict:
    if not STATE_FILE.exists():
        print("analyst not provisioned — run scripts/setup_obs_analyst.py")
        raise SystemExit(1)
    state = json.loads(STATE_FILE.read_text())
    if state.get("mode") != "hosted":
        print("analyst is in local (D22) mode — hosted commands need --recreate --hosted setup")
        raise SystemExit(1)
    return state


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
                print("brief lands in lab.obs_briefs when done (obs_analysis.py latest)")
                return
            if getattr(dr, "error", None):
                print(f"run failed: {dr.error.type}: {dr.error.message}")
                return
    print("no session recorded yet — try obs_analysis.py status")


def cmd_status(client, state) -> None:
    print(f"deployment: {state['deployment_id']} (agent {state['agent_id']})")
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

    briefs = PgObsStore().list_briefs(limit=1)
    if not briefs:
        print("no briefs yet")
        return
    b = briefs[0]
    print(f"# brief {b['brief_date']} (session {b['session_id']}, {b['queries_run']} queries)\n")
    print(b["brief_md"])


def cmd_pause(client, state) -> None:
    client.beta.deployments.pause(state["deployment_id"])
    print("paused — nightly cron suppressed (manual runs still work)")


def cmd_resume(client, state) -> None:
    client.beta.deployments.unpause(state["deployment_id"])
    print("resumed — nightly cron active")


def main() -> int:
    load_dotenv()
    from anthropic import Anthropic

    commands = {
        "run": cmd_run,
        "status": cmd_status,
        "latest": cmd_latest,
        "pause": cmd_pause,
        "resume": cmd_resume,
    }
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd not in commands:
        print(f"usage: obs_analysis.py [{'|'.join(commands)}]")
        return 2
    commands[cmd](Anthropic(), _state())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
