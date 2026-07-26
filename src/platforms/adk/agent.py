"""The ADK agent + A2A executor deployed to Vertex AI Agent Engine (WS2).

Shape mirrors src/interop/servers/a2a.py's AdapterExecutor — same task
lifecycle (initial Task, start_work, one text artifact, complete) — but the
interior is a Google ADK ``LlmAgent`` run by the ADK ``Runner`` instead of a
lab adapter, and the whole thing is wrapped in the Agent Engine ``A2aAgent``
template, which serves the platform-native A2A endpoint (the lab's first
remote cell where the PLATFORM speaks A2A, no shim).

Everything here must be picklable by module reference: the deploy script
ships this package via ``extra_packages`` and Agent Engine cloudpickles the
``A2aAgent`` object (deploy/adk/deploy_adk.py).

Sessions: A2A contextId -> ADK session, via InMemorySessionService — session
continuity holds within a warm instance and resets on scale-to-zero. Honest
limitation for v1; VertexAiSessionService is the durable follow-up.

Delegation guard (D27): the executor computes the inbound delegation depth
from the incoming message (the bridge's rider survives the A2A hop as text)
and builds the per-request ``ask_agentforce`` tool closed over it.
"""

from __future__ import annotations

import uuid

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, Task, TaskState, TaskStatus
from google.protobuf.json_format import MessageToDict

from interop import delegation
from interop.models import AgentRequest
from platforms.adk.core import (
    adk_model,
    make_ask_agentforce,
    make_ask_agentforce_a2a,
    make_ask_foundry_agent,
    real_search_enabled,
    research_instruction,
    search_industry_news,
)

APP_NAME = "a2a-lab-adk"
USER_ID = "a2a-lab"


def build_llm_agent(
    inbound_depth: int = 0,
    trace_id: str | None = None,
    user_context: dict | None = None,
    user_token: str | None = None,
):
    """One LlmAgent per request: the ask_agentforce tools are closed over
    the request's delegation depth (D27) and trace id (downstream hops join
    the caller's trace), so the agent object can't be shared."""
    from google.adk.agents import LlmAgent

    tools = [
        make_ask_agentforce(inbound_depth, trace_id, user_context, user_token),
        make_ask_agentforce_a2a(inbound_depth, trace_id, user_context, user_token),
        make_ask_foundry_agent(inbound_depth, trace_id, user_context, user_token),
    ]
    if real_search_enabled():
        # Live grounding alongside function tools: the bypass flag is ADK's
        # sanctioned escape from the one-built-in-tool-per-agent API rule.
        from google.adk.tools.google_search_tool import GoogleSearchTool

        tools.append(GoogleSearchTool(bypass_multi_tools_limit=True))
    else:
        tools.append(search_industry_news)

    return LlmAgent(
        model=adk_model(),
        name="adk_researcher",
        description=(
            "Gemini-powered research assistant (A2A interop lab). Delegates "
            "open-ended research and summarization. Platform: Vertex AI Agent Engine."
        ),
        instruction=research_instruction(),
        tools=tools,
    )


class AdkResearchExecutor(AgentExecutor):
    """a2a-sdk executor driving the ADK Runner (same lifecycle as the lab's
    AdapterExecutor so wire captures look identical across platforms)."""

    def __init__(self):
        from google.adk.sessions import InMemorySessionService

        self._sessions = InMemorySessionService()
        # A2A contextId -> ADK session id (ids are service-generated)
        self._session_ids: dict[str, str] = {}

    async def _session_for(self, context_id: str | None) -> str:
        key = context_id or f"adhoc-{uuid.uuid4().hex}"
        if key not in self._session_ids:
            session = await self._sessions.create_session(app_name=APP_NAME, user_id=USER_ID)
            self._session_ids[key] = session.id
        return self._session_ids[key]

    async def _run_adk(
        self,
        text: str,
        session_id: str,
        inbound_depth: int,
        trace_id: str | None,
        user_context: dict | None = None,
        user_token: str | None = None,
    ) -> str:
        from google.adk.runners import Runner
        from google.genai import types as genai_types

        runner = Runner(
            agent=build_llm_agent(inbound_depth, trace_id, user_context, user_token),
            app_name=APP_NAME,
            session_service=self._sessions,
        )
        final = ""
        message = genai_types.Content(role="user", parts=[genai_types.Part(text=text)])
        async for event in runner.run_async(
            user_id=USER_ID, session_id=session_id, new_message=message
        ):
            if event.is_final_response() and event.content and event.content.parts:
                final = "".join(p.text or "" for p in event.content.parts)
        return final.strip()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        text = context.get_user_input()
        # MessageToDict, not dict(): nested values (metadata["delegation"])
        # must arrive as plain dicts, not protobuf Structs.
        metadata = MessageToDict(context.message.metadata) if context.message is not None else {}
        inbound_depth = delegation.depth_of(AgentRequest(message=text, metadata=metadata))
        trace_id = str(metadata.get("trace_id") or "") or None

        initial = Task(
            id=context.task_id,
            context_id=context.context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        )
        if context.message is not None:
            initial.history.append(context.message)
        await event_queue.enqueue_event(initial)

        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.start_work()
        try:
            session_id = await self._session_for(context.context_id)
            user_ctx, user_token = delegation.user_of(AgentRequest(message=text, metadata=metadata))
            answer = await self._run_adk(
                text, session_id, inbound_depth, trace_id, user_ctx, user_token
            )
        except Exception as exc:
            await updater.failed(
                updater.new_agent_message([Part(text=f"{type(exc).__name__}: {exc}")])
            )
            return
        await updater.add_artifact([Part(text=answer)], name="answer")
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel()


