"""Unit tests for the Strands platform scaffold (WS5/Path C).

These cover the LAB's scaffold: adapter selection, the stub backend, and the
prompt contract. The real backend (STRANDS_BACKEND=strands-sdk) and its own
tests are Kiro's deliverable (plan/12-strands-kiro-handoff.md) — mirrors the
OpenAI/Codex split, where test_openai_platform.py stayed lab-side and
test_openai_agents_backend.py was Codex's.
"""

from __future__ import annotations

import pytest

from interop.models import AgentRequest
from platforms.strands.core import STRANDS_RESEARCH_SYSTEM_PROMPT, make_adapter


async def test_stub_backend_answers_deterministically():
    adapter = make_adapter("stub")
    assert adapter.name == "strands-researcher"
    resp = await adapter.handle(AgentRequest(message="What is the A2A protocol?", trace_id="t-1"))
    assert "strands-stub" in resp.text
    assert "What is the A2A protocol?" in resp.text
    assert resp.raw == {"backend": "stub"}
    assert resp.latency_ms is not None


async def test_stub_is_default_backend(monkeypatch):
    monkeypatch.delenv("STRANDS_BACKEND", raising=False)
    assert make_adapter().backend.backend_name == "stub"


async def test_strands_sdk_backend_can_be_selected(monkeypatch):
    # The real backend is Kiro's; before it lands, selecting it raises
    # ModuleNotFoundError on the lazy import (strands_backend.py absent).
    # Once delivered, this asserts the backend name. Skip cleanly until then.
    try:
        adapter = make_adapter("strands-sdk")
    except ModuleNotFoundError:
        pytest.skip("strands-sdk backend not yet delivered (Kiro, plan/12)")
    assert adapter.backend.backend_name == "strands-sdk"


def test_unknown_backend_rejected():
    with pytest.raises(ValueError, match="unknown STRANDS_BACKEND"):
        make_adapter("nope")


def test_prompt_mentions_delegation_tool():
    # The Path C collaboration contract: the agent must know to delegate
    # CRM questions through ask_agentforce and attribute the answer.
    assert "ask_agentforce" in STRANDS_RESEARCH_SYSTEM_PROMPT
    assert "Agentforce" in STRANDS_RESEARCH_SYSTEM_PROMPT


def test_prompt_names_bedrock_as_model_host():
    # WS5 isolates the FRAMEWORK variable at a constant model cloud: Strands
    # runs on Bedrock (IAM role, no new key). The prompt says so honestly.
    assert "Bedrock" in STRANDS_RESEARCH_SYSTEM_PROMPT
