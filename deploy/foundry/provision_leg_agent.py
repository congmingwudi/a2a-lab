"""Provision the fan-out scenario's Foundry leg agent (WS8).

    uv run python deploy/foundry/provision_leg_agent.py            # create/update
    uv run python deploy/foundry/provision_leg_agent.py --show     # just report

Creates `a2alab-commercial-agent` — the Commercial/Legal business unit's agent
in the supplier-disruption fan-out — as a prompt agent with inbound A2A
enabled, so the orchestrator can reach it over the platform's native A2A.

Separate from provision_foundry.py on purpose: that script owns the WS3
*researcher* and its Agentforce A2A tool connection. This agent has NO tools —
it answers a scoped business question from model knowledge and must not
delegate onward. Giving it the shim connection would let it wander off the
scenario, which is exactly the determinism the fan-out depends on.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from orchestration.agents import COMMERCIAL  # noqa: E402
from platforms.foundry.core import make_project_client, project_endpoint  # noqa: E402

AGENT_NAME = COMMERCIAL.agent_name

# Both A2A generations, same as the researcher's card: Foundry itself speaks the
# 0.3 dialect, but a 1.x caller must find 1.x fields or it rejects the card.
AGENT_CARD = {
    "name": AGENT_NAME,
    "description": (
        "Commercial and contracts agent for the A2A lab's supplier-disruption "
        "fan-out — delay penalties, force majeure, at-risk commitments."
    ),
    "version": "1.0.0",
    "protocolVersion": "0.3.0",
    "preferredTransport": "JSONRPC",
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["text/plain"],
    "capabilities": {"streaming": False},
    "skills": [
        {
            "id": "commercial-position",
            "name": "Commercial position",
            "description": "Contractual exposure in a supply disruption.",
            "tags": ["contracts", "force-majeure", "penalties"],
        }
    ],
}


def _entra_token(resource: str) -> str:
    from observability.credentials import azure_credential

    # Explicit service principal, never DefaultAzureCredential (D39) — the same
    # rule that stopped the Foundry harvest passing locally for the wrong reason.
    return azure_credential().get_token(f"{resource}/.default").token


def create_agent_version() -> str:
    from azure.ai.projects.models import PromptAgentDefinition

    client = make_project_client()
    agent = client.agents.create_version(
        agent_name=AGENT_NAME,
        definition=PromptAgentDefinition(
            model=os.environ["AZURE_FOUNDRY_MODEL_DEPLOYMENT"],
            instructions=COMMERCIAL.instructions,
            # No tools. A fan-out leg answers and stops.
        ),
        description="A2A lab fan-out leg — Commercial/Legal (WS8)",
    )
    print(f"agent: {agent.name} version {agent.version}")
    return agent.version


def enable_inbound_a2a() -> None:
    """Turn on the agent's inbound A2A protocol endpoint, then PROVE it.

    The enabling field is `agent_endpoint.protocol_configuration`, not a
    boolean. A first version of this script sent `is_a2a_enabled: true` — the
    PATCH returned 200, the API silently ignored the unknown field, and the
    card 404'd with "Endpoint Protocol Not Enabled". So the card fetch below is
    not decoration: it is the only thing that distinguishes "enabled" from
    "accepted and discarded".
    """
    token = _entra_token("https://ai.azure.com")
    base = project_endpoint()
    r = httpx.patch(
        f"{base}/agents/{AGENT_NAME}?api-version=v1",
        json={
            "agent_card": AGENT_CARD,
            "agent_endpoint": {"protocol_configuration": {"responses": {}, "a2a": {}}},
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    r.raise_for_status()
    protocols = (r.json().get("agent_endpoint") or {}).get("protocols")
    print(f"inbound protocols: {protocols}")

    for version in ("v1.0", "v0.3"):
        card = httpx.get(
            f"{base}/agents/{AGENT_NAME}/endpoint/protocols/a2a/agentCard/{version}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        card.raise_for_status()
        print(f"card {version}: OK ({len(card.text)} bytes)")


def show() -> None:
    r = httpx.get(
        f"{project_endpoint()}/agents/{AGENT_NAME}?api-version=v1",
        headers={"Authorization": f"Bearer {_entra_token('https://ai.azure.com')}"},
        timeout=60,
    )
    print(f"GET {AGENT_NAME} -> {r.status_code}")
    print(r.text[:800])


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()
    if args.show:
        show()
        return
    create_agent_version()
    enable_inbound_a2a()
    print(
        "\nAdd to .env to route the fan-out's commercial leg here:\n"
        "  A2ALAB_LEG_COMMERCIAL_TARGET=foundry-commercial-a2a"
    )


if __name__ == "__main__":
    main()
