"""Transport-agnostic JSON-RPC dispatch for the obs MCP server.

Implements the minimal server half of MCP's Streamable HTTP transport: a single
JSON-RPC message in, a single JSON-RPC reply (or None for notifications) out.
Per the MCP spec, tool execution failures are *results* with isError=true, not
JSON-RPC protocol errors.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

SERVER_INFO = {"name": "a2alab-obs-mcp", "version": "0.1.0"}
KNOWN_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = "2025-06-18"

METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
PARSE_ERROR = -32700


@dataclass
class ToolDef:
    """One MCP tool: metadata plus the function that runs it.

    `fn` receives the tool-call arguments dict and returns the text result; if
    it raises, the exception becomes an isError=true tool result.
    """

    name: str
    description: str
    input_schema: dict
    fn: Callable[[dict], str]


class ToolRegistry:
    """The set of tools the server exposes, keyed by name."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    def all(self) -> list[ToolDef]:
        return list(self._tools.values())


def _result(req_id: object, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: object, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle_message(body: dict, registry: ToolRegistry) -> dict | None:
    """Dispatch one JSON-RPC message. Returns the reply dict, or None for
    notifications (the HTTP adapters turn None into a 202 with empty body)."""
    method = body.get("method")
    if isinstance(method, str) and method.startswith("notifications/"):
        return None
    req_id = body.get("id")  # non-notification without an id still gets id=null back
    params = body.get("params") or {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        negotiated = requested if requested in KNOWN_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
        return _result(
            req_id,
            {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {}},
                "serverInfo": dict(SERVER_INFO),
            },
        )

    if method == "ping":
        return _result(req_id, {})

    if method == "tools/list":
        tools = [
            {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
            for t in registry.all()
        ]
        return _result(req_id, {"tools": tools})

    if method == "tools/call":
        name = params.get("name")
        tool = registry.get(name) if isinstance(name, str) else None
        if tool is None:
            return _error(req_id, INVALID_PARAMS, f"Unknown tool: {name}")
        try:
            text = tool.fn(params.get("arguments") or {})
        except Exception as exc:  # tool errors are results, not protocol errors
            return _result(
                req_id,
                {
                    "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                    "isError": True,
                },
            )
        return _result(req_id, {"content": [{"type": "text", "text": text}], "isError": False})

    return _error(req_id, METHOD_NOT_FOUND, f"Method not found: {method}")
