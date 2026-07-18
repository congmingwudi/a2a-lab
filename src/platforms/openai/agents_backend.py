"""OpenAI Agents SDK backend — THE CODEX DELIVERABLE.

Contract (full brief: plan/06-openai-codex-handoff.md):

- ``AgentsSdkBackend.answer(req: AgentRequest) -> AgentResponse`` runs one
  turn of an OpenAI Agents SDK agent using OPENAI_RESEARCH_SYSTEM_PROMPT
  (platforms/openai/core.py) and the model from OPENAI_MODEL (env).
- An ``ask_agentforce`` function tool delegates Salesforce-side questions
  through ``platforms.agentforce.client.AgentforceClient.from_env()`` —
  credentials stay host-side (mirror: platforms/claude/sdk_backend.py).
- Every model run is wrapped in an ``interop.trace.Hop`` whose
  ``platform_ref`` carries the OpenAI response/trace id at emit time, and
  ids are persisted to .a2alab/openai_responses.json — OpenAI has no
  read-back API for traces, so ids captured here are the ONLY join key the
  observability layer will ever have (ADR D18 / M9).
- Budget: OPENAI_ANSWER_TIMEOUT_S (default 40) — the Path A chain allows
  ~45s at the bridge.
"""

from __future__ import annotations

from interop.models import AgentRequest, AgentResponse


class AgentsSdkBackend:
    backend_name = "agents-sdk"

    async def answer(self, req: AgentRequest) -> AgentResponse:
        raise NotImplementedError(
            "The OpenAI Agents SDK backend is not implemented yet — "
            "see plan/06-openai-codex-handoff.md for the build brief. "
            "Run with OPENAI_BACKEND=stub in the meantime."
        )
