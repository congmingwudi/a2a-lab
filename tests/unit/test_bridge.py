import pytest
from fastapi.testclient import TestClient

from bridge.app import create_bridge_app
from interop import delegation
from interop.clients.base import RemoteAgentClient
from interop.models import AgentRequest, AgentResponse
from interop.registry import Registry, Target


class FakeClient(RemoteAgentClient):
    protocol = "rest"

    def __init__(self):
        self.requests: list[AgentRequest] = []

    async def ask(self, req: AgentRequest) -> AgentResponse:
        self.requests.append(req)
        return AgentResponse(text=f"echo: {req.message}", session_id=req.session_id)


class FakeRegistry(Registry):
    def __init__(self):
        super().__init__(
            {
                "claude-rest": Target(
                    name="claude-rest", platform="claude", protocol="rest", status="via-bridge"
                )
            }
        )
        self.fake_client = FakeClient()
        self.client_for_calls = 0

    def client_for(self, name):
        self.client_for_calls += 1
        return self.fake_client


@pytest.fixture
def bridge():
    registry = FakeRegistry()
    app = create_bridge_app(registry)
    return TestClient(app), registry


def test_healthz(bridge):
    client, _ = bridge
    assert client.get("/healthz").json()["ok"] is True


def test_invoke_forwards_and_annotates(bridge, monkeypatch):
    monkeypatch.delenv("BRIDGE_TOKEN", raising=False)
    client, registry = bridge
    r = client.post(
        "/invoke/claude-rest",
        json={"message": "hi", "session_id": "s1"},
        headers={"x-trace-id": "trace-abc"},
    )
    assert r.status_code == 200
    data = r.json()
    # The forwarded message = original + the D27 delegation rider at depth 1.
    assert data["text"].startswith("echo: hi")
    assert delegation.MARKER in data["text"]
    assert data["bridge"]["target"] == "claude-rest"
    assert data["bridge"]["status"] == "via-bridge"
    req = registry.fake_client.requests[0]
    assert req.trace_id == "trace-abc"  # header propagated
    assert req.session_id == "s1"
    assert req.metadata["delegation"]["depth"] == 1


def test_delegation_guard_refuses_over_depth(bridge, monkeypatch):
    """A request that was already delegated (depth >= max) must not be
    forwarded — the bridge answers with the standard refusal instead of
    letting a circular chain form (D27)."""
    monkeypatch.delenv("BRIDGE_TOKEN", raising=False)
    client, registry = bridge
    r = client.post(
        "/invoke/claude-rest",
        json={
            "message": "loop attempt",
            "metadata": {"delegation": {"caller": "claude-sdk-agent", "depth": 1}},
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["delegation_refused"] is True
    assert "circular" in data["text"]
    assert registry.fake_client.requests == []  # nothing forwarded


def test_unknown_target_404(bridge, monkeypatch):
    monkeypatch.delenv("BRIDGE_TOKEN", raising=False)
    client, _ = bridge
    assert client.post("/invoke/nope", json={"message": "hi"}).status_code == 404


def test_client_cached_across_requests(bridge, monkeypatch):
    """One long-lived client per target — a per-request client would discard
    AgentforceClient's OAuth/session caches and orphan prod-org sessions."""
    monkeypatch.delenv("BRIDGE_TOKEN", raising=False)
    client, registry = bridge
    client.post("/invoke/claude-rest", json={"message": "one", "session_id": "s1"})
    client.post("/invoke/claude-rest", json={"message": "two", "session_id": "s1"})
    assert registry.client_for_calls == 1
    assert len(registry.fake_client.requests) == 2


def test_auth_enforced_when_token_set(bridge, monkeypatch):
    monkeypatch.setenv("BRIDGE_TOKEN", "sekrit")
    client, _ = bridge
    assert client.post("/invoke/claude-rest", json={"message": "hi"}).status_code == 401
    ok = client.post(
        "/invoke/claude-rest",
        json={"message": "hi"},
        headers={"x-bridge-token": "sekrit"},
    )
    assert ok.status_code == 200
