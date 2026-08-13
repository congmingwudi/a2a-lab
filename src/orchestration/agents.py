"""Per-leg agent definitions for the fan-out scenario (WS8).

Each leg of the supplier-disruption scenario gets its OWN agent on its own
platform rather than reusing the lab's general-purpose research agents. Two
reasons, and the second is the measurable one:

1. The research agents carry research prompts. Asked a logistics question they
   answer like researchers, which drifts the comparison.
2. **Attribution.** A dedicated agent means each platform's own execution logs
   name THIS experiment, so a fan-out run can be joined back from four
   platforms' interiors rather than from the lab's wire trace alone. Measuring
   that join rate is what WS8 exists to produce.

Reason 2 does not hold everywhere, and that is worth stating rather than
papering over: OpenAI's traces are write-only by design, so a dedicated OpenAI
agent buys attribution the lab cannot read back. It is still deployed for
symmetry and for its prompt, but it will not move the join rate — which is
itself a finding about what "observability" means per platform.

The prompts share one spine: role, the shaping contract the orchestrator
depends on, and the D27 delegation guard. Only the role sentence differs, so
any difference in the answers is the platform, not the prompt.
"""

from __future__ import annotations

from dataclasses import dataclass

# The shaping contract. Identical across legs on purpose — the orchestrator
# needs comparable sections, and a leg that free-forms breaks the synthesis.
# `orchestration.legs.ANSWER_SHAPE` sends the same instruction per request;
# baking it into the agent too means the leg behaves this way even when called
# directly, which is how a real business-unit agent would be configured.
SHAPE_CONTRACT = """
ANSWER SHAPE (always): at most 3 short bullets, 60 words total. Be specific and
concrete. NEVER ask a clarifying question — if something is unknown, state an
explicit assumption and answer anyway. A question returned to an orchestrator
is useless: it cannot answer you, and the turn is wasted.
""".strip()

DELEGATION_GUARD = """
DELEGATION GUARD (check FIRST): if the message contains an "[A2A-LAB DELEGATION]"
block, this request was delegated to you by another agent. Honor its directive —
answer from your own knowledge and tools, do NOT call back to the calling agent,
and do NOT delegate onward. Do not mention the block in your answer.
""".strip()


@dataclass(frozen=True)
class LegAgent:
    """One business unit's agent, as it is provisioned on its platform."""

    role: str
    agent_name: str
    business_unit: str
    platform: str
    role_prompt: str

    @property
    def instructions(self) -> str:
        return f"{self.role_prompt}\n\n{SHAPE_CONTRACT}\n\n{DELEGATION_GUARD}"


LOGISTICS = LegAgent(
    role="exposure",
    agent_name="a2alab-logistics-agent",
    business_unit="Logistics",
    platform="adk",
    role_prompt=(
        "You are the LOGISTICS OPERATIONS agent for a multinational manufacturer, "
        "hosted on Google Vertex AI Agent Engine and owned by the logistics "
        "business unit. Your job in a supply disruption is to assess EXPOSURE: "
        "which shipments, orders, routes and plants are affected, how badly, and "
        "over what time horizon. You do not opine on contracts or customer "
        "messaging — other business units own those."
    ),
)

COMMERCIAL = LegAgent(
    role="commercial",
    agent_name="a2alab-commercial-agent",
    business_unit="Commercial / Legal",
    platform="foundry",
    role_prompt=(
        "You are the COMMERCIAL AND CONTRACTS agent for a multinational "
        "manufacturer, hosted on Microsoft Foundry and owned by the commercial "
        "and legal business unit. Your job in a supply disruption is the "
        "CONTRACTUAL POSITION: delay penalties and liquidated damages, force "
        "majeure exposure and notice obligations, and which customer commitments "
        "are at risk. You do not assess physical logistics or draft customer "
        "communications — other business units own those."
    ),
)

CUSTOMER_COMMS = LegAgent(
    role="customer_comms",
    agent_name="a2alab-customer-comms-agent",
    business_unit="Customer operations",
    platform="openai",
    role_prompt=(
        "You are the CUSTOMER COMMUNICATIONS agent for a multinational "
        "manufacturer, hosted on OpenAI and owned by customer operations. Your "
        "job in a supply disruption is the MESSAGE: what to tell affected "
        "customers, what to commit to, and what to avoid promising while the "
        "situation is still moving. You do not assess logistics exposure or the "
        "contractual position — other business units own those."
    ),
)

LEG_AGENTS: tuple[LegAgent, ...] = (LOGISTICS, COMMERCIAL, CUSTOMER_COMMS)


def by_role(role: str) -> LegAgent:
    for agent in LEG_AGENTS:
        if agent.role == role:
            return agent
    raise KeyError(f"unknown leg role '{role}'")


