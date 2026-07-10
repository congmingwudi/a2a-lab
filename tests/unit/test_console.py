import importlib
import json

from fastapi.testclient import TestClient

from interop.clients.base import RemoteAgentClient
from interop.models import AgentRequest, AgentResponse
from interop.registry import Registry, Target


def make_app(trace_dir, monkeypatch, registry=None):
    monkeypatch.setenv("A2ALAB_TRACE_DIR", str(trace_dir))
    import console.app as console_app

    importlib.reload(console_app)
    return console_app.create_console_app(registry)


class FakeClient(RemoteAgentClient):
    protocol = "rest"

    def __init__(self):
        self.requests: list[AgentRequest] = []

    async def ask(self, req: AgentRequest) -> AgentResponse:
        if req.message == "boom":
            raise RuntimeError("kaboom")
        self.requests.append(req)
        return AgentResponse(text=f"echo: {req.message}", session_id=req.session_id, latency_ms=7)


class FakeRegistry(Registry):
    def __init__(self):
        super().__init__(
            {
                "claude-rest": Target(
                    name="claude-rest", platform="claude", protocol="rest", status="native"
                )
            }
        )
        self.fake_client = FakeClient()

    def client_for(self, name):
        return self.fake_client


def test_traces_grouped_and_sorted(tmp_path, monkeypatch):
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    events = [
        {"trace_id": "t1", "ts": 100.0, "hop_seq": 0, "protocol": "rest"},
        {"trace_id": "t2", "ts": 200.0, "hop_seq": 0, "protocol": "mcp"},
        {"trace_id": "t1", "ts": 101.0, "hop_seq": 1, "protocol": "a2a"},
    ]
    (trace_dir / "2026-07-09.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n"
    )
    app = make_app(trace_dir, monkeypatch)
    client = TestClient(app)
    data = client.get("/api/traces").json()["traces"]
    assert [t["trace_id"] for t in data] == ["t2", "t1"]  # newest first
    t1 = data[1]
    assert len(t1["hops"]) == 2
    assert t1["protocols"] == ["a2a", "rest"]
    assert [h["hop_seq"] for h in t1["hops"]] == [0, 1]


def test_index_served(tmp_path, monkeypatch):
    app = make_app(tmp_path / "traces", monkeypatch)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "A2A Interop Lab" in r.text


def test_targets_listed(tmp_path, monkeypatch):
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    client = TestClient(app)
    data = client.get("/api/targets").json()
    assert data["targets"] == [
        {"name": "claude-rest", "platform": "claude", "protocol": "rest", "status": "native"}
    ]
    assert "MCP" in data["default_question"]


def test_run_experiment(tmp_path, monkeypatch):
    registry = FakeRegistry()
    app = make_app(tmp_path / "traces", monkeypatch, registry)
    client = TestClient(app)
    r = client.post(
        "/api/run",
        json={"target": "claude-rest", "message": "hi", "trace_id": "ui-trace-1",
              "session_id": "ui-claude-rest"},
    )
    data = r.json()
    assert data["ok"] is True
    assert data["text"] == "echo: hi"
    assert data["trace_id"] == "ui-trace-1"
    req = registry.fake_client.requests[0]
    assert req.trace_id == "ui-trace-1"
    assert req.session_id == "ui-claude-rest"


def test_run_defaults_and_errors(tmp_path, monkeypatch):
    registry = FakeRegistry()
    app = make_app(tmp_path / "traces", monkeypatch, registry)
    client = TestClient(app)
    # empty message -> default question
    data = client.post("/api/run", json={"target": "claude-rest", "message": "  "}).json()
    assert data["ok"] and "MCP" in registry.fake_client.requests[0].message
    # client failure -> ok:false result, not a 500
    data = client.post("/api/run", json={"target": "claude-rest", "message": "boom"}).json()
    assert data["ok"] is False and "kaboom" in data["error"]
    # unknown target -> 404
    assert client.post("/api/run", json={"target": "nope"}).status_code == 404
    # missing target -> 400
    assert client.post("/api/run", json={}).status_code == 400
