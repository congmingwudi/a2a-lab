"""Unit tests for the OpenAI Agents SDK backend.

The SDK is faked here so default test runs never need OpenAI network access
or the optional dependency.
"""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest

from interop import delegation
from interop.models import AgentRequest, AgentResponse
from platforms.openai import agents_backend as backend_mod
from platforms.openai.agents_backend import AgentsSdkBackend
from platforms.openai.core import OPENAI_RESEARCH_SYSTEM_PROMPT


class FakeAgent:
    calls = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        FakeAgent.calls.append(kwargs)


class FakeRunner:
    calls = []
    result = SimpleNamespace(final_output="final answer", last_response_id="resp_123")

    @classmethod
    async def run(cls, agent, input, **kwargs):
        cls.calls.append({"agent": agent, "input": input, "kwargs": kwargs})
        return cls.result


class FakeModelSettings:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def fake_function_tool(**decorator_kwargs):
    def decorate(func):
        func.tool_kwargs = decorator_kwargs
        return func

    return decorate


@pytest.fixture
def fake_agents_module(monkeypatch):
    FakeAgent.calls = []
    FakeRunner.calls = []
    FakeRunner.result = SimpleNamespace(final_output="final answer", last_response_id="resp_123")
    fake = SimpleNamespace(
        Agent=FakeAgent,
        Runner=FakeRunner,
        ModelSettings=FakeModelSettings,
        function_tool=fake_function_tool,
    )
    monkeypatch.setitem(sys.modules, "agents", fake)
    return fake


async def test_answer_runs_sdk_agent_and_records_response_id(
    fake_agents_module, isolated_traces, tmp_path, monkeypatch
):
    monkeypatch.setenv("A2ALAB_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    req = AgentRequest(message="Summarize account Omega", session_id="s1", trace_id="trace-1")

    resp = await AgentsSdkBackend().answer(req)

    assert resp == AgentResponse(
        text="final answer",
        session_id="s1",
        latency_ms=resp.latency_ms,
        raw={"response_id": "resp_123", "model": "gpt-test"},
    )
    assert resp.latency_ms is not None
    assert FakeRunner.calls[0]["input"] == "Summarize account Omega"
    assert FakeRunner.calls[0]["kwargs"]["max_turns"] == 3
    agent_kwargs = FakeAgent.calls[0]
    assert agent_kwargs["instructions"] == OPENAI_RESEARCH_SYSTEM_PROMPT
    assert agent_kwargs["model"] == "gpt-test"
    settings = agent_kwargs["model_settings"].kwargs
    assert settings["max_tokens"] == 2000  # reasoning models spend output tokens on reasoning
    assert settings["verbosity"] == "low"
    # Default run-until-done behavior (no stop_on_first_tool): the model
    # gets a synthesis round after ask_agentforce so it can attribute the
    # CRM portion — the Path C collaboration contract.
    assert "tool_use_behavior" not in agent_kwargs
    assert agent_kwargs["tools"][0].tool_kwargs["name_override"] == "ask_agentforce"

    lines = [
        json.loads(line)
        for f in isolated_traces.glob("*.jsonl")
        for line in f.read_text().splitlines()
    ]
    assert lines[-1]["trace_id"] == "trace-1"
    assert lines[-1]["platform_ref"] == "resp_123"
    assert lines[-1]["response_payload_raw"] == {
        "text": "final answer",
        "response_id": "resp_123",
    }

    records = json.loads((tmp_path / "state" / "openai_responses.json").read_text())
    assert records == [
        {
            "response_id": "resp_123",
            "trace_id": "trace-1",
            "session_id": "s1",
            "ts": records[0]["ts"],
        }
    ]


async def test_answer_uses_raw_response_id_fallback(fake_agents_module):
    FakeRunner.result = SimpleNamespace(
        final_output="fallback id",
        last_response_id=None,
        raw_responses=[SimpleNamespace(response_id="resp_raw")],
    )

    resp = await AgentsSdkBackend(model="gpt-local").answer(AgentRequest(message="hi"))

    assert resp.text == "fallback id"
    assert resp.raw == {"response_id": "resp_raw", "model": "gpt-local"}


async def test_agentforce_tool_returns_failure_text(fake_agents_module, monkeypatch):
    class FailingClient:
        async def ask(self, req):
            raise RuntimeError(f"boom for {req.message}")

    monkeypatch.setattr(backend_mod, "_agentforce_client", FailingClient())
    tool = backend_mod._build_agentforce_tool()

    text = await tool("what is account Omega status?")

    assert "CRM lookup failed via Agentforce" in text
    assert "RuntimeError" in text


async def test_agentforce_tool_timeout_returns_failure_text(fake_agents_module, monkeypatch):
    class SlowClient:
        async def ask(self, req):
            await backend_mod.asyncio.sleep(1)
            return AgentResponse(text="too late")

    monkeypatch.setenv("OPENAI_AGENTFORCE_TOOL_TIMEOUT_S", "0.001")
    monkeypatch.setattr(backend_mod, "_agentforce_client", SlowClient())
    tool = backend_mod._build_agentforce_tool()

    text = await tool("what is account Omega status?")

    assert "CRM lookup failed via Agentforce" in text
    assert "TimeoutError" in text


async def test_agentforce_tool_uses_module_level_client(fake_agents_module, monkeypatch):
    calls = []

    class FakeClient:
        async def ask(self, req):
            calls.append(req)
            return AgentResponse(text="from crm")

    monkeypatch.setattr(backend_mod, "_agentforce_client", FakeClient())
    tool = backend_mod._build_agentforce_tool()

    assert await tool("what is account Omega status?") == "from crm"
    # The outbound question carries the D27 delegation rider at depth 1.
    assert len(calls) == 1
    assert calls[0].message.startswith("what is account Omega status?")
    assert delegation.MARKER in calls[0].message
    assert calls[0].metadata["delegation"]["depth"] == 1


@pytest.mark.live
async def test_live_openai_agents_sdk_answer(monkeypatch, tmp_path):
    from dotenv import load_dotenv

    load_dotenv()
    monkeypatch.setenv("A2ALAB_STATE_DIR", str(tmp_path / "state"))
    # The live marker is deselected by default; keep an explicit credential
    # gate for targeted runs in incomplete shells.
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is required for the live OpenAI Agents SDK test")

    resp = await AgentsSdkBackend().answer(
        AgentRequest(message="Give a one sentence answer: what is this lab testing?")
    )

    assert resp.text
    assert resp.raw and resp.raw["response_id"]
