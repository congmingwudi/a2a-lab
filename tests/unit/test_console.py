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
    (trace_dir / "2026-07-09.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
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
    (target,) = data["targets"]
    assert {k: target[k] for k in ("name", "platform", "protocol", "status")} == {
        "name": "claude-rest",
        "platform": "claude",
        "protocol": "rest",
        "status": "native",
    }
    # component deep links to the real agent assets (Details tab)
    assert [c["kind"] for c in target["components"]] == ["claude"]
    from console.app import DEFAULT_QUESTION

    assert data["default_question"] == DEFAULT_QUESTION


def test_run_experiment(tmp_path, monkeypatch):
    registry = FakeRegistry()
    app = make_app(tmp_path / "traces", monkeypatch, registry)
    client = TestClient(app)
    r = client.post(
        "/api/run",
        json={
            "target": "claude-rest",
            "message": "hi",
            "trace_id": "ui-trace-1",
            "session_id": "ui-claude-rest",
        },
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
    from console.app import DEFAULT_QUESTION

    data = client.post("/api/run", json={"target": "claude-rest", "message": "  "}).json()
    assert data["ok"] and registry.fake_client.requests[0].message == DEFAULT_QUESTION
    # client failure -> ok:false result, not a 500
    data = client.post("/api/run", json={"target": "claude-rest", "message": "boom"}).json()
    assert data["ok"] is False and "kaboom" in data["error"]
    # unknown target -> 404
    assert client.post("/api/run", json={"target": "nope"}).status_code == 404
    # missing target -> 400
    assert client.post("/api/run", json={}).status_code == 400


def test_scenarios_listed(tmp_path, monkeypatch):
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    client = TestClient(app)
    data = client.get("/api/scenarios").json()["scenarios"]
    names = {s["name"]: s for s in data}
    assert "claude-to-agentforce" in names and names["claude-to-agentforce"]["status"] == "live"
    assert names["chatgpt-to-agentforce"]["status"] == "coming-soon"
    # D15: the experiment enters through the real Agentforce agent (Agent
    # API); the org itself initiates the bridge hop, not the console.
    assert names["agentforce-to-claude"]["target"] == "agentforce-rest"
    assert names["agentforce-to-claude"]["via_bridge"] is False


def test_run_scenario_resolves_target_and_suffix(tmp_path, monkeypatch):
    registry = FakeRegistry()
    app = make_app(tmp_path / "traces", monkeypatch, registry)
    client = TestClient(app)
    data = client.post(
        "/api/run", json={"scenario": "claude-to-agentforce", "message": "What can you do?"}
    ).json()
    assert data["ok"] is True
    req = registry.fake_client.requests[0]
    assert req.message.startswith("What can you do?")
    assert "ask_agentforce" in req.message  # prompt_suffix appended
    # coming-soon scenario refuses to run
    assert client.post("/api/run", json={"scenario": "chatgpt-to-agentforce"}).status_code == 409
    # unknown scenario
    assert client.post("/api/run", json={"scenario": "nope"}).status_code == 404


def test_run_cell_via_bridge(tmp_path, monkeypatch):
    """The via-bridge shape survives on protocol calls: a cell run with
    via_bridge=true routes through the bridge exactly like the Apex action."""
    import console.app as console_app

    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    calls = {}

    async def fake_bridge(req, target):
        calls["target"] = target
        return {"ok": True, "trace_id": req.trace_id, "text": "loop", "via_bridge": True}

    monkeypatch.setattr(console_app, "run_via_bridge", fake_bridge)
    client = TestClient(app)
    data = client.post(
        "/api/run", json={"target": "claude-rest", "message": "hi", "via_bridge": True}
    ).json()
    assert data["ok"] is True and data["via_bridge"] is True
    assert calls["target"] == "claude-rest"


def test_run_via_bridge(tmp_path, monkeypatch):
    import console.app as console_app

    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    calls = {}

    async def fake_bridge(req, target):
        calls["req"], calls["target"] = req, target
        return {"ok": True, "trace_id": req.trace_id, "text": "via bridge", "via_bridge": True}

    monkeypatch.setattr(console_app, "run_via_bridge", fake_bridge)
    client = TestClient(app)
    data = client.post(
        "/api/run",
        json={"target": "claude-rest", "message": "hi", "trace_id": "t-b", "via_bridge": True},
    ).json()
    assert data == {"ok": True, "trace_id": "t-b", "text": "via bridge", "via_bridge": True}
    assert calls["target"] == "claude-rest" and calls["req"].trace_id == "t-b"


def test_run_async_scenario_returns_immediately(tmp_path, monkeypatch):
    """D16: async scenarios fire a background research run and ack at once."""
    import briefs.runner as brief_runner

    async def fake_run_brief(accounts, trace_id, extra_context=""):
        return {"deliveries": [], "elapsed_s": 0.0, "web_lookups": 0,
                "session_id": "sesn_x", "text": ""}

    monkeypatch.setattr(brief_runner, "run_brief", fake_run_brief)
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    client = TestClient(app)
    data = client.post(
        "/api/run", json={"scenario": "account-brief-async", "message": "hi"}
    ).json()
    assert data["ok"] is True and data.get("async") is True
    assert data["trace_id"]
    assert "research started" in data["text"].lower()
