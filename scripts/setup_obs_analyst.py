"""One-time control-plane setup for the observability analyst (M11.5/D22).

Creates the "A2ALab Observability Analyst" managed agent (no toolset beyond
the query_obs_store custom tool — analysis only, no web access needed) and
writes the IDs to .a2alab/obs_analyst.json. Reuses the a2a-lab environment
from .a2alab/managed.json.

    uv run python scripts/setup_obs_analyst.py              # create if missing
    uv run python scripts/setup_obs_analyst.py --recreate   # replace
    uv run python scripts/setup_obs_analyst.py --run        # + run one analysis now

A nightly scheduled deployment is a later step (mirror setup_brief_agent.py)
once the ad-hoc briefs prove useful — every firing costs a real session.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

STATE_DIR = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab"))
STATE_FILE = STATE_DIR / "obs_analyst.json"
MANAGED_FILE = STATE_DIR / "managed.json"

AGENT_NAME = "A2ALab Observability Analyst"


def main() -> int:
    load_dotenv()
    from anthropic import Anthropic

    from observability.analyst import ANALYST_SYSTEM_PROMPT, QUERY_TOOL_DEF

    parser = argparse.ArgumentParser()
    parser.add_argument("--recreate", action="store_true")
    parser.add_argument("--run", action="store_true", help="run one analysis after setup")
    parser.add_argument(
        "--model", default=os.environ.get("A2ALAB_ANALYST_MODEL") or "claude-sonnet-5"
    )
    args = parser.parse_args()

    if STATE_FILE.exists() and not args.recreate:
        print(f"already provisioned ({STATE_FILE}) — use --recreate to replace")
    else:
        if not MANAGED_FILE.exists():
            print("no .a2alab/managed.json — run scripts/setup_managed_agent.py first")
            return 1
        env_id = json.loads(MANAGED_FILE.read_text())["environment_id"]
        client = Anthropic()
        agent = client.beta.agents.create(
            name=AGENT_NAME,
            model=args.model,
            system=ANALYST_SYSTEM_PROMPT,
            description="Interprets the lab's harvested observability store (M11.5).",
            tools=[QUERY_TOOL_DEF],
        )
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps(
                {
                    "agent_id": agent.id,
                    "agent_version": agent.version,
                    "environment_id": env_id,
                    "model": args.model,
                },
                indent=1,
            )
        )
        print(f"created {AGENT_NAME}: {agent.id} v{agent.version} (model {args.model})")
        print(f"ids -> {STATE_FILE}")

    if args.run:
        from observability.analyst import ObsAnalyst

        result = asyncio.run(ObsAnalyst().run_analysis())
        print(
            f"analysis done: {result['queries_run']} queries, "
            f"brief -> {result['brief_path']} (session {result['session_id']})"
        )
        print("\n" + result["brief"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
