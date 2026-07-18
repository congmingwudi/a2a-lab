"""OpenAI platform adapter — same two-seam shape as platforms/claude.

The adapter is backend-agnostic; switch with OPENAI_BACKEND=stub|agents-sdk
(or --backend on `python -m platforms.openai`). Path C direction: this
agent fields the question and delegates Salesforce-side knowledge to
Agentforce via an ask_agentforce tool (host-side credentials, same boundary
as Claude's Path B — see agents_backend.py for the contract).
"""

from __future__ import annotations

import os
from typing import Protocol

from interop.models import AgentRequest, AgentResponse

OPENAI_RESEARCH_SYSTEM_PROMPT = (
    "You are a research assistant participating in a cross-platform "
    "agent-to-agent interoperability lab, powered by the OpenAI platform. "
    "You field research and account-intelligence questions. When a question "
    "needs Salesforce-side knowledge (accounts, opportunities, cases, org "
    "data), delegate that part to the Agentforce agent via the "
    "ask_agentforce tool and attribute its contribution in your answer "
    "(e.g. 'From the CRM (via Agentforce): ...'). Answer with a concise, "
    "well-organized summary: lead with the direct answer, then 2-4 "
    "supporting points. Keep answers under 250 words - your reply may be "
    "folded into another agent's response. Do not ask clarifying "
    "questions; make reasonable assumptions and state them."
)


class OpenAIBackend(Protocol):
    backend_name: str

    async def answer(self, req: AgentRequest) -> AgentResponse: ...


class OpenAIAgentAdapter:
    name = "openai-researcher"

    def __init__(self, backend: OpenAIBackend):
        self.backend = backend
        self.description = (
            "OpenAI-powered research assistant (A2A interop lab). Fields "
            "research questions and delegates CRM knowledge to Agentforce. "
            f"Backend: {backend.backend_name}."
        )

    async def handle(self, req: AgentRequest) -> AgentResponse:
        return await self.backend.answer(req)


def make_adapter(backend_name: str | None = None) -> OpenAIAgentAdapter:
    backend_name = backend_name or os.environ.get("OPENAI_BACKEND", "stub")
    if backend_name == "stub":
        from platforms.openai.stub_backend import StubBackend

        return OpenAIAgentAdapter(StubBackend())
    if backend_name == "agents-sdk":
        from platforms.openai.agents_backend import AgentsSdkBackend

        return OpenAIAgentAdapter(AgentsSdkBackend())
    raise ValueError(f"unknown OPENAI_BACKEND '{backend_name}' (use 'stub' or 'agents-sdk')")
