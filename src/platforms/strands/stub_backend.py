"""Deterministic stand-in for the Strands Agents SDK backend.

Keeps every protocol server, loopback test, matrix cell, and console flow
runnable before (and independently of) the real backend — which is Kiro's
deliverable (plan/12-strands-kiro-handoff.md). No network, no LLM — but it
records a trace hop so the wire view stays honest about which backend
produced an answer. Mirrors platforms/openai/stub_backend.py exactly.
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
            "[strands-stub] Research summary (deterministic placeholder — "
            "set STRANDS_BACKEND=strands-sdk for the real agent):\n"
            f"Question received: {req.message[:300]}\n"
            "- This response was produced by the stub backend; no Bedrock "
            "call was made and no Agentforce delegation occurred."
        )
        with Hop(
            trace_id,
            source="strands-researcher",
            target="strands-stub",
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
