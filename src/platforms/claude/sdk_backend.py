"""Self-hosted Claude Agent SDK backend (claude-agent-sdk).

The fallback/low-latency variant: the SDK spawns a local Claude Code CLI
subprocess, so this is the backend that containerizes for Bedrock AgentCore
(image must bundle Node — see deploy/agentcore/).

Path B under this backend: the `ask_agentforce` custom tool is registered as
an in-process SDK MCP server, so the Claude agent can call Agentforce
mid-answer.
"""

from __future__ import annotations

import asyncio
import os
import time

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from interop.models import AgentRequest, AgentResponse, new_trace_id
from interop.trace import Hop
from platforms.claude.core import RESEARCH_SYSTEM_PROMPT

ANSWER_TIMEOUT_S = float(os.environ.get("CLAUDE_ANSWER_TIMEOUT_S", "40"))
AGENTFORCE_TOOL = "mcp__a2alab__ask_agentforce"

# One process-lifetime client so the OAuth token survives across tool calls
# (a per-call client would re-authenticate and leak its connection pool on
# every mid-answer Agentforce question).
_agentforce_client = None


def _get_agentforce_client():
    global _agentforce_client
    if _agentforce_client is None:
        from platforms.agentforce.client import AgentforceClient

        _agentforce_client = AgentforceClient.from_env()
    return _agentforce_client


def _build_agentforce_tool():
    @tool(
        "ask_agentforce",
        "Ask the Salesforce Agentforce service agent a question and return "
        "its answer. Use when the user's request needs Salesforce-side "
        "knowledge (cases, org data, service workflows).",
        {"question": str},
    )
    async def ask_agentforce(args: dict) -> dict:
        resp = await _get_agentforce_client().ask(
            AgentRequest(message=str(args["question"]))
        )
        return {"content": [{"type": "text", "text": resp.text}]}

    return ask_agentforce


class SdkBackend:
    backend_name = "sdk"

    def __init__(self, enable_agentforce_tool: bool | None = None):
        if enable_agentforce_tool is None:
            enable_agentforce_tool = bool(os.environ.get("SF_CLIENT_ID"))
        self.enable_agentforce_tool = enable_agentforce_tool
        # lab session_id -> SDK session id (for `resume`)
        self._sessions: dict[str, str] = {}

    def _options(self, req: AgentRequest) -> ClaudeAgentOptions:
        kwargs = dict(
            system_prompt=RESEARCH_SYSTEM_PROMPT,
            max_turns=int(os.environ.get("CLAUDE_MAX_TURNS", "3")),
            model=os.environ.get("CLAUDE_AGENT_MODEL"),
        )
        if self.enable_agentforce_tool:
            server = create_sdk_mcp_server(name="a2alab", tools=[_build_agentforce_tool()])
            kwargs["mcp_servers"] = {"a2alab": server}
            kwargs["allowed_tools"] = [AGENTFORCE_TOOL]
        if req.session_id and req.session_id in self._sessions:
            kwargs["resume"] = self._sessions[req.session_id]
        return ClaudeAgentOptions(**kwargs)

    async def _run(self, req: AgentRequest) -> tuple[str, str | None]:
        texts: list[str] = []
        sdk_session_id: str | None = None
        async for message in query(prompt=req.message, options=self._options(req)):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        texts.append(block.text)
            elif isinstance(message, ResultMessage):
                sdk_session_id = getattr(message, "session_id", None)
        # The last assistant text is the final answer; earlier ones are
        # tool-use narration.
        return (texts[-1] if texts else ""), sdk_session_id

    async def answer(self, req: AgentRequest) -> AgentResponse:
        trace_id = req.trace_id or new_trace_id()
        start = time.perf_counter()
        with Hop(
            trace_id,
            source="claude-researcher",
            target="claude-agent-sdk",
            protocol="internal",
            transport_detail="claude_agent_sdk.query",
            request_payload={"message": req.message, "session_id": req.session_id},
        ) as hop:
            text, sdk_session_id = await asyncio.wait_for(self._run(req), ANSWER_TIMEOUT_S)
            hop.response_payload = text
        if req.session_id and sdk_session_id:
            self._sessions[req.session_id] = sdk_session_id
        return AgentResponse(
            text=text.strip(),
            session_id=req.session_id,
            latency_ms=int((time.perf_counter() - start) * 1000),
            raw={"backend": "sdk", "sdk_session_id": sdk_session_id},
        )
