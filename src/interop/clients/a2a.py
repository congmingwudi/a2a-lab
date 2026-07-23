"""A2A client: discovers the remote AgentCard, sends message/send, and reads
the completed Task's text artifact. contextId <-> session_id; trace_id rides
in message metadata."""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from a2a.client import ClientConfig, create_client
from a2a.types import Message, Part, Role, SendMessageRequest, TaskState
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
            from google.auth import default as google_default
            from google.auth.transport.requests import Request as AuthRequest

            credentials, _ = google_default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )

            class _AdcAuth(httpx.Auth):
                def auth_flow(self, request):
                    if not credentials.valid:
                        credentials.refresh(AuthRequest())
                    request.headers["Authorization"] = f"Bearer {credentials.token}"
                    yield request

            return _AdcAuth()
        if scheme == "azure-ad":
            import time as _time

            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
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
