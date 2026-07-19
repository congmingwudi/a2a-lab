"""Deterministic stand-in for the OpenAI Agents SDK backend.

Keeps every protocol server, loopback test, matrix cell, and console flow
runnable before (and independently of) the real backend. No network, no
LLM — but it does record a trace hop so the wire view stays honest about
which backend produced an answer.
"""

from __future__ import annotations

import time

from interop.models import AgentRequest, AgentResponse, new_trace_id
from interop.trace import Hop


class StubBackend:
    backend_name = "stub"

    async def answer(self, req: AgentRequest) -> AgentResponse:
        start = time.perf_counter()
        trace_id = req.trace_id or new_trace_id()
        text = (
            "[openai-stub] Research summary (deterministic placeholder — "
            "set OPENAI_BACKEND=agents-sdk for the real agent):\n"
            f"Question received: {req.message[:300]}\n"
            "- This response was produced by the stub backend; no OpenAI "
            "call was made and no Agentforce delegation occurred."
        )
        with Hop(
            trace_id,
            source="openai-researcher",
            target="openai-stub",
            protocol="internal",
            transport_detail="stub answer",
            request_payload=req.to_dict(),
        ) as hop:
            hop.response_payload = {"text": text}
        return AgentResponse(
            text=text,
            session_id=req.session_id,
            latency_ms=int((time.perf_counter() - start) * 1000),
            raw={"backend": self.backend_name},
        )
