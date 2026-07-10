import json

import httpx
import pytest

from interop.models import AgentRequest
from platforms.agentforce.client import AgentforceClient


class FakeAgentAPI:
    """httpx.MockTransport handler simulating OAuth + the Agent API."""

    def __init__(self):
        self.token_calls = 0
        self.session_calls = 0
        self.messages: list[dict] = []
        self.deleted: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/services/oauth2/token"):
            self.token_calls += 1
            return httpx.Response(200, json={"access_token": f"tok-{self.token_calls}"})
        if path.endswith("/sessions") and "/agents/" in path:
            self.session_calls += 1
            assert request.headers["authorization"].startswith("Bearer tok-")
            return httpx.Response(200, json={"sessionId": f"sf-sess-{self.session_calls}"})
        if "/sessions/" in path and path.endswith("/messages"):
            body = json.loads(request.content)
            self.messages.append({"path": path, "body": body})
            return httpx.Response(
                200,
                json={
                    "messages": [
                        {"type": "Inform", "message": f"answer #{len(self.messages)}"}
                    ]
                },
            )
        if request.method == "DELETE" and "/sessions/" in path:
            self.deleted.append(path.rsplit("/", 1)[-1])
            return httpx.Response(204)
        return httpx.Response(404, text=f"unexpected {request.method} {path}")


@pytest.fixture
def fake_api():
    return FakeAgentAPI()


@pytest.fixture
def client(fake_api):
    c = AgentforceClient(
        my_domain="test.my.salesforce.com",
        client_id="cid",
        client_secret="csecret",
        agent_id="0XxTEST",
    )
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(fake_api.handler))
    return c


async def test_round_trip(client, fake_api):
    resp = await client.ask(AgentRequest(message="hello", trace_id="t1"))
    assert resp.text == "answer #1"
    assert resp.raw["messages"][0]["type"] == "Inform"
    assert fake_api.messages[0]["body"]["message"]["text"] == "hello"
    assert fake_api.messages[0]["body"]["message"]["sequenceId"] == 1


async def test_session_reuse_and_sequence(client, fake_api):
    await client.ask(AgentRequest(message="q1", session_id="lab-1"))
    await client.ask(AgentRequest(message="q2", session_id="lab-1"))
    assert fake_api.session_calls == 1  # reused
    assert fake_api.messages[1]["body"]["message"]["sequenceId"] == 2
    assert "sf-sess-1" in fake_api.messages[1]["path"]


async def test_oneshot_sessions_not_reused_and_deleted(client, fake_api):
    await client.ask(AgentRequest(message="q1"))
    await client.ask(AgentRequest(message="q2"))
    assert fake_api.session_calls == 2
    # One-shot sessions must not accumulate on the org: create -> message -> DELETE.
    assert fake_api.deleted == ["sf-sess-1", "sf-sess-2"]


async def test_token_cached(client, fake_api):
    await client.ask(AgentRequest(message="q1", session_id="s"))
    await client.ask(AgentRequest(message="q2", session_id="s"))
    assert fake_api.token_calls == 1


async def test_end_session(client, fake_api):
    await client.ask(AgentRequest(message="q1", session_id="lab-9"))
    await client.end_session("lab-9")
    assert fake_api.deleted == ["sf-sess-1"]


async def test_aclose_ends_cached_sessions(client, fake_api):
    await client.ask(AgentRequest(message="q1", session_id="lab-1"))
    await client.ask(AgentRequest(message="q2", session_id="lab-2"))
    await client.aclose()
    assert sorted(fake_api.deleted) == ["sf-sess-1", "sf-sess-2"]


async def test_traces_recorded(client, fake_api, isolated_traces):
    await client.ask(AgentRequest(message="q", trace_id="trace-x"))
    lines = [
        json.loads(line)
        for f in isolated_traces.glob("*.jsonl")
        for line in f.read_text().splitlines()
    ]
    protocols = {e["protocol"] for e in lines}
    assert protocols == {"agentforce-api"}
    assert all(e["trace_id"] == "trace-x" for e in lines)
    # oneshot: session create + message + delete = 3 hops
    assert len(lines) == 3


def test_from_env_missing(monkeypatch):
    for var in ("SF_MY_DOMAIN", "SF_CLIENT_ID", "SF_CLIENT_SECRET", "SF_AGENT_ID"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError, match="missing env var"):
        AgentforceClient.from_env()
