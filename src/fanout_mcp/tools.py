"""One MCP tool per business unit (WS7 item 4).

Each tool runs exactly one leg of the supplier-disruption scenario through
`orchestration.run_one`, so the delegation rider, the D27 depth guard, the
per-leg timeout and the trace Hop are the same code the host-side variant
uses. Only the *scheduling* moves: it is the model's, not `asyncio.gather`'s.

Two design points are load-bearing for the comparison.

**Identical evidence.** A tool returns `LegResult.render()` — the same source
header, the same `[leg unavailable: ...]` marker — because the previous
experiment showed that attribution changes what the orchestrator writes. If
this variant handed back differently-shaped text, every difference in the brief
would be confounded by the input.

**Coverage becomes the model's job.** The host-side tool ends its result with
`[fan-out coverage: n/3 legs answered]`, computed by code that knows how many
legs exist. Three independent tools have no such vantage point: nothing but the
model knows whether it called all three. That is not a gap to paper over — it
is the sharpest question in this experiment, because the lab's 2026-07-25
finding was a delegated section that went silently empty. Each tool therefore
states its own unit and status explicitly, and the agent prompt carries the
roster; whether the model then notices a unit it never consulted is a result to
report, not a property to assume.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from mcp_http.core import ToolDef, ToolRegistry
from orchestration.agents import LEG_AGENTS, LegAgent, platform_label
from orchestration.runner import leg_timeout_s, run_one

# The caller identity stamped on every delegation these tools make. It is the
# ORCHESTRATOR's name, not this server's: the business-unit agents are being
# consulted on the orchestrator's behalf, and the D34 join convention reads
# this to attribute a platform session back to the run.
CALLER = "a2alab-supply-orchestrator"
CALLER_PLATFORM = "claude"

TOOL_PREFIX = "consult_"


def tool_name(agent: LegAgent) -> str:
    """`consult_logistics`, `consult_commercial`, `consult_customer_comms`.

    Named after the BUSINESS UNIT rather than the role id or the platform. The
    model is choosing who to ask, and "consult_logistics" is a question about
    the org chart; "consult_adk" would be a question about our deployment, which
    is not information the model should be reasoning with — nor should a
    replatformed unit change the tool the model calls.
    """
    return TOOL_PREFIX + agent.business_unit.split("/")[0].strip().lower().replace(" ", "_")


def _description(agent: LegAgent) -> str:
    return (
        f"Ask the {agent.business_unit} business unit about a supply "
        f"disruption. Runs {agent.agent_name} on {platform_label(agent.platform)}. "
        f"It answers ONLY for {agent.business_unit} — it will not cover the "
        "other units' questions, so consult each unit you need. Independent of "
        "the other consult_* tools: they may be called in any order, and "
        "together in one turn."
    )


INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "situation": {
            "type": "string",
            "description": "The disruption as reported, verbatim.",
        },
        "run_id": {
            "type": "string",
            "description": (
                "The run id given to you in the task. Pass the SAME value to "
                "every consult_* call so the units' work is correlated as one run."
            ),
        },
    },
    "required": ["situation", "run_id"],
}


class FanOutTools:
    """The three per-unit tool bodies.

    Stateless between calls on purpose: each MCP tool call may land on a
    different Lambda instance, so anything remembered here is a lie the second
    invocation tells. The trace_id arrives as an argument for the same reason —
    it is the only thread connecting three separate processes into one run.
    """

    def __init__(self, runner: Any = None):
        # injectable for tests: same signature as orchestration.run_one
        self._runner = runner or run_one

    def consult(self, agent: LegAgent, args: dict[str, Any]) -> str:
        situation = str(args.get("situation") or "").strip()
        run_id = str(args.get("run_id") or "").strip()
        if not situation:
            return json.dumps({"error": "situation is required"})
        if not run_id:
            # Refuse rather than invent one. A generated id would make the call
            # succeed and the RUN incoherent — three legs under three trace ids
            # look identical to a successful fan-out until you try to join them,
            # which is exactly the class of silent failure this lab measures.
            return json.dumps(
                {"error": "run_id is required — pass the run id from your task to every tool"}
            )
        result = asyncio.run(
            self._runner(
                agent.role,
                situation,
                caller=CALLER,
                caller_platform=CALLER_PLATFORM,
                trace_id=run_id,
            )
        )
        # The rendered section, not a JSON envelope: identical text to the
        # host-side variant, so the orchestrator is reasoning over the same
        # evidence in both and the comparison isolates the topology.
        return result.render()


def build_registry(tools: FanOutTools | None = None) -> ToolRegistry:
    tools = tools or FanOutTools()
    registry = ToolRegistry()
    for agent in LEG_AGENTS:
        registry.register(
            ToolDef(
                name=tool_name(agent),
                description=_description(agent),
                input_schema=INPUT_SCHEMA,
                # default-arg binding, not a closure over the loop variable:
                # a late-binding closure would give all three tools whichever
                # agent the loop ended on, and every tool would still WORK —
                # it would just consult Customer Operations three times.
                fn=lambda args, agent=agent: tools.consult(agent, args),
            )
        )
    return registry


def roster() -> str:
    """The unit list, for the orchestrator's system prompt.

    Generated from the same LEG_AGENTS tuple the tools are, so a fourth unit
    cannot be added to one and forgotten in the other — the prompt is where a
    model learns that a unit it never called is missing.
    """
    lines = [
        f"- {agent.business_unit} — tool `{tool_name(agent)}` "
        f"({agent.agent_name} on {platform_label(agent.platform)})"
        for agent in LEG_AGENTS
    ]
    return "\n".join(lines)


def timeout_note() -> str:
    return (
        f"Each unit answers within about {int(leg_timeout_s())}s, or the tool "
        "returns a '[leg unavailable: ...]' marker instead of an answer."
    )


def auth_token() -> str | None:
    """Bearer token this server requires, if any (matches the vault credential
    on the Anthropic side). Same env-var shape as the obs MCP server."""
    return os.environ.get("A2ALAB_FANOUT_MCP_TOKEN")
