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


class _FakeFanout:
    """Stand-in for orchestration.FanOutResult — the bridge only reads these."""

    def __init__(self):
        self.results = [object(), object(), object()]
        self.ok_count = 3
        self.dispatch_summary = "async: exposure, commercial; async→sync: customer_comms"

    def render(self) -> str:
        return "rendered sections"


def test_fanout_reads_dispatch_mode_from_the_situation_and_strips_the_block(bridge, monkeypatch):
    """WS11: Agentforce cannot poll, so the async loop runs at the bridge. The
    mode rides the situation as an [A2A-LAB ROUTING] block (the Apex body has no
    field for it); the bridge must (1) dispatch with that mode and (2) strip the
    block so no routing directive leaks into a business unit's prompt."""
    monkeypatch.delenv("BRIDGE_TOKEN", raising=False)
    client, _ = bridge

    from interop import af_channel

    captured = {}

    async def fake_dispatch(task, **kwargs):
        captured["task"] = task
        captured.update(kwargs)
        return _FakeFanout()

    monkeypatch.setattr("orchestration.dispatch", fake_dispatch)

    situation = "A port strike halts traffic through Rotterdam."
    r = client.post(
        "/invoke/fanout:supplier-disruption",
        json={"message": situation + af_channel.dispatch_block("async")},
    )
    assert r.status_code == 200
    data = r.json()
    # (1) the async mode was threaded into dispatch...
    assert captured["dispatch_mode"] == "async"
    # ...and (2) the routing block never reached the legs.
    assert af_channel.ROUTING_MARKER not in captured["task"]
    assert captured["task"] == situation
    # The bridge reports what was requested and what actually happened per leg.
    assert data["bridge"]["dispatch_mode"] == "async"
    assert "async→sync" in data["bridge"]["dispatch"]
    assert data["bridge"]["coverage"] == "3/3"


def test_fanout_defaults_to_sync_when_no_block_is_present(bridge, monkeypatch):
    """A never-injected or stripped block degrades to the blocking path, never
    to an error."""
    monkeypatch.delenv("BRIDGE_TOKEN", raising=False)
    client, _ = bridge

    captured = {}

    async def fake_dispatch(task, **kwargs):
        captured.update(kwargs)
        return _FakeFanout()

    monkeypatch.setattr("orchestration.dispatch", fake_dispatch)

    r = client.post(
        "/invoke/fanout:supplier-disruption",
        json={"message": "A port strike halts traffic."},
    )
    assert r.status_code == 200
    assert captured["dispatch_mode"] == "sync"
    assert r.json()["bridge"]["dispatch_mode"] == "sync"


def test_invoke_submit_poll_for_async_target(monkeypatch):
    """WS4/D77 reverse Path A: a target flagged bridge_dispatch: submit_poll is
    driven fire-then-poll instead of a blocking ask() — the fix for a remote
    behind a hard router timeout (Heroku H12). Verifies submit+poll ran, ask()
    did NOT, and the wire payload reports the async dispatch + poll count."""
    from types import SimpleNamespace

    monkeypatch.delenv("BRIDGE_TOKEN", raising=False)
    monkeypatch.setenv("A2ALAB_ASYNC_POLL_INTERVAL_S", "0")  # no real sleeps

    class FakeAsyncClient(RemoteAgentClient):
        protocol = "a2a"

        def __init__(self):
            self.submits = 0
            self.polls = 0

        async def submit(self, req):
            self.submits += 1
            return SimpleNamespace(answered_immediately=False, task_id="t-1")

        async def poll(self, task_id, *, trace_id=None, expect_transient=False):
            self.polls += 1
            done = self.polls >= 2
            return SimpleNamespace(
                done=done,
                state="TASK_STATE_COMPLETED" if done else "TASK_STATE_WORKING",
                text="the async answer" if done else "",
                interrupted=False,
                detail=None,
            )

        async def ask(self, req):
            raise AssertionError("ask() must not be called on the submit_poll path")

        async def aclose(self):
            pass

    fake = FakeAsyncClient()

    class AsyncRegistry(Registry):
        def __init__(self):
            super().__init__(
                {
                    "langgraph-a2a": Target(
                        name="langgraph-a2a",
                        platform="langgraph",
                        protocol="a2a",
                        status="native",
                        options={"bridge_dispatch": "submit_poll"},
                    )
                }
            )

        def client_for(self, name):
            return fake

    client = TestClient(create_bridge_app(AsyncRegistry()))
    r = client.post("/invoke/langgraph-a2a", json={"message": "research Acme", "session_id": "s9"})
    assert r.status_code == 200
    data = r.json()
    assert data["text"] == "the async answer"
    assert data["bridge"]["dispatch_mode"] == "async"
    assert data["bridge"]["polls"] == 2
    assert fake.submits == 1
    assert fake.polls == 2


def test_invoke_submit_poll_flag_on_sync_client_degrades(monkeypatch):
    """The flag on a target whose client has no async half falls back to a
    blocking ask(), recorded honestly as sync — never an error."""
    monkeypatch.delenv("BRIDGE_TOKEN", raising=False)

    sync_client = FakeClient()

    class SyncFlaggedRegistry(Registry):
        def __init__(self):
            super().__init__(
                {
                    "rest-flagged": Target(
                        name="rest-flagged",
                        platform="x",
                        protocol="rest",
                        status="native",
                        options={"bridge_dispatch": "submit_poll"},
                    )
                }
            )

        def client_for(self, name):
            return sync_client

    client = TestClient(create_bridge_app(SyncFlaggedRegistry()))
    r = client.post("/invoke/rest-flagged", json={"message": "hi", "session_id": "s"})
    assert r.status_code == 200
    data = r.json()
    assert data["text"].startswith("echo: hi")  # blocking ask() answered it
    assert data["bridge"]["dispatch_mode"] == "sync"
    assert data["bridge"]["polls"] == 0
    assert len(sync_client.requests) == 1
