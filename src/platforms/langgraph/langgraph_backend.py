"""LangGraph backend — the real open-source-framework agent (WS4).

Contract:

- ``LangGraphBackend.answer(req) -> AgentResponse`` runs one turn of a
  LangGraph ReAct graph (an agent node + a tool node) built with
  ``langgraph.prebuilt.create_react_agent``, using
  LANGGRAPH_RESEARCH_SYSTEM_PROMPT (platforms/langgraph/core.py) and the model
  from LANGGRAPH_MODEL_ID (env; a Haiku-tier Anthropic brain by default, via
  langchain-anthropic — keeps the Path A sync budget comfortable).
- An async ``ask_agentforce`` tool delegates Salesforce-side questions through
  ``platforms.agentforce.client.AgentforceClient.from_env()`` — credentials
  stay host-side (same boundary as the OpenAI, Claude, and Strands agents).
- Every delegation goes through ``interop.delegation`` (D27).
- Each graph run is wrapped in an ``interop.trace.Hop``.
- Budget: LANGGRAPH_ANSWER_TIMEOUT_S (default 40) — the Path A chain allows
  ~45s at the bridge.

Observability: LangGraph/LangChain auto-emit runs to LangSmith when
LANGSMITH_API_KEY + LANGCHAIN_TRACING_V2=true are set in the environment. No
code here is needed for that — it is the WS4 "queryable SaaS observability"
column, and it is host-agnostic (works the same on Heroku as on LangGraph
Platform). ``langgraph_source.py`` harvests those runs back into lab.db.

The ``langgraph`` extra (pyproject) provides langgraph + langchain-anthropic;
imported lazily so the stub backend runs without them.
"""

from __future__ import annotations

import asyncio
import os
import time

from interop import delegation
from interop.models import AgentRequest, AgentResponse, new_trace_id
from interop.trace import Hop
from platforms.langgraph.core import LANGGRAPH_RESEARCH_SYSTEM_PROMPT

DEFAULT_MODEL_ID = "claude-haiku-4-5-20251001"
DEFAULT_TIMEOUT_S = "40"
DEFAULT_AGENTFORCE_TOOL_TIMEOUT_S = "34"

# One process-lifetime client so the OAuth token survives across tool calls.
_agentforce_client = None


def _get_agentforce_client():
    global _agentforce_client
    if _agentforce_client is None:
        from platforms.agentforce.client import AgentforceClient

        _agentforce_client = AgentforceClient.from_env()
        # D25: the LangGraph agent talks to its LangGraph-paired Agentforce twin.
        paired = os.environ.get("SF_LANGGRAPH_AGENT_ID")
        if paired:
            _agentforce_client.agent_id = paired
    return _agentforce_client


def _build_agentforce_tool(
    inbound_depth: int = 0,
    trace_id: str | None = None,
    user_context: dict | None = None,
    user_token: str | None = None,
):
    """Build the Agentforce delegation tool for one request, closed over the
    delegation depth and effective run trace id. LangGraph/LangChain tools can
    be async, so — unlike Strands — no worker-thread dance is needed."""
    from langchain_core.tools import tool

    @tool
    async def ask_agentforce(question: str) -> str:
        """Ask the Salesforce Agentforce agent a question. Use for accounts, opportunities, cases, or org data."""  # noqa: E501
        if inbound_depth >= delegation.max_depth():
            return delegation.refusal("ask_agentforce")
        message, meta = delegation.delegate(
            question,
            caller="langgraph-agent",
            platform="langgraph",
            inbound_depth=inbound_depth,
            trace_id=trace_id,
            user_context=user_context,
            user_token=user_token,
        )
        try:
            timeout = float(
                os.environ.get(
                    "LANGGRAPH_AGENTFORCE_TOOL_TIMEOUT_S", DEFAULT_AGENTFORCE_TOOL_TIMEOUT_S
                )
            )
            resp = await asyncio.wait_for(
                _get_agentforce_client().ask(
                    AgentRequest(message=message, metadata=meta, trace_id=trace_id)
                ),
                timeout,
            )
        except Exception as exc:  # noqa: BLE001 - model-visible, not fatal
            return f"CRM lookup failed via Agentforce: {type(exc).__name__}: {exc}"
        return resp.text

    return ask_agentforce


class LangGraphBackend:
    backend_name = "langgraph"

    def __init__(self, model_id: str | None = None):
        self.model_id = model_id or os.environ.get("LANGGRAPH_MODEL_ID", DEFAULT_MODEL_ID)

    def _make_graph(
        self,
        inbound_depth: int = 0,
        trace_id: str | None = None,
        user_context: dict | None = None,
        user_token: str | None = None,
    ):
        from langchain_anthropic import ChatAnthropic
        from langgraph.prebuilt import create_react_agent

        model = ChatAnthropic(model=self.model_id, max_tokens=1024)
        tools = [_build_agentforce_tool(inbound_depth, trace_id, user_context, user_token)]
        return create_react_agent(model, tools, prompt=LANGGRAPH_RESEARCH_SYSTEM_PROMPT)

    @staticmethod
    def _final_text(result) -> str:
        """Pull the last AI message's text out of the graph's returned state."""
        messages = result.get("messages", []) if isinstance(result, dict) else []
        for msg in reversed(messages):
            content = getattr(msg, "content", None)
            if content is None:
                continue
            if isinstance(content, str):
                if content.strip():
                    return content.strip()
            elif isinstance(content, list):
                # Anthropic content blocks: keep only the text parts.
                parts = [
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                joined = "".join(parts).strip()
                if joined:
                    return joined
        return ""

    async def answer(self, req: AgentRequest) -> AgentResponse:
        trace_id = req.trace_id or new_trace_id()
        start = time.perf_counter()
        final_text = ""
        with Hop(
            trace_id,
            source="langgraph-researcher",
            target="langgraph-platform",
            protocol="internal",
            transport_detail="langgraph create_react_agent ainvoke",
            request_payload=req.to_dict(),
        ) as hop:
            graph = self._make_graph(delegation.depth_of(req), trace_id, *delegation.user_of(req))
            result = await asyncio.wait_for(
                graph.ainvoke({"messages": [{"role": "user", "content": req.message}]}),
                float(os.environ.get("LANGGRAPH_ANSWER_TIMEOUT_S", DEFAULT_TIMEOUT_S)),
            )
            final_text = self._final_text(result)
            hop.response_payload = {"text": final_text}
        return AgentResponse(
            text=final_text,
            session_id=req.session_id,
            latency_ms=int((time.perf_counter() - start) * 1000),
            raw={"model": self.model_id, "backend": self.backend_name},
        )
