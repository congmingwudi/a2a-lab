"""Unit tests for the LangGraph platform scaffold (WS4).

These cover the LAB's scaffold: adapter selection, the stub backend, and the
prompt contract. The real backend (LANGGRAPH_BACKEND=langgraph) needs the
`langgraph` extra + an LLM key and is exercised live, not here — mirrors the
Strands/OpenAI split.
"""

from __future__ import annotations

import pytest

from interop.models import AgentRequest
from platforms.langgraph.core import LANGGRAPH_RESEARCH_SYSTEM_PROMPT, make_adapter


async def test_stub_backend_answers_deterministically():
    adapter = make_adapter("stub")
    assert adapter.name == "langgraph-researcher"
    resp = await adapter.handle(AgentRequest(message="What is the A2A protocol?", trace_id="t-1"))
    assert "langgraph-stub" in resp.text
    assert "What is the A2A protocol?" in resp.text
    assert resp.raw == {"backend": "stub"}
    assert resp.latency_ms is not None


async def test_stub_is_default_backend(monkeypatch):
    monkeypatch.delenv("LANGGRAPH_BACKEND", raising=False)
    assert make_adapter().backend.backend_name == "stub"


async def test_langgraph_backend_can_be_selected(monkeypatch):
    # The real backend needs the `langgraph` extra; before it is installed,
    # selecting it raises ModuleNotFoundError on the lazy import. Once present,
    # this asserts the backend name. Skip cleanly until then.
    try:
        adapter = make_adapter("langgraph")
    except ModuleNotFoundError:
        pytest.skip("langgraph extra not installed (uv sync --all-extras)")
    assert adapter.backend.backend_name == "langgraph"


def test_unknown_backend_rejected():
    with pytest.raises(ValueError, match="unknown LANGGRAPH_BACKEND"):
        make_adapter("nope")


def test_prompt_mentions_delegation_tool():
    # The Path C collaboration contract: the agent must know to delegate CRM
    # questions through ask_agentforce and attribute the answer.
    assert "ask_agentforce" in LANGGRAPH_RESEARCH_SYSTEM_PROMPT
    assert "Agentforce" in LANGGRAPH_RESEARCH_SYSTEM_PROMPT


def test_prompt_names_langgraph_as_framework():
    # WS4 is the open-source-FRAMEWORK column: the prompt says so honestly.
    assert "LangGraph" in LANGGRAPH_RESEARCH_SYSTEM_PROMPT
