"""Outbound seam: a RemoteAgentClient asks a remote agent one question and
returns its answer, whatever protocol carries it."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from interop.models import AgentRequest, AgentResponse


def auth_headers(auth: dict[str, Any] | None) -> dict[str, str]:
    """Headers for a target's auth config — one implementation for every
    protocol client, so bearer_token and header_name/header_value behave
    identically over REST, MCP, and A2A."""
    headers: dict[str, str] = {}
    auth = auth or {}
    token = auth.get("bearer_token")
    if token:
        headers["authorization"] = f"Bearer {token}"
    header_name = auth.get("header_name")
    if header_name and auth.get("header_value"):
        headers[header_name] = auth["header_value"]
    return headers


class RemoteAgentError(RuntimeError):
    """A remote agent answered with an error. ``error_code`` is set when the
    server typed it on the wire (our REST face does for typed refusals such as
    ``stale_memory_generation``), so callers can branch on the code instead of
    parsing the message."""

    def __init__(self, message: str, *, status: int | None = None, error_code: str | None = None):
        super().__init__(message)
        self.status = status
        self.error_code = error_code


class RemoteAgentClient(ABC):
    """One implementation per protocol (rest/mcp/a2a) plus one per
    platform-native API (Agentforce Agent API)."""

    protocol: str = "unknown"

    @abstractmethod
    async def ask(self, req: AgentRequest) -> AgentResponse: ...

    async def aclose(self) -> None:  # optional cleanup
        return None
