"""Unit tests for the Strands Agents SDK backend.

The SDK is faked here so default test runs never need AWS/Bedrock network
access or the optional strands dependency.
"""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest

from interop import delegation
from interop.models import AgentRequest, AgentResponse
from platforms.strands import strands_backend as backend_mod
from platforms.strands.strands_backend import StrandsSdkBackend
from platforms.strands.core import STRANDS_RESEARCH_SYSTEM_PROMPT


# --- Fakes for the strands SDK ---


class FakeAgentResult:
    """Fake AgentResult whose str() returns the text."""

    def __init__(self, text="strands answer", request_id="bedrock-req-123"):
        self._text = text
        self.metrics = SimpleNamespace(request_id=request_id) if request_id else None
        self.state = {}
        self.message = {"role": "assistant", "content": [{"text": text}]}

    def __str__(self):
        return self._text


class FakeBedrockModel:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeAgent:
    """Minimal fake of strands.Agent that captures constructor args."""

    calls = []
    _result = FakeAgentResult()

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        FakeAgent.calls.append(kwargs)

    async def invoke_async(self, prompt, **kwargs):
        return FakeAgent._result


def fake_tool(**decorator_kwargs):
    """Fake @tool decorator that just passes through the function."""

    def decorate(func):
        func.tool_kwargs = decorator_kwargs
        return func

    return decorate


@pytest.fixture(autouse=True)
def fake_strands_module(monkeypatch):
    """Inject a fake strands module so tests run without the strands extra."""
    FakeAgent.calls = []
    FakeAgent._result = FakeAgentResult()

    fake_strands = SimpleNamespace(Agent=FakeAgent, tool=fake_tool)
    fake_bedrock = SimpleNamespace(BedrockModel=FakeBedrockModel)
    monkeypatch.setitem(sys.modules, "strands", fake_strands)
    monkeypatch.setitem(sys.modules, "strands.models", SimpleNamespace(bedrock=fake_bedrock))
    monkeypatch.setitem(sys.modules, "strands.models.bedrock", fake_bedrock)
    return fake_strands


# --- Tests ---


