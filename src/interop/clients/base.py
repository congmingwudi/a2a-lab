"""Outbound seam: a RemoteAgentClient asks a remote agent one question and
returns its answer, whatever protocol carries it."""

from __future__ import annotations

from abc import ABC, abstractmethod

from interop.models import AgentRequest, AgentResponse


class RemoteAgentClient(ABC):
    """One implementation per protocol (rest/mcp/a2a) plus one per
    platform-native API (Agentforce Agent API)."""

    protocol: str = "unknown"

    @abstractmethod
    async def ask(self, req: AgentRequest) -> AgentResponse: ...

    async def aclose(self) -> None:  # optional cleanup
        return None
