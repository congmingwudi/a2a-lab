"""FoundryClient (WS3): the platform-native Responses-surface client —
agent_reference call shape, previous_response_id session chaining, and
platform_ref carrying the response id."""

from types import SimpleNamespace

from interop.models import AgentRequest
from platforms.foundry.client import FoundryClient


class FakeResponses:
    def __init__(self):
        self.calls = []
        self.counter = 0

    def create(self, **kwargs):
        self.calls.append(kwargs)
        self.counter += 1
        return SimpleNamespace(id=f"resp_{self.counter}", output_text=f"answer {self.counter}")


def make_client():
    client = FoundryClient(target_name="foundry-rest")
    client._openai = SimpleNamespace(responses=FakeResponses())
    return client


async def test_agent_reference_and_platform_ref(isolated_traces):
    client = make_client()
    resp = await client.ask(AgentRequest(message="hi", trace_id="t-f1"))
    assert resp.text == "answer 1"
    assert resp.raw["response_id"] == "resp_1"
    call = client._openai.responses.calls[0]
    assert call["extra_body"]["agent_reference"]["name"] == "a2alab-foundry-researcher"
    assert "previous_response_id" not in call


async def test_session_chains_previous_response_id(isolated_traces):
    client = make_client()
    await client.ask(AgentRequest(message="turn 1", session_id="s1"))
    await client.ask(AgentRequest(message="turn 2", session_id="s1"))
    calls = client._openai.responses.calls
    assert "previous_response_id" not in calls[0]
    assert calls[1]["previous_response_id"] == "resp_1"
