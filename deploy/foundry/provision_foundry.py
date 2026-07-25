"""Provision (or re-provision) the WS3 Foundry side — codifies what was
first done by hand on 2026-07-22 so the Azure state is reproducible:

    uv run python deploy/foundry/provision_foundry.py

1. RemoteA2A project connection to the lab's hosted Agentforce A2A shim
   (the docs' EXACT ARM payload: category RemoteA2A + authType CustomKeys,
   api-version 2025-04-01-preview — a plausible-but-different shape
   resolves fine and then fails at tool time with an undiagnosable 424).
2. A new version of the prompt agent from FOUNDRY_INSTRUCTIONS in
   platforms.foundry.core (model from AZURE_FOUNDRY_MODEL_DEPLOYMENT) with
   the A2APreviewTool bound to that connection by FULL connection id.
3. Incoming A2A enabled + the agent card set (PATCH — the portal and the
   Python SDK cannot set the card yet), then both version cards verified.

Requires: az login (Entra ADC), AZURE_* and AF_SHIM_A2A_URL + A2ALAB_TOKEN
in .env. Idempotent: connection PUT overwrites, agent versions append,
endpoint PATCH re-applies.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env")

from platforms.foundry.core import (  # noqa: E402
    AGENT_NAME,
    FOUNDRY_INSTRUCTIONS,
    SHIM_CONNECTION_NAME,
    make_project_client,
    project_endpoint,
)

AGENT_CARD = {
    "description": (
        "Research assistant for the A2A interop lab, hosted on Microsoft "
        "Foundry Agent Service. Answers research and protocol questions; "
        "consults its Salesforce Agentforce twin for CRM data over A2A."
    ),
    "version": "1.0",
    "skills": [
        {
            "id": "ask",
            "name": "Ask the Foundry research agent",
            "description": (
                "Open-ended research and summarization; can consult the "
                "Salesforce Agentforce twin for CRM data."
            ),
        }
    ],
}


def _require(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"{name} unset — see plan/07-workstreams.md WS3")
    return value


def _entra_token(resource: str) -> str:
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential().get_token(f"{resource}/.default").token


def ensure_connection() -> None:
    sub = _require("AZURE_SUBSCRIPTION_ID")
    rg = os.environ.get("AZURE_RESOURCE_GROUP", "a2a-lab")
    endpoint = project_endpoint()
    # https://{account}.services.ai.azure.com/api/projects/{project}
    account = endpoint.split("//")[1].split(".")[0]
    project = endpoint.rstrip("/").rsplit("/", 1)[-1]
    url = (
        f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{rg}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account}"
        f"/projects/{project}/connections/{SHIM_CONNECTION_NAME}"
        "?api-version=2025-04-01-preview"
    )
    body = {
        "tags": None,
        "location": None,
        "name": SHIM_CONNECTION_NAME,
        "type": "Microsoft.MachineLearningServices/workspaces/connections",
        "properties": {
            "authType": "CustomKeys",
            "group": "ServicesAndApps",
            "category": "RemoteA2A",
            "expiryTime": None,
            "target": _require("AF_SHIM_A2A_URL"),
            "isSharedToAll": True,
            "sharedUserList": [],
            "Credentials": {"Keys": {"x-lab-token": _require("A2ALAB_TOKEN")}},
            "metadata": {"ApiType": "Azure"},
        },
    }
    r = httpx.put(
        url,
        json=body,
        headers={"Authorization": f"Bearer {_entra_token('https://management.azure.com')}"},
        timeout=60,
    )
    r.raise_for_status()
    print(f"connection: {SHIM_CONNECTION_NAME} ({r.json()['properties']['category']})")


def create_agent_version() -> None:
    from azure.ai.projects.models import A2APreviewTool, PromptAgentDefinition

    client = make_project_client()
    conn = client.connections.get(SHIM_CONNECTION_NAME)
    agent = client.agents.create_version(
        agent_name=AGENT_NAME,
        definition=PromptAgentDefinition(
            model=_require("AZURE_FOUNDRY_MODEL_DEPLOYMENT"),
            instructions=FOUNDRY_INSTRUCTIONS,
            tools=[A2APreviewTool(project_connection_id=conn.id)],
        ),
        description="A2A lab researcher (WS3) — provisioned by provision_foundry.py",
    )
    print(f"agent: {agent.name} version {agent.version}")


def enable_inbound_a2a() -> None:
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
        print(
            f"card {version}: protocolVersion={card.json().get('protocolVersion', '1.0/interfaces')}"
        )


def main() -> None:
    ensure_connection()
    create_agent_version()
    enable_inbound_a2a()
    print("smoke: uv run python scripts/matrix.py foundry-a2a --runs 1 --no-record")


if __name__ == "__main__":
    main()
