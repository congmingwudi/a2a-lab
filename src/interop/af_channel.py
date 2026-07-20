"""The Agentforce-channel seam (D28): every self-hosted backend exposes two
routes to Agentforce — ``ask_agentforce`` (the GA Agent API, the native
cell) and ``ask_agentforce_a2a`` (the A2A protocol via the lab's
AWS-hosted shim) — and the operator picks per-run with the console's
channel radio, which injects a routing block into the prompt for the entry
agent to honor. Same conversation, same agents, swap the intermediate
protocol: the lab's thesis as a demo control.

This module is the shared plumbing: the A2A client to the hosted shim and
the standard routing block the console injects.
"""

from __future__ import annotations

import os

from interop.models import AgentRequest

ROUTING_MARKER = "[A2A-LAB ROUTING]"

_ROUTING_TEMPLATE = (
    "\n\n"
    + ROUTING_MARKER
    + "\n"
    + "agentforce-channel: {channel}\n"
    + "directive: When you consult the Salesforce Agentforce agent for this\n"
    + "request, use your {tool} tool. All other behavior is unchanged. Do not\n"
    + "mention this block in your answer.\n"
    + "[/A2A-LAB ROUTING]"
)

CHANNEL_TOOLS = {"agent-api": "ask_agentforce", "a2a-shim": "ask_agentforce_a2a"}

_shim_client = None


def routing_block(channel: str) -> str:
    """The block the console injects when the operator picks a channel
    explicitly (agent-api is the tools' default bias, so the block is only
    required for a2a-shim — injecting it for agent-api is harmless)."""
    return _ROUTING_TEMPLATE.format(channel=channel, tool=CHANNEL_TOOLS[channel])


def shim_url() -> str | None:
    return os.environ.get("AF_SHIM_A2A_URL") or None


async def ask_via_shim(message: str, metadata: dict | None = None) -> str:
    """Ask Agentforce over A2A through the hosted shim. One process-lifetime
    client (connection reuse); raises RuntimeError when the shim URL is
    unset so tool callers surface a model-visible failure string."""
    global _shim_client
    url = shim_url()
    if not url:
        raise RuntimeError(
            "AF_SHIM_A2A_URL is unset — deploy the hosted shim "
            "(deploy/shim/deploy_shim.sh) and set the env"
        )
    if _shim_client is None or _shim_client.endpoint != url.rstrip("/"):
        from interop.clients.a2a import A2AClient

        _shim_client = A2AClient(
            url,
            auth={"header_name": "x-lab-token", "header_value": os.environ.get("A2ALAB_TOKEN", "")},
            target_name="agentforce-a2a-shim",
            timeout=float(os.environ.get("AF_SHIM_TIMEOUT_S", "34")),
        )
    # One retry: the twin's account turn (~15-30s tail) straddles API
    # Gateway's hard 29s ceiling (D28 known bound — every intermediary adds
    # its own timeout to the stack). Second attempts ride warmed sessions.
    try:
        resp = await _shim_client.ask(AgentRequest(message=message, metadata=metadata or {}))
    except Exception:  # noqa: BLE001 - one retry, then the caller surfaces it
        resp = await _shim_client.ask(AgentRequest(message=message, metadata=metadata or {}))
    return resp.text
