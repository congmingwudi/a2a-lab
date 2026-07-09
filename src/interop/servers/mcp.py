"""MCP protocol server: exposes an adapter as a single `ask` tool over the
streamable-http transport.

Mapping rule (plan/01-architecture.md): MCP has no first-class session
semantics, so session_id and trace_id ride along as ordinary tool arguments
— that asymmetry vs. A2A's contextId is itself a lab finding. The tool
returns the AgentResponse as JSON text so session_id round-trips.
"""

from __future__ import annotations

import json

import uvicorn
from mcp.server.fastmcp import FastMCP

from interop.adapter import AgentAdapter
from interop.models import AgentRequest, new_trace_id
from interop.servers.wiretap import WireTapMiddleware

MCP_PATH = "/mcp"


def create_mcp_server(adapter: AgentAdapter, host: str = "0.0.0.0", port: int = 8002) -> FastMCP:
    mcp = FastMCP(
        name=adapter.name,
        instructions=adapter.description,
        host=host,
        port=port,
        # Stateless keeps the loopback/e2e story simple; the lack of session
        # affinity at the transport level is part of the protocol comparison.
        stateless_http=True,
    )

    @mcp.tool(
        name="ask",
        description=f"Ask the {adapter.name} agent a question. {adapter.description}",
    )
    async def ask(message: str, session_id: str | None = None, trace_id: str | None = None) -> str:
        req = AgentRequest(
            message=message,
            session_id=session_id,
            trace_id=trace_id or new_trace_id(),
        )
        resp = await adapter.handle(req)
        return json.dumps(resp.to_dict())

    return mcp


def create_mcp_app(adapter: AgentAdapter):
    mcp = create_mcp_server(adapter)
    app = mcp.streamable_http_app()
    return WireTapMiddleware(app, protocol="mcp", service=adapter.name)


def serve_mcp(adapter: AgentAdapter, port: int, host: str = "0.0.0.0") -> None:
    mcp = create_mcp_server(adapter, host=host, port=port)
    app = WireTapMiddleware(mcp.streamable_http_app(), protocol="mcp", service=adapter.name)
    uvicorn.run(app, host=host, port=port, log_level="info")
