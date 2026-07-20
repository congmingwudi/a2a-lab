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

import hashlib
import os

from interop import delegation
from interop.models import AgentRequest

_RESEARCH_PROMPT_BASE = (
    "You are a research assistant participating in a cross-platform "
    "agent-to-agent interoperability lab. Another agent (often a Salesforce "
    "Agentforce agent talking to an end user) delegates open-ended research "
    "and summarization questions to you. Answer with a concise, "
    "well-organized summary: lead with the direct answer, then 2-4 "
    "supporting points. Keep answers under 250 words - your reply is folded "
    "into another agent's response. Do not ask clarifying questions; make "
    "reasonable assumptions and state them.\n"
    "You must always contribute your own value, never act as a pass-through: "
    "when a question concerns a company, account, or market, {search_rule} "
    "and weave its signals into your own analysis (industry framing, what "
    "the data means, risks and openings for an account team). If you also "
    "consult the Agentforce agent, its CRM facts complement your "
    "contribution - they never replace it."
)


def real_search_enabled() -> bool:
    """ADK_REAL_SEARCH=1 swaps the deterministic synthetic news tool for
    live Google Search grounding (GoogleSearchTool with
    bypass_multi_tools_limit=True — ADK's sanctioned escape from the
    one-built-in-tool-per-agent API rule, available since ADK 1.16).
    Default off: synthetic keeps demo runs repeatable, adds no grounding
    latency to the ~30s+ direct-route turn, and bills nothing per query.
    Redeploy the engine after flipping (deploy/adk/deploy_adk.py)."""
    return os.environ.get("ADK_REAL_SEARCH", "").strip().lower() in ("1", "true", "yes")


def research_instruction() -> str:
    """The system prompt, with the search guidance matching the active
    search tool (real grounding vs the labeled-synthetic generator)."""
    rule = (
        "use your google_search tool to look up current industry news, "
        "funding/M&A activity, and market signals"
        if real_search_enabled()
        else "call your search_industry_news tool"
    )
    return _RESEARCH_PROMPT_BASE.format(search_rule=rule)


# Backward-compatible alias (local runners import the constant): the
# synthetic-tool variant, which is also the deployed default.
ADK_RESEARCH_SYSTEM_PROMPT = _RESEARCH_PROMPT_BASE.format(
    search_rule="call your search_industry_news tool"
)

# Synthetic industry-news generator behind search_industry_news: the lab
# demo needs the ADK agent to add outside-in value beyond relaying CRM
# facts, without wiring a real news API into the container (ADK's built-in
# google_search grounding can't be combined with function tools on the
# gemini-2.x surface). Deterministic per topic so demo runs are repeatable;
# every item is labeled synthetic - honest-demos rule.
_NEWS_ANGLES = [
    (
        "Funding & M&A",
        "Consolidation pressure around {topic}: two mid-market vendors in the "
        "segment announced a merger this quarter, and PE interest is rising.",
    ),
    (
        "Competitive",
        "Niche entrants are unbundling the {topic} stack with narrow, "
        "AI-first point solutions aimed at mid-market buyers.",
    ),
    (
        "Technology",
        "Buyers adjacent to {topic} are prioritizing AI-assisted automation "
        "and consolidation onto fewer platforms in current RFPs.",
    ),
    (
        "Buying signals",
        "Renewal-cycle surveys around {topic} show budget shifting from new "
        "licenses toward integration, security, and compliance line items.",
    ),
    (
        "Talent & operations",
        "Hiring around {topic} tilts toward platform and data-engineering "
        "roles - a leading indicator of build-out over run-rate spending.",
    ),
]


def search_industry_news(topic: str) -> str:
    """Look up recent industry news and market signals for a company,
    industry, or topic: funding and M&A chatter, competitive moves,
    technology trends, and buying signals. Use this to add outside-in
    market context to any account or company question."""
    digest = int(hashlib.md5(topic.strip().lower().encode()).hexdigest(), 16)
    picks = [_NEWS_ANGLES[(digest + i * 7) % len(_NEWS_ANGLES)] for i in range(3)]
    seen: dict[str, str] = {}
    for angle, item in picks:
        seen.setdefault(angle, item.format(topic=topic.strip() or "the sector"))
    lines = [f"- [{angle}] {item}" for angle, item in seen.items()]
    return (
        f'Industry signals for "{topic.strip()}" (synthetic demo intel - '
        "lab-generated, clearly-labeled illustrative research, not real news):\n" + "\n".join(lines)
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


def make_ask_agentforce(inbound_depth: int = 0, trace_id: str | None = None):
    """Build the ask_agentforce tool for one request, closed over the
    inbound delegation depth (D27) — a delegated-to agent refuses to
    delegate onward instead of looping back to its caller — and the
    caller's trace id, so downstream hops join the console's trace."""

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
        resp = await _get_agentforce_client().ask(
            AgentRequest(message=message, metadata=meta, trace_id=trace_id)
        )
        return resp.text

    return ask_agentforce


def make_ask_agentforce_a2a(inbound_depth: int = 0, trace_id: str | None = None):
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
            return await af_channel.ask_via_shim(message, meta, trace_id=trace_id)
        except Exception as exc:  # noqa: BLE001 - model-visible, not fatal
            return f"A2A shim call failed: {type(exc).__name__}: {exc}"

    return ask_agentforce_a2a


def adk_model() -> str:
    return os.environ.get("ADK_MODEL", "gemini-2.5-flash-lite")
