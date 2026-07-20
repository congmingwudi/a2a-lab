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
    real_search_enabled,
    research_instruction,
    search_industry_news,
)

APP_NAME = "a2a-lab-adk"
USER_ID = "a2a-lab"


def build_llm_agent(inbound_depth: int = 0, trace_id: str | None = None):
    """One LlmAgent per request: the ask_agentforce tools are closed over
    the request's delegation depth (D27) and trace id (downstream hops join
    the caller's trace), so the agent object can't be shared."""
    from google.adk.agents import LlmAgent

    tools = [
        make_ask_agentforce(inbound_depth, trace_id),
        make_ask_agentforce_a2a(inbound_depth, trace_id),
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
        self, text: str, session_id: str, inbound_depth: int, trace_id: str | None
    ) -> str:
        from google.adk.runners import Runner
        from google.genai import types as genai_types

        runner = Runner(
            agent=build_llm_agent(inbound_depth, trace_id),
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
            answer = await self._run_adk(text, session_id, inbound_depth, trace_id)
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
