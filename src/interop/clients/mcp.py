"""MCP client: connects over streamable-http and calls the remote agent's
single `ask` tool. session_id/trace_id travel as tool arguments (MCP has no
session semantics of its own — a lab finding, see plan/02-matrix.md)."""

from __future__ import annotations

import json
import time
from typing import Any

from datetime import timedelta

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from interop.clients.base import RemoteAgentClient, auth_headers
from interop.models import AgentRequest, AgentResponse, new_trace_id
from interop.trace import Hop

DEFAULT_TIMEOUT = 45.0


class McpClient(RemoteAgentClient):
    protocol = "mcp"

    def __init__(
        self,
        endpoint: str,
        *,
        auth: dict[str, Any] | None = None,
        target_name: str = "remote",
        source_name: str = "client",
        timeout: float = DEFAULT_TIMEOUT,
    ):
        # endpoint should include the /mcp path, e.g. http://localhost:8002/mcp
        self.endpoint = endpoint
        self.auth = auth or {}
        self.target_name = target_name
        self.source_name = source_name
        self.timeout = timeout

    def _headers(self) -> dict[str, str] | None:
        return auth_headers(self.auth) or None

    async def ask(self, req: AgentRequest) -> AgentResponse:
        req.trace_id = req.trace_id or new_trace_id()
        arguments = {
            "message": req.message,
            "session_id": req.session_id,
            "trace_id": req.trace_id,
        }
        # WS6 U2 — MCP's only channel for user context is a tool argument
        # (the protocol has no session or auth semantics to ride; metadata
        # does not cross this hop at all — a lab finding).
        for key in ("user_context", "user_token"):
            if (req.metadata or {}).get(key) is not None:
                arguments[key] = req.metadata[key]
        start = time.perf_counter()
        with Hop(
            req.trace_id,
            source=self.source_name,
            target=self.target_name,
            protocol="mcp",
            transport_detail=f"tools/call ask @ {self.endpoint}",
            request_payload={"jsonrpc-method": "tools/call", "name": "ask", "arguments": arguments},
        ) as hop:
            # Pass the configured timeout at every layer — the library
            # defaults (30s connect / 300s sse read) would otherwise govern
            # and a hung agent holds the caller far past its budget.
            async with streamablehttp_client(
                self.endpoint,
                headers=self._headers(),
                timeout=self.timeout,
                sse_read_timeout=self.timeout,
            ) as (
                read,
                write,
                _get_session_id,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "ask", arguments, read_timeout_seconds=timedelta(seconds=self.timeout)
                    )
            text_parts = [c.text for c in result.content if getattr(c, "type", "") == "text"]
            raw_text = "\n".join(text_parts)
            hop.response_payload = raw_text
            if result.isError:
                raise RuntimeError(f"MCP tool error from {self.target_name}: {raw_text}")

        # F4: prefer the declared structured result (contract v1) when the
        # server publishes one; fall back to parsing the JSON text content
        # (pre-contract lab servers), then to plain text (non-lab agents —
        # a bare number/list raises TypeError).
        structured = getattr(result, "structuredContent", None)
        try:
            data = structured if isinstance(structured, dict) else json.loads(raw_text)
            resp = AgentResponse.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            resp = AgentResponse(text=raw_text)
        resp.latency_ms = int((time.perf_counter() - start) * 1000)
        return resp
