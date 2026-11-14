"""Agentforce Agent API client (the only GA inbound surface, verified July
2026).

Flow: OAuth client-credentials against the org's External Client App ->
POST /einstein/ai-agent/v1/agents/{id}/sessions ->
POST /sessions/{sid}/messages -> DELETE /sessions/{sid}.
Base URL is https://api.salesforce.com; each Agent API call has a 120s hard
timeout on Salesforce's side.

Sessions are created lazily and cached per lab session_id so multi-turn
conversations reuse one Agentforce session (and Einstein requests aren't
wasted on session churn — this is a real prod org).
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import time
import uuid
from typing import Any, Callable

import httpx

from interop.clients.base import RemoteAgentClient
from interop.memory import StaleMemoryGeneration, mask_owner
from interop.models import AgentRequest, AgentResponse, new_trace_id
from interop.trace import Hop

API_BASE = "https://api.salesforce.com/einstein/ai-agent/v1"
DEFAULT_TIMEOUT = 110.0  # under Salesforce's 120s hard cap


class MemoryCustomerChanged(RuntimeError):
    """A lab chat attempted to reuse an Agentforce session for another owner."""

    error_code = "memory_customer_changed"
    http_status = 409


class AgentforceClient(RemoteAgentClient):
    protocol = "agentforce-api"

    def __init__(
        self,
        *,
        my_domain: str,
        client_id: str,
        client_secret: str,
        agent_id: str,
        api_base: str = API_BASE,
        timeout: float = DEFAULT_TIMEOUT,
        source_name: str = "agentforce-client",
        session_variables: list[dict[str, Any]] | None = None,
        token_provider: Callable[[], dict[str, Any]] | None = None,
        bypass_user: bool = True,
        memory_customer_binding: bool = False,
    ):
        # The hop label for this client's Agent API calls — the hosted shim
        # passes its own name so the diagram shows the shim as a network
        # node (foundry -> shim -> agentforce), not a generic client.
        self.source_name = source_name
        self.my_domain = my_domain.rstrip("/")
        if not self.my_domain.startswith("https://"):
            self.my_domain = f"https://{self.my_domain}"
        self.client_id = client_id
        self.client_secret = client_secret
        self.agent_id = agent_id
        # Agent API session variables ({name, type, value}), sent at session
        # create: how a caller sets an agent's External variable, e.g. the memory
        # concierge's memory_owner (plan/18). None sends no `variables` key.
        self.session_variables = session_variables
        # An Employee agent runs as the token's user and refuses bypassUser, so
        # its caller passes a token for that user (a JWT-bearer grant returning
        # the OAuth token response) and bypass_user=False (D95). None keeps the
        # client-credentials grant, whose agent runs as its own agent user.
        self.token_provider = token_provider
        self.bypass_user = bypass_user
        self.memory_customer_binding = memory_customer_binding
        self.api_base = api_base.rstrip("/")
        self._http = httpx.AsyncClient(timeout=timeout)
        self._token: str | None = None
        self._token_expiry: float = 0.0
        # lab session_id -> (agentforce session id, sequence counter)
        self._sessions: dict[str, dict[str, Any]] = {}
        # One lock per lab session_id so concurrent asks on the same key create
        # exactly one Agentforce session, not one per racing coroutine (A11/F02).
        # dict.setdefault is atomic between awaits, so the lock itself is safe to
        # create lock-free.
        self._session_locks: dict[str, asyncio.Lock] = {}

    @classmethod
    def from_env(cls) -> "AgentforceClient":
        try:
            return cls(
                my_domain=os.environ["SF_MY_DOMAIN"],
                client_id=os.environ["SF_CLIENT_ID"],
                client_secret=os.environ["SF_CLIENT_SECRET"],
                agent_id=os.environ["SF_AGENT_ID"],
            )
        except KeyError as missing:
            raise RuntimeError(
                f"Agentforce is not configured: missing env var {missing}. "
                "See .env.example and plan/04-runbooks.md."
            ) from None

    @classmethod
    def from_target(cls, target) -> "AgentforceClient":
        auth = target.auth or {}
        if auth.get("my_domain"):
            missing = [
                key
                for key in ("my_domain", "client_id", "client_secret", "agent_id")
                if not str(auth.get(key) or "").strip()
            ]
            if missing:
                raise RuntimeError(
                    "Agentforce target is not configured: missing " + ", ".join(missing)
                )
            return cls(
                my_domain=auth["my_domain"],
                client_id=auth["client_id"],
                client_secret=auth["client_secret"],
                agent_id=auth["agent_id"],
                memory_customer_binding=bool(
                    (target.options or {}).get("memory_customer_binding")
                ),
            )
        return cls.from_env()

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        if self.token_provider:
            payload = await asyncio.to_thread(self.token_provider)
            self._token = payload["access_token"]
            self._token_expiry = time.time() + min(int(payload.get("expires_in", 1800)), 1800)
            return self._token
        r = await self._http.post(
            f"{self.my_domain}/services/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        r.raise_for_status()
        payload = r.json()
        self._token = payload["access_token"]
        # client-credentials tokens don't carry expires_in reliably; refresh
        # conservatively every 25 minutes.
        self._token_expiry = time.time() + 25 * 60
        return self._token

    async def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {await self._get_token()}",
            "content-type": "application/json",
        }

    async def start_session(
        self, trace_id: str, session_variables: list[dict[str, Any]] | None = None
    ) -> str:
        body = {
            "externalSessionKey": str(uuid.uuid4()),
            "instanceConfig": {"endpoint": self.my_domain},
            "streamingCapabilities": {"chunkTypes": ["Text"]},
            "bypassUser": self.bypass_user,
        }
        variables = session_variables if session_variables is not None else self.session_variables
        if variables:
            body["variables"] = variables
        with Hop(
            trace_id,
            source=self.source_name,
            target="agentforce",
            protocol="agentforce-api",
            transport_detail=f"POST /agents/{self.agent_id}/sessions",
            request_payload=body,
        ) as hop:
            r = await self._http.post(
                f"{self.api_base}/agents/{self.agent_id}/sessions",
                json=body,
                headers=await self._headers(),
            )
            hop.response_payload = r.text
            r.raise_for_status()
            sf_session_id = r.json()["sessionId"]
            hop.platform_ref = sf_session_id  # M11.1: join key to STDM session logs
            return sf_session_id

    async def ensure_session(
        self,
        lab_session_id: str,
        trace_id: str,
        *,
        session_variables: list[dict[str, Any]] | None = None,
        memory_binding: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        """Get-or-create the cached Agentforce session for a lab session_id.

        Serialized per key (double-checked inside the lock): two concurrent asks
        sharing one session_id — the norm now that the shim reuses a session per
        (platform, verified subject) — must create ONE Agentforce session, not
        one each. The previous check-then-create had an await between the check
        and the create, so both racers saw "absent" and each started a session
        (A11/F02)."""
        lock = self._session_locks.setdefault(lab_session_id, asyncio.Lock())
        async with lock:
            existing = self._sessions.get(lab_session_id)
            if existing and memory_binding is not None:
                old_owner, old_generation = existing.get("memory_binding", (None, None))
                owner, generation = memory_binding
                if old_owner != owner:
                    raise MemoryCustomerChanged(
                        "customer memory session is already bound to a different customer"
                    )
                if old_generation != generation:
                    self._sessions.pop(lab_session_id, None)
                    await self._delete_session(existing["id"], trace_id)
                    raise StaleMemoryGeneration(
                        "customer memory changed after this chat started; start a new chat"
                    )
            if existing is None:
                sf_session_id = (
                    await self.start_session(trace_id, session_variables)
                    if session_variables is not None
                    else await self.start_session(trace_id)
                )
                existing = {"id": sf_session_id, "seq": 0}
                if memory_binding is not None:
                    existing["memory_binding"] = memory_binding
                self._sessions[lab_session_id] = existing
            return self._sessions[lab_session_id]

    def _memory_binding(
        self, req: AgentRequest, trace_id: str
    ) -> tuple[tuple[str, str], list[dict[str, Any]], str]:
        """Resolve a verified customer key to immutable Agent API variables."""
        from interop.memory_adapter import owner_of

        metadata = req.metadata or {}
        customer_key = metadata.get("memory_customer")
        generation = metadata.get("memory_generation", "")
        owner = owner_of(req)
        if (
            owner is None
            or not isinstance(customer_key, str)
            or not customer_key
            or not isinstance(generation, str)
        ):
            raise RuntimeError(
                "customer memory binding requires a verified mapped customer and generation"
            )
        correlation = req.session_id or trace_id
        variables = [
            {"name": "memory_owner", "type": "Text", "value": owner},
            {"name": "memory_generation", "type": "Text", "value": generation},
            {"name": "memory_correlation", "type": "Text", "value": correlation},
        ]
        return (owner, generation), variables, customer_key

    async def ask(self, req: AgentRequest) -> AgentResponse:
        trace_id = req.trace_id or new_trace_id()
        start = time.perf_counter()
        memory_binding = None
        request_variables = None
        customer_key = None
        if self.memory_customer_binding:
            memory_binding, request_variables, customer_key = self._memory_binding(req, trace_id)
        # Session-less asks are one-shot: create -> message -> DELETE, so
        # each request leaves nothing running on the (production) org.
        oneshot = not req.session_id
        if oneshot:
            session: dict[str, Any] = {
                "id": await self.start_session(trace_id, request_variables),
                "seq": 0,
            }
        else:
            session = await self.ensure_session(
                req.session_id,
                trace_id,
                session_variables=request_variables,
                memory_binding=memory_binding,
            )
        try:
            session["seq"] += 1
            body = {
                "message": {
                    "sequenceId": session["seq"],
                    "type": "Text",
                    "text": req.message,
                }
            }
            with Hop(
                trace_id,
                source=self.source_name,
                target="agentforce",
                protocol="agentforce-api",
                transport_detail=f"POST /sessions/{session['id']}/messages",
                request_payload=body,
            ) as hop:
                hop.platform_ref = session["id"]  # M11.1: join key to STDM session logs
                r = await self._http.post(
                    f"{self.api_base}/sessions/{session['id']}/messages",
                    json=body,
                    headers=await self._headers(),
                )
                hop.response_payload = r.text
                r.raise_for_status()
                data = r.json()
        finally:
            if oneshot:
                try:
                    await self._delete_session(session["id"], trace_id)
                except Exception:
                    # The Hop already recorded the failed DELETE; don't let
                    # cleanup mask the answer (or the original error).
                    pass

        texts = [
            m.get("message", "")
            for m in data.get("messages", [])
            if m.get("type") in ("Inform", "TextChunk") and m.get("message")
        ]
        response = AgentResponse(
            text="\n".join(texts).strip(),
            session_id=req.session_id,
            latency_ms=int((time.perf_counter() - start) * 1000),
            raw=data,
            # WS23: the REAL Agent API sessionId (a UUIDv7, the same id the
            # Session Trace OTel API keys on — verified 2026-08-12 it equals
            # ssot__Id__c on the session DMO). session_id above stays the lab
            # id (our per-conversation cache key); this is the join key the
            # console's "view session trace" deep-link needs. Not the lab id,
            # which Salesforce never minted and the OTel endpoint 404s on.
            metadata={"agent_session_id": session["id"]},
        )
        if memory_binding is not None:
            owner, generation = memory_binding
            response = dataclasses.replace(
                response,
                metadata={
                    **(response.metadata or {}),
                    "memory": {
                        "agent_id": "A2ALab_Memory_Case",
                        "owner_id": mask_owner(owner, owner),
                        "customer_key": customer_key,
                        "generation": generation or None,
                        "profile_memory_suppressed": bool(generation),
                        # Agent API returns the final answer, not the Apex
                        # action outputs. A successful Agent API call is not
                        # evidence that memory read/wrote; correlation joins
                        # the authoritative bridge and tenant hops.
                        "binding_ok": True,
                        "evidence_pending": True,
                        "correlation_id": req.session_id or trace_id,
                        "agent_session_id": session["id"],
                    },
                },
            )
        return response

    async def _delete_session(self, sf_session_id: str, trace_id: str) -> None:
        with Hop(
            trace_id,
            source=self.source_name,
            target="agentforce",
            protocol="agentforce-api",
            transport_detail=f"DELETE /sessions/{sf_session_id}",
            request_payload=None,
        ) as hop:
            hop.platform_ref = sf_session_id
            headers = await self._headers()
            headers["x-session-end-reason"] = "UserRequest"
            r = await self._http.delete(
                f"{self.api_base}/sessions/{sf_session_id}", headers=headers
            )
            hop.response_payload = r.text
            r.raise_for_status()

    async def end_session(self, lab_session_id: str | None, trace_id: str | None = None) -> None:
        session = self._sessions.pop(lab_session_id, None) if lab_session_id else None
        if not session:
            return
        await self._delete_session(session["id"], trace_id or new_trace_id())

    async def aclose(self) -> None:
        # End every cached session before closing — cached sessions are live
        # objects on a real org, not just client state.
        for lab_session_id in list(self._sessions):
            try:
                await self.end_session(lab_session_id)
            except Exception:
                pass  # recorded by the Hop; closing must not raise
        await self._http.aclose()