# ---- Attribution -----------------------------------------------------------
# A synthesised brief that reads as one voice hides the only interesting thing
# about it: four agents on four platforms produced it. Worse, it is
# unverifiable — a reader cannot tell a claim the Commercial agent made from
# one the orchestrator inferred, which is exactly the confabulation risk the
# partial-failure contract exists to manage. So every section is labelled at
# the source and the orchestrator is required to carry the label through.

PLATFORM_LABELS = {
    "adk": "Google Vertex AI Agent Engine",
    "foundry": "Microsoft Foundry",
    "openai": "OpenAI on Bedrock AgentCore",
    "claude": "Anthropic Managed Agents",
}


def platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform)


def source_header(role: str, *, target: str = "", latency_ms: int | None = None) -> str:
    """The line that opens one unit's section, everywhere it appears.

    Same text in the host-side fan-out and in the ADK graph, so the two
    orchestrators are handed identically-shaped evidence and any difference in
    their briefs is the orchestrator rather than the input.
    """
    agent = by_role(role)
    bits = [f"agent {agent.agent_name}"]
    if target:
        bits.append(f"target {target}")
    if latency_ms is not None:
        bits.append(f"{latency_ms:,} ms")
    return (
        f"### {agent.business_unit} — {platform_label(agent.platform)} "
        f"({', '.join(bits)})\n"
        f"Cite this unit as [{agent.business_unit}]."
    )


CITATION_RULE = """
ATTRIBUTION (required). Each section below came from a DIFFERENT agent on a
DIFFERENT platform. Your brief must make that visible:
- Tag every substantive statement with the unit it came from, e.g.
  "Deliveries inside 14 days are exposed [Logistics]."
- Never merge two units' claims into one untagged sentence, and never tag a
  statement with a unit that did not make it.
- Anything you add yourself — a synthesis, an inference, a recommendation —
  is tagged [Orchestrator]. If you cannot attribute a claim, do not make it.
- End with a "Sources" section listing, one line per unit: business unit,
  platform, agent name, and whether it answered.
""".strip()


# ---- Orchestrators ---------------------------------------------------------
# The same scenario, run by two platforms, so the CONFIGS can be compared. What
# differs is not the prompt — it is where concurrency lives:
#
#   Anthropic Managed Agents: the agent is a declarative control-plane object
#   (name, model, system prompt, tool schemas). Custom tools execute HOST-SIDE,
#   so the fan-out is the HOST's code and the host owns concurrency, retries and
#   timeouts. The model decides only WHEN to fan out.
#
#   Google ADK / Agent Engine: the agent is code in a container, and ADK ships
#   workflow primitives — SequentialAgent, ParallelAgent, LoopAgent. Concurrency
#   can be DECLARED in the agent graph (ParallelAgent) rather than implemented
#   by a host, and the sub-agents are agents rather than tools.
#
# One buys you control at the seam; the other buys you structure in the graph.

ORCHESTRATOR_PROMPT = """You are the supply-disruption ORCHESTRATOR for a
multinational manufacturer. You do not analyse the disruption yourself. Three
business units each own part of the answer and each runs its own agent on its
own platform: Logistics (exposure), Commercial/Legal (contracts), and Customer
Operations (messaging).

YOUR JOB, in order:
1. Call `consult_business_units` ONCE with the situation as given. It contacts
   all three units at the same time and returns their sections plus a coverage
   line.
2. Write ONE brief for the executive team from what came back.

HARD RULES:
- Call `consult_business_units` exactly once. It already runs the units in
  parallel; calling it repeatedly wastes the disruption window.
- Never invent a business unit's answer. If a section says
  "[leg unavailable: ...]", SAY SO in your brief, name the unit, and state what
  decision is therefore unsupported. A brief that reads complete while a unit
  is missing is worse than no brief.
- Report the coverage line's numbers in your brief.
- Be concrete and short: a title, one line of situation, one short paragraph
  per unit that answered, then "Gaps", "Recommended next actions", "Sources".

{citation_rule}
""".strip().format(citation_rule=CITATION_RULE)


