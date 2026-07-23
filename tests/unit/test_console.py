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
    # D25: the OpenAI pair went live, mirroring the Claude pair — each
    # direction enters through its own platform and stays two-platform.
    assert names["chatgpt-to-agentforce"]["status"] == "live"
    assert names["chatgpt-to-agentforce"]["target"] == "openai-rest"
    assert names["agentforce-to-chatgpt"]["target"] == "agentforce-openai-rest"
    # D15: the experiment enters through the real Agentforce agent (Agent
    # API); the org itself initiates the bridge hop, not the console.
    assert names["agentforce-to-claude"]["target"] == "agentforce-rest"
    assert names["agentforce-to-claude"]["via_bridge"] is False


def test_scenarios_include_nav_groups(tmp_path, monkeypatch):
    """The two-level Experiments nav: yaml-ordered groups (4 live pairs +
    2 upcoming workstream placeholders), every scenario bucketed into one."""
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    client = TestClient(app)
    data = client.get("/api/scenarios").json()
    assert [g["id"] for g in data["groups"]] == [
        "claude-agentforce",
        "openai-agentforce",
        "adk-agentforce",
        "foundry-agentforce",
        "langgraph-agentforce",
        "strands-agentforce",
    ]
    assert [bool(g.get("upcoming")) for g in data["groups"]] == [False] * 4 + [True] * 2
    group_ids = {g["id"] for g in data["groups"]}
    for s in data["scenarios"]:
        assert s["group"] in group_ids, s["name"]


def test_scenarios_include_adk_pair(tmp_path, monkeypatch):
    """WS2: the ADK pair went live in group adk-agentforce, and the
    agent-engine tag resolves the Vertex AI Agent Engine component row."""
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    client = TestClient(app)
    data = client.get("/api/scenarios").json()
    names = {s["name"]: s for s in data["scenarios"]}
    assert names["adk-to-agentforce"]["group"] == "adk-agentforce"
    assert names["adk-to-agentforce"]["target"] == "google-adk-a2a"
    assert names["agentforce-to-adk"]["group"] == "adk-agentforce"
    assert names["agentforce-to-adk"]["target"] == "agentforce-google-adk-rest"
    assert "adk" in [c["kind"] for c in names["adk-to-agentforce"]["components"]]


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
    # a non-live scenario refuses to run (none ship as coming-soon since
    # D25, so patch one in to keep the refusal path covered)
    import console.app as console_app

    scenarios = console_app.load_scenarios()
    scenarios["not-yet"] = {"title": "Not yet", "status": "coming-soon"}
    monkeypatch.setattr(console_app, "load_scenarios", lambda: scenarios)
    assert client.post("/api/run", json={"scenario": "not-yet"}).status_code == 409
    # unknown scenario
    assert client.post("/api/run", json={"scenario": "nope"}).status_code == 404


def test_config_reports_delegation(tmp_path, monkeypatch):
    """D27: the run panel shows the injected rider read-only — the API must
    hand the console the real rider text, depth limit, and seam list."""
    monkeypatch.delenv("A2ALAB_MODE", raising=False)
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    client = TestClient(app)
    data = client.get("/api/config").json()
    assert data["mode"] == "local"
    d = data["delegation"]
    assert "A2A-LAB DELEGATION" in d["rider"]
    assert d["max_depth"] >= 1 and len(d["seams"]) == 5  # 4 tool paths + bridge
    # Placeholders are display-only; the API names the real seam identities.
    assert any("adk-gemini-agent" in c for c in d["callers"])
    # D28: the channel-routing sibling exhibit
    assert "A2A-LAB ROUTING" in data["af_channel"]["routing_block"]
    assert data["af_channel"]["tools"]["a2a-shim"] == "ask_agentforce_a2a"


