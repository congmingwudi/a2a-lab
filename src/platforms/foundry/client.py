"""Platform-native client for the Foundry research agent (WS3): the
project's Responses surface with an ``agent_reference`` — the D15 entry
path (you invoke the agent through the platform's own front door, not a
lab server). Protocol name ``foundry-api``, sibling of ``agentforce-api``.

Sessions: the Responses API chains turns with ``previous_response_id`` —
the client maps lab session ids to the last response id per session, so
conversation continuity holds for the console chat. platform_ref carries
the response id (the Foundry-native join key for observability).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from interop.clients.base import RemoteAgentClient
from interop.models import AgentRequest, AgentResponse, new_trace_id
from interop.trace import Hop
from platforms.foundry.core import AGENT_NAME, make_project_client

DEFAULT_TIMEOUT = 65.0


class FoundryClient(RemoteAgentClient):
    protocol = "foundry-api"

    def __init__(
        self,
        *,
        agent_name: str = AGENT_NAME,
        target_name: str = "foundry",
        source_name: str = "foundry-client",
        timeout: float = DEFAULT_TIMEOUT,
        tool_choice: str | None = None,
    ):
        self.agent_name = agent_name
        self.target_name = target_name
        self.source_name = source_name
        self.timeout = timeout
        # "required" forces the platform-side tool call: gpt-5-mini skips
        # its A2A delegation ~half the time under the default (and may
        # fabricate the skipped call's answer — fabricated-attribution
        # insight). The deterministic fix Foundry offers.
        self.tool_choice = tool_choice
        self._openai = None
        # lab session id -> last response id (previous_response_id chain)
        self._sessions: dict[str, str] = {}

    @classmethod
    def from_target(cls, target) -> "FoundryClient":
        kwargs: dict[str, Any] = {"target_name": target.name}
        if (target.options or {}).get("agent_name"):
            kwargs["agent_name"] = target.options["agent_name"]
        if (target.options or {}).get("timeout"):
            kwargs["timeout"] = float(target.options["timeout"])
        if (target.options or {}).get("tool_choice"):
            kwargs["tool_choice"] = target.options["tool_choice"]
        return cls(**kwargs)

    def _client(self):
        if self._openai is None:
            self._openai = make_project_client().get_openai_client()
        return self._openai

    def _create(self, req: AgentRequest):
        kwargs: dict[str, Any] = {
            "input": req.message,
            "extra_body": {"agent_reference": {"type": "agent_reference", "name": self.agent_name}},
        }
        if self.tool_choice:
            kwargs["tool_choice"] = self.tool_choice
        if req.session_id and req.session_id in self._sessions:
            kwargs["previous_response_id"] = self._sessions[req.session_id]
        return self._client().responses.create(**kwargs)

    async def ask(self, req: AgentRequest) -> AgentResponse:
        req.trace_id = req.trace_id or new_trace_id()
        start = time.perf_counter()
        with Hop(
            req.trace_id,
            source=self.source_name,
            target=self.target_name,
            protocol="foundry-api",
            transport_detail=f"responses.create agent_reference={self.agent_name}",
            request_payload={"message": req.message, "session_id": req.session_id},
        ) as hop:
            # The Azure OpenAI client is sync — keep the event loop free.
            # One retry on a failed platform-side tool call: Foundry's A2A
            # tool does not retry, and a cold twin turn behind the shim's
            # API Gateway 29s ceiling fails the first attempt — the second
            # rides the warmed shim session. Retrying also removes the
            # model's incentive to fabricate the tool's answer (the
            # fabricated-attribution insight): a completed tool call beats
            # any instruction about failed ones.
            try:
                resp = await asyncio.wait_for(
                    asyncio.to_thread(self._create, req), timeout=self.timeout
                )
            except Exception as exc:  # noqa: BLE001 - retry tool failures only
                message = str(exc)
                if "tool_user_error" not in message and "Failed Dependency" not in message:
                    raise
                resp = await asyncio.wait_for(
                    asyncio.to_thread(self._create, req), timeout=self.timeout
                )
            text = (resp.output_text or "").strip()
            hop.platform_ref = resp.id
            hop.response_payload = {"response_id": resp.id, "text": text}
        if req.session_id:
            self._sessions[req.session_id] = resp.id
        return AgentResponse(
            text=text,
            session_id=req.session_id,
            latency_ms=int((time.perf_counter() - start) * 1000),
            raw={"response_id": resp.id, "agent": self.agent_name},
        )
