"""AgentforceProxyAdapter twin routing (D25 on the shim channel): the shared
shim must route each calling platform to its paired twin — including when the
delegation context only survived as rider text (the D28 regression: the A2A
hop dropped metadata and every experiment collapsed onto the default twin).
"""

import pytest

from interop import delegation
from interop.models import AgentRequest, AgentResponse
from platforms.agentforce import proxy as proxy_mod
from platforms.agentforce.proxy import AgentforceProxyAdapter


class StubAgentforceClient:
    instances: list["StubAgentforceClient"] = []

    def __init__(self, agent_id="0XxDEFAULT"):
        self.agent_id = agent_id
        self.asked: list[AgentRequest] = []
        StubAgentforceClient.instances.append(self)

    @classmethod
    def from_env(cls):
        return cls()

    async def ask(self, req: AgentRequest) -> AgentResponse:
        self.asked.append(req)
        return AgentResponse(text=f"answered-by:{self.agent_id}")


@pytest.fixture
def adapter(monkeypatch):
    StubAgentforceClient.instances = []
    monkeypatch.setattr(proxy_mod, "AgentforceClient", StubAgentforceClient)
    monkeypatch.setenv("SF_ADK_AGENT_ID", "0XxADK")
    monkeypatch.setenv("SF_OPENAI_AGENT_ID", "0XxOPENAI")
    return AgentforceProxyAdapter(client=StubAgentforceClient(), session_reuse=True)


async def test_metadata_routes_to_platform_twin(adapter):
    resp = await adapter.handle(
        AgentRequest(
            message="q",
            metadata={"delegation": {"caller": "adk-gemini-agent", "platform": "adk", "depth": 1}},
        )
    )
    assert resp.text == "answered-by:0XxADK"


async def test_rider_text_routes_to_platform_twin(adapter):
    # No metadata at all — only the rider text names the caller platform.
    message, _ = delegation.delegate(
        "q", caller="adk-gemini-agent", platform="adk", inbound_depth=0
    )
    resp = await adapter.handle(AgentRequest(message=message))
    assert resp.text == "answered-by:0XxADK"


async def test_origin_request_uses_default_agent(adapter):
    resp = await adapter.handle(AgentRequest(message="q"))
    assert resp.text == "answered-by:0XxDEFAULT"


async def test_session_reuse_keys_by_platform(adapter):
    req = AgentRequest(
        message="q",
        metadata={"delegation": {"caller": "adk-gemini-agent", "platform": "adk", "depth": 1}},
    )
    await adapter.handle(req)
    assert req.session_id == "shim-shared-adk"
    # The twin client is cached — a second ask must not mint another one.
    twins_before = len(StubAgentforceClient.instances)
    await adapter.handle(
        AgentRequest(
            message="q2",
            metadata={"delegation": {"caller": "adk-gemini-agent", "platform": "adk", "depth": 1}},
        )
    )
    assert len(StubAgentforceClient.instances) == twins_before
    twin = next(c for c in StubAgentforceClient.instances if c.agent_id == "0XxADK")
    assert len(twin.asked) == 2
