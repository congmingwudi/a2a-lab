"""One-time control-plane setup for the Managed Agents (beta) Claude backend.

Creates (or reuses) the lab's environment + agent and writes their IDs to
.a2alab/managed.json, which platforms/claude/managed_backend.py reads at
request time. Safe to re-run: existing IDs are kept unless --recreate.

    uv run python scripts/setup_managed_agent.py            # create if missing
    uv run python scripts/setup_managed_agent.py --recreate # new agent version/env

Needs ANTHROPIC_API_KEY (or an `ant auth login` profile).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

from platforms.claude.core import RESEARCH_SYSTEM_PROMPT

STATE_FILE = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "managed.json"

ENVIRONMENT_NAME = "a2a-lab"
AGENT_NAME = "A2ALab Research Assistant"

AGENT_TOOLS = [
    # Full prebuilt toolset: bash/read/write/edit/glob/grep/web_fetch/web_search.
    # web_search + web_fetch are what make it a real research assistant.
    {"type": "agent_toolset_20260401", "default_config": {"enabled": True}},
    # Path B: the managed agent asks Agentforce via a custom tool; our
    # orchestrator (managed_backend.py) executes it host-side so Salesforce
    # credentials never enter the sandbox.
    {
        "type": "custom",
        "name": "ask_agentforce",
        "description": (
            "Ask the Salesforce Agentforce service agent a question and get "
            "its answer. Use when the request needs Salesforce-side knowledge "
            "(cases, org data, service workflows)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question for Agentforce"}
            },
            "required": ["question"],
        },
    },
]


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--recreate", action="store_true")
    parser.add_argument(
        "--model", default=os.environ.get("CLAUDE_AGENT_MODEL") or "claude-haiku-4-5"
    )
    args = parser.parse_args()

    if STATE_FILE.exists() and not args.recreate:
        state = json.loads(STATE_FILE.read_text())
        print(f"Already provisioned (use --recreate to redo): {state}")
        return

    from anthropic import Anthropic

    client = Anthropic()

    environment = client.beta.environments.create(
        name=ENVIRONMENT_NAME,
        description="A2A interop lab sandbox",
        config={"type": "cloud", "networking": {"type": "unrestricted"}},
    )
    print(f"environment: {environment.id}")

    agent = client.beta.agents.create(
        name=AGENT_NAME,
        model=args.model,
        description="Research assistant for the A2A interop lab",
        system=RESEARCH_SYSTEM_PROMPT,
        tools=AGENT_TOOLS,
    )
    print(f"agent: {agent.id} (version {agent.version})")

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(
            {
                "agent_id": agent.id,
                "agent_version": agent.version,
                "environment_id": environment.id,
                "model": args.model,
            },
            indent=2,
        )
    )
    print(f"wrote {STATE_FILE}")


if __name__ == "__main__":
    main()