def build_a2a_agent():
    """The deployable Agent Engine object (called by deploy/adk/deploy_adk.py;
    also usable locally: build_a2a_agent().set_up())."""
    from a2a.types import AgentSkill
    from vertexai.agent_engines.templates.a2a import A2aAgent, create_agent_card

    card = create_agent_card(
        agent_name="ADK research agent",
        description=(
            "Gemini-powered research assistant (A2A interop lab) on Vertex AI "
            "Agent Engine — platform-native A2A."
        ),
        skills=[
            AgentSkill(
                id="ask",
                name="Ask the ADK research agent",
                description=(
                    "Open-ended research and summarization; can consult the "
                    "Salesforce Agentforce twin for CRM data."
                ),
                tags=["research", "a2a-interop-lab"],
            )
        ],
    )
    return A2aAgent(agent_card=card, agent_executor_builder=AdkResearchExecutor)


# ---- WS8 fan-out leg agent -------------------------------------------------
# A dedicated business-unit agent for the supplier-disruption fan-out, deployed
# as its OWN Agent Engine so Cloud Logging attributes this experiment
# separately from the WS2 researcher — that attribution is what the fan-out's
# join-rate measurement reads.


class AdkLegExecutor(AdkResearchExecutor):
    """Same A2A lifecycle as the researcher, different interior.

    A fan-out leg answers a scoped business question and STOPS: no
    ask_agentforce, no ask_foundry, no search. Giving a leg tools would let it
    wander off the scenario, and the determinism shaping is what makes runs
    comparable at all.
    """

    def _build_agent(self, *_args, **_kwargs):
        from google.adk.agents import LlmAgent

        from orchestration.agents import LOGISTICS

        return LlmAgent(
            model=adk_model(),
            name="adk_logistics",
            description=(
                "Logistics operations agent (A2A interop lab fan-out leg). "
                "Assesses shipment and route exposure in a supply disruption."
            ),
            instruction=LOGISTICS.instructions,
            tools=[],
        )

    async def _run_adk(
        self,
        text: str,
        session_id: str,
        inbound_depth: int,
        trace_id: str | None,
        user_context: dict | None = None,
        user_token: str | None = None,
    ) -> str:
        from google.adk.runners import Runner
        from google.genai import types as genai_types

        runner = Runner(
            agent=self._build_agent(),
            app_name=APP_NAME,
            session_service=self._sessions,
        )
        final = ""
        message = genai_types.Content(role="user", parts=[genai_types.Part(text=text)])
        async for event in runner.run_async(
            user_id=USER_ID, session_id=session_id, new_message=message
        ):
            if event.is_final_response() and event.content and event.content.parts:
                final = "".join(p.text or "" for p in event.content.parts)
        return final.strip()


def build_leg_a2a_agent():
    """The deployable Agent Engine object for the fan-out's logistics leg."""
    from a2a.types import AgentSkill
    from vertexai.agent_engines.templates.a2a import A2aAgent, create_agent_card

    card = create_agent_card(
        agent_name="A2A lab logistics agent",
        description=(
            "Logistics operations agent for the A2A lab's supplier-disruption "
            "fan-out, on Vertex AI Agent Engine — platform-native A2A."
        ),
        skills=[
            AgentSkill(
                id="assess-exposure",
                name="Assess disruption exposure",
                description=(
                    "Which shipments, orders, routes and plants a supply "
                    "disruption exposes, how badly, and over what horizon."
                ),
                tags=["logistics", "supply-chain", "a2a-interop-lab"],
            )
        ],
    )
    return A2aAgent(agent_card=card, agent_executor_builder=AdkLegExecutor)


# ---- WS8 fan-out orchestrator ----------------------------------------------
# The ADK counterpart to the Managed Agents orchestrator. Same scenario, same
# legs, same partial-failure contract — different place for the concurrency.
#
# On Managed Agents the fan-out is a CUSTOM tool the host executes, so the host
# owns parallelism (orchestration/cma.py). ADK ships workflow primitives, so
# the fan-out can instead be DECLARED in the agent graph: a ParallelAgent whose
# sub-agents are the three business units, wrapped by a SequentialAgent that
# synthesises afterwards. Nothing calls back to the lab host at all.
#
# The legs still have to reach other clouds, so each sub-agent gets a function
# tool that performs its own A2A call. What ParallelAgent buys is the
# scheduling: the three sub-agents run concurrently because the graph says so,
# not because a host wrote asyncio.gather.


