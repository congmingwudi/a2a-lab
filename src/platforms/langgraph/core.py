"""LangGraph platform adapter — same two-seam shape as platforms/strands.

The adapter is backend-agnostic; switch with LANGGRAPH_BACKEND=stub|langgraph
(or --backend on `python -m platforms.langgraph`). Path C direction: this agent
fields the question and delegates Salesforce-side knowledge to Agentforce via
an ask_agentforce tool (host-side credentials, same boundary as the OpenAI,
Claude, and Strands agents — see langgraph_backend.py for the contract).
"""

from __future__ import annotations

import os
from typing import Protocol

from interop.models import AgentRequest, AgentResponse

LANGGRAPH_RESEARCH_SYSTEM_PROMPT = (
    "You are a research assistant participating in a cross-platform "
    "agent-to-agent interoperability lab, powered by LangGraph (an "
    "open-source agent framework). You field research and account-"
    "intelligence questions. When a question needs Salesforce-side knowledge "
    "(accounts, opportunities, cases, org data), delegate that part to the "
    "Agentforce agent via the ask_agentforce tool and attribute its "
    "contribution in your answer (e.g. 'From the CRM (via Agentforce): ...'). "
    "Answer with a concise, well-organized summary: lead with the direct "
    "answer, then 2-4 supporting points. Keep answers under 250 words - your "
    "reply may be folded into another agent's response. Do not ask clarifying "
    "questions; make reasonable assumptions and state them."
)


class LangGraphBackendProto(Protocol):
    backend_name: str

    async def answer(self, req: AgentRequest) -> AgentResponse: ...


class LangGraphAgentAdapter:
    name = "langgraph-researcher"

    def __init__(self, backend: LangGraphBackendProto):
        self.backend = backend
        self.description = (
            "LangGraph-powered research assistant (A2A interop lab). Fields "
            "research questions and delegates CRM knowledge to Agentforce. "
            f"Backend: {backend.backend_name}."
        )

    async def handle(self, req: AgentRequest) -> AgentResponse:
        return await self.backend.answer(req)


def make_adapter(backend_name: str | None = None) -> LangGraphAgentAdapter:
    backend_name = backend_name or os.environ.get("LANGGRAPH_BACKEND", "stub")
    if backend_name == "stub":
        from platforms.langgraph.stub_backend import StubBackend

        return LangGraphAgentAdapter(StubBackend())
    if backend_name == "langgraph":
        # Imported lazily so the scaffold, loopback tests, and matrix all run
        # on the stub before the real backend (and its `langgraph` extra) exist.
        from platforms.langgraph.langgraph_backend import LangGraphBackend

        return LangGraphAgentAdapter(LangGraphBackend())
    raise ValueError(f"unknown LANGGRAPH_BACKEND '{backend_name}' (use 'stub' or 'langgraph')")
