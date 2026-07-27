"""A minimal server half of MCP's Streamable HTTP transport.

Hand-rolled rather than taken from the MCP SDK because the deployment target is
an AWS Lambda: `core.py` is transport-agnostic JSON-RPC dispatch and the Lambda
adapter in `http.py` is stdlib-only, so a server that needs no other
dependencies ships as a zip of this repo's own source. The SDK's server is
built around a long-lived ASGI process, which a request-scoped Lambda is not.

It lived in `obs_mcp/` until the fan-out server (WS7 item 4) became the second
thing to need it. Nothing about it was ever observability-specific — the tool
registry is the only part that differs between servers, which is the argument
for this package existing at all.

Two servers use it today:

    obs_mcp     the observability analyst's read-only store access (D23)
    fanout_mcp  one tool per business unit for the supply-disruption fan-out
"""

from mcp_http.core import (
    LATEST_PROTOCOL_VERSION,
    ToolDef,
    ToolRegistry,
    handle_message,
)
from mcp_http.http import create_local_app, make_lambda_handler

__all__ = [
    "ToolDef",
    "ToolRegistry",
    "handle_message",
    "create_local_app",
    "make_lambda_handler",
    "LATEST_PROTOCOL_VERSION",
]
