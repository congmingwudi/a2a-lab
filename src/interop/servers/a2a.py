"""A2A protocol server: publishes an AgentCard at /.well-known/agent-card.json
and serves the JSON-RPC binding at /, backed by an AgentExecutor that
delegates to the adapter.

Mapping rule (plan/01-architecture.md): A2A contextId <-> session_id;
trace_id rides in message metadata; the answer is a completed Task carrying
one text artifact.
"""

from __future__ import annotations


from fastapi import FastAPI
from google.protobuf.json_format import MessageToDict

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.utils import TransportProtocol

from interop.adapter import AgentAdapter
from interop.models import AgentRequest, new_trace_id
from interop.servers.wiretap import WireTapMiddleware


def build_agent_card(adapter: AgentAdapter, public_url: str) -> AgentCard:
    return AgentCard(
        name=adapter.name,
        description=adapter.description,
        version="0.1.0",
        supported_interfaces=[
            AgentInterface(
                url=public_url,
                protocol_binding=TransportProtocol.JSONRPC,
                protocol_version="1.0",
            )
        ],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="ask",
                name=f"Ask {adapter.name}",
                description=adapter.description,
                tags=["research", "a2a-interop-lab"],
            )
        ],
    )


class AdapterExecutor(AgentExecutor):
    def __init__(self, adapter: AgentAdapter):
        self.adapter = adapter

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        text = context.get_user_input()
        # MessageToDict, not dict(): nested values (metadata["delegation"])
        # must arrive as plain dicts, not protobuf Structs.
        metadata = MessageToDict(context.message.metadata) if context.message is not None else {}
        trace_id = str(metadata.get("trace_id") or new_trace_id())

        # The framework requires the initial Task object on the queue before
        # any status/artifact update events.
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
            req = AgentRequest(
                message=text,
                session_id=context.context_id or None,
                trace_id=trace_id,
                metadata=metadata,
            )
            resp = await self.adapter.handle(req)
        except Exception as exc:
            await updater.failed(
                updater.new_agent_message([Part(text=f"{type(exc).__name__}: {exc}")])
            )
            return
        await updater.add_artifact([Part(text=resp.text)], name="answer")
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel()


def create_a2a_app(
    adapter: AgentAdapter, public_url: str = "http://localhost/", wiretap: bool = True
):
    """wiretap=True everywhere since the WireTap buffer-and-replay rewrite
    (it previously hung under Mangum single-shot bodies); the flag remains
    for tests or hosts that want envelope capture off."""
    card = build_agent_card(adapter, public_url)
    handler = DefaultRequestHandler(
        agent_executor=AdapterExecutor(adapter),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    # The served card is the sdk 1.x JSON plus the 0.3-era top-level fields
    # (url / protocolVersion / preferredTransport): Microsoft Foundry's A2A
    # tool fails card discovery without them (observed 2026-07-22 —
    # "missing required properties" parsing a pure 1.x card), and they're
    # additive noise to 1.x clients. The A2A version spectrum, at the
    # discovery layer.
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    card_payload = agent_card_to_dict(card)
    card_payload.setdefault("url", public_url)
    card_payload.setdefault("protocolVersion", "0.3.0")
    card_payload.setdefault("preferredTransport", "JSONRPC")

    async def _compat_card(_request):
        return JSONResponse(card_payload)

    app = FastAPI(title=f"{adapter.name} (A2A)")
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=[
            Route(path="/.well-known/agent-card.json", endpoint=_compat_card, methods=["GET"])
        ],
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"),
    )
    # Bilingual server (see a2a_compat): 0.3-dialect clients (Foundry's A2A
    # tool) get translated in/out; 1.x traffic passes through. Sits inside
    # the WireTap so raw captures show the ORIGINAL dialect on the wire.
    from interop.servers.a2a_compat import A2A03CompatMiddleware

    app = A2A03CompatMiddleware(app)
    if not wiretap:
        return app
    service = getattr(adapter, "hop_label", None) or adapter.name
    return WireTapMiddleware(app, protocol="a2a", service=service)
