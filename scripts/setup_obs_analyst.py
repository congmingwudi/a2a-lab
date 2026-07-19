"""Control-plane setup for the observability analyst (M11.5, D22/D23).

Hosted mode (default, ADR D23) — no driver loop, everything server-servable:
  creates a vault + static_bearer credential for the obs MCP server (URL and
  token from .a2alab/obs_mcp.json — run deploy/obs/expose_mcp.sh first),
  the "A2ALab Observability Analyst" agent wired to that MCP server
  (query_obs_store + save_brief), and a nightly scheduled deployment which
  is immediately PAUSED (real sessions bill per firing). Fire ad-hoc runs
  with scripts/obs_analysis.py run, or the console's Observability section.

    uv run python scripts/setup_obs_analyst.py                # create if missing
    uv run python scripts/setup_obs_analyst.py --recreate     # replace
    uv run python scripts/setup_obs_analyst.py --run          # + one manual run now

Legacy local mode (D22 prototype, laptop-bound custom tool over sqlite):
    uv run python scripts/setup_obs_analyst.py --local [--run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

STATE_DIR = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab"))
STATE_FILE = STATE_DIR / "obs_analyst.json"
MANAGED_FILE = STATE_DIR / "managed.json"
MCP_FILE = STATE_DIR / "obs_mcp.json"

AGENT_NAME = "A2ALab Observability Analyst"
MCP_SERVER_NAME = "obs-store"
VAULT_NAME = "A2ALab Obs Store"
DEPLOYMENT_NAME = "A2ALab Obs Analysis Nightly"

HOSTED_SYSTEM_PROMPT = """You are the observability analyst for the A2A Interop Lab — a \
cross-platform agent-to-agent experiment rig (Salesforce Agentforce ↔ Claude, REST/MCP/A2A \
protocols compared side by side).

Your input is the lab's hosted observability store (Aurora Postgres, schema `lab`), reachable \
only through the query_obs_store tool (read-only SQL). lab.trace_events is the lab's own wire \
view of every hop; lab.obs_sessions / lab.obs_events are what each *platform* recorded \
internally about the same runs, joined via trace_events.platform_ref = obs_sessions.native_id. \
Usage fields are jsonb — use -> / ->> operators.

Investigate before writing: volumes and time span; error hops and their causes \
(status='error' — look at latency_ms near timeout budgets, e.g. the documented ~45s bridge cap \
and cold-start session provisioning); latency by protocol and target; token spend from \
usage_json and where it concentrates; per-platform coverage gaps from lab.obs_harvest. Compare \
platforms where the data allows it. Every claim must come from a query you actually ran — \
include the number. Flag anomalies worth a human's attention, and say plainly when the data is \
too thin to conclude something.

