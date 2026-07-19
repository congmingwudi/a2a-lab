"""Hand-rolled MCP "Streamable HTTP" server transport for the lab's hosted
observability MCP server (ADR D23).

Deliberately built without the `mcp` SDK: this repo is a protocol lab and the
module doubles as a raw-wire artifact. `core` is transport-agnostic JSON-RPC
dispatch; `http` provides a local Starlette app and an AWS Lambda Function URL
handler over the same dispatch. Tools are registered elsewhere via ToolRegistry.
"""

from obs_mcp.core import ToolDef, ToolRegistry, handle_message
from obs_mcp.http import create_local_app, make_lambda_handler

__all__ = [
    "ToolDef",
    "ToolRegistry",
    "handle_message",
    "create_local_app",
    "make_lambda_handler",
]
