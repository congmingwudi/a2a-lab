"""Anthropic Managed Agents (beta) backend.

Control plane vs data plane:
- The agent + environment are persistent, versioned resources created ONCE
  by scripts/setup_managed_agent.py (which stores the IDs in
  .a2alab/managed.json). This backend never calls agents.create() in the
  request path.
- Per request, this backend drives a session: create (or reuse, keyed by the
  lab's session_id), open the event stream FIRST, send the user message,
  collect agent.message text until the session idles with a terminal
  stop_reason.

Path B (Claude -> Agentforce) under this backend: the managed agent declares
an `ask_agentforce` custom tool; when the stream emits agent.custom_tool_use
we call the AgentforceClient host-side and send back user.custom_tool_result
— the sandbox never sees Salesforce credentials.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic

from interop.models import AgentRequest, AgentResponse, new_trace_id
from interop.trace import Hop

STATE_FILE = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "managed.json"
AGENT_ID_ENV = "CLAUDE_MANAGED_AGENT_ID"
ENV_ID_ENV = "CLAUDE_MANAGED_ENV_ID"

# Same self-cap as the SDK backend: the agent must answer inside the bridge
# clients' 45s budget, which sits inside Apex's 110s callout budget.
ANSWER_TIMEOUT_S = float(os.environ.get("CLAUDE_ANSWER_TIMEOUT_S", "40"))

AGENTFORCE_TOOL_NAME = "ask_agentforce"


def load_managed_ids() -> tuple[str, str]:
    """Resolve agent_id + environment_id from env vars or the state file
    written by scripts/setup_managed_agent.py."""
    agent_id = os.environ.get(AGENT_ID_ENV)
    env_id = os.environ.get(ENV_ID_ENV)
    if agent_id and env_id:
        return agent_id, env_id
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
        return state["agent_id"], state["environment_id"]
    raise RuntimeError(
        "Managed Agents backend is not provisioned. Run "
        "`uv run python scripts/setup_managed_agent.py` once (needs "
        f"ANTHROPIC_API_KEY), or set {AGENT_ID_ENV} and {ENV_ID_ENV}."
    )


class ManagedBackend:
    backend_name = "managed"

    def __init__(self, client: AsyncAnthropic | None = None):
        self._client = client or AsyncAnthropic()
        self._ids: tuple[str, str] | None = None
        # lab session_id -> CMA session id
        self._sessions: dict[str, str] = {}
        self._agentforce_client = None

    def _get_agentforce_client(self):
        """Lazy Path B client — only built when the managed agent actually
        calls the ask_agentforce custom tool."""
        if self._agentforce_client is None:
            from platforms.agentforce.client import AgentforceClient

            self._agentforce_client = AgentforceClient.from_env()
        return self._agentforce_client

    async def _get_or_create_session(self, lab_session_id: str | None) -> str:
        agent_id, env_id = self._ids or load_managed_ids()
        self._ids = (agent_id, env_id)
        if lab_session_id and lab_session_id in self._sessions:
            return self._sessions[lab_session_id]
        session = await self._client.beta.sessions.create(
            agent=agent_id,
            environment_id=env_id,
            title=f"a2a-lab {lab_session_id or 'oneshot'}",
        )
        if lab_session_id:
            self._sessions[lab_session_id] = session.id
        return session.id

    async def _handle_custom_tool(self, session_id: str, event: Any, trace_id: str) -> None:
        tool_input = dict(event.input) if getattr(event, "input", None) else {}
        if event.name == AGENTFORCE_TOOL_NAME:
            question = str(tool_input.get("question", ""))
            client = self._get_agentforce_client()
            resp = await client.ask(
                AgentRequest(message=question, trace_id=trace_id)
            )
            result_text = resp.text
        else:
            result_text = f"Unknown tool: {event.name}"
        await self._client.beta.sessions.events.send(
            session_id=session_id,
            events=[
                {
                    "type": "user.custom_tool_result",
                    "custom_tool_use_id": event.id,
                    "content": [{"type": "text", "text": result_text}],
                }
            ],
        )

    async def _converse(self, session_id: str, req: AgentRequest, trace_id: str) -> tuple[list[str], list[str]]:
        texts: list[str] = []
        events_seen: list[str] = []

        # Stream-first: open the stream before sending the kickoff so no
        # early events are missed.
        stream = await self._client.beta.sessions.events.stream(session_id=session_id)
        try:
            await self._client.beta.sessions.events.send(
                session_id=session_id,
                events=[
                    {
                        "type": "user.message",
                        "content": [{"type": "text", "text": req.message}],
                    }
                ],
            )
            async for event in stream:
                etype = getattr(event, "type", "")
                events_seen.append(etype)
                if etype == "agent.message":
                    for block in getattr(event, "content", []) or []:
                        if getattr(block, "type", "") == "text":
                            texts.append(block.text)
                elif etype == "agent.custom_tool_use":
                    await self._handle_custom_tool(session_id, event, trace_id)
                elif etype == "session.status_idle":
                    stop = getattr(event, "stop_reason", None)
                    if getattr(stop, "type", None) != "requires_action":
                        break
                elif etype == "session.status_terminated":
                    break
                elif etype == "session.error":
                    raise RuntimeError(f"managed session error: {event}")
        finally:
            await stream.close()
        return texts, events_seen

    async def answer(self, req: AgentRequest) -> AgentResponse:
        trace_id = req.trace_id or new_trace_id()
        start = time.perf_counter()
        with Hop(
            trace_id,
            source="claude-researcher",
            target="anthropic-managed-agents",
            protocol="managed-agents-api",
            transport_detail="sessions.events send/stream",
            request_payload={"message": req.message, "session_id": req.session_id},
        ) as hop:
            session_id = await self._get_or_create_session(req.session_id)
            try:
                texts, events_seen = await asyncio.wait_for(
                    self._converse(session_id, req, trace_id), ANSWER_TIMEOUT_S
                )
            except TimeoutError:
                raise TimeoutError(
                    f"Claude answer exceeded CLAUDE_ANSWER_TIMEOUT_S={ANSWER_TIMEOUT_S:.0f}s "
                    f"(managed session {session_id}). Cold-start session provisioning and "
                    "mid-answer ask_agentforce round trips both count against this cap — "
                    "raise CLAUDE_ANSWER_TIMEOUT_S in .env or use a faster CLAUDE_AGENT_MODEL."
                ) from None
            hop.response_payload = {"events": events_seen, "text": "\n".join(texts)}

        return AgentResponse(
            text="\n".join(texts).strip(),
            session_id=req.session_id,
            latency_ms=int((time.perf_counter() - start) * 1000),
            raw={"managed_session_id": session_id, "backend": "managed"},
        )