def test_run_af_channel_routing_block(tmp_path, monkeypatch):
    """D28: on a toggle scenario, af_channel=a2a-shim appends the routing
    block after the prompt suffix; agent-api (the tools' default bias) and
    non-toggle scenarios never inject."""
    registry = FakeRegistry()
    registry.targets["agentforce-rest"] = Target(
        name="agentforce-rest", platform="agentforce", protocol="rest"
    )
    app = make_app(tmp_path / "traces", monkeypatch, registry)
    client = TestClient(app)
    data = client.post(
        "/api/run",
        json={"scenario": "claude-to-agentforce", "message": "hi", "af_channel": "a2a-shim"},
    ).json()
    assert data["ok"] is True and data["af_channel"] == "a2a-shim"
    msg = registry.fake_client.requests[0].message
    assert "[A2A-LAB ROUTING]" in msg and "ask_agentforce_a2a" in msg
    assert msg.rstrip().endswith("[/A2A-LAB ROUTING]")  # after the prompt_suffix
    # agent-api: no injection, but the channel is still echoed for the badge
    data = client.post(
        "/api/run",
        json={"scenario": "claude-to-agentforce", "message": "hi", "af_channel": "agent-api"},
    ).json()
    assert data["af_channel"] == "agent-api"
    assert "[A2A-LAB ROUTING]" not in registry.fake_client.requests[1].message
    # non-toggle scenario: a2a-shim request is ignored entirely
    data = client.post(
        "/api/run",
        json={"scenario": "agentforce-to-claude", "message": "hi", "af_channel": "a2a-shim"},
    ).json()
    assert data.get("af_channel") is None
    assert "[A2A-LAB ROUTING]" not in registry.fake_client.requests[2].message


def test_run_scenario_requires_mode_gate(tmp_path, monkeypatch):
    """A requires_mode scenario is refused with instructions in the wrong
    deployment mode, and runs once A2ALAB_MODE matches."""
    monkeypatch.delenv("A2ALAB_MODE", raising=False)
    registry = FakeRegistry()
    registry.targets["agentforce-rest"] = Target(
        name="agentforce-rest", platform="agentforce", protocol="rest"
    )
    app = make_app(tmp_path / "traces", monkeypatch, registry)
    client = TestClient(app)
    r = client.post("/api/run", json={"scenario": "agentforce-to-claude-aws", "message": "hi"})
    assert r.status_code == 409
    assert "A2ALAB_MODE=hosted" in r.json()["detail"]
    # flipping the mode opens the gate
    monkeypatch.setenv("A2ALAB_MODE", "hosted")
    data = client.post(
        "/api/run", json={"scenario": "agentforce-to-claude-aws", "message": "hi"}
    ).json()
    assert data["ok"] is True


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


class ColdClient(RemoteAgentClient):
    """A runtime whose cold start blows the client timeout — the failure is
    the data point, so the console must record it, not 500."""

    protocol = "rest"

    async def ask(self, req: AgentRequest) -> AgentResponse:
        raise TimeoutError("cold start exceeded 65s")


class WarmupRegistry(Registry):
    """Two warmup-flagged targets plus a plain one; client_for takes the
    exact= kwarg like the real registry (warm-ups are never mode-remapped)."""

    def __init__(self, client=None):
        super().__init__(
            {
                "claude-agentcore": Target(
                    name="claude-agentcore",
                    platform="claude",
                    protocol="rest",
                    options={"warmup": True},
                ),
                "openai-agentcore": Target(
                    name="openai-agentcore",
                    platform="openai",
                    protocol="rest",
                    options={"warmup": True},
                ),
                "claude-rest": Target(name="claude-rest", platform="claude", protocol="rest"),
            }
        )
        self.fake_client = client or FakeClient()
        self.exact_calls: list[bool] = []

    def client_for(self, name, *, exact=False):
        self.exact_calls.append(exact)
        return self.fake_client


def test_warmup_lists_only_flagged_targets(tmp_path, monkeypatch):
    app = make_app(tmp_path / "traces", monkeypatch, WarmupRegistry())
    client = TestClient(app)
    data = client.get("/api/warmup").json()["targets"]
    assert [t["name"] for t in data] == ["claude-agentcore", "openai-agentcore"]
    assert all(t["last"] is None and t["history"] == [] for t in data)


def test_warmup_post_records_and_returns(tmp_path, monkeypatch):
    registry = WarmupRegistry()
    trace_dir = tmp_path / "traces"
    app = make_app(trace_dir, monkeypatch, registry)
    client = TestClient(app)
    rec = client.post("/api/warmup/claude-agentcore").json()
    assert rec["target"] == "claude-agentcore" and rec["ok"] is True
    assert rec["duration_ms"] >= 0 and "ready" in rec["note"]
    assert registry.exact_calls == [True]  # warm-ups are never mode-remapped
    # appended to warmups.jsonl in the (isolated) trace dir
    lines = (trace_dir / "warmups.jsonl").read_text().splitlines()
    assert json.loads(lines[-1]) == rec
    # and surfaced as the target's last + history head on the next GET
    listed = client.get("/api/warmup").json()["targets"]
    claude = next(t for t in listed if t["name"] == "claude-agentcore")
    assert claude["last"] == rec and claude["history"] == [rec]


