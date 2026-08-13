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
from orchestration.runner import async_leg_timeout_s, leg_timeout_s, run_one

# The caller identity stamped on every delegation these tools make. It is the
# ORCHESTRATOR's name, not this server's: the business-unit agents are being
# consulted on the orchestrator's behalf, and the D34 join convention reads
# this to attribute a platform session back to the run.
CALLER = "a2alab-supply-orchestrator"
CALLER_PLATFORM = "claude"

TOOL_PREFIX = "consult_"
SUBMIT_PREFIX = "submit_"
CHECK_TOOL = "check_task"


def _unit_slug(agent: LegAgent) -> str:
    """The business-unit slug a tool is named after: `logistics`,
    `commercial`, `customer_comms`. Named after the BUSINESS UNIT rather than
    the role id or the platform — the model is choosing who to ask, and
    "consult_adk" would be a question about our deployment, not the org chart,
    nor should a replatformed unit change the tool the model calls."""
    return agent.business_unit.split("/")[0].strip().lower().replace(" ", "_")


def tool_name(agent: LegAgent) -> str:
    """`consult_logistics`, `consult_commercial`, `consult_customer_comms` —
    the synchronous (blocking) per-unit tool."""
    return TOOL_PREFIX + _unit_slug(agent)


def submit_tool_name(agent: LegAgent) -> str:
    """`submit_logistics`, … — the ASYNC half's fire tool (WS11 items 6-7). It
    starts the leg and returns a task id without waiting; the model then polls
    `check_task`. Same slug as the consult tool, different verb: submit vs ask."""
    return SUBMIT_PREFIX + _unit_slug(agent)


def _agent_by_slug(slug: str) -> LegAgent | None:
    for agent in LEG_AGENTS:
        if _unit_slug(agent) == slug:
            return agent
    return None


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


# --- async fire-then-poll (WS11 items 6-7) --------------------------------

# submit returns fast and does NOT carry the answer; check_task carries state.
SUBMIT_SCHEMA = INPUT_SCHEMA  # same inputs as consult: situation + run_id
CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {
            "type": "string",
            "description": "A task id returned by a submit_* call. Reads that one task.",
        },
        "run_id": {
            "type": "string",
            "description": (
                "Your run id. Reads ALL tasks under it at once — the quickest "
                "way to see whether every unit is done. Pass this OR task_id."
            ),
        },
    },
}


def _submit_description(agent: LegAgent) -> str:
    return (
        f"START the {agent.business_unit} business unit working on a supply "
        f"disruption and return a task id IMMEDIATELY, without waiting for the "
        f"answer (it runs {agent.agent_name} on {platform_label(agent.platform)}). "
        "Then poll `check_task` with that id until it is COMPLETED or FAILED. "
        "Independent of the other submit_* tools: call them in any order, "
        "together in one turn."
    )


_CHECK_DESCRIPTION = (
    "Read the state of asynchronous unit work. Pass a `task_id` from a submit_* "
    "call for one task, or your `run_id` for all of them. Each task's `state` is "
    "SUBMITTED or WORKING (keep polling), COMPLETED (its `result` is the unit's "
    "section) or FAILED (its `error` says why — treat the unit as unavailable)."
)


def worker_runner(run_id: str) -> Any:
    """Build the `run_task` runner for one run: `(unit_slug, situation) -> str`.

    Bound to the run id (which the worker resolves from the task row before
    calling this) so the leg's trace Hop correlates under the SAME id the model
    threaded through submit — the only thread joining legs that execute in
    separate Lambda invocations. Maps the stored unit slug back to its leg agent
    and runs the identical `run_one` path the sync tools use, so the only
    difference between sync and async is WHEN the answer is read, not how it is
    produced. Returns the rendered section (or the `[leg unavailable: ...]`
    marker) as text, so check_task hands back the same evidence consult does.
    """

    def _run(unit_slug: str, situation: str) -> str:
        agent = _agent_by_slug(unit_slug)
        if agent is None:
            return f"[leg unavailable: unknown unit '{unit_slug}']"
        result = asyncio.run(
            run_one(
                agent.role,
                situation,
                caller=CALLER,
                caller_platform=CALLER_PLATFORM,
                trace_id=run_id,
                # The worker is NOT gateway-bound (self-invoke Event window,
                # D47), so it gets the full async budget rather than the tight
                # sync one the deploy pins for the consult_* HTTP path. Without
                # this a COLD leg (Foundry ~26.5s) is killed at 25s and stored
                # as '[leg unavailable: timed out]' though the task COMPLETES —
                # the exact case fire-then-poll exists to serve (WS11).
                timeout_s=async_leg_timeout_s(),
            )
        )
        return result.render()

    return _run


