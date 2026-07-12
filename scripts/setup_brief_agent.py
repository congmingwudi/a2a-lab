"""One-time control-plane setup for the ASYNC account-brief pattern (D16).

Creates:
1. the "A2ALab Account Intelligence Researcher" managed agent (web research
   toolset + the save_account_brief custom tool), and
2. a SCHEDULED DEPLOYMENT — Anthropic's platform-native cron — that fires a
   research session daily. This is the piece the async experiment exists to
   exercise: the long-running job is scheduled on the Claude platform, not
   by the lab host.

The lab host's `python -m briefs --watch` services each fired session
(executes the Salesforce delivery tool host-side).

    uv run python scripts/setup_brief_agent.py             # create if missing
    uv run python scripts/setup_brief_agent.py --recreate  # replace

Cost note: every scheduled firing runs a real multi-minute research session
on CLAUDE_BRIEF_MODEL. Pause anytime:
    client.beta.deployments.pause(deployment_id)

Reuses the a2a-lab environment from .a2alab/managed.json when present.
Writes IDs to .a2alab/brief.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

from briefs.runner import BRIEF_SYSTEM_PROMPT, KICKOFF_TEMPLATE, SAVE_TOOL_DEF

STATE_DIR = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab"))
STATE_FILE = STATE_DIR / "brief.json"
MANAGED_FILE = STATE_DIR / "managed.json"

AGENT_NAME = "A2ALab Account Intelligence Researcher"
DEPLOYMENT_NAME = "A2ALab Daily Account Brief"

AGENT_TOOLS = [
    {"type": "agent_toolset_20260401", "default_config": {"enabled": True}},
    SAVE_TOOL_DEF,
]


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--recreate", action="store_true")
    parser.add_argument(
        "--model", default=os.environ.get("CLAUDE_BRIEF_MODEL") or "claude-sonnet-5"
    )
    parser.add_argument(
        "--accounts", default=os.environ.get("A2ALAB_BRIEF_ACCOUNTS") or "Omega, Inc."
    )
    parser.add_argument("--cron", default=os.environ.get("A2ALAB_BRIEF_CRON") or "0 6 * * *")
    parser.add_argument("--timezone", default=os.environ.get("A2ALAB_BRIEF_TZ") or "America/Denver")
    args = parser.parse_args()

    if STATE_FILE.exists() and not args.recreate:
        print(f"Already provisioned (use --recreate to redo): {STATE_FILE.read_text()}")
        return

    from anthropic import Anthropic

    client = Anthropic()

    # Environment: reuse the lab's existing sandbox env if provisioned.
    env_id = None
    if MANAGED_FILE.exists():
        env_id = json.loads(MANAGED_FILE.read_text()).get("environment_id")
    if not env_id:
        environment = client.beta.environments.create(
            name="a2a-lab",
            description="A2A interop lab sandbox",
            config={"type": "cloud", "networking": {"type": "unrestricted"}},
        )
        env_id = environment.id
    print(f"environment: {env_id}")

    agent = client.beta.agents.create(
        name=AGENT_NAME,
        model=args.model,
        description=(
            "Long-running daily account-intelligence researcher for the A2A "
            "interop lab (async pattern, ADR D16). Delivers briefs to "
            "Salesforce via the save_account_brief custom tool."
        ),
        system=BRIEF_SYSTEM_PROMPT,
        tools=AGENT_TOOLS,
    )
    print(f"agent: {agent.id} (version {agent.version}, model {args.model})")

    kickoff = KICKOFF_TEMPLATE.format(accounts=args.accounts, extra="")
    deployment = client.beta.deployments.create(
        name=DEPLOYMENT_NAME,
        agent=agent.id,
        environment_id=env_id,
        initial_events=[{"type": "user.message", "content": [{"type": "text", "text": kickoff}]}],
        schedule={"type": "cron", "expression": args.cron, "timezone": args.timezone},
    )
    upcoming = getattr(getattr(deployment, "schedule", None), "upcoming_runs_at", None)
    print(f"deployment: {deployment.id} (cron '{args.cron}' {args.timezone})")
    if upcoming:
        print(f"next runs: {upcoming[:3]}")

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(
            {
                "agent_id": agent.id,
                "agent_version": agent.version,
                "environment_id": env_id,
                "deployment_id": deployment.id,
                "model": args.model,
                "accounts": args.accounts,
                "cron": args.cron,
                "timezone": args.timezone,
            },
            indent=2,
        )
    )
    print(f"wrote {STATE_FILE}")
    print(
        "\nDaily runs will bill real research sessions. Pause with:\n"
        f"  uv run python - <<'EOF'\nfrom anthropic import Anthropic\n"
        f"Anthropic().beta.deployments.pause('{deployment.id}')\nEOF"
    )


if __name__ == "__main__":
    main()
