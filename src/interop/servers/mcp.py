"""MCP protocol server: exposes an adapter as a single `ask` tool over the
streamable-http transport.

Mapping rule (plan/01-architecture.md): MCP has no first-class session
semantics, so session_id and trace_id ride along as ordinary tool arguments
— that asymmetry vs. A2A's contextId is itself a lab finding. The tool
returns the AgentResponse both as structuredContent (declared outputSchema,
contract v1 — F4) and as JSON text content, so session_id round-trips for
schema-aware and legacy clients alike.
"""

from __future__ import annotations

from typing import Any, TypedDict

from mcp.server.fastmcp import FastMCP

from interop.adapter import AgentAdapter
from interop.models import AgentRequest, new_trace_id
from interop.servers.wiretap import WireTapMiddleware

MCP_PATH = "/mcp"

# F4 (A7/A8): the `ask` tool's output contract, versioned. Bump when the
# shape changes incompatibly; additive fields don't bump the version.
ASK_CONTRACT_VERSION = 1


class AskResult(TypedDict):
    """`ask` tool output, contract v1 — an AgentResponse plus the contract
    version. FastMCP derives the published outputSchema from this TypedDict
    (structured_output=True), so callers get a declared schema instead of an
    opaque string blob; the JSON text content is kept for older clients."""

    contract_version: int
    text: str
    session_id: str | None
    latency_ms: int | None
    raw: dict[str, Any] | None


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
        description=(
            f"Ask the {adapter.name} agent a question. {adapter.description} "
            "Returns a structured result (contract v1, see outputSchema): "
            "{contract_version: int, text: str, session_id: str|null, "
            "latency_ms: int|null, raw: object|null}. The same JSON is also "
            "returned as text content for clients that ignore structuredContent."
        ),
        structured_output=True,
    )
    async def ask(
        message: str,
        session_id: str | None = None,
        trace_id: str | None = None,
        user_context: dict | None = None,
        user_token: str | None = None,
    ) -> AskResult:
        # user_context/user_token (WS6 U2): tool arguments are MCP's only
        # carriage for user identity — reassembled into metadata here so
        # the adapter sees the same shape every protocol delivers.
        metadata: dict = {}
        if user_context is not None:
            metadata["user_context"] = user_context
        if user_token is not None:
            metadata["user_token"] = user_token
        req = AgentRequest(
            message=message,
            session_id=session_id,
            trace_id=trace_id or new_trace_id(),
            metadata=metadata,
        )
        resp = await adapter.handle(req)
        return {"contract_version": ASK_CONTRACT_VERSION, **resp.to_dict()}

    # Adapters may publish extra read tools alongside ask (the Lab Guide's
    # raw-tools shape: the CLIENT's model reasons over lab data). Plain
    # typed functions — FastMCP derives schemas from signature + docstring.
    for fn in getattr(adapter, "extra_mcp_tools", None) or []:
        mcp.tool(name=fn.__name__.removeprefix("mcp_"))(fn)

    return mcp


def create_mcp_app(adapter: AgentAdapter):
    mcp = create_mcp_server(adapter)
    app = mcp.streamable_http_app()
    return WireTapMiddleware(app, protocol="mcp", service=adapter.name)