def _leg_tool(leg):
    """A function tool that calls one business unit's agent over A2A."""

    async def consult(situation: str) -> str:
        import asyncio

        from interop import delegation
        from interop.models import AgentRequest
        from interop.registry import Registry

        client = None
        try:
            # Construction is INSIDE the try on purpose: building a client can
            # itself fail — openai-agentcore needs AWS credentials, which a GCP
            # container does not have — and an exception escaping here kills the
            # whole ParallelAgent branch instead of degrading to one dead leg.
            registry = Registry.load()
            client = registry.client_for(leg.target)
            message, meta = delegation.delegate(
                leg.prompt(situation),
                caller="a2alab-supply-orchestrator-adk",
                platform="adk",
                inbound_depth=0,
            )
            resp = await asyncio.wait_for(
                client.ask(AgentRequest(message=message, metadata=meta)), timeout=120
            )
            text = (resp.text or "").strip()
            return text or f"[leg unavailable: {leg.platform} — empty answer]"
        except Exception as exc:  # a dead leg is data, not a crash
            return f"[leg unavailable: {leg.platform} — {type(exc).__name__}: {exc}]"
        finally:
            if client is not None:
                await client.aclose()

    consult.__name__ = f"consult_{leg.role}"
    consult.__doc__ = (
        f"Ask the {leg.business_unit} business unit about the situation. "
        "Pass the situation verbatim."
    )
    return consult


def build_fanout_orchestrator():
    """SequentialAgent[ ParallelAgent[3 units] -> synthesiser ]."""
    from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent

    from orchestration.legs import legs_for

    unit_agents = []
    for leg in legs_for():
        unit_agents.append(
            LlmAgent(
                model=adk_model(),
                name=f"unit_{leg.role}",
                description=f"{leg.business_unit} business unit agent.",
                instruction=(
                    f"You represent {leg.business_unit}. Call your "
                    f"consult_{leg.role} tool ONCE with the situation exactly as "
                    "given, then return its result verbatim. Add nothing. If it "
                    "returns a '[leg unavailable: ...]' marker, return that marker "
                    "unchanged — never replace it with your own guess."
                ),
                tools=[_leg_tool(leg)],
                output_key=f"unit_{leg.role}",
            )
        )

    fan_out = ParallelAgent(
        name="consult_business_units",
        sub_agents=unit_agents,
        description="Consults all three business units concurrently.",
    )

    synthesiser = LlmAgent(
        model=adk_model(),
        name="synthesise_brief",
        description="Writes the executive brief from the units' answers.",
        instruction=(
            "Write ONE executive brief from the three business-unit answers in "
            "state: {unit_exposure}, {unit_commercial}, {unit_customer_comms}.\n\n"
            "HARD RULES: never invent a unit's answer. Any section containing a "
            "'[leg unavailable: ...]' marker MUST be reported as a gap — name the "
            "unit and say what decision is therefore unsupported. State how many "
            "of the three units answered. A brief that reads complete while a "
            "unit is missing is worse than no brief.\n\n"
            "Structure: title, one line of situation, one short paragraph per "
            "unit that answered, then 'Gaps' and 'Recommended next actions'."
        ),
    )

    return SequentialAgent(
        name="supply_disruption_orchestrator",
        sub_agents=[fan_out, synthesiser],
        description=(
            "Supply-disruption orchestrator: consults three business-unit agents "
            "in parallel, then writes one brief."
        ),
    )


def build_orchestrator_a2a_agent():
    """The deployable Agent Engine object for the ADK orchestrator variant."""
    from a2a.types import AgentSkill
    from vertexai.agent_engines.templates.a2a import A2aAgent, create_agent_card

    card = create_agent_card(
        agent_name="A2A lab supply orchestrator (ADK)",
        description=(
            "Supply-disruption fan-out orchestrator on Vertex AI Agent Engine — "
            "concurrency declared with ADK's ParallelAgent."
        ),
        skills=[
            AgentSkill(
                id="orchestrate-disruption",
                name="Orchestrate a supply disruption response",
                description=(
                    "Consults Logistics, Commercial/Legal and Customer Operations "
                    "concurrently, then writes one executive brief."
                ),
                tags=["orchestration", "fan-out", "a2a-interop-lab"],
            )
        ],
    )

    class _OrchestratorExecutor(AdkLegExecutor):
        def _build_agent(self, *_a, **_k):
            return build_fanout_orchestrator()

    return A2aAgent(agent_card=card, agent_executor_builder=_OrchestratorExecutor)
