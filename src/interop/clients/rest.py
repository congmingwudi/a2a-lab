"""REST client: POST /invoke with AgentRequest JSON."""

from __future__ import annotations

import time
from typing import Any

import httpx

from interop.clients.base import RemoteAgentClient, auth_headers
from interop.models import AgentRequest, AgentResponse, new_trace_id
from interop.trace import Hop

DEFAULT_TIMEOUT = 45.0


class RestClient(RemoteAgentClient):
    protocol = "rest"

    def __init__(
        self,
        endpoint: str,
        *,
        auth: dict[str, Any] | None = None,
        target_name: str = "remote",
        source_name: str = "client",
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.auth = auth or {}
        self.target_name = target_name
        self.source_name = source_name
        self._client = httpx.AsyncClient(timeout=timeout)

    def _headers(self, trace_id: str) -> dict[str, str]:
        return {"x-trace-id": trace_id, **auth_headers(self.auth)}

    async def ask(self, req: AgentRequest) -> AgentResponse:
        req.trace_id = req.trace_id or new_trace_id()
        url = f"{self.endpoint}/invoke"
        body = req.to_dict()
        start = time.perf_counter()
        with Hop(
            req.trace_id,
            source=self.source_name,
            target=self.target_name,
            protocol="rest",
            transport_detail=f"POST {url}",
            request_payload=body,
        ) as hop:
            r = await self._client.post(url, json=body, headers=self._headers(req.trace_id))
            hop.response_payload = r.text
            if r.status_code >= 400:
                # Surface the server's error body (our REST server sends a
                # structured {"error": ...}) instead of a bare status line —
                # this message is what the console shows the operator.
                detail = r.text[:400] if r.text else "(empty body)"
                raise RuntimeError(
                    f"HTTP {r.status_code} from {url} — server said: {detail}"
                )
            data = r.json()
        resp = AgentResponse.from_dict(data)
        resp.latency_ms = int((time.perf_counter() - start) * 1000)
        return resp

    async def aclose(self) -> None:
        await self._client.aclose()
