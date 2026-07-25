"""The Lab Guide agent — the console's docent (plan/07-workstreams.md).

An ``AgentAdapter`` whose interior is a direct Anthropic tool-use loop
(Haiku-tier by default) grounded in the lab's own docs (corpus.py) with
curated read tools (tools.py) for the ADR log, results, analyst briefs,
and individual wire traces. Because it implements the adapter contract,
``serve(adapter, protocol, port)`` gives it REST/MCP/A2A surfaces for
free — the guide is just another lab agent (the meta exhibit), and its
MCP server additionally exposes the raw read tools so a CLIENT's model
can do the reasoning instead (two integration shapes, side by side).

The console's ``POST /api/guide`` uses ``answer_stream`` directly for
token streaming; ``handle`` is the buffered protocol-facing wrapper.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, AsyncIterator

from interop.models import AgentRequest, AgentResponse, new_trace_id
from interop.trace import Hop
from platforms.guide import corpus, tools

MAX_TOOL_ROUNDS = 6
MAX_TOKENS = 1500

TOOL_DEFS = [
    {
        "name": "get_decision",
        "description": (
            "Read one ADR from the decision log by id (e.g. 'D27'). The ADR "
            "index in your grounding lists what exists."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"decision_id": {"type": "string"}},
            "required": ["decision_id"],
        },
    },
    {
        "name": "read_doc",
        "description": (
            "Read a whitelisted lab doc in full: plan/00-decisions.md, "
            "plan/03-results.md (measured numbers), plan/04-runbooks.md, "
            "plan/05-observability.md, plan/07-workstreams.md, "
            "config/targets.yaml, config/scenarios.yaml."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "list_recent_runs",
        "description": (
            "Recent lab runs (newest first): trace_id, hop count, targets and "
            "protocols touched. Use to resolve 'the last X run' to a concrete "
            "trace before get_trace. Optional target_contains filter."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
                "target_contains": {"type": "string"},
            },
        },
    },
    {
        "name": "get_trace",
        "description": (
            "One run's full hop list from the wire record (payloads clipped): "
            "source→target, protocol, latency, status per hop. The ground "
            "truth for 'why did this run take 35s' or 'which twin answered'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"trace_id": {"type": "string"}},
            "required": ["trace_id"],
        },
    },
    {
        "name": "list_briefs",
        "description": (
            "The hosted obs analyst's findings briefs (D23), newest first — "
            "headers and a preview; read one in full with read_brief."
        ),
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}},
    },
    {
        "name": "read_brief",
        "description": "One analyst brief in full, by id from list_briefs.",
        "input_schema": {
            "type": "object",
            "properties": {"brief_id": {"type": "integer"}},
            "required": ["brief_id"],
        },
    },
]


def _run_tool(name: str, args: dict[str, Any]) -> Any:
    if name == "get_decision":
        return corpus.get_decision(str(args["decision_id"]))
    if name == "read_doc":
        return corpus.read_doc(str(args["name"]))
    if name == "list_recent_runs":
        return tools.list_recent_runs(
            limit=int(args.get("limit") or 10),
            target_contains=args.get("target_contains"),
        )
    if name == "get_trace":
        return tools.get_trace(str(args["trace_id"]))
    if name == "list_briefs":
        return tools.list_briefs(limit=int(args.get("limit") or 5))
    if name == "read_brief":
        return tools.read_brief(int(args["brief_id"]))
    raise ValueError(f"unknown tool {name}")


def guide_model() -> str:
    return (
        os.environ.get("GUIDE_MODEL") or os.environ.get("CLAUDE_AGENT_MODEL") or "claude-haiku-4-5"
    )


# ---- MCP extra tools (the raw-tools integration shape) ----------------------
# Registered alongside `ask` on the guide's MCP server: the CLIENT's model
# reasons over lab data instead of the lab's. Sync, typed, docstring-described
# — FastMCP derives the schemas.


def mcp_get_decision(decision_id: str) -> str:
    """Read one lab ADR (architecture decision record) by id, e.g. 'D27'."""
    return corpus.get_decision(decision_id)


def mcp_read_doc(name: str) -> str:
    """Read a whitelisted lab doc in full (plan/*.md, config/*.yaml)."""
    return corpus.read_doc(name)


def mcp_list_recent_runs(limit: int = 10, target_contains: str | None = None) -> str:
    """Recent lab runs: trace_id, hop count, targets/protocols touched."""
    return json.dumps(tools.list_recent_runs(limit=limit, target_contains=target_contains))


def mcp_get_trace(trace_id: str) -> str:
    """One lab run's full hop list from the wire record (payloads clipped)."""
    return json.dumps(tools.get_trace(trace_id))


def mcp_list_briefs(limit: int = 5) -> str:
    """The hosted obs analyst's findings briefs — headers and previews."""
    return json.dumps(tools.list_briefs(limit=limit))


def mcp_read_brief(brief_id: int) -> str:
    """One analyst brief in full, by id."""
    return json.dumps(tools.read_brief(brief_id))


class GuideAdapter:
    name = "lab-guide"
    description = (
        "Q&A docent for the A2A interop lab: explains call paths, protocol "
        "seams, hosting and observability per platform, and the measured "
        "findings — grounded in the lab's own docs, ADR log, analyst briefs, "
        "and wire traces."
    )
    # The meta exhibit's second shape: raw read tools on the MCP server.
    extra_mcp_tools = [
        mcp_get_decision,
        mcp_read_doc,
        mcp_list_recent_runs,
        mcp_get_trace,
        mcp_list_briefs,
        mcp_read_brief,
    ]

    def __init__(self, client=None):
        self._client = client  # injected in tests

    def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic()
        return self._client

    def _system(self, view: dict | None) -> list[dict]:
        blocks: list[dict] = [
            {
                "type": "text",
                "text": corpus.system_prompt(),
                "cache_control": {"type": "ephemeral"},
            }
        ]
        if view:
            blocks.append(
                {
                    "type": "text",
                    "text": (
                        "[CONSOLE CONTEXT] The visitor is currently looking at: "
                        + json.dumps(view, default=str)[:600]
                        + " — 'this' in their question likely refers to it."
                    ),
                }
            )
        return blocks

    async def answer_stream(
        self,
        message: str,
        history: list[dict] | None = None,
        view: dict | None = None,
    ) -> AsyncIterator[dict]:
        """Yield {'type': 'delta'|'tool'|'done', ...} events. History is
        client-held [{role, content}] pairs (stateless turns, like the
        mega-demo's Solution Guide)."""
        client = self._get_client()
        messages: list[dict] = []
        for turn in (history or [])[-12:]:
            role = "assistant" if turn.get("role") == "assistant" else "user"
            content = str(turn.get("content") or "")[:4000]
            if content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": message})

        full_text: list[str] = []
        for _ in range(MAX_TOOL_ROUNDS):
            async with client.messages.stream(
                model=guide_model(),
                max_tokens=MAX_TOKENS,
                system=self._system(view),
                tools=TOOL_DEFS,
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    full_text.append(text)
                    yield {"type": "delta", "text": text}
                final = await stream.get_final_message()

            if final.stop_reason != "tool_use":
                break
            messages.append({"role": "assistant", "content": final.content})
            results = []
            for block in final.content:
                if block.type != "tool_use":
                    continue
                yield {"type": "tool", "name": block.name, "input": block.input}
                try:
                    out = _run_tool(block.name, dict(block.input or {}))
                    content = out if isinstance(out, str) else json.dumps(out, default=str)
                except Exception as exc:
                    content = f"tool error: {exc}"
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": content[:40_000],
                    }
                )
            messages.append({"role": "user", "content": results})

        yield {"type": "done", "text": "".join(full_text)}

    async def handle(self, req: AgentRequest) -> AgentResponse:
        trace_id = req.trace_id or new_trace_id()
        start = time.perf_counter()
        with Hop(
            trace_id,
            source="lab-guide",
            target="anthropic-api",
            protocol="internal",
            transport_detail=f"messages.stream tool loop ({guide_model()})",
            request_payload={"message": req.message},
        ) as hop:
            text = ""
            async for event in self.answer_stream(req.message):
                if event["type"] == "done":
                    text = event["text"]
            hop.response_payload = text
        return AgentResponse(
            text=text.strip(),
            session_id=req.session_id,
            latency_ms=int((time.perf_counter() - start) * 1000),
            raw={"backend": "lab-guide", "model": guide_model()},
        )


def make_adapter() -> GuideAdapter:
    return GuideAdapter()