When you are done, deliver the findings brief by calling save_brief exactly once with the \
complete markdown (under ~500 words, leading with the two or three findings that matter) and \
your query count. The brief in save_brief IS the deliverable — do not only reply with text."""

KICKOFF = """Analyze the lab's observability store as of now and produce today's findings \
brief. Start by checking lab.obs_harvest freshness and the row counts of each table so the \
brief states what window and volume it covers. Finish by saving the brief with save_brief."""


def _setup_hosted(client, args) -> dict:
    env_id = json.loads(MANAGED_FILE.read_text())["environment_id"]
    mcp = json.loads(MCP_FILE.read_text())
    if not mcp.get("url"):
        print("no MCP URL in .a2alab/obs_mcp.json — run deploy/obs/expose_mcp.sh first")
        raise SystemExit(1)
    mcp_url = mcp["url"].rstrip("/")

    vault = client.beta.vaults.create(display_name=VAULT_NAME)
    credential = client.beta.vaults.credentials.create(
        vault_id=vault.id,
        display_name="obs MCP bearer",
        auth={"type": "static_bearer", "mcp_server_url": mcp_url, "token": mcp["token"]},
    )
    print(f"vault: {vault.id} (credential {credential.id})")

    agent = client.beta.agents.create(
        name=AGENT_NAME,
        model=args.model,
        description="Interprets the lab's hosted observability store (M11.5/D23).",
        system=HOSTED_SYSTEM_PROMPT,
        mcp_servers=[{"type": "url", "name": MCP_SERVER_NAME, "url": mcp_url}],
        # Explicit always_allow: this workspace evaluates MCP tools as "ask"
        # by default, which would deadlock unattended deployment runs (no
        # client is connected to confirm — the whole point of D23).
        tools=[
            {
                "type": "mcp_toolset",
                "mcp_server_name": MCP_SERVER_NAME,
                "default_config": {
                    "enabled": True,
                    "permission_policy": {"type": "always_allow"},
                },
            }
        ],
    )
    print(f"agent: {agent.id} v{agent.version} (model {args.model})")

    deployment = client.beta.deployments.create(
        name=DEPLOYMENT_NAME,
        agent=agent.id,
        environment_id=env_id,
        vault_ids=[vault.id],
        initial_events=[{"type": "user.message", "content": [{"type": "text", "text": KICKOFF}]}],
        schedule={"type": "cron", "expression": args.cron, "timezone": args.timezone},
    )
    client.beta.deployments.pause(deployment.id)
    print(f"deployment: {deployment.id} (cron '{args.cron}' {args.timezone}) — created PAUSED")
    print("fire ad-hoc: uv run python scripts/obs_analysis.py run")

    return {
        "mode": "hosted",
        "agent_name": AGENT_NAME,
        "agent_id": agent.id,
        "agent_version": agent.version,
        "environment_id": env_id,
        "vault_id": vault.id,
        "credential_id": credential.id,
        "deployment_id": deployment.id,
        "mcp_url": mcp_url,
        "model": args.model,
    }


def _setup_local(client, args) -> dict:
    from observability.analyst import ANALYST_SYSTEM_PROMPT, QUERY_TOOL_DEF

    env_id = json.loads(MANAGED_FILE.read_text())["environment_id"]
    agent = client.beta.agents.create(
        name=AGENT_NAME,
        model=args.model,
        system=ANALYST_SYSTEM_PROMPT,
        description="Interprets the lab's harvested observability store (M11.5, local mode).",
        tools=[QUERY_TOOL_DEF],
    )
    print(f"agent: {agent.id} v{agent.version} (model {args.model}, local custom-tool mode)")
    return {
        "mode": "local",
        "agent_id": agent.id,
        "agent_version": agent.version,
        "environment_id": env_id,
        "model": args.model,
    }


def _manual_run(client) -> None:
    state = json.loads(STATE_FILE.read_text())
    if state.get("mode") != "hosted":
        from observability.analyst import ObsAnalyst

        result = asyncio.run(ObsAnalyst().run_analysis())
        print(f"analysis done: {result['queries_run']} queries -> {result['brief_path']}")
        print("\n" + result["brief"])
        return

    deployment_id = state["deployment_id"]
    client.beta.deployments.run(deployment_id)
    print("manual run fired — waiting for the deployment run to record a session...")
    session_id = None
    for _ in range(30):
        time.sleep(5)
        runs = client.beta.deployment_runs.list(deployment_id=deployment_id)
        for run in runs:
            if run.session_id:
                session_id = run.session_id
                break
            if getattr(run, "error", None):
                print(f"run failed: {run.error.type}: {run.error.message}")
                return
        if session_id:
            break
    if not session_id:
        print("no session recorded yet — check scripts/obs_analysis.py status later")
        return
    print(f"session: {session_id}")
    print(f"watch: https://platform.claude.com/workspaces/default/sessions/{session_id}")
    print(
        "the brief lands in lab.obs_briefs when the session finishes "
        "(scripts/obs_analysis.py latest)"
    )


def main() -> int:
    load_dotenv()
    from anthropic import Anthropic

    parser = argparse.ArgumentParser()
    parser.add_argument("--recreate", action="store_true")
    parser.add_argument("--run", action="store_true", help="fire one analysis after setup")
    parser.add_argument("--local", action="store_true", help="legacy D22 laptop mode")
    parser.add_argument(
        "--model", default=os.environ.get("A2ALAB_ANALYST_MODEL") or "claude-sonnet-5"
    )
    parser.add_argument("--cron", default="0 6 * * *")
    parser.add_argument("--timezone", default="America/New_York")
    args = parser.parse_args()

    client = Anthropic()
    if STATE_FILE.exists() and not args.recreate:
        print(f"already provisioned ({STATE_FILE}) — use --recreate to replace")
    else:
        if not MANAGED_FILE.exists():
            print("no .a2alab/managed.json — run scripts/setup_managed_agent.py first")
            return 1
        state = _setup_local(client, args) if args.local else _setup_hosted(client, args)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=1))
        print(f"ids -> {STATE_FILE}")

    if args.run:
        _manual_run(client)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
