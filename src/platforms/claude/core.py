"""Claude platform: one AgentAdapter, two interchangeable backends.

- backend "managed"  (default): Anthropic Managed Agents (beta) — Anthropic
  hosts the agent loop and a per-session sandbox. The lab's primary Claude
  hosting per the project decision log (plan/00-decisions.md).
- backend "sdk": self-hosted Claude Agent SDK (claude-agent-sdk) — full
  control, lowest latency, and the variant that containerizes for Bedrock
  AgentCore. Fallback + timing comparison cell.

Both implement ClaudeBackend and sit behind the same adapter, so every
protocol server and matrix cell is backend-agnostic; switch with
CLAUDE_BACKEND=managed|sdk (or per-target options in config/targets.yaml).
"""

from __future__ import annotations

import os
from typing import Protocol

from interop.models import AgentRequest, AgentResponse

RESEARCH_SYSTEM_PROMPT = (
    "You are a research assistant participating in a cross-platform "
    "agent-to-agent interoperability lab. Another agent (often a Salesforce "
    "Agentforce agent talking to an end user) delegates open-ended research "
    "and summarization questions to you. Answer with a concise, "
    "well-organized summary: lead with the direct answer, then 2-4 "
    "supporting points. Keep answers under 250 words - your reply is folded "
    "into another agent's response. Do not ask clarifying questions; make "
    "reasonable assumptions and state them."
)


class ClaudeBackend(Protocol):
    backend_name: str

    async def answer(self, req: AgentRequest) -> AgentResponse: ...


class ClaudeAgentAdapter:
    name = "claude-researcher"

    def __init__(self, backend: ClaudeBackend):
        self.backend = backend
        self.description = (
            "Claude-powered research assistant (A2A interop lab). Delegates "
            f"open-ended research and summarization. Backend: {backend.backend_name}."
        )

    async def handle(self, req: AgentRequest) -> AgentResponse:
        return await self.backend.answer(req)


def make_adapter(backend_name: str | None = None) -> ClaudeAgentAdapter:
    backend_name = backend_name or os.environ.get("CLAUDE_BACKEND", "managed")
    if backend_name == "managed":
        from platforms.claude.managed_backend import ManagedBackend

        return ClaudeAgentAdapter(ManagedBackend())
    if backend_name == "sdk":
        from platforms.claude.sdk_backend import SdkBackend

        return ClaudeAgentAdapter(SdkBackend())
    raise ValueError(f"unknown CLAUDE_BACKEND '{backend_name}' (use 'managed' or 'sdk')")