async def test_answer_runs_agent_and_returns_response(
    fake_strands_module, isolated_traces, monkeypatch
):
    monkeypatch.setenv("STRANDS_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    req = AgentRequest(message="What is the A2A protocol?", session_id="s1", trace_id="trace-1")

    resp = await StrandsSdkBackend().answer(req)

    assert resp.text == "strands answer"
    assert resp.session_id == "s1"
    assert resp.latency_ms is not None
    assert resp.raw == {
        "request_id": "bedrock-req-123",
        "model": "us.anthropic.claude-sonnet-4-20250514-v1:0",
        "backend": "strands-sdk",
    }
    # Agent was configured correctly
    agent_kwargs = FakeAgent.calls[0]
    assert agent_kwargs["system_prompt"] == STRANDS_RESEARCH_SYSTEM_PROMPT
    assert agent_kwargs["callback_handler"] is None
    assert isinstance(agent_kwargs["model"], FakeBedrockModel)
    assert agent_kwargs["model"].kwargs["model_id"] == "us.anthropic.claude-sonnet-4-20250514-v1:0"
    assert agent_kwargs["model"].kwargs["region_name"] == "us-east-1"
    # Four tools: the two Agentforce channels (ask_agentforce / _a2a) and the
    # two cross-hyperscaler Google ADK routes (ask_google_adk native-direct /
    # ask_google_adk_bridge via the lab bridge — WS5 cross-hyperscaler cell).
    assert len(agent_kwargs["tools"]) == 4
    tool_names = {getattr(t, "tool_name", getattr(t, "__name__", "")) for t in agent_kwargs["tools"]}
    assert tool_names == {
        "ask_agentforce",
        "ask_agentforce_a2a",
        "ask_google_adk",
        "ask_google_adk_bridge",
    }


async def test_answer_records_hop_with_platform_ref(
    fake_strands_module, isolated_traces, monkeypatch
):
    monkeypatch.setenv("STRANDS_MODEL_ID", "test-model")
    req = AgentRequest(message="test", trace_id="trace-hop")

    await StrandsSdkBackend().answer(req)

    lines = [
        json.loads(line)
        for f in isolated_traces.glob("*.jsonl")
        for line in f.read_text().splitlines()
    ]
    hop = lines[-1]
    assert hop["trace_id"] == "trace-hop"
    assert hop["source"] == "strands-researcher"
    assert hop["target"] == "strands-platform"
    assert hop["protocol"] == "internal"
    assert hop["platform_ref"] == "bedrock-req-123"
    assert hop["response_payload_raw"] == {
        "text": "strands answer",
        "request_id": "bedrock-req-123",
    }


async def test_answer_generates_trace_id_when_absent(
    fake_strands_module, isolated_traces, monkeypatch
):
    monkeypatch.setenv("STRANDS_MODEL_ID", "test-model")
    req = AgentRequest(message="hi")

    await StrandsSdkBackend().answer(req)

    lines = [
        json.loads(line)
        for f in isolated_traces.glob("*.jsonl")
        for line in f.read_text().splitlines()
    ]
    assert lines[-1]["trace_id"]  # generated, not empty


async def test_answer_handles_no_request_id(fake_strands_module, isolated_traces, monkeypatch):
    monkeypatch.setenv("STRANDS_MODEL_ID", "test-model")
    FakeAgent._result = FakeAgentResult(text="no id answer", request_id=None)
    req = AgentRequest(message="hi", trace_id="trace-noid")

    resp = await StrandsSdkBackend().answer(req)

    assert resp.text == "no id answer"
    assert resp.raw["request_id"] is None


async def test_agentforce_tool_delegates_with_correct_trace_id(fake_strands_module, monkeypatch):
    calls = []

    class FakeClient:
        async def ask(self, req):
            calls.append(req)
            return AgentResponse(text="from crm")

    monkeypatch.setattr(backend_mod, "_agentforce_client", FakeClient())
    tool = backend_mod._build_agentforce_tool(trace_id="trace-direct")

    result = tool(question="what is account Omega status?")

    assert result == "from crm"
    assert len(calls) == 1
    assert calls[0].message.startswith("what is account Omega status?")
    assert delegation.MARKER in calls[0].message
    assert calls[0].metadata["delegation"]["depth"] == 1
    assert calls[0].metadata["delegation"]["caller"] == "strands-sdk-agent"
    assert calls[0].metadata["delegation"]["platform"] == "strands"
    assert calls[0].trace_id == "trace-direct"


async def test_agentforce_a2a_tool_forwards_trace_id(fake_strands_module, monkeypatch):
    from interop import af_channel

    calls = []

    async def fake_ask_via_shim(message, metadata, trace_id=None):
        calls.append((message, metadata, trace_id))
        return "from shim"

    monkeypatch.setattr(af_channel, "ask_via_shim", fake_ask_via_shim)
    tool = backend_mod._build_agentforce_a2a_tool(trace_id="trace-a2a")

    result = tool(question="what is account Omega status?")

    assert result == "from shim"
    assert len(calls) == 1
    message, metadata, trace_id = calls[0]
    assert message.startswith("what is account Omega status?")
    assert delegation.MARKER in message
    assert metadata["delegation"]["depth"] == 1
    assert metadata["delegation"]["platform"] == "strands"
    assert trace_id == "trace-a2a"


async def test_agentforce_tool_respects_delegation_guard(fake_strands_module, monkeypatch):
    monkeypatch.setenv("A2ALAB_MAX_DELEGATION_DEPTH", "1")
    tool = backend_mod._build_agentforce_tool(inbound_depth=1, trace_id="trace-guard")

    result = tool(question="should be refused")

    assert "delegation guard" in result
    assert "ask_agentforce" in result


async def test_agentforce_a2a_tool_respects_delegation_guard(fake_strands_module, monkeypatch):
    monkeypatch.setenv("A2ALAB_MAX_DELEGATION_DEPTH", "1")
    tool = backend_mod._build_agentforce_a2a_tool(inbound_depth=1, trace_id="trace-guard")

    result = tool(question="should be refused")

    assert "delegation guard" in result
    assert "ask_agentforce_a2a" in result


async def test_agentforce_tool_returns_failure_on_error(fake_strands_module, monkeypatch):
    class FailingClient:
        async def ask(self, req):
            raise RuntimeError("boom")

    monkeypatch.setattr(backend_mod, "_agentforce_client", FailingClient())
    tool = backend_mod._build_agentforce_tool(trace_id="trace-fail")

    result = tool(question="will fail")

    assert "CRM lookup failed via Agentforce" in result
    assert "RuntimeError" in result


async def test_agentforce_a2a_tool_returns_failure_on_error(fake_strands_module, monkeypatch):
    from interop import af_channel

    async def failing_shim(message, metadata, trace_id=None):
        raise RuntimeError("shim down")

    monkeypatch.setattr(af_channel, "ask_via_shim", failing_shim)
    tool = backend_mod._build_agentforce_a2a_tool(trace_id="trace-fail")

    result = tool(question="will fail")

    assert "A2A shim call failed" in result
    assert "RuntimeError" in result


async def test_answer_threads_trace_id_to_tool_builders(
    fake_strands_module, isolated_traces, monkeypatch
):
    """Both tool builders receive the effective trace id."""
    captured = []

    def fake_direct_builder(inbound_depth=0, trace_id=None, user_context=None, user_token=None):
        captured.append(("direct", inbound_depth, trace_id))
        return "direct-tool"

    def fake_a2a_builder(inbound_depth=0, trace_id=None, user_context=None, user_token=None):
        captured.append(("a2a", inbound_depth, trace_id))
        return "a2a-tool"

    monkeypatch.setattr(backend_mod, "_build_agentforce_tool", fake_direct_builder)
    monkeypatch.setattr(backend_mod, "_build_agentforce_a2a_tool", fake_a2a_builder)
    monkeypatch.setattr(backend_mod, "new_trace_id", lambda: "trace-generated")

    await StrandsSdkBackend(model_id="test-model").answer(AgentRequest(message="hi"))

    assert captured == [
        ("direct", 0, "trace-generated"),
        ("a2a", 0, "trace-generated"),
    ]


async def test_backend_name():
    assert StrandsSdkBackend.backend_name == "strands-sdk"


async def test_strands_paired_agent_id_override(fake_strands_module, monkeypatch):
    """SF_STRANDS_AGENT_ID overrides the shared agent_id on the client."""
    monkeypatch.setenv("SF_STRANDS_AGENT_ID", "strands-twin-id")
    # Reset the module-level client
    monkeypatch.setattr(backend_mod, "_agentforce_client", None)

    class FakeAgentforceClient:
        agent_id = "shared-default"

        @classmethod
        def from_env(cls):
            return cls()

        async def ask(self, req):
            return AgentResponse(text="paired")

    monkeypatch.setattr("platforms.agentforce.client.AgentforceClient", FakeAgentforceClient)
    client = backend_mod._get_agentforce_client()
    assert client.agent_id == "strands-twin-id"
    # Cleanup
    monkeypatch.setattr(backend_mod, "_agentforce_client", None)


@pytest.mark.live
async def test_live_strands_answer(monkeypatch, tmp_path):
    from dotenv import load_dotenv

    load_dotenv()
    if not os.environ.get("AWS_PROFILE") and not os.environ.get("AWS_ACCESS_KEY_ID"):
        pytest.skip("AWS credentials required for live Strands test")

    resp = await StrandsSdkBackend().answer(
        AgentRequest(message="In one sentence: what is the A2A protocol?")
    )

    assert resp.text
    assert resp.raw["backend"] == "strands-sdk"
    assert resp.raw["model"]