def test_warmup_failure_recorded_not_500(tmp_path, monkeypatch):
    trace_dir = tmp_path / "traces"
    app = make_app(trace_dir, monkeypatch, WarmupRegistry(ColdClient()))
    client = TestClient(app)
    r = client.post("/api/warmup/openai-agentcore")
    assert r.status_code == 200
    rec = r.json()
    assert rec["ok"] is False and "cold start exceeded 65s" in rec["note"]
    assert json.loads((trace_dir / "warmups.jsonl").read_text().splitlines()[-1]) == rec


def test_warmup_non_warmable_404(tmp_path, monkeypatch):
    app = make_app(tmp_path / "traces", monkeypatch, WarmupRegistry())
    client = TestClient(app)
    assert client.post("/api/warmup/claude-rest").status_code == 404  # no warmup flag
    assert client.post("/api/warmup/nope").status_code == 404  # unknown target


def test_run_async_scenario_returns_immediately(tmp_path, monkeypatch):
    """D16: async scenarios fire a background research run and ack at once."""
    import briefs.runner as brief_runner

    async def fake_run_brief(accounts, trace_id, extra_context=""):
        return {
            "deliveries": [],
            "elapsed_s": 0.0,
            "web_lookups": 0,
            "session_id": "sesn_x",
            "text": "",
        }

    monkeypatch.setattr(brief_runner, "run_brief", fake_run_brief)
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    client = TestClient(app)
    data = client.post("/api/run", json={"scenario": "account-brief-async", "message": "hi"}).json()
    assert data["ok"] is True and data.get("async") is True
    assert data["trace_id"]
    assert "research started" in data["text"].lower()


def test_decisions_parsed_and_served(tmp_path, monkeypatch):
    """/api/decisions: the ADR log parsed per id — revised decisions keep
    every entry in one markdown body; non-decision sections (M10) excluded."""
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    client = TestClient(app)
    decisions = client.get("/api/decisions").json()["decisions"]
    assert "D27" in decisions and "D28" in decisions
    assert "M10" not in decisions
    d27 = decisions["D27"]
    assert d27["id"] == "D27" and d27["date"] and d27["title"]
    assert "delegation" in d27["markdown"].lower()
    # D12 was revised: both entries live in one body, separated by a rule.
    assert decisions["D12"]["markdown"].count("### ") == 2
    assert "\n---\n" in decisions["D12"]["markdown"]


def test_decisions_missing_file_empty(tmp_path, monkeypatch):
    import console.app as console_app

    assert console_app.load_decisions(tmp_path / "nope.md") == {}


def test_run_af_route_direct_block(tmp_path, monkeypatch):
    """The reverse-direction sibling of D28's channel radio: on an
    af_route_toggle scenario, af_route=direct appends the outbound-route
    block for the twin's script; bridge (the script's default) never
    injects."""
    registry = FakeRegistry()
    registry.targets["agentforce-google-adk-rest"] = Target(
        name="agentforce-google-adk-rest", platform="agentforce", protocol="agentforce-api"
    )
    app = make_app(tmp_path / "traces", monkeypatch, registry)
    client = TestClient(app)
    data = client.post(
        "/api/run",
        json={"scenario": "agentforce-to-adk", "message": "hi", "af_route": "direct"},
    ).json()
    assert data["ok"] is True and data["af_route"] == "direct"
    msg = registry.fake_client.requests[0].message
    assert "agentforce-route: direct" in msg and "ask_external_researcher_direct" in msg
    data = client.post(
        "/api/run",
        json={"scenario": "agentforce-to-adk", "message": "hi", "af_route": "bridge"},
    ).json()
    assert data["af_route"] == "bridge"
    assert "[A2A-LAB ROUTING]" not in registry.fake_client.requests[1].message


def test_targets_carry_cell_details(tmp_path, monkeypatch):
    """Every protocol cell ships a specific blurb, a planned flow (with the
    untraced interior legs), and a default question the agent can answer
    alone — CRM question only for Agentforce cells."""
    import console.app as console_app

    app = make_app(tmp_path / "traces", monkeypatch, None)
    client = TestClient(app)
    targets = {t["name"]: t for t in client.get("/api/targets").json()["targets"]}
    claude = targets["claude-rest"]
    assert "matrix" not in claude["blurb"].lower()
    assert "Managed Agents" in claude["blurb"]
    assert claude["question"] == console_app.CELL_RESEARCH_QUESTION
    assert [h["target"] for h in claude["flow"]][0] == "claude-rest"
    shim = targets["agentforce-a2a"]
    assert "shim" in shim["blurb"]
    assert shim["question"] == console_app.DEFAULT_QUESTION
    assert any(h["protocol"] == "internal" for h in shim["flow"])  # untraced interior leg
