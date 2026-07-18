"""Unit tests for the OpenAI platform scaffold (M9/Path C)."""

from __future__ import annotations

import pytest

from interop.models import AgentRequest
from platforms.openai.core import OPENAI_RESEARCH_SYSTEM_PROMPT, make_adapter


async def test_stub_backend_answers_deterministically():
    adapter = make_adapter("stub")
    assert adapter.name == "openai-researcher"
    resp = await adapter.handle(AgentRequest(message="What is the A2A protocol?", trace_id="t-1"))
    assert "openai-stub" in resp.text
    assert "What is the A2A protocol?" in resp.text
    assert resp.raw == {"backend": "stub"}
    assert resp.latency_ms is not None


async def test_stub_is_default_backend(monkeypatch):
    monkeypatch.delenv("OPENAI_BACKEND", raising=False)
    assert make_adapter().backend.backend_name == "stub"


async def test_agents_sdk_backend_is_a_clear_todo():
    adapter = make_adapter("agents-sdk")
    with pytest.raises(NotImplementedError, match="codex-handoff"):
        await adapter.handle(AgentRequest(message="hi"))


def test_unknown_backend_rejected():
    with pytest.raises(ValueError, match="unknown OPENAI_BACKEND"):
        make_adapter("nope")


def test_prompt_mentions_delegation_tool():
    # The Path C collaboration contract: the agent must know to delegate
    # CRM questions through ask_agentforce and attribute the answer.
    assert "ask_agentforce" in OPENAI_RESEARCH_SYSTEM_PROMPT
    assert "Agentforce" in OPENAI_RESEARCH_SYSTEM_PROMPT
