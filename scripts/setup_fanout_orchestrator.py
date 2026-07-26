"""Provision the Managed Agents fan-out orchestrator (WS8).

    uv run python scripts/setup_fanout_orchestrator.py            # create if missing
    uv run python scripts/setup_fanout_orchestrator.py --update   # push prompt/tools
    uv run python scripts/setup_fanout_orchestrator.py --recreate # new agent + env

Writes IDs to .a2alab/fanout_orchestrator.json, which orchestration/cma.py
reads at run time. Separate from setup_managed_agent.py on purpose: that owns
the WS2 research assistant with its Agentforce tool; this is a different agent
with a different job, one tool, and no research toolset at all.

The contrast this variant exists to show: on Managed Agents the agent is a
DECLARATIVE control-plane object — name, model, system prompt, tool schemas —
and a `custom` tool means the HOST executes it. So the fan-out runs in
orchestration/dispatch and the host owns concurrency, timeouts and the
partial-failure contract. Compare the ADK variant, which declares concurrency
in its agent graph with ParallelAgent.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

from orchestration.agents import (  # noqa: E402
    CMA_ORCHESTRATOR_NAME,
    FANOUT_TOOL,
    ORCHESTRATOR_PROMPT,
)
from orchestration.cma import STATE_FILE  # noqa: E402

# Deliberately NOT the prebuilt agent toolset. An orchestrator that can also
# search the web will answer the disruption itself instead of consulting the
# business units, which destroys the experiment — the fan-out has to be the
# only way it can learn anything.
AGENT_TOOLS = [FANOUT_TOOL]


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--recreate", action="store_true")
    ap.add_argument("--update", action="store_true", help="push prompt/tools to the existing agent")
    ap.add_argument(
        "--model",
        default=os.environ.get("A2ALAB_ORCHESTRATOR_MODEL") or "claude-sonnet-5",
        help="synthesis quality matters more here than leg latency does",
    )
    args = ap.parse_args()

    from anthropic import Anthropic

    client = Anthropic()

    if args.update:
        state = json.loads(STATE_FILE.read_text())
        agent = client.beta.agents.update(
            state["agent_id"],
            version=state["agent_version"],
            system=ORCHESTRATOR_PROMPT,
            tools=AGENT_TOOLS,
        )
        state["agent_version"] = agent.version
        STATE_FILE.write_text(json.dumps(state, indent=2))
        print(f"updated {agent.id} -> version {agent.version}")
        return

    if STATE_FILE.exists() and not args.recreate:
        print(f"already provisioned (use --update or --recreate): {STATE_FILE.read_text()}")
        return

    environment = client.beta.environments.create(
        name="a2a-lab-fanout",
        description="A2A lab — fan-out orchestrator sandbox (WS8)",
        config={"type": "cloud", "networking": {"type": "unrestricted"}},
    )
    print(f"environment: {environment.id}")

    agent = client.beta.agents.create(
        name=CMA_ORCHESTRATOR_NAME,
        model=args.model,
        description="Supply-disruption fan-out orchestrator (A2A interop lab, WS8)",
        system=ORCHESTRATOR_PROMPT,
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
    print("run it: uv run python scripts/run_fanout.py --orchestrator cma")


if __name__ == "__main__":
    main()
