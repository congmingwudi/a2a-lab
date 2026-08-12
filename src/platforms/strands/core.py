"""Strands platform adapter — same two-seam shape as platforms/openai.

The adapter is backend-agnostic; switch with STRANDS_BACKEND=stub|strands-sdk
(or --backend on `python -m platforms.strands`). Path C direction: this agent
fields the question and delegates Salesforce-side knowledge to Agentforce via
an ask_agentforce tool (host-side credentials, same boundary as the OpenAI and
Claude agents — see strands_backend.py for the contract, which is Kiro's to
deliver: plan/12-strands-kiro-handoff.md).
"""

from __future__ import annotations

import os
from typing import Protocol

from interop.models import AgentRequest, AgentResponse

STRANDS_RESEARCH_SYSTEM_PROMPT = (
    "You are a research assistant participating in a cross-platform "
    "agent-to-agent interoperability lab, powered by AWS Strands Agents "
    "(model hosted on Amazon Bedrock). You field research and "
    "account-intelligence questions. When a question needs Salesforce-side "
    "knowledge (accounts, opportunities, cases, org data), delegate that "
    "part to the Agentforce agent via the ask_agentforce tool and attribute "
    "its contribution in your answer (e.g. 'From the CRM (via Agentforce): "
    "...'). When a request asks for a second opinion from the Google ADK / "
    "Gemini agent, consult it with your ask_google_adk tool (or "
    "ask_google_adk_bridge if the [A2A-LAB ROUTING] block selects the bridge "
    "route) and attribute its contribution the same way. Answer with a "
    "concise, well-organized summary: lead with the "
    "direct answer, then 2-4 supporting points. Keep answers under 250 "
    "words - your reply may be folded into another agent's response. Do not "
    "ask clarifying questions; make reasonable assumptions and state them."
)


class StrandsBackend(Protocol):
    backend_name: str

    async def answer(self, req: AgentRequest) -> AgentResponse: ...


class StrandsAgentAdapter:
    name = "strands-researcher"

    def __init__(self, backend: StrandsBackend):
        self.backend = backend
        self.description = (
            "AWS Strands-powered research assistant (A2A interop lab). Fields "
            "research questions and delegates CRM knowledge to Agentforce. "
            f"Backend: {backend.backend_name}."
        )

    async def handle(self, req: AgentRequest) -> AgentResponse:
        return await self.backend.answer(req)


def make_adapter(backend_name: str | None = None) -> StrandsAgentAdapter:
    backend_name = backend_name or os.environ.get("STRANDS_BACKEND", "stub")
    if backend_name == "stub":
        from platforms.strands.stub_backend import StubBackend

        return StrandsAgentAdapter(StubBackend())
    if backend_name == "strands-sdk":
        # Kiro's deliverable (plan/12-strands-kiro-handoff.md). Imported
        # lazily so the scaffold, loopback tests, and matrix all run on the
        # stub before the real backend (and its `strands` extra) exist.
        from platforms.strands.strands_backend import StrandsSdkBackend

        return StrandsAgentAdapter(StrandsSdkBackend())
    raise ValueError(f"unknown STRANDS_BACKEND '{backend_name}' (use 'stub' or 'strands-sdk')")
