"""A2A client: discovers the remote AgentCard, sends message/send, and reads
the completed Task's text artifact. contextId <-> session_id; trace_id rides
in message metadata.

Two shapes, both first-class (WS11):

- `ask()` — blocking. Send, wait, read the artifact. What every lab leg used
  before 2026-07-27 and what the matrix's latency numbers are measured with.
- `submit()` + `poll()` — the protocol's asynchronous half. `submit()` sets
  `configuration.return_immediately`, so the server responds as soon as the
  task exists rather than when the work is done, and `poll()` reads
  `tasks/get`. The point is not elegance: it takes the agent's runtime OFF the
  HTTP request, which is what dissolves API Gateway's 29s integration ceiling
  for the fan-out legs (D41) instead of working around it.
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from a2a.client import ClientConfig, create_client
from a2a.types import GetTaskRequest, Message, Part, Role, SendMessageRequest, TaskState
from a2a.utils import TransportProtocol

from interop.clients.base import RemoteAgentClient, auth_headers
from interop.models import AgentRequest, AgentResponse, new_trace_id
from interop.trace import Hop

# Same budget as the other protocol clients — the timeout chain must be
# uniform (Apex 110s -> bridge clients 45s -> agent self-cap 40s) or the
# matrix's cross-protocol timeout measurements aren't comparable.
DEFAULT_TIMEOUT = 45.0


def _texts_from_parts(parts) -> list[str]:
    return [p.text for p in parts if p.WhichOneof("content") == "text"]


# Terminal in the protocol's sense: no further events will arrive without a new
# request. INPUT_REQUIRED and AUTH_REQUIRED are NOT here — they are stalled, not
# finished, and a caller that treats them as failure loses the distinction the
# state machine exists to express.
TERMINAL_STATES = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
    }
)

# Stalled: the task is alive but needs something from outside before it moves.
INTERRUPTED_STATES = frozenset(
    {TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED}
)


@dataclass
class TaskHandle:
    """What `submit()` returns — enough to poll with, and nothing else. The
    absence of an answer here is the point: the work has not happened yet."""

    task_id: str
    context_id: str | None
    state: str
    trace_id: str
    submit_ms: int
    #: Set when the server answered with a finished task anyway — i.e. it
    #: ignored `return_immediately` and blocked. That is a per-platform finding
    #: (WS11), not an error: the lab records who implements the async half.
    answered_immediately: bool = False
    text: str = ""


@dataclass
class TaskSnapshot:
    """One `tasks/get` reading."""

    task_id: str
    state: str
    text: str
    done: bool
    interrupted: bool
    detail: str = ""

    @property
    def working(self) -> bool:
        return not self.done and not self.interrupted


class A2AClient(RemoteAgentClient):
    protocol = "a2a"

    def __init__(
        self,
        endpoint: str,
        *,
        auth: dict[str, Any] | None = None,
        target_name: str = "remote",
        source_name: str = "client",
        timeout: float = DEFAULT_TIMEOUT,
        card_path: str | None = None,
        transport: str | None = None,
    ):
        # endpoint is the agent's base URL; the card is discovered at
        # /.well-known/agent-card.json unless the platform serves it
        # elsewhere (options.card_path) or discovery is skipped entirely
        # with a pinned transport (options.transport, e.g. http_json —
        # Vertex AI Agent Engine's preview A2A serves messages fine but its
        # public card route 404s, so the card is built locally via
        # minimal_agent_card).
        self.endpoint = endpoint.rstrip("/")
        self.auth = auth or {}
        self.target_name = target_name
        self.source_name = source_name
        self.timeout = timeout
        self.card_path = card_path
        self.transport = transport

    def _httpx_auth(self) -> httpx.Auth | None:
        """Refreshing cloud-IAM bearer auth for platform endpoints with an
        IAM data plane — a static header would go stale when the token
        expires. Both hyperscaler A2A endpoints put their cloud identity
        layer ABOVE the protocol (the agent card doesn't negotiate it):
        auth: {scheme: google-adc} for Vertex AI Agent Engine,
        auth: {scheme: azure-ad} for Foundry's incoming A2A (Entra-only —
        key auth is not offered there)."""
        scheme = self.auth.get("scheme")
        if scheme == "google-adc":
            from google.auth.transport.requests import Request as AuthRequest

            from interop.cloud_auth import google_credentials

            # Not google.auth.default() directly: on AWS there IS no ambient
            # Google identity for it to find, and the fan-out MCP server is a
            # Lambda. cloud_auth federates when a workload identity pool is
            # configured and falls through to plain ADC when it is not, so the
            # laptop and the Agent Engine container behave exactly as before.
            credentials = google_credentials()

            class _AdcAuth(httpx.Auth):
                def auth_flow(self, request):
                    if not credentials.valid:
                        credentials.refresh(AuthRequest())
                    request.headers["Authorization"] = f"Bearer {credentials.token}"
                    yield request

            return _AdcAuth()
        if scheme == "azure-ad":
            import time as _time

            from interop.cloud_auth import azure_credential

            # Explicit service principal, never DefaultAzureCredential (D39):
            # the chain would find a developer's `az login` on the laptop and
            # nothing at all in the ADK container, so the local run would pass
            # while the hosted one failed — the exact failure that produced D39.
            credential = azure_credential()
            scope = self.auth.get("scope", "https://ai.azure.com/.default")
            state: dict[str, Any] = {"token": None, "expires": 0.0}

            class _EntraAuth(httpx.Auth):
                def auth_flow(self, request):
                    if _time.time() > state["expires"] - 120:
                        access = credential.get_token(scope)
                        state.update(token=access.token, expires=float(access.expires_on))
                    request.headers["Authorization"] = f"Bearer {state['token']}"
                    yield request

            return _EntraAuth()
        return None

    @asynccontextmanager
    async def _connected(self):
        """Card discovery + transport selection, shared by ask/submit/poll.

        A fresh httpx client per call, deliberately: `poll()` is a separate
        request that may happen minutes after `submit()`, possibly from another
        process entirely (the fan-out Lambda's whole point), so nothing may
        depend on a connection surviving between them."""
        adc_auth = self._httpx_auth()
        headers = {} if adc_auth else auth_headers(self.auth)
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers, auth=adc_auth) as hc:
            config = ClientConfig(streaming=False, httpx_client=hc)
            if self.transport:
                from a2a.client import minimal_agent_card

                transport = self.transport.upper().replace("-", "_")
                config.supported_protocol_bindings = [getattr(TransportProtocol, transport)]
                agent = minimal_agent_card(self.endpoint, [getattr(TransportProtocol, transport)])
            else:
                agent = self.endpoint
            client = await create_client(agent, config, relative_card_path=self.card_path)
            try:
                yield client
            finally:
                await client.close()

    async def submit(self, req: AgentRequest) -> TaskHandle:
        """Fire: hand the work over and return the task id without waiting.

        `configuration.return_immediately` is the protocol's own switch — the
        server's `DefaultRequestHandler` reads it as `blocking = not
        return_immediately` and, when non-blocking, returns after the first
        Task event while a tracked background task keeps consuming the rest.
        No change to `AdapterExecutor` was needed for this; the executor runs
        as its own producer task, so awaiting the adapter inline never blocked
        the response in the first place (WS11 corrected its own premise here)."""
        req.trace_id = req.trace_id or new_trace_id()

        message = Message(
            message_id=uuid.uuid4().hex,
            role=Role.ROLE_USER,
            parts=[Part(text=req.message)],
        )
        if req.session_id:
            message.context_id = req.session_id
        message.metadata.update({**(req.metadata or {}), "trace_id": req.trace_id})
        request = SendMessageRequest(message=message)
        request.configuration.return_immediately = True

        start = time.perf_counter()
        with Hop(
            req.trace_id,
            source=self.source_name,
            target=self.target_name,
            protocol="a2a",
            transport_detail=f"SendMessage(return_immediately) @ {self.endpoint}",
            request_payload={
                "message": req.message,
                "contextId": req.session_id,
                "metadata": req.metadata or {},
                "returnImmediately": True,
            },
        ) as hop:
            async with self._connected() as client:
                task = None
                async for chunk in client.send_message(request):
                    if chunk.HasField("task"):
                        task = chunk.task
                        break
                    if chunk.HasField("message"):
                        # A direct Message reply means the remote never made a
                        # task at all — there is nothing to poll.
                        texts = _texts_from_parts(chunk.message.parts)
                        raise RuntimeError(
                            f"A2A submit to {self.target_name} returned a message, not a task — "
                            f"this endpoint has no task lifecycle to poll ({' '.join(texts)[:120]})"
                        )

            if task is None:
                raise RuntimeError(f"A2A submit to {self.target_name} yielded no task")

            elapsed = int((time.perf_counter() - start) * 1000)
            state = TaskState.Name(task.status.state)
            texts: list[str] = []
            for artifact in task.artifacts:
                texts.extend(_texts_from_parts(artifact.parts))
            done = task.status.state in TERMINAL_STATES
            hop.response_payload = {
                "taskId": task.id,
                "contextId": task.context_id,
                "state": state,
                "answeredImmediately": done,
            }
            return TaskHandle(
                task_id=task.id,
                context_id=task.context_id or None,
                state=state,
                trace_id=req.trace_id,
                submit_ms=elapsed,
                answered_immediately=done,
                text="\n".join(texts),
            )

    async def poll(self, task_id: str, *, trace_id: str | None = None) -> TaskSnapshot:
        """Then-poll: one `tasks/get`. The caller decides the cadence — for the
        fan-out orchestrator that caller is the model, which is the point."""
        trace_id = trace_id or new_trace_id()
        with Hop(
            trace_id,
            source=self.source_name,
            target=self.target_name,
            protocol="a2a",
            transport_detail=f"GetTask @ {self.endpoint}",
            request_payload={"taskId": task_id},
        ) as hop:
            async with self._connected() as client:
                task = await client.get_task(GetTaskRequest(id=task_id))

            state = TaskState.Name(task.status.state)
            texts: list[str] = []
            for artifact in task.artifacts:
                texts.extend(_texts_from_parts(artifact.parts))
            detail = ""
            if task.status.state == TaskState.TASK_STATE_FAILED:
                detail = "\n".join(_texts_from_parts(task.status.message.parts)) or "task failed"
            hop.response_payload = {
                "taskId": task.id,
                "state": state,
                "artifacts": texts,
            }
            return TaskSnapshot(
                task_id=task.id,
                state=state,
                text="\n".join(texts),
                done=task.status.state in TERMINAL_STATES,
                interrupted=task.status.state in INTERRUPTED_STATES,
                detail=detail,
            )

    async def ask(self, req: AgentRequest) -> AgentResponse:
        req.trace_id = req.trace_id or new_trace_id()

        message = Message(
            message_id=uuid.uuid4().hex,
            role=Role.ROLE_USER,
            parts=[Part(text=req.message)],
        )
        if req.session_id:
            message.context_id = req.session_id
        # The full request metadata rides the message — dropping it here
        # severs metadata["delegation"], which the shim's twin routing and
        # the remote seams' depth checks read (D25/D27).
        message.metadata.update({**(req.metadata or {}), "trace_id": req.trace_id})
        request = SendMessageRequest(message=message)

        adc_auth = self._httpx_auth()
        headers = {} if adc_auth else auth_headers(self.auth)

        start = time.perf_counter()
        with Hop(
            req.trace_id,
            source=self.source_name,
            target=self.target_name,
            protocol="a2a",
            transport_detail=f"SendMessage @ {self.endpoint}",
            request_payload={
                "message": req.message,
                "contextId": req.session_id,
                "metadata": req.metadata or {},
            },
        ) as hop:
            async with httpx.AsyncClient(
                timeout=self.timeout, headers=headers, auth=adc_auth
            ) as hc:
                config = ClientConfig(streaming=False, httpx_client=hc)
                if self.transport:
                    from a2a.client import minimal_agent_card

                    transport = self.transport.upper().replace("-", "_")
                    config.supported_protocol_bindings = [getattr(TransportProtocol, transport)]
                    agent = minimal_agent_card(
                        self.endpoint, [getattr(TransportProtocol, transport)]
                    )
                else:
                    agent = self.endpoint
                client = await create_client(
                    agent,
                    config,
                    relative_card_path=self.card_path,
                )
                try:
                    task = None
                    direct_message = None
                    async for chunk in client.send_message(request):
                        if chunk.HasField("task"):
                            task = chunk.task
                        elif chunk.HasField("status_update"):
                            pass  # interim lifecycle event
                        elif chunk.HasField("message"):
                            direct_message = chunk.message
                finally:
                    await client.close()

            if task is not None:
                texts: list[str] = []
                for artifact in task.artifacts:
                    texts.extend(_texts_from_parts(artifact.parts))
                state = TaskState.Name(task.status.state)
                hop.response_payload = {
                    "taskId": task.id,
                    "contextId": task.context_id,
                    "state": state,
                    "artifacts": texts,
                }
                if task.status.state == TaskState.TASK_STATE_FAILED:
                    detail = (
                        "\n".join(_texts_from_parts(task.status.message.parts)) or "task failed"
                    )
                    raise RuntimeError(f"A2A task failed on {self.target_name}: {detail}")
                # A COMPLETED task carrying no text is not a success. Vertex AI
                # Agent Engine has been seen returning {"state": COMPLETED,
                # "artifacts": [""]}, which every layer above happily reported
                # as ok with an empty answer — the run looked green in the
                # console and said nothing. The protocol offers no way to tell
                # "answered with silence" from "answered nothing", so treat an
                # empty completed task as the failure it is, at the seam where
                # the raw payload is still in hand.
                if not any(t.strip() for t in texts):
                    raise RuntimeError(
                        f"A2A task completed with no answer text on {self.target_name} "
                        f"(state {state}, {len(task.artifacts)} artifact(s)) — the remote "
                        "agent returned an empty result"
                    )
                resp = AgentResponse(
                    text="\n".join(texts),
                    session_id=task.context_id or None,
                    raw={"task_id": task.id, "state": state},
                )
            elif direct_message is not None:
                texts = _texts_from_parts(direct_message.parts)
                hop.response_payload = {"message": texts}
                resp = AgentResponse(
                    text="\n".join(texts),
                    session_id=direct_message.context_id or None,
                )
            else:
                raise RuntimeError(f"A2A send_message to {self.target_name} yielded no result")

        resp.latency_ms = int((time.perf_counter() - start) * 1000)
        return resp
