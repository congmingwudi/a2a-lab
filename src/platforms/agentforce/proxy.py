"""Agentforce proxy adapter: wraps the Agent API client in the AgentAdapter
seam, so the generic serve() can expose Agentforce over MCP and A2A.

These are the Path B "via-shim" cells: Agentforce has no GA MCP/A2A inbound
surface, so the shim speaks the protocol and proxies to the Agent API. The
matrix records these cells as via-shim, never native.
"""

from __future__ import annotations

import os

from interop.models import AgentRequest, AgentResponse
from platforms.agentforce.client import AgentforceClient

# D25 on the shim channel: a shared shim must still route each caller to its
# platform-paired twin, or the a2a-shim channel silently collapses every
# experiment onto one twin. The delegation rider's metadata names the
# calling platform; map it to the twin env vars (unset → SF_AGENT_ID).
TWIN_ENV_BY_PLATFORM = {
    "claude": "SF_AGENT_ID",
    "openai": "SF_OPENAI_AGENT_ID",
    "adk": "SF_ADK_AGENT_ID",
}


class AgentforceProxyAdapter:
    name = "agentforce-service-agent"
    description = (
        "Salesforce Agentforce service agent (A2A interop lab), reached via "
        "a protocol shim that proxies to the GA Agent API. Ask it questions "
        "that need Salesforce-side knowledge."
    )

    def __init__(self, client: AgentforceClient | None = None, session_reuse: bool = False):
        self._client = client
        # Hosted shim (D28): session-less delegated asks would otherwise pay
        # an SF session create per call — inside API Gateway's 29s ceiling
        # that's the difference between fitting and timing out. Reuse one
        # session per (twin, platform) for session-less requests.
        self.session_reuse = session_reuse

    @property
    def client(self) -> AgentforceClient:
        if self._client is None:
            self._client = AgentforceClient.from_env()
        return self._client

    async def handle(self, req: AgentRequest) -> AgentResponse:
        platform = ((req.metadata or {}).get("delegation") or {}).get("platform")
        twin_id = os.environ.get(TWIN_ENV_BY_PLATFORM.get(platform, ""), "") or None
        if req.session_id is None and self.session_reuse:
            req.session_id = f"shim-shared-{platform or 'direct'}"
        if twin_id and twin_id != self.client.agent_id:
            # Per-request twin override: the client caches sessions per
            # session_id, so distinct twins ride distinct session keys above.
            client = AgentforceClient.from_env()
            client.agent_id = twin_id
            self._twin_clients = getattr(self, "_twin_clients", {})
            client = self._twin_clients.setdefault(twin_id, client)
            return await client.ask(req)
        return await self.client.ask(req)