class AsyncFanOutTools:
    """The fire-then-poll tool bodies over the durable task store.

    `submit` writes SUBMITTED and asks the dispatcher to start a worker in its
    OWN invocation, then returns a task id — it does NOT run the leg, because on
    a function runtime work started before the response is frozen until a later
    call thaws it (D47). `check` reads the row every instance shares. The store
    and dispatcher are injected so the whole flow tests without AWS.
    """

    def __init__(self, store: Any = None, dispatcher: Any = None):
        self._store = store
        self._dispatcher = dispatcher

    @property
    def store(self) -> Any:
        if self._store is None:
            from fanout_mcp.tasks import TaskStore

            self._store = TaskStore()
        return self._store

    @property
    def dispatcher(self) -> Any:
        if self._dispatcher is None:
            from fanout_mcp.tasks import lambda_dispatcher

            self._dispatcher = lambda_dispatcher()
        return self._dispatcher

    def submit(self, agent: LegAgent, args: dict[str, Any]) -> str:
        situation = str(args.get("situation") or "").strip()
        run_id = str(args.get("run_id") or "").strip()
        if not situation:
            return json.dumps({"error": "situation is required"})
        if not run_id:
            # Same refusal as consult: a generated id would make the call
            # succeed and the run incoherent — three legs under three ids look
            # like a working fan-out until you try to join them.
            return json.dumps(
                {"error": "run_id is required — pass the run id from your task to every tool"}
            )
        row = self.store.create(run_id, _unit_slug(agent), situation)
        self.dispatcher(row.task_id)
        return json.dumps(
            {
                "task_id": row.task_id,
                "unit": row.unit,
                "state": row.state,
                "poll_with": CHECK_TOOL,
            }
        )

    def check(self, args: dict[str, Any]) -> str:
        task_id = str(args.get("task_id") or "").strip()
        run_id = str(args.get("run_id") or "").strip()
        if task_id:
            row = self.store.get(task_id)
            if row is None:
                return json.dumps({"error": f"unknown task_id '{task_id}'"})
            return json.dumps(row.as_dict())
        if run_id:
            rows = self.store.for_run(run_id)
            return json.dumps({"run_id": run_id, "tasks": [r.as_dict() for r in rows]})
        return json.dumps({"error": "pass a task_id or a run_id"})


def build_registry(
    tools: FanOutTools | None = None,
    async_tools: AsyncFanOutTools | None = None,
) -> ToolRegistry:
    """Register every fan-out tool the server exposes.

    ONE server carries both topologies — the synchronous `consult_<unit>` tools
    (dispatch_mode sync) and the fire-then-poll `submit_<unit>` + `check_task`
    tools (dispatch_mode async, WS11) — so no second deploy is needed to switch.
    Which set a run actually USES is steered by the orchestrator's system prompt
    (mcp_orchestrator_prompt vs mcp_orchestrator_prompt_async), not by hiding
    tools here: tools/list returns all of them, and the prompt names the pair
    for the mode.
    """
    tools = tools or FanOutTools()
    async_tools = async_tools or AsyncFanOutTools()
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
        registry.register(
            ToolDef(
                name=submit_tool_name(agent),
                description=_submit_description(agent),
                input_schema=SUBMIT_SCHEMA,
                fn=lambda args, agent=agent: async_tools.submit(agent, args),
            )
        )
    registry.register(
        ToolDef(
            name=CHECK_TOOL,
            description=_CHECK_DESCRIPTION,
            input_schema=CHECK_SCHEMA,
            fn=lambda args: async_tools.check(args),
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


def roster_async() -> str:
    """The unit list for the ASYNC prompt: each unit is a `submit_<unit>` fire
    tool plus the shared `check_task` poll tool (WS11 items 6-7)."""
    lines = [
        f"- {agent.business_unit} — submit with `{submit_tool_name(agent)}`, then "
        f"poll `{CHECK_TOOL}` ({agent.agent_name} on {platform_label(agent.platform)})"
        for agent in LEG_AGENTS
    ]
    return "\n".join(lines)


def timeout_note() -> str:
    return (
        f"Each unit answers within about {int(leg_timeout_s())}s, or the tool "
        "returns a '[leg unavailable: ...]' marker instead of an answer."
    )


def timeout_note_async() -> str:
    """The budget line for the ASYNC prompt. The submit/poll worker runs off the
    gateway path (D47), so its per-leg budget is `async_leg_timeout_s()` — much
    larger than the sync tools' — and a cold platform that would time out under
    the blocking consult_* tools has room to finish here. Distinct from
    `timeout_note()` so the async prompt does not quote the sync ceiling."""
    return (
        f"Each unit runs asynchronously and answers within about "
        f"{int(async_leg_timeout_s())}s; keep polling check_task until it is "
        "COMPLETED or FAILED. A unit that never finishes is reported as a "
        "'[leg unavailable: ...]' marker, not a hang."
    )


def auth_token() -> str | None:
    """Bearer token this server requires, if any (matches the vault credential
    on the Anthropic side). Same env-var shape as the obs MCP server."""
    return os.environ.get("A2ALAB_FANOUT_MCP_TOKEN")
