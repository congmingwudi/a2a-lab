"""Agentforce proxy adapter: wraps the Agent API client in the AgentAdapter
seam, so the generic serve() can expose Agentforce over MCP and A2A.

These are the Path B "via-shim" cells: Agentforce has no GA MCP/A2A inbound
surface, so the shim speaks the protocol and proxies to the Agent API. The
matrix records these cells as via-shim, never native.
"""

from __future__ import annotations

from interop.models import AgentRequest, AgentResponse
from platforms.agentforce.client import AgentforceClient


class AgentforceProxyAdapter:
    name = "agentforce-service-agent"
    description = (
        "Salesforce Agentforce service agent (A2A interop lab), reached via "
        "a protocol shim that proxies to the GA Agent API. Ask it questions "
        "that need Salesforce-side knowledge."
    )

    def __init__(self, client: AgentforceClient | None = None):
        self._client = client

    @property
    def client(self) -> AgentforceClient:
        if self._client is None:
            self._client = AgentforceClient.from_env()
        return self._client

    async def handle(self, req: AgentRequest) -> AgentResponse:
        return await self.client.ask(req)
