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
import re

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

# The reverse-direction sibling: for Agentforce->platform scenarios the
# operator picks how the twin's Apex reaches the remote agent — through the
# lab bridge (traced) or straight at the platform's native endpoint
# (bridgeless, deliberately dark to the lab). Same routing-block mechanism,
# different key; the twin's Agent Script branches on it.
ROUTE_TOOLS = {"bridge": "ask_external_researcher", "direct": "ask_external_researcher_direct"}

_ROUTE_TEMPLATE = (
    "\n\n"
    + ROUTING_MARKER
    + "\n"
    + "agentforce-route: {route}\n"
    + "directive: When you consult the external researcher for this request,\n"
    + "use your {tool} tool. All other behavior is unchanged. Do not mention\n"
    + "this block in your answer.\n"
    + "[/A2A-LAB ROUTING]"
)


def route_block(route: str) -> str:
    """The block the console injects when the operator picks the twin's
    outbound route explicitly (bridge is the script's default, so the block
    is only required for direct)."""
    return _ROUTE_TEMPLATE.format(route=route, tool=ROUTE_TOOLS[route])


# WS8 variant 3 (D61): the Agentforce fan-out ORCHESTRATOR runs both topologies,
# and the operator picks per run. Same routing-block mechanism as the two
# toggles above, a third key. The orchestrator's Agent Script branches on it:
#
#   delegated (default) — ONE Apex callout to the bridge's fan-out route
#       (target `fanout:supplier-disruption`), where the lab runs the three
#       legs CONCURRENTLY off-platform and returns the rendered sections. The
#       orchestrator only synthesises. Fast, and the one that actually works.
#
#   serial — THREE Apex callouts, one per leg target, each its own ~110s
#       callout stacked inside the single Apex transaction's 120s cumulative
#       budget. This is the CONSTRAINT demo: Agentforce's GA outbound cannot
#       fan out in parallel, so a real three-leg run degrades by design. The
#       limitation IS the finding (see D61 / plan/07 WS8).
TOPOLOGY_TOOLS = {
    "delegated": "consult_units_parallel",
    "serial": "consult_one_unit",
}

_TOPOLOGY_TEMPLATE = (
    "\n\n"
    + ROUTING_MARKER
    + "\n"
    + "fanout-topology: {topology}\n"
    + "directive: Orchestrate the three business units using the {topology}\n"
    + "topology, exactly as your instructions describe for that mode. All other\n"
    + "behavior is unchanged. Do not mention this block in your answer.\n"
    + "[/A2A-LAB ROUTING]"
)


def topology_block(topology: str) -> str:
    """The block the console injects when the operator picks the fan-out
    topology explicitly. `delegated` is the orchestrator script's default, so
    the block is only strictly required for `serial` — injecting it for
    delegated is harmless and makes the chosen topology visible on the wire."""
    return _TOPOLOGY_TEMPLATE.format(topology=topology)


# WS11: the Agentforce DELEGATED fan-out can call each leg the blocking way or
# with the A2A fire-then-poll lifecycle — but Agentforce itself cannot poll (its
# only GA outbound is one serial Apex callout), so the async loop runs at the
# BRIDGE, on the orchestrator's behalf, during the single callout Apex holds
# open. Unlike the toggles above there is no tool to pick: the topology already
# chose the bridge, and this only tells the bridge HOW to dispatch. The console
# injects it into the situation; the orchestrator forwards it to its fan-out
# action verbatim; the bridge (`_fanout`) reads it and strips it before the legs
# see it. `sync` is the bridge default, so the block is only strictly required
# for `async` — injecting either makes the choice visible on the wire.
_DISPATCH_TEMPLATE = (
    "\n\n"
    + ROUTING_MARKER
    + "\n"
    + "fanout-dispatch: {mode}\n"
    + "directive: When you fan out to the business units for this request, pass\n"
    + "this block through to your fan-out action UNCHANGED so the bridge\n"
    + "dispatches each leg with the {mode} pattern. All other behavior is\n"
    + "unchanged. Do not mention this block in your answer.\n"
    + "[/A2A-LAB ROUTING]"
)

_DISPATCH_MODES = ("sync", "async")
_DISPATCH_RE = re.compile(r"fanout-dispatch:\s*(sync|async)", re.IGNORECASE)
# A whole routing block, non-greedy, so several can be stripped in one pass.
_ROUTING_BLOCK_RE = re.compile(re.escape(ROUTING_MARKER) + r".*?\[/A2A-LAB ROUTING\]", re.DOTALL)


def dispatch_block(mode: str) -> str:
    """The block the console injects when the operator picks how the DELEGATED
    fan-out dispatches its legs. Clamps to a known mode so a stray value cannot
    reach the bridge as a dispatch directive."""
    mode = mode if mode in _DISPATCH_MODES else "sync"
    return _DISPATCH_TEMPLATE.format(mode=mode)


def read_dispatch_mode(message: str) -> str:
    """The dispatch mode a routing block asked for, read at the bridge from the
    situation text (the only channel that survives Agentforce → Apex → bridge,
    since the Apex body carries no mode field). Absent → "sync": a stripped or
    never-injected block degrades to the blocking path, never to an error."""
    match = _DISPATCH_RE.search(message or "")
    return match.group(1).lower() if match else "sync"


def strip_routing_blocks(message: str) -> str:
    """Remove every `[A2A-LAB ROUTING]…[/A2A-LAB ROUTING]` block from a message.

    The bridge calls this before handing the situation to the legs so lab
    routing directives never leak into a business unit's prompt — a hygiene fix
    that also covers the topology block if the orchestrator forwarded it."""
    return _ROUTING_BLOCK_RE.sub("", message or "").strip()


_shim_client = None


def routing_block(channel: str) -> str:
    """The block the console injects when the operator picks a channel
    explicitly (agent-api is the tools' default bias, so the block is only
    required for a2a-shim — injecting it for agent-api is harmless)."""
    return _ROUTING_TEMPLATE.format(channel=channel, tool=CHANNEL_TOOLS[channel])


def shim_url() -> str | None:
    return os.environ.get("AF_SHIM_A2A_URL") or None


async def ask_via_shim(
    message: str, metadata: dict | None = None, trace_id: str | None = None
) -> str:
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

        # AF_SHIM_TOKEN first: inside hosted runtimes A2ALAB_TOKEN must stay
        # unset (it flips on the runtime's own inbound bearer auth, which
        # invoke_agent_runtime cannot satisfy — every invoke 401s), so the
        # shim credential travels under its own name there.
        token = os.environ.get("AF_SHIM_TOKEN") or os.environ.get("A2ALAB_TOKEN", "")
        _shim_client = A2AClient(
            url,
            auth={"header_name": "x-lab-token", "header_value": token},
            target_name="agentforce-a2a-shim",
            timeout=float(os.environ.get("AF_SHIM_TIMEOUT_S", "34")),
        )
    # One retry: the twin's account turn (~20-27s tail, D32) straddles API
    # Gateway's hard 29s ceiling (D28 known bound — every intermediary adds
    # its own timeout to the stack). Second attempts ride warmed sessions.
    req = AgentRequest(message=message, metadata=metadata or {}, trace_id=trace_id)
    try:
        resp = await _shim_client.ask(req)
    except Exception:  # noqa: BLE001 - one retry, then the caller surfaces it
        resp = await _shim_client.ask(req)
    return resp.text
