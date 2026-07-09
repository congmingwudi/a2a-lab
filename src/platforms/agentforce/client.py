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
    ):
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
            source="agentforce-client",
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
            return r.json()["sessionId"]

    async def _get_session(self, req: AgentRequest, trace_id: str) -> dict[str, Any]:
        key = req.session_id or "__oneshot__"
        if key not in self._sessions or key == "__oneshot__":
            sf_session_id = await self.start_session(trace_id)
            self._sessions[key] = {"id": sf_session_id, "seq": 0}
        return self._sessions[key]

    async def ask(self, req: AgentRequest) -> AgentResponse:
        trace_id = req.trace_id or new_trace_id()
        start = time.perf_counter()
        session = await self._get_session(req, trace_id)
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
            source="agentforce-client",
            target="agentforce",
            protocol="agentforce-api",
            transport_detail=f"POST /sessions/{session['id']}/messages",
            request_payload=body,
        ) as hop:
            r = await self._http.post(
                f"{self.api_base}/sessions/{session['id']}/messages",
                json=body,
                headers=await self._headers(),
            )
            hop.response_payload = r.text
            r.raise_for_status()
            data = r.json()

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

    async def end_session(self, lab_session_id: str | None, trace_id: str | None = None) -> None:
        key = lab_session_id or "__oneshot__"
        session = self._sessions.pop(key, None)
        if not session:
            return
        trace_id = trace_id or new_trace_id()
        with Hop(
            trace_id,
            source="agentforce-client",
            target="agentforce",
            protocol="agentforce-api",
            transport_detail=f"DELETE /sessions/{session['id']}",
            request_payload=None,
        ) as hop:
            headers = await self._headers()
            headers["x-session-end-reason"] = "UserRequest"
            r = await self._http.delete(
                f"{self.api_base}/sessions/{session['id']}", headers=headers
            )
            hop.response_payload = r.text

    async def aclose(self) -> None:
        await self._http.aclose()
