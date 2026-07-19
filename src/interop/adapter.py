"""Inbound seam: each hosted agent implements AgentAdapter once and becomes
servable over REST, MCP, and A2A via serve()."""

from __future__ import annotations

import os
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


def build_app(adapter: AgentAdapter, protocol: Protocols, **kwargs):
    """Return the ASGI app for a protocol without running a server.
    Used by tests, serve(), and callers that embed the app in their own
    server. For a2a, pass public_url= so the AgentCard advertises a
    reachable URL.

    Every app is wrapped in TokenAuthMiddleware: these servers get exposed
    through the tunnel, so auth is a property of the seam, not of individual
    entrypoints (no-op while A2ALAB_TOKEN is unset).

    Imports are deferred so an adapter can be unit-tested without pulling in
    every protocol stack.
    """
    if protocol == "rest":
        from interop.servers.rest import create_rest_app

        app = create_rest_app(adapter)
    elif protocol == "mcp":
        from interop.servers.mcp import create_mcp_app

        app = create_mcp_app(adapter)
    elif protocol == "a2a":
        from interop.servers.a2a import create_a2a_app

        app = create_a2a_app(adapter, **kwargs)
    else:
        raise ValueError(f"unknown protocol: {protocol}")

    from interop.servers.auth import TokenAuthMiddleware

    return TokenAuthMiddleware(app)


def serve(
    adapter: AgentAdapter,
    protocol: Protocols,
    port: int,
    host: str = "0.0.0.0",
    public_url: str | None = None,
) -> None:
    """Mount the chosen protocol server for an adapter and block.

    public_url (a2a only): the URL the AgentCard advertises. Explicit arg
    wins, then A2A_PUBLIC_URL, then http://<host>:<port>/ — pass it per
    server when running more than one A2A server on a machine, or the cards
    will all advertise whatever the shared env var says.
    """
    import uvicorn

    kwargs = {}
    if protocol == "a2a":
        advertised_host = "localhost" if host in ("0.0.0.0", "::") else host
        kwargs["public_url"] = (
            public_url or os.environ.get("A2A_PUBLIC_URL") or f"http://{advertised_host}:{port}/"
        )
    uvicorn.run(build_app(adapter, protocol, **kwargs), host=host, port=port, log_level="info")
