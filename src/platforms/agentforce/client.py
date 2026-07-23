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

import os
import time
import uuid
from typing import Any

import httpx

from interop.clients.base import RemoteAgentClient
from interop.models import AgentRequest, AgentResponse, new_trace_id
from interop.trace import Hop

API_BASE = "https://api.salesforce.com/einstein/ai-agent/v1"
DEFAULT_TIMEOUT = 110.0  # under Salesforce's 120s hard cap


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
        self.api_base = api_base.rstrip("/")
        self._http = httpx.AsyncClient(timeout=timeout)
        self._token: str | None = None
        self._token_expiry: float = 0.0
        # lab session_id -> (agentforce session id, sequence counter)
        self._sessions: dict[str, dict[str, Any]] = {}

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
            return cls(
                my_domain=auth["my_domain"],
                client_id=auth["client_id"],
                client_secret=auth["client_secret"],
                agent_id=auth["agent_id"],
            )
        return cls.from_env()

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
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

    async def start_session(self, trace_id: str) -> str:
        body = {
            "externalSessionKey": str(uuid.uuid4()),
            "instanceConfig": {"endpoint": self.my_domain},
            "streamingCapabilities": {"chunkTypes": ["Text"]},
            "bypassUser": True,
        }
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

    async def ensure_session(self, lab_session_id: str, trace_id: str) -> dict[str, Any]:
        """Get-or-create the cached Agentforce session for a lab session_id."""
        if lab_session_id not in self._sessions:
            sf_session_id = await self.start_session(trace_id)
            self._sessions[lab_session_id] = {"id": sf_session_id, "seq": 0}
        return self._sessions[lab_session_id]

    async def ask(self, req: AgentRequest) -> AgentResponse:
        trace_id = req.trace_id or new_trace_id()
        start = time.perf_counter()
        # Session-less asks are one-shot: create -> message -> DELETE, so
        # each request leaves nothing running on the (production) org.
        oneshot = not req.session_id
        if oneshot:
            session: dict[str, Any] = {"id": await self.start_session(trace_id), "seq": 0}
        else:
            session = await self.ensure_session(req.session_id, trace_id)
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
        return AgentResponse(
            text="\n".join(texts).strip(),
            session_id=req.session_id,
            latency_ms=int((time.perf_counter() - start) * 1000),
            raw=data,
        )

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
