"""Provision the Managed Agents fan-out orchestrator (WS8, WS7 item 4).

    uv run python scripts/setup_fanout_orchestrator.py            # create if missing
    uv run python scripts/setup_fanout_orchestrator.py --update   # push prompt/tools
    uv run python scripts/setup_fanout_orchestrator.py --recreate # new agent + env
    uv run python scripts/setup_fanout_orchestrator.py --mcp      # + the remote-MCP variant

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

`--mcp` adds the second topology **without adding a second agent**. It creates a
vault holding the fan-out MCP server's bearer token and records the prompt and
URL in .a2alab/fanout_mcp_orchestrator.json; the run then passes them as
`agent_with_overrides` on sessions.create. Keeping one agent is deliberate — two
agents would drift, and the experiment's whole claim is that the only difference
between the runs is where the tools execute.
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
    mcp_orchestrator_prompt,
    mcp_orchestrator_prompt_async,
)
from orchestration.cma import MCP_STATE_FILE, STATE_FILE  # noqa: E402

# Deliberately NOT the prebuilt agent toolset. An orchestrator that can also
# search the web will answer the disruption itself instead of consulting the
# business units, which destroys the experiment — the fan-out has to be the
# only way it can learn anything.
AGENT_TOOLS = [FANOUT_TOOL]


MCP_FILE = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "fanout_mcp.json"
VAULT_NAME = "A2ALab Business Units"


def _setup_mcp(client) -> None:
    """Wire the remote MCP fan-out server to the existing orchestrator agent.

    No new agent: the tool inventory and prompt are recorded here and applied
    per run via agent_with_overrides. What genuinely has to exist server-side is
    the vault — Anthropic's orchestration layer calls the MCP server directly,
    so the bearer token has to be somewhere it can reach, and that is a vault
    credential keyed by the server URL rather than anything in our process.
    """
    from fanout_mcp.tools import roster, roster_async, timeout_note, timeout_note_async

    if not STATE_FILE.exists():
        raise SystemExit("provision the orchestrator first (run without --mcp)")
    if not MCP_FILE.exists():
        raise SystemExit(
            f"no {MCP_FILE} — deploy the server first: "
            "deploy/fanout/build_zip.sh && deploy/fanout/deploy_fanout.sh"
        )
    base = json.loads(STATE_FILE.read_text())
    mcp = json.loads(MCP_FILE.read_text())
    url = (mcp.get("url") or "").rstrip("/")
    token = mcp.get("token") or os.environ.get("A2ALAB_FANOUT_MCP_TOKEN")
    if not (url and token):
        raise SystemExit(f"{MCP_FILE} is missing url or token — re-run deploy_fanout.sh")

    vault = client.beta.vaults.create(display_name=VAULT_NAME)
    # static_bearer, not mcp_oauth: this server is ours and authenticates with a
    # fixed token, so there is no refresh grant to configure. The credential is
    # matched to the server by URL at call time.
    credential = client.beta.vaults.credentials.create(
        vault_id=vault.id,
        display_name="fan-out MCP bearer",
        auth={"type": "static_bearer", "mcp_server_url": url, "token": token},
    )
    print(f"vault: {vault.id} (credential {credential.id})")

    state = {
        "agent_id": base["agent_id"],
        "environment_id": base["environment_id"],
        "vault_id": vault.id,
        "credential_id": credential.id,
        "mcp_url": url,
        "system": mcp_orchestrator_prompt(roster(), timeout_note()),
        # The fire-then-poll prompt for the SAME agent/server (WS11 items 6-7).
        # Both prompts are recorded here and applied per run via
        # agent_with_overrides; cma.py picks by dispatch_mode. The tool sets
        # (consult_* and submit_*/check_task) both live on the deployed server.
        "system_async": mcp_orchestrator_prompt_async(roster_async(), timeout_note_async()),
    }
    MCP_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    MCP_STATE_FILE.write_text(json.dumps(state, indent=2))
    print(f"wrote {MCP_STATE_FILE}")
    print("run it: uv run python scripts/run_fanout.py --orchestrator cma-mcp")
    print("     or: uv run python scripts/run_fanout.py --orchestrator cma-mcp --async")


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--recreate", action="store_true")
    ap.add_argument("--update", action="store_true", help="push prompt/tools to the existing agent")
    ap.add_argument(
        "--mcp",
        action="store_true",
        help="provision the remote-MCP variant (vault + overrides) on the existing agent",
    )
    ap.add_argument(
        "--model",
        default=os.environ.get("A2ALAB_ORCHESTRATOR_MODEL") or "claude-sonnet-5",
        help="synthesis quality matters more here than leg latency does",
    )
    args = ap.parse_args()

    from anthropic import Anthropic

    client = Anthropic()

    if args.mcp:
        _setup_mcp(client)
        return

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
