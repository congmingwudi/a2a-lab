"""The Anthropic Managed Agents orchestrator variant (WS8), in two topologies.

Runs the supplier-disruption fan-out with a Managed Agent as the orchestrator.
The same agent runs both ways; what changes is where the tools live.

**tool** (the original, and now the control). `consult_business_units` is a
CUSTOM tool, which on this platform means the model asks for it and **the host
executes it** — so the fan-out is `orchestration.dispatch` running here, and the
host owns concurrency, per-leg timeouts and the partial-failure contract. The
model's only scheduling decision is when to call the tool. It also needs a
process attached to the session while it works, the same constraint
`briefs/runner.py` has, and the reason WS7 lists a hosted watcher.

**mcp** (WS7 item 4). The three units are separate tools on a remote MCP server,
which executes on Anthropic's orchestration layer. Nothing is attached to the
session, and the MODEL chooses which units to call, in what order, and whether
to issue them together. Selected per run via `agent_with_overrides` on
`sessions.create` — same agent id, different `system` and `tools` — because a
tool inventory is not something a prompt can change.

Both are compared against the ADK variant, which declares concurrency in its
agent graph (`ParallelAgent`). Three points on one axis: who decides what runs
in parallel — a host, a graph, or the model.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from interop.models import new_trace_id
from interop.trace import Hop
from orchestration.runner import FanOutResult, dispatch

STATE_DIR = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab"))
STATE_FILE = STATE_DIR / "fanout_orchestrator.json"
MCP_STATE_FILE = STATE_DIR / "fanout_mcp_orchestrator.json"
# Env overrides carrying the whole state JSON, for hosts with no .a2alab/ — the
# same shape managed_backend.load_managed_ids() uses for CLAUDE_MANAGED_*, and
# for the same reason: the ids (and the mcp variant's system prompt) live in a
# file no container has, so the hosted console must be handed them explicitly.
# The blobs are ids + a prompt, not secrets — the MCP bearer stays in the
# Anthropic vault referenced by vault_id, never in this JSON — so they ride the
# task definition's plain env, not the secret (deploy/console/deploy_console.sh).
STATE_ENV = "A2ALAB_FANOUT_ORCH_STATE"
MCP_STATE_ENV = "A2ALAB_FANOUT_MCP_ORCH_STATE"
TOOL_NAME = "consult_business_units"
MCP_SERVER_NAME = "business-units"


class OrchestratorNotProvisioned(RuntimeError):
    """No orchestrator state on this host — env override empty and no state file.

    A distinct type, not SystemExit, so the console's /api/run can catch it and
    answer with a 409 JSON body instead of letting it escape as a plaintext HTTP
    500 that the browser then tries to JSON.parse (the "Unexpected token 'I',
    Internal S..." the operator saw). CLI callers still get the message.
    """


def load_state() -> dict[str, Any]:
    raw = os.environ.get(STATE_ENV)
    if raw:
        return json.loads(raw)
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    raise OrchestratorNotProvisioned(
        "no orchestrator provisioned — run scripts/setup_fanout_orchestrator.py first "
        f"(or set {STATE_ENV} on a host with no .a2alab/)"
    )


def load_mcp_state() -> dict[str, Any]:
    raw = os.environ.get(MCP_STATE_ENV)
    if raw:
        return json.loads(raw)
    if MCP_STATE_FILE.exists():
        return json.loads(MCP_STATE_FILE.read_text())
    raise OrchestratorNotProvisioned(
        "no MCP variant provisioned — run scripts/setup_fanout_orchestrator.py --mcp first "
        f"(or set {MCP_STATE_ENV} on a host with no .a2alab/)"
    )


@dataclass
class ToolCall:
    """One tool invocation the model made, with the turn it belonged to.

    `turn` is what the parallelism question actually needs: the MCP variant is
    interesting only if the model issues several units in ONE turn rather than
    walking them one at a time, and a flat list of calls cannot tell those
    apart. Turns are counted off `span.model_request_start`, which is the only
    boundary the event stream gives.
    """

    name: str
    turn: int
    at_ms: int


@dataclass
class CallPath:
    """What the model actually did — the measurement, not the brief."""

    calls: list[ToolCall] = field(default_factory=list)
    turns: int = 0

    @property
    def parallel(self) -> bool:
        """True if any single turn carried more than one unit."""
        return any(self.per_turn().values()) and max(self.per_turn().values()) > 1

    def per_turn(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for call in self.calls:
            counts[call.turn] = counts.get(call.turn, 0) + 1
        return counts

    def render(self) -> str:
        if not self.calls:
            return "no tool calls"
        groups = []
        for turn in sorted(self.per_turn()):
            names = [c.name for c in self.calls if c.turn == turn]
            groups.append(f"turn {turn}: {' + '.join(names)}")
        shape = "parallel" if self.parallel else "serial"
        return f"{shape} — " + "; ".join(groups)


class CmaOrchestrator:
    """Drives one managed session to completion, servicing the fan-out tool."""

    def __init__(
        self,
        client: Any = None,
        state: dict[str, Any] | None = None,
        variant: str = "tool",
        dispatch_mode: str = "sync",
    ):
        if variant not in ("tool", "mcp"):
            raise ValueError(f"unknown variant '{variant}' — known: tool, mcp")
        if dispatch_mode not in ("sync", "async"):
            raise ValueError(f"unknown dispatch_mode '{dispatch_mode}' — known: sync, async")
        self._client = client
        self._variant = variant
        # How each leg is called (WS11). Under "tool" the host runs `dispatch()`,
        # so sync/async is the host's poll behaviour. Under "mcp" the legs run
        # inside the Lambda's MCP tools: sync uses the blocking consult_<unit>
        # tools, async the fire-then-poll submit_<unit> + check_task tools backed
        # by the durable store (fanout_mcp.tasks) — the model drives the poll
        # loop. The choice reaches "mcp" by selecting the system prompt below.
        self._dispatch_mode = dispatch_mode
        self._state = state or (load_mcp_state() if variant == "mcp" else load_state())
        # The mcp variant reaches dispatch_mode too now (WS11 items 6-7): async
        # selects the fire-then-poll SYSTEM prompt (submit_<unit> + check_task)
        # over the blocking consult_<unit> one. Both prompts and both tool sets
        # live on the one provisioned agent/server; the prompt is what steers
        # the model to the right pair, so switching topology needs no redeploy.
        if variant == "mcp" and dispatch_mode == "async" and not self._state.get("system_async"):
            raise OrchestratorNotProvisioned(
                "MCP async prompt missing — re-run "
                "scripts/setup_fanout_orchestrator.py --mcp to provision `system_async` "
                f"(or set it in {MCP_STATE_ENV})"
            )
        self.fanout: FanOutResult | None = None
        self.call_path = CallPath()

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
                dispatch_mode=self._dispatch_mode,
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

    def _session_kwargs(self) -> dict[str, Any]:
        """How this variant's session is created.

        The MCP variant is `agent_with_overrides`, not a second agent: the same
        agent id runs with a different `system` and `tools` for this session
        only. That is the whole point of keeping one agent — a difference in the
        brief can then be attributed to the tool topology rather than to two
        subtly different agent configs that drifted apart.

        Overrides REPLACE rather than merge, so `tools` here lists everything
        the session gets — the custom fan-out tool is deliberately absent, which
        is what forces the model to reach for the three MCP tools instead.
        """
        if self._variant != "mcp":
            return {
                "agent": self._state["agent_id"],
                "environment_id": self._state["environment_id"],
                "title": "a2a-lab fan-out orchestrator",
            }
        # Async selects the fire-then-poll prompt; sync the blocking one. The
        # tool inventory on the server is the same either way (build_registry
        # exposes both sets) — the prompt is what tells the model which pair to
        # use, so this is the only line that changes between MCP topologies.
        system = (
            self._state["system_async"] if self._dispatch_mode == "async" else self._state["system"]
        )
        return {
            "agent": {
                "type": "agent_with_overrides",
                "id": self._state["agent_id"],
                "system": system,
                "mcp_servers": [
                    {"type": "url", "name": MCP_SERVER_NAME, "url": self._state["mcp_url"]}
                ],
                "tools": [
                    {
                        "type": "mcp_toolset",
                        "mcp_server_name": MCP_SERVER_NAME,
                        # always_allow, explicitly: this workspace evaluates MCP
                        # tools as "ask" by default, and an unattended run has
                        # nobody to confirm — the session would idle forever
                        # waiting, which is the exact laptop-dependency this
                        # variant exists to remove.
                        "default_config": {
                            "enabled": True,
                            "permission_policy": {"type": "always_allow"},
                        },
                    }
                ],
            },
            "environment_id": self._state["environment_id"],
            "vault_ids": [self._state["vault_id"]],
            "title": "a2a-lab fan-out orchestrator (remote MCP)",
        }

    async def run(self, situation: str, trace_id: str | None = None) -> dict[str, Any]:
        client = self._anthropic()
        trace_id = trace_id or new_trace_id()
        start = time.perf_counter()

        # The run id the model must thread through every tool call. Stated in
        # the message rather than the system prompt because it changes per run
        # and the prompt is cached across them.
        kickoff = situation if self._variant != "mcp" else f"RUN ID: {trace_id}\n\n{situation}"

        session = await client.beta.sessions.create(**self._session_kwargs())
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
                        {"type": "user.message", "content": [{"type": "text", "text": kickoff}]}
                    ],
                )
                async for event in stream:
                    etype = getattr(event, "type", "")
                    if etype == "agent.message":
                        for block in getattr(event, "content", []) or []:
                            if getattr(block, "type", "") == "text":
                                texts.append(block.text)
                    elif etype == "span.model_request_start":
                        # The only turn boundary the stream offers, and the whole
                        # basis of the parallelism measurement: tool calls
                        # between two model requests were issued together.
                        self.call_path.turns += 1
                    elif etype == "agent.mcp_tool_use":
                        self.call_path.calls.append(
                            ToolCall(
                                name=getattr(event, "name", "?"),
                                turn=self.call_path.turns,
                                at_ms=int((time.perf_counter() - start) * 1000),
                            )
                        )
                    elif etype == "agent.custom_tool_use":
                        self.call_path.calls.append(
                            ToolCall(
                                name=getattr(event, "name", "?"),
                                turn=self.call_path.turns,
                                at_ms=int((time.perf_counter() - start) * 1000),
                            )
                        )
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
            "variant": self._variant,
            "dispatch_mode": self._dispatch_mode,
            "wall_ms": int((time.perf_counter() - start) * 1000),
            "fanout": self.fanout,
            "call_path": self.call_path,
        }
