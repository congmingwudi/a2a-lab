"""Google ADK research agent (WS2) — the Gemini-brained twin of the Claude
and OpenAI researchers, deployed to Vertex AI Agent Engine where it serves
the lab's first platform-native A2A endpoint.

This module holds the framework-independent core: the system prompt and the
``ask_agentforce`` tool function. ADK wraps plain Python functions as tools
(name, docstring, and signature become the tool schema), so the same
function serves the local dev runner and the Agent Engine deployment.
Framework imports live in agent.py to keep this importable without the
``gcp`` extra.
"""

from __future__ import annotations

import os

from interop import delegation
from interop.models import AgentRequest

ADK_RESEARCH_SYSTEM_PROMPT = (
    "You are a research assistant participating in a cross-platform "
    "agent-to-agent interoperability lab. Another agent (often a Salesforce "
    "Agentforce agent talking to an end user) delegates open-ended research "
    "and summarization questions to you. Answer with a concise, "
    "well-organized summary: lead with the direct answer, then 2-4 "
    "supporting points. Keep answers under 250 words - your reply is folded "
    "into another agent's response. Do not ask clarifying questions; make "
    "reasonable assumptions and state them."
)

# One process-lifetime client so the OAuth token survives across tool calls
# (mirror of the sdk/openai backends).
_agentforce_client = None


def _get_agentforce_client():
    global _agentforce_client
    if _agentforce_client is None:
        from platforms.agentforce.client import AgentforceClient

        _agentforce_client = AgentforceClient.from_env()
        # D25: the ADK agent talks to its ADK-paired Agentforce twin,
        # keeping the experiment a closed two-platform system. Falls back
        # to the shared SF_AGENT_ID until the twin is provisioned.
        adk_paired = os.environ.get("SF_ADK_AGENT_ID")
        if adk_paired:
            _agentforce_client.agent_id = adk_paired
    return _agentforce_client


def make_ask_agentforce(inbound_depth: int = 0):
    """Build the ask_agentforce tool for one request, closed over the
    inbound delegation depth (D27): a delegated-to agent refuses to
    delegate onward instead of looping back to its caller."""

    async def ask_agentforce(question: str) -> str:
        """Ask the Salesforce Agentforce agent a question. Use for accounts,
        opportunities, cases, or org data."""
        if inbound_depth >= delegation.max_depth():
            return delegation.refusal("ask_agentforce")
        message, meta = delegation.delegate(
            question,
            caller="adk-gemini-agent",
            platform="adk",
            inbound_depth=inbound_depth,
        )
        resp = await _get_agentforce_client().ask(AgentRequest(message=message, metadata=meta))
        return resp.text

    return ask_agentforce


def make_ask_agentforce_a2a(inbound_depth: int = 0):
    """The channel twin of ask_agentforce (D28): same Agentforce agent, but
    over the A2A protocol through the lab's hosted shim — used when the
    operator's routing block selects the a2a-shim channel."""

    async def ask_agentforce_a2a(question: str) -> str:
        """Ask the Salesforce Agentforce agent a question over the A2A
        protocol (via the lab's hosted shim). Use ONLY when the request's
        [A2A-LAB ROUTING] block selects the a2a-shim channel; otherwise
        prefer ask_agentforce."""
        from interop import af_channel

        if inbound_depth >= delegation.max_depth():
            return delegation.refusal("ask_agentforce_a2a")
        message, meta = delegation.delegate(
            question,
            caller="adk-gemini-agent",
            platform="adk",
            inbound_depth=inbound_depth,
        )
        try:
            return await af_channel.ask_via_shim(message, meta)
        except Exception as exc:  # noqa: BLE001 - model-visible, not fatal
            return f"A2A shim call failed: {type(exc).__name__}: {exc}"

    return ask_agentforce_a2a


def adk_model() -> str:
    return os.environ.get("ADK_MODEL", "gemini-2.5-flash-lite")
