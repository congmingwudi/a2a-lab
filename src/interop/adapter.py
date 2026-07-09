"""Inbound seam: each hosted agent implements AgentAdapter once and becomes
servable over REST, MCP, and A2A via serve()."""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from interop.models import AgentRequest, AgentResponse

Protocols = Literal["rest", "mcp", "a2a"]


@runtime_checkable
class AgentAdapter(Protocol):
    """A hosted agent. `name` and `description` feed the MCP tool description
    and the A2A AgentCard skill; `handle` answers one request."""

    name: str
    description: str

    async def handle(self, req: AgentRequest) -> AgentResponse: ...


def serve(adapter: AgentAdapter, protocol: Protocols, port: int, host: str = "0.0.0.0") -> None:
    """Mount the chosen protocol server for an adapter and block.

    Imports are deferred so an adapter can be unit-tested without pulling in
    every protocol stack.
    """
    if protocol == "rest":
        from interop.servers.rest import serve_rest

        serve_rest(adapter, port=port, host=host)
    elif protocol == "mcp":
        from interop.servers.mcp import serve_mcp

        serve_mcp(adapter, port=port, host=host)
    elif protocol == "a2a":
        from interop.servers.a2a import serve_a2a

        serve_a2a(adapter, port=port, host=host)
    else:  # pragma: no cover
        raise ValueError(f"unknown protocol: {protocol}")


def build_app(adapter: AgentAdapter, protocol: Protocols, **kwargs):
    """Return the ASGI app for a protocol without running a server.
    Used by tests and by callers that embed the app in their own server.
    For a2a, pass public_url= so the AgentCard advertises a reachable URL."""
    if protocol == "rest":
        from interop.servers.rest import create_rest_app

        return create_rest_app(adapter)
    if protocol == "mcp":
        from interop.servers.mcp import create_mcp_app

        return create_mcp_app(adapter)
    if protocol == "a2a":
        from interop.servers.a2a import create_a2a_app

        return create_a2a_app(adapter, **kwargs)
    raise ValueError(f"unknown protocol: {protocol}")
