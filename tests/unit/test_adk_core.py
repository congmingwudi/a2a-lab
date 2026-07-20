"""The ADK researcher's search-source switch (ADK_REAL_SEARCH): synthetic
deterministic signals by default, live Google Search grounding opt-in —
prompt guidance and tool selection must move together."""

import pytest

from platforms.adk import core


def test_synthetic_default(monkeypatch):
    monkeypatch.delenv("ADK_REAL_SEARCH", raising=False)
    assert not core.real_search_enabled()
    assert "search_industry_news" in core.research_instruction()


def test_real_search_flag(monkeypatch):
    monkeypatch.setenv("ADK_REAL_SEARCH", "1")
    assert core.real_search_enabled()
    assert "google_search" in core.research_instruction()
    monkeypatch.setenv("ADK_REAL_SEARCH", "0")
    assert not core.real_search_enabled()


def test_synthetic_news_is_deterministic_and_labeled():
    a = core.search_industry_news("Omega, Inc.")
    assert a == core.search_industry_news("Omega, Inc.")
    assert "synthetic" in a.lower()


def test_agent_tools_follow_flag(monkeypatch):
    pytest.importorskip("google.adk")
    from platforms.adk.agent import build_llm_agent

    monkeypatch.delenv("ADK_REAL_SEARCH", raising=False)
    names = [getattr(t, "__name__", type(t).__name__) for t in build_llm_agent().tools]
    assert "search_industry_news" in names

    monkeypatch.setenv("ADK_REAL_SEARCH", "1")
    names = [getattr(t, "__name__", type(t).__name__) for t in build_llm_agent().tools]
    assert "GoogleSearchTool" in names and "search_industry_news" not in names
