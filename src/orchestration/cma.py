"""The Anthropic Managed Agents orchestrator variant (WS8).

Runs the supplier-disruption fan-out with a Managed Agent as the orchestrator.
Its `consult_business_units` tool is a CUSTOM tool, which on this platform means
the model asks for it and **the host executes it** — so the fan-out itself is
`orchestration.dispatch` running here, and the host owns concurrency, per-leg
timeouts and the partial-failure contract. The model's only scheduling decision
is when to call the tool.

That is the axis this variant exists to expose. The ADK variant declares
concurrency in its agent graph (`ParallelAgent`) instead, so the same scenario
is run two ways and the difference is where the fan-out lives, not what it does.

Consequence worth naming: because the tool runs host-side, this orchestrator
needs a process attached to the session while it works — the same constraint
`briefs/runner.py` has, and the reason WS7 lists a hosted watcher.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from interop.models import new_trace_id
from interop.trace import Hop
from orchestration.runner import FanOutResult, dispatch

STATE_FILE = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "fanout_orchestrator.json"
TOOL_NAME = "consult_business_units"


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        raise SystemExit(
            "no orchestrator provisioned — run scripts/setup_fanout_orchestrator.py first"
        )
    return json.loads(STATE_FILE.read_text())


class CmaOrchestrator:
    """Drives one managed session to completion, servicing the fan-out tool."""

    def __init__(self, client: Any = None, state: dict[str, Any] | None = None):
        self._client = client
        self._state = state or load_state()
        self.fanout: FanOutResult | None = None

    def _anthropic(self):
        if self._client is None:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic()
        return self._client

    async def _service_tool(self, session_id: str, event: Any, trace_id: str) -> None:
        """Execute the fan-out host-side and hand the result back to the model."""
        args = dict(getattr(event, "input", None) or {})
        situation = str(args.get("situation") or "").strip()
        if getattr(event, "name", "") != TOOL_NAME:
            result_text = f"Unknown tool: {getattr(event, 'name', '?')}"
        elif not situation:
            result_text = "No situation supplied — cannot consult the business units."
        else:
            self.fanout = await dispatch(
                situation,
                caller="a2alab-supply-orchestrator",
                caller_platform="claude",
                trace_id=trace_id,
            )
            result_text = self.fanout.render()

        client = self._anthropic()
        await client.beta.sessions.events.send(
            session_id=session_id,
            events=[
                {
                    "type": "user.custom_tool_result",
                    "custom_tool_use_id": event.id,
                    "content": [{"type": "text", "text": result_text}],
                }
            ],
        )

    async def run(self, situation: str, trace_id: str | None = None) -> dict[str, Any]:
        client = self._anthropic()
        trace_id = trace_id or new_trace_id()
        start = time.perf_counter()

        session = await client.beta.sessions.create(
            agent=self._state["agent_id"],
            environment_id=self._state["environment_id"],
            title="a2a-lab fan-out orchestrator",
        )
        texts: list[str] = []
        with Hop(
            trace_id,
            source="operator",
            target="a2alab-supply-orchestrator",
            protocol="managed-agents-api",
            transport_detail="sessions.events stream (fan-out orchestrator)",
            request_payload={"situation": situation},
        ) as hop:
            hop.platform_ref = session.id
            stream = await client.beta.sessions.events.stream(session_id=session.id)
            try:
                await client.beta.sessions.events.send(
                    session_id=session.id,
                    events=[
                        {"type": "user.message", "content": [{"type": "text", "text": situation}]}
                    ],
                )
                async for event in stream:
                    etype = getattr(event, "type", "")
                    if etype == "agent.message":
                        for block in getattr(event, "content", []) or []:
                            if getattr(block, "type", "") == "text":
                                texts.append(block.text)
                    elif etype == "agent.custom_tool_use":
                        await self._service_tool(session.id, event, trace_id)
                    elif etype == "session.status_idle":
                        stop = getattr(event, "stop_reason", None)
                        if getattr(stop, "type", None) != "requires_action":
                            break
                    elif etype == "session.status_terminated":
                        break
                    elif etype == "session.error":
                        raise RuntimeError(f"managed session error: {event}")
            finally:
                await stream.close()
            brief = "\n".join(t for t in texts if t).strip()
            hop.response_payload = {"brief": brief}

        return {
            "brief": brief,
            "trace_id": trace_id,
            "session_id": session.id,
            "wall_ms": int((time.perf_counter() - start) * 1000),
            "fanout": self.fanout,
        }