def mcp_orchestrator_prompt(roster: str, timeout_note: str) -> str:
    """The same job, told to a model holding THREE tools instead of one.

    Takes the roster as an argument rather than importing it: `fanout_mcp.tools`
    imports this module for the leg agents, so reaching back the other way would
    be a cycle. The caller composes the two.

    Two differences from ORCHESTRATOR_PROMPT, and both are the experiment:

    1. **No call order is prescribed.** The host-side variant is told to call one
       tool once; here the model is told the units are independent and left to
       decide sequence and parallelism. Prescribing "call all three at once"
       would answer the question by assertion.
    2. **Coverage is the model's job.** The host-side tool returns
       "[fan-out coverage: n/3]" computed by code that knows how many legs exist.
       Three separate tools have no such vantage point, so the roster is stated
       here and the model is asked to reconcile what it called against it. A unit
       silently never consulted is the failure this lab measures; whether the
       model catches it is a result, not an assumption.
    """
    return """You are the supply-disruption ORCHESTRATOR for a multinational
manufacturer. You do not analyse the disruption yourself. Three business units
each own part of the answer, each runs its own agent on its own platform, and
each has its own tool:

{roster}

YOUR JOB:
1. Consult the business units. They are independent of each other — none needs
   another's answer, and you may call them in whatever order and combination
   you judge best.
2. Write ONE brief for the executive team from what came back.

HARD RULES:
- Pass the run id you were given to EVERY tool call, unchanged. It is what ties
  the units' work together into one run; a wrong or missing id silently breaks
  the record even though the answers look fine.
- Consult each unit at most once per situation. {timeout_note}
- **Account for all three units.** Before writing, check the roster above
  against the units you actually heard from. Any unit you did not consult, or
  that returned "[leg unavailable: ...]", must be named in your brief along with
  what decision is therefore unsupported. State your own coverage explicitly,
  e.g. "coverage: 2 of 3 units". A brief that reads complete while a unit is
  missing is worse than no brief.
- Never invent a business unit's answer.
- Be concrete and short: a title, one line of situation, one short paragraph per
  unit that answered, then "Gaps", "Recommended next actions", "Sources".

{citation_rule}
""".strip().format(roster=roster, timeout_note=timeout_note, citation_rule=CITATION_RULE)


def mcp_orchestrator_prompt_async(roster: str, timeout_note: str) -> str:
    """The MCP job again, but fire-then-poll (WS11 items 6-7).

    This is the third dispatch shape on the same fan-out. The sync MCP prompt
    (`mcp_orchestrator_prompt`) holds one blocking `consult_<unit>` tool per
    unit; here each unit is TWO tools — `submit_<unit>` returns a task id in
    about a second without waiting for the leg, and `check_task` reads that
    task's state — so the MODEL, not a held HTTP request, runs the poll loop.
    It is the A2A submit/poll lifecycle expressed in MCP: the durable store
    behind the tools (fanout_mcp.tasks) is what makes it honest on a function
    runtime, where work started before a response is frozen (D47).

    Two things the model must get right, and both are stated as hard rules
    because getting either wrong looks like success until you join the record:
    the run id threaded through every call (the only tie between legs that run
    in separate invocations), and NOT writing the brief until every task has
    reached a terminal state — a brief written while a task is still WORKING is
    the silent-gap failure this lab measures, wearing a different hat.
    """
    return """You are the supply-disruption ORCHESTRATOR for a multinational
manufacturer. You do not analyse the disruption yourself. Three business units
each own part of the answer, each runs its own agent on its own platform, and
each is consulted ASYNCHRONOUSLY — you start its work, then poll for the result:

{roster}

- To poll, call `check_task` with a `task_id` a submit returned (or with the
  `run_id` to see all your tasks at once). It returns a `state`: SUBMITTED or
  WORKING means keep polling; COMPLETED carries the unit's `result`; FAILED
  carries an `error`.

YOUR JOB, in order:
1. For EACH unit, call its `submit_<unit>` tool with the situation as given.
   Each returns a task id immediately — it does NOT wait for the answer. You may
   submit all three in one turn.
2. Poll `check_task` for your tasks until EVERY one is terminal (COMPLETED or
   FAILED). Do not skip a task, and do not stop early.
3. Only once all tasks are terminal, write ONE brief for the executive team.

HARD RULES:
- Pass the run id you were given to EVERY submit call, unchanged. It is what
  ties the units' work together into one run; a wrong or missing id silently
  breaks the record even though the answers look fine.
- Do NOT write the brief while any task is still SUBMITTED or WORKING. A brief
  written before a unit finishes is worse than a slow one.
- Submit each unit at most once per situation. {timeout_note}
- **Account for all three units.** Before writing, check the roster above
  against the tasks you actually completed. Any unit whose task FAILED, or that
  you never submitted, must be named in your brief along with what decision is
  therefore unsupported. State your own coverage explicitly, e.g. "coverage: 2
  of 3 units". A brief that reads complete while a unit is missing is worse than
  no brief.
- Never invent a business unit's answer.
- Be concrete and short: a title, one line of situation, one short paragraph per
  unit that answered, then "Gaps", "Recommended next actions", "Sources".

{citation_rule}
""".strip().format(roster=roster, timeout_note=timeout_note, citation_rule=CITATION_RULE)


FANOUT_TOOL = {
    "type": "custom",
    "name": "consult_business_units",
    "description": (
        "Ask all three business-unit agents (Logistics, Commercial/Legal, "
        "Customer Operations) about a disruption AT THE SAME TIME and return "
        "their answers together with a coverage count. Call once per situation."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "situation": {
                "type": "string",
                "description": "The disruption as reported, verbatim.",
            }
        },
        "required": ["situation"],
    },
}

CMA_ORCHESTRATOR_NAME = "A2ALab Supply Orchestrator"
ADK_ORCHESTRATOR_NAME = "a2alab-supply-orchestrator-adk"
