import pytest

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

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


def test_shell_asks_browsers_to_revalidate(tmp_path, monkeypatch):
    # The SPA shell changes on every deploy and the whole app lives inside it,
    # so a heuristically-cached shell hides just-shipped sections. Both entry
    # points must send no-cache. Regression guard for the Monitoring section
    # (WS18) that deployed correctly yet stayed invisible behind a stale shell.
    app = make_app(tmp_path / "traces", monkeypatch)
    client = TestClient(app)
    for path in ("/", "/guide"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers.get("cache-control") == "no-cache", path


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
        "cross-cloud",
        "fan-out",
        "strands-agentforce",
        "langgraph-agentforce",
    ]
    # strands-agentforce (WS5) is live and now sits above the sole remaining
    # upcoming placeholder, langgraph-agentforce (WS4) — a live pair reads
    # ahead of a coming-soon one. Groups order: [...6 live, strands(live),
    # langgraph(upcoming)].
    assert [bool(g.get("upcoming")) for g in data["groups"]] == ([False] * 7 + [True])
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


def test_every_component_has_a_console_url(tmp_path, monkeypatch):
    """A component with no url renders as 'not yet available' — which reads as
    a missing capability, not a missing env var. Every row must therefore have
    a working default; only the Salesforce ones legitimately depend on env
    (the org domain is not ours to hardcode)."""
    monkeypatch.setenv("SF_MY_DOMAIN", "example.my.salesforce.com")
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    client = TestClient(app)
    # Signed in on purpose: the anonymous payload has the URLs stripped by
    # design (see test_public_endpoints_do_not_publish_console_deep_links), and
    # what this test is about is whether the CONFIG resolves a link at all.
    data = client.get("/api/scenarios", headers={"X-Lab-Token": "sekrit"}).json()
    missing = [
        (s["name"], c["title"])
        for s in data["scenarios"]
        for c in s.get("components") or []
        if not c.get("url")
    ]
    assert missing == [], f"components with no console link: {missing}"


def test_foundry_scenario_has_a_platform_component(tmp_path, monkeypatch):
    """WS3 shipped the Foundry agent but no component row, so its Details tab
    listed nothing at all — the one platform whose agent lives entirely in a
    vendor console."""
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    client = TestClient(app)
    names = {s["name"]: s for s in client.get("/api/scenarios").json()["scenarios"]}
    kinds = [c["kind"] for c in names["foundry-to-agentforce"]["components"]]
    assert "foundry" in kinds


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
    assert d["max_depth"] >= 1
    # Assert on CONTENT, not a count. The old `len(seams) == 5` passed happily
    # while the exhibit listed only the Agentforce-consulting seams, so the
    # fan-out scenarios — which delegate three times per run and never touch
    # Salesforce — showed a seam list describing something they don't do.
    assert any("bridge" == s for s in d["seams"])
    assert any("ask_agentforce" in s for s in d["seams"])
    assert any("consult_business_units" in s for s in d["seams"]), (
        "the host-side fan-out is a delegation seam and must appear in the exhibit"
    )
    assert any("consult_<unit>" in s for s in d["seams"])
    # Placeholders are display-only; the API names the real seam identities.
    assert any("adk-gemini-agent" in c for c in d["callers"])
    assert any("supply-orchestrator" in c for c in d["callers"])
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
    # No internal Salesforce-interior ghost: how the twin fulfills the
    # request is its own business — the experiment measures the wire.
    assert not any(h["protocol"] == "internal" for h in shim["flow"])


# ---- viewer role enforcement (WS6 U3 console half, role model per D36) -----


def _operator_headers(monkeypatch, tmp_path):
    """Ryan's persona: the owner role in config/users.yaml, which carries the
    full operator privilege set (identity.OPERATOR_ROLES). _is_operator reloads
    the real directory, so this reflects Ryan's actual role there."""
    from interop import identity

    monkeypatch.setenv(identity.KEY_DIR_ENV, str(tmp_path / "keys"))
    users = {"ryan": {"name": "Ryan Cox", "role": "master of the universe", "reviewer": True}}
    token = identity.issue_token("ryan", users=users)
    return {"authorization": f"Bearer {token}"}


def _viewer_headers(monkeypatch, tmp_path):
    from interop import identity

    monkeypatch.setenv(identity.KEY_DIR_ENV, str(tmp_path / "keys"))
    users = {"vic": {"name": "Vic", "role": "viewer"}}
    token = identity.issue_token("vic", users=users)
    return {"authorization": f"Bearer {token}"}


def test_viewer_403_on_operator_surfaces(tmp_path, monkeypatch):
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    app = make_app(tmp_path, monkeypatch)
    client = TestClient(app)
    headers = _viewer_headers(monkeypatch, tmp_path)
    for path, method in [
        ("/api/run", "post"),
        ("/api/warmup/claude-agentcore", "post"),
        ("/api/obs/harvest", "post"),
        ("/api/obs/analysis/run", "post"),
    ]:
        r = getattr(client, method)(
            path, headers=headers, **({"json": {}} if method == "post" else {})
        )
        assert r.status_code == 403, f"{path} let a viewer in: {r.status_code}"
        assert "operator-only" in r.json()["detail"]


def test_viewer_allowed_surfaces(tmp_path, monkeypatch):
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    app = make_app(tmp_path, monkeypatch)
    client = TestClient(app)
    headers = _viewer_headers(monkeypatch, tmp_path)
    for path in (
        "/api/insights",
        "/api/obs/summary",
        "/api/obs/sessions",
        "/api/decisions",
        "/api/scenarios",
        "/api/config",
        "/api/traces",  # dummy demo data only — the wire record IS the exhibit
    ):
        r = client.get(path, headers=headers)
        assert r.status_code == 200, f"{path} blocked a viewer: {r.status_code}"


def test_owner_role_keeps_the_full_operator_privilege_set(tmp_path, monkeypatch):
    """The lab owner's role ("master of the universe", D36) is DISTINCT from
    operator so the operator password can be handed to colleagues without the
    owner's login — but it must carry every operator surface. This guards the
    strict-equality regression: an operator gate testing `role == "operator"`
    would silently 403 the owner on expiry/config/warmup/harvest. The gate
    must ask identity.is_operator_role, the one source of truth."""
    from interop import identity

    assert identity.is_operator_role("master of the universe")
    assert identity.is_operator_role("operator")
    assert not identity.is_operator_role("viewer")
    assert not identity.is_operator_role(None)
    # The owner's role has a password env of its own, or login fails closed.
    assert identity.ROLE_PASSWORD_ENVS["master of the universe"] == "A2ALAB_MASTER_PASSWORD"

    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    monkeypatch.setenv("A2ALAB_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True)
    (tmp_path / "state" / "expiry.json").write_text(
        json.dumps({"credentials": [{"name": "k", "status": "ok", "days_left": 9}]})
    )
    monkeypatch.setenv(identity.KEY_DIR_ENV, str(tmp_path / "keys"))
    # A token minted for a directory where ryan holds the owner role — exactly
    # what config/users.yaml carries — reaches an operator-only surface.
    users = {"ryan": {"name": "Ryan Cox", "role": "master of the universe", "reviewer": True}}
    token = identity.issue_token("ryan", users=users)
    headers = {"authorization": f"Bearer {token}"}
    app = make_app(tmp_path, monkeypatch)
    r = TestClient(app).get("/api/expiry", headers=headers)
    assert r.status_code == 200, f"owner role blocked from operator surface: {r.status_code}"


def test_tableau_next_app_link_is_owner_only(tmp_path, monkeypatch):
    """WS19/M10: the in-org 'A2A Lab' app deep link launches the live Tableau
    Next dashboard behind a Salesforce login, so it is shown to the OWNER alone
    (role 'master of the universe'). The operator (Ana) — who has every
    experiment surface — must NOT get the link, and neither must an anonymous
    caller; all three still get the screenshot slug and the owner-only flag so
    the UI can explain itself. The scrub is server-side (it names the org
    my-domain), same posture as public_components."""
    from interop import identity

    monkeypatch.setenv("SF_MY_DOMAIN", "example.my.salesforce.com")
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    monkeypatch.setenv(identity.KEY_DIR_ENV, str(tmp_path / "keys"))
    # Embed ECA unwired for this test — the deep link stands on its own.
    monkeypatch.delenv("SF_CLIENT_ID_TAB_EMBED", raising=False)
    monkeypatch.delenv("SF_TAB_EMBED_RUNAS_USER", raising=False)
    monkeypatch.delenv("A2ALAB_TAB_EMBED_JWT_KEY", raising=False)
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    client = TestClient(app)

    # _is_owner reloads the real config/users.yaml: ryan=owner, ana=operator,
    # vic=viewer. Tokens are minted against the same real roles.
    owner = {"authorization": f"Bearer {identity.issue_token('ryan')}"}
    operator = {"authorization": f"Bearer {identity.issue_token('ana')}"}

    owner_tn = client.get("/api/config", headers=owner).json()["tableau_next"]
    # Deep link to the in-org custom TAB, built from SF_MY_DOMAIN (no hardcode).
    assert owner_tn["app_url"] == (
        "https://example.lightning.force.com/lightning/n/A2A_Lab_Traffic"
    )
    assert owner_tn["app_url_owner_only"] is True

    op_tn = client.get("/api/config", headers=operator).json()["tableau_next"]
    assert op_tn["app_url"] is None, "operator (Ana) must not receive the owner-only deep link"
    assert op_tn["app_url_owner_only"] is True

    anon_tn = client.get("/api/config", headers={"x-lab-token": "sekrit"}).json()["tableau_next"]
    assert anon_tn["app_url"] is None, "the shared service token identifies no owner"
    assert anon_tn["app_url_owner_only"] is True


def test_tableau_embed_config_and_frontdoor_are_owner_only(tmp_path, monkeypatch):
    """WS19/M10: the inline embed is owner-only in TWO places that must agree —
    the `embed` block in /api/config (which tells the UI to render inline) and
    the /api/tableau/frontdoor endpoint (which mints the session). Both require
    the dedicated a2a_lab_tab_embed credential (consumer key + run-as user +
    signing key for the JWT-bearer mint); neither is offered to the operator,
    a viewer, or the shared token. When the ECA is unwired the embed is absent
    even for the owner (offering it would 502 on every render)."""
    from interop import identity

    monkeypatch.setenv("SF_MY_DOMAIN", "example.my.salesforce.com")
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    monkeypatch.setenv(identity.KEY_DIR_ENV, str(tmp_path / "keys"))
    # JWT-bearer path (2026-08-09): the embed is offered when the ECA consumer
    # key, the run-as username, AND the signing key are all present. No consumer
    # secret — client-credentials can't reach /singleaccess (only grants `api`),
    # so the frontdoor is minted with a JWT-bearer assertion instead.
    monkeypatch.setenv("SF_CLIENT_ID_TAB_EMBED", "cid")
    monkeypatch.setenv("SF_TAB_EMBED_RUNAS_USER", "admin@example.demo")
    monkeypatch.setenv(
        "A2ALAB_TAB_EMBED_JWT_KEY", "-----BEGIN PRIVATE KEY-----\\nx\\n-----END PRIVATE KEY-----"
    )
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    client = TestClient(app)

    owner = {"authorization": f"Bearer {identity.issue_token('ryan')}"}
    operator = {"authorization": f"Bearer {identity.issue_token('ana')}"}
    service = {"x-lab-token": "sekrit"}

    owner_tn = client.get("/api/config", headers=owner).json()["tableau_next"]
    assert owner_tn["embed"] is not None, "owner with the ECA wired gets the embed config"
    assert owner_tn["embed"]["org_url"] == "https://example.lightning.force.com"
    assert owner_tn["embed"]["dashboard"] == "New_Dashboard"
    assert owner_tn["embed"]["frontdoor_endpoint"] == "/api/tableau/frontdoor"
    assert "cdn.jsdelivr.net" in owner_tn["embed"]["sdk_url"]
    # The frontdoor URL (a live credential) is NEVER in the config payload.
    assert "frontdoor" not in str(owner_tn["embed"]).lower() or "endpoint" in str(owner_tn["embed"])
    assert owner_tn["embed_owner_only"] is True

    op_tn = client.get("/api/config", headers=operator).json()["tableau_next"]
    assert op_tn["embed"] is None, "operator must not receive the embed config"
    svc_tn = client.get("/api/config", headers=service).json()["tableau_next"]
    assert svc_tn["embed"] is None, "the shared service token identifies no owner"

    # The frontdoor endpoint itself is owner-gated — 403 for non-owners even
    # though the ECA is wired. (Owner path is not exercised here: it makes a
    # real Salesforce call; that is proven by the live spike, not a unit test.)
    assert client.get("/api/tableau/frontdoor", headers=operator).status_code == 403
    assert client.get("/api/tableau/frontdoor", headers=service).status_code == 403


def test_tableau_embed_absent_when_eca_unwired(tmp_path, monkeypatch):
    """No SF_CLIENT_ID_TAB_EMBED → the embed is not offered to anyone, including the
    owner, and the frontdoor endpoint 503s rather than minting against the
    scope-lacking shared app (which would 403 Invalid_Scope at Salesforce)."""
    from interop import identity

    monkeypatch.setenv("SF_MY_DOMAIN", "example.my.salesforce.com")
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    monkeypatch.setenv(identity.KEY_DIR_ENV, str(tmp_path / "keys"))
    monkeypatch.delenv("SF_CLIENT_ID_TAB_EMBED", raising=False)
    monkeypatch.delenv("SF_TAB_EMBED_RUNAS_USER", raising=False)
    monkeypatch.delenv("A2ALAB_TAB_EMBED_JWT_KEY", raising=False)
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    client = TestClient(app)

    owner = {"authorization": f"Bearer {identity.issue_token('ryan')}"}
    tn = client.get("/api/config", headers=owner).json()["tableau_next"]
    assert tn["embed"] is None
    assert tn["embed_owner_only"] is False
    # Owner, but no credential to mint with → 503, not a 502 from a bad exchange.
    assert client.get("/api/tableau/frontdoor", headers=owner).status_code == 503


def test_service_token_unaffected_by_role_gate(tmp_path, monkeypatch):
    # The header-borne shared token carries no user — full access (it's the
    # service credential for matrix.py, the bridge, and scripts).
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    app = make_app(tmp_path, monkeypatch)
    client = TestClient(app)
    r = client.get("/api/traces", headers={"x-lab-token": "sekrit"})
    assert r.status_code == 200


def test_public_landing_surface_vs_gated(tmp_path, monkeypatch):
    # D36: signed-out visitors get the landing exhibit (tiles, chips) but
    # nothing live — no traces, obs, runs, or guide.
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    app = make_app(tmp_path, monkeypatch)
    client = TestClient(app)
    for path in ("/api/scenarios", "/api/targets", "/api/decisions", "/api/users"):
        assert client.get(path).status_code == 200, path
    assert client.get("/api/docs/plan/02-matrix.md").status_code == 200
    assert client.get("/api/docs/docs/lab-guide-mcp.md").status_code == 200
    for path in ("/api/traces", "/api/insights", "/api/obs/sessions", "/api/config"):
        assert client.get(path).status_code == 401, path


# ---- insight sign-off (console: Insights → Approve / Request changes) ------


def _reviewer_headers(monkeypatch, tmp_path, username="ryan"):
    from interop import identity

    monkeypatch.setenv(identity.KEY_DIR_ENV, str(tmp_path / "keys"))
    users = {username: {"name": "Ryan Cox", "role": "operator"}}
    return {"authorization": f"Bearer {identity.issue_token(username, users=users)}"}


def _fake_insights(monkeypatch, evidence="as first published"):
    import console.app as console_app

    items = [
        {
            "id": "needs-a-look",
            "category": "Method",
            "status": "observed",
            "review": "required",
            "headline": "A claim the lab has not vouched for yet",
            "evidence": evidence,
            "advisory": "say this to a customer",
            "refs": ["D37"],
        },
        {"id": "already-public", "category": "Method", "status": "observed", "headline": "no gate"},
    ]
    monkeypatch.setattr(console_app, "load_insights", lambda *a, **k: items)
    return items


def test_insight_signoff_records_person_and_pins_content(tmp_path, monkeypatch):
    # An approval is of WORDS, not of an id: edit the claim afterwards and the
    # sign-off must read as stale rather than silently carrying over.
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    from console import reviews

    monkeypatch.setattr(reviews, "REVIEWS_PATH", tmp_path / "insight_reviews.yaml")
    app = make_app(tmp_path, monkeypatch)
    _fake_insights(monkeypatch)
    client = TestClient(app)
    headers = _reviewer_headers(monkeypatch, tmp_path)

    data = client.get("/api/insights", headers=headers).json()
    assert data["can_review"] is True
    gated, ungated = data["insights"]
    assert gated["review_state"] == {"required": True, "state": "pending", "stale": False}
    assert ungated["review_state"]["required"] is False  # no control on this tile

    r = client.post(
        "/api/insights/needs-a-look/review",
        headers=headers,
        json={"decision": "approved", "comment": "  reads right   to me "},
    )
    assert r.status_code == 200
    state = r.json()["review_state"]
    assert state["state"] == "approved" and state["by"] == "ryan"
    assert state["name"] == "Ryan Cox" and state["comment"] == "reads right to me"
    assert state["stale"] is False

    # Same text -> still approved; edited text -> approved-but-stale.
    fresh = client.get("/api/insights", headers=headers).json()["insights"][0]
    assert fresh["review_state"]["state"] == "approved" and fresh["review_state"]["stale"] is False
    _fake_insights(monkeypatch, evidence="rewritten after the sign-off")
    edited = client.get("/api/insights", headers=headers).json()["insights"][0]
    assert edited["review_state"]["state"] == "approved" and edited["review_state"]["stale"] is True


def test_insight_signoff_reserved_to_the_reviewer(tmp_path, monkeypatch):
    # reviewer is a grant of its own (config/users.yaml), not something the
    # operator role implies — and the service token identifies nobody.
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    from console import reviews

    monkeypatch.setattr(reviews, "REVIEWS_PATH", tmp_path / "insight_reviews.yaml")
    app = make_app(tmp_path, monkeypatch)
    _fake_insights(monkeypatch)
    client = TestClient(app)
    body = {"decision": "approved"}

    viewer = _viewer_headers(monkeypatch, tmp_path)
    assert client.get("/api/insights", headers=viewer).json()["can_review"] is False
    assert (
        client.post("/api/insights/needs-a-look/review", headers=viewer, json=body).status_code
        == 403
    )

    service = {"x-lab-token": "sekrit"}
    assert client.get("/api/insights", headers=service).json()["can_review"] is False
    assert (
        client.post("/api/insights/needs-a-look/review", headers=service, json=body).status_code
        == 403
    )
    assert not (tmp_path / "insight_reviews.yaml").exists()  # nothing was written


def test_insight_signoff_rejects_bad_input(tmp_path, monkeypatch):
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    from console import reviews

    monkeypatch.setattr(reviews, "REVIEWS_PATH", tmp_path / "insight_reviews.yaml")
    app = make_app(tmp_path, monkeypatch)
    _fake_insights(monkeypatch)
    client = TestClient(app)
    headers = _reviewer_headers(monkeypatch, tmp_path)

    bad = client.post(
        "/api/insights/needs-a-look/review", headers=headers, json={"decision": "maybe"}
    )
    assert bad.status_code == 400
    missing = client.post(
        "/api/insights/no-such-insight/review", headers=headers, json={"decision": "approved"}
    )
    assert missing.status_code == 404


# ---- WS9: Build Telemetry (coding-agent cost) ------------------------------


def _seed_coding(trace_dir):
    """A day of Claude Code usage in the obs store the console reads."""
    from observability.store import ObsStore

    store = ObsStore(db_path=trace_dir / "lab.db")
    store.upsert_session(
        "coding",
        "claude-code:2026-07-25",
        title="claude-code · 2026-07-25",
        created_at="2026-07-25T00:00:00+00:00",
        usage={
            "input_tokens": 120_000,
            "output_tokens": 8_000,
            # The realistic shape of an agent workload: cache reads dwarf
            # uncached input. A fixture with only two buckets would let the
            # under-reporting bug this guards against pass unnoticed.
            "cache_read_input_tokens": 4_000_000,
            "cache_creation_input_tokens": 300_000,
            "cost_usd_estimated": 4.2,
        },
        raw={
            "tool": "claude-code",
            "sessions": 6,
            "active_time_s": 5400,
            "by_model": {},
            "by_repo": {
                "someone/rc-a2a": {
                    "repo": "someone/rc-a2a",
                    "project": "rc-a2a",
                    "cost_usd": 4.2,
                    # by_repo keeps the raw OTel `type` spelling
                    "tokens": {
                        "input": 120_000,
                        "output": 8_000,
                        "cacheRead": 4_000_000,
                        "cacheCreation": 300_000,
                    },
                    "sessions": 6,
                    "active_time_s": 5400,
                }
            },
        },
    )
    # A Codex tool-day: activity counters only, no cost or token metric — the
    # shape the segmented per-tool tiles must render as real numbers (sessions,
    # turns) rather than the hardcoded cost/token n/a. `metrics` is the summed
    # {metric_name: value} dict the harvest stores in raw_json.
    store.upsert_session(
        "coding",
        "codex:2026-07-24",
        title="codex · 2026-07-24",
        created_at="2026-07-24T00:00:00+00:00",
        usage={"cost_usd_estimated": 0.0},
        raw={
            "tool": "codex",
            "sessions": 3,
            "metrics": {
                "codex.thread.started": 3,
                "codex.conversation.turn.count": 41,
            },
            "by_model": {},
            "by_repo": {},
        },
    )
    store.set_harvest_status("coding", "ok", "2 tool-day(s)")
    store.close()


def test_build_telemetry_rolls_up_cost_by_tool(tmp_path, monkeypatch):
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    _seed_coding(trace_dir)
    app = make_app(trace_dir, monkeypatch, FakeRegistry())
    data = TestClient(app).get("/api/build-telemetry").json()

    assert data["enabled"] is True
    assert data["totals"]["cost_usd"] == 4.2
    assert data["totals"]["input_tokens"] == 120_000
    assert data["by_tool"][0]["tool"] == "claude-code"
    assert data["days"][0]["date"] == "2026-07-25"
    # the caveat is part of the payload, so no consumer can render the number
    # without it
    assert "not an invoice" in data["cost_note"]


def test_build_telemetry_exposes_per_tool_activity_for_segmented_tiles(tmp_path, monkeypatch):
    """Codex and Cursor publish no cost or token metric, but they DO publish
    activity counters — sessions and turns for Codex — and the segmented
    per-tool tiles need those as real numbers rather than the cost/token n/a.

    The harvest already stores them in raw["metrics"]; this asserts the endpoint
    surfaces them as a named `activity` map per tool.
    """
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    _seed_coding(trace_dir)
    data = (
        TestClient(make_app(trace_dir, monkeypatch, FakeRegistry()))
        .get("/api/build-telemetry")
        .json()
    )
    by_tool = {b["tool"]: b for b in data["by_tool"]}

    assert by_tool["claude-code"]["activity"]["sessions"] == 6
    codex = by_tool["codex"]["activity"]
    assert codex["sessions"] == 3
    assert codex["turns"] == 41
    # Codex publishes no Cursor prompt/tool metric, so those are absent (None),
    # which the tile renders as an explained blank rather than a misleading 0.
    assert codex["prompts"] is None
    # And the cost/token honesty flags are unchanged — activity does not make
    # Codex cost-supported.
    assert by_tool["codex"]["cost_supported"] is False
    assert by_tool["codex"]["tokens_supported"] is False


def test_build_telemetry_reports_all_four_billed_token_buckets(tmp_path, monkeypatch):
    """`input_tokens` is the UNCACHED remainder, not the prompt.

    The harvest has stored four buckets since WS9 and this endpoint reported
    two, so the dashboard showed 120K "input" for a day that actually processed
    4.42M prompt tokens — a 36x understatement on exactly the workload shape
    (long agent sessions, heavy caching) the section exists to measure. The
    three input buckets also bill at different multiples, which is why they are
    kept separate here rather than summed.
    """
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    _seed_coding(trace_dir)
    data = (
        TestClient(make_app(trace_dir, monkeypatch, FakeRegistry()))
        .get("/api/build-telemetry")
        .json()
    )

    for scope in (data["totals"], data["by_tool"][0], data["days"][0], data["by_repo"][0]):
        assert scope["input_tokens"] == 120_000
        assert scope["cache_read_tokens"] == 4_000_000
        assert scope["cache_creation_tokens"] == 300_000
        assert scope["output_tokens"] == 8_000

    # The reason they cannot be one number has to travel with them.
    assert "cache read" in data["token_note"]
    assert "contract" in data["token_note"]


def test_build_telemetry_never_appears_as_a_platform_column(tmp_path, monkeypatch):
    """The whole reason this is a separate section.

    The Observability coverage panel's honesty rests on its columns all being
    agent platforms whose interiors the lab harvests. The tool that BUILT the
    lab is not one of those, and must not be listed beside Agentforce.
    """
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    _seed_coding(trace_dir)
    app = make_app(trace_dir, monkeypatch, FakeRegistry())
    client = TestClient(app)

    summary = client.get("/api/obs/summary").json()
    assert "coding" not in summary["platforms"]
    # but it is still reachable in its own section
    assert client.get("/api/build-telemetry").json()["enabled"] is True


def test_coding_harvest_is_reachable_by_name_but_not_in_the_sweep(tmp_path, monkeypatch):
    """The section's own Harvest button posts ?platform=coding.

    It must route to CodingSource (before this, the console's harvest map had
    four platform sources and no coding entry, so the button could only ever
    answer "unknown platform"). The unqualified sweep behind Observability's
    Harvest must NOT pick it up — that button reports "harvested from all
    platforms", and coding is not one of them.
    """
    called = []

    def fake_source(name):
        class FakeSource:
            def harvest(self, store):
                called.append(name)
                return SimpleNamespace(platform=name, status="ok", detail="fake")

        return FakeSource

    import observability.adk_source as adk_source
    import observability.anthropic_source as anthropic_source
    import observability.coding_source as coding_source
    import observability.openai_source as openai_source
    import observability.salesforce_source as salesforce_source

    monkeypatch.setattr(coding_source, "CodingSource", fake_source("coding"))
    monkeypatch.setattr(anthropic_source, "AnthropicSource", fake_source("claude"))
    monkeypatch.setattr(salesforce_source, "SalesforceSource", fake_source("salesforce"))
    monkeypatch.setattr(openai_source, "OpenAISource", fake_source("openai"))
    monkeypatch.setattr(adk_source, "AdkSource", fake_source("adk"))
    # This test is about the in-process source MAP, so force that path: the
    # endpoint now defaults to firing the harvest Lambda (D54).
    monkeypatch.setenv("A2ALAB_HARVEST_FUNCTION", "")
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    client = TestClient(make_app(trace_dir, monkeypatch, FakeRegistry()))

    r = client.post("/api/obs/harvest?platform=coding").json()
    assert r["ok"] is True
    assert called == ["coding"]
    assert r["results"][0]["platform"] == "coding"

    called.clear()
    sweep = client.post("/api/obs/harvest").json()
    assert sweep["ok"] is True
    assert "coding" not in called
    assert set(called) == {"claude", "salesforce", "openai", "adk"}


def test_build_telemetry_explains_setup_when_nothing_collected(tmp_path, monkeypatch):
    """Until the exporters are on there is nothing to show, and the useful
    answer is how to start — telemetry is not retroactive."""
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    app = make_app(trace_dir, monkeypatch, FakeRegistry())
    data = TestClient(app).get("/api/build-telemetry").json()

    assert data["enabled"] is False
    assert data["days"] == []
    assert "D64" in data["comparison_md"]
    assert "n/a" in data["comparison_md"]
    steps = " ".join(s["step"] + s["detail"] for s in data["setup"])
    assert "CloudWatchAPIKeyAccess" in steps
    assert "tool=codex" in steps
    # the three things the AWS docs corrected in the first draft, kept honest
    assert "/v1/metrics" in steps  # the PATH is required
    assert "http/protobuf" in steps  # not http/json
    assert "cannot call the logs" in steps  # a metrics token is metrics-only


def test_cost_brief_reports_unprovisioned_as_a_state_not_an_error(tmp_path, monkeypatch):
    """WS12. A sentinel that has never been created is the normal first state,
    and the useful answer is the setup command — not a 500 and not an empty
    panel. The list-price caveat ships with the payload for the same reason it
    ships with the telemetry totals: no consumer can render the money without
    it."""
    monkeypatch.setenv("A2ALAB_STATE_DIR", str(tmp_path / "state"))
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    data = TestClient(app).get("/api/cost-brief").json()

    assert data["provisioned"] is False
    assert data["briefs"] == []
    assert "setup_cost_sentinel.py" in data["setup_hint"]
    assert "not an invoice" in data["cost_note"]


def test_cost_brief_run_is_operator_only(tmp_path, monkeypatch):
    """A firing bills a real Claude session. That is spend, so it sits behind
    the same gate as the credential analyst rather than behind mere sign-in."""
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    monkeypatch.setenv("A2ALAB_STATE_DIR", str(tmp_path / "state"))
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    assert TestClient(app).post("/api/cost-brief/run").status_code in (401, 403)


def test_cost_brief_schedule_is_operator_only(tmp_path, monkeypatch):
    """Resuming the schedule turns on recurring billed sessions — a spend
    decision, gated exactly like the manual run rather than behind sign-in."""
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    monkeypatch.setenv("A2ALAB_STATE_DIR", str(tmp_path / "state"))
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    resp = TestClient(app).post("/api/cost-brief/schedule", json={"action": "resume"})
    assert resp.status_code in (401, 403)


def test_architecture_endpoint_serves_the_deployment_map(tmp_path, monkeypatch):
    """The console's Architecture section parses plan/09-deployment-map.md on
    every request — the doc is the source, the UI is the view."""
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    data = TestClient(app).get("/api/architecture").json()

    ids = [x["id"] for x in data["levels"]]
    # L0 opens the document; the rest may include decimal levels inserted
    # between the originals (L0.5, L5.5, L5.7), so assert ORDER rather than a
    # fixed pair — pinning ["L0", "L1"] failed the moment a level was added
    # between them, which is a thing this doc is expected to do.
    assert ids[0] == "L0"
    assert len(ids) >= 5
    assert ids == sorted(ids, key=lambda i: float(i[1:])), f"levels out of order: {ids}"
    # Every level carries a diagram: the Architecture section is the pictures,
    # and a level without one renders as a heading and a wall of prose.
    assert all(x["mermaid"] for x in data["levels"])
    assert data["path"].endswith("09-deployment-map.md")


def test_docs_endpoint_serves_every_doc_tree_the_ui_chips(tmp_path, monkeypatch):
    """A doc chip that 404s is worse than plain text — it invites a click and
    fails. The UI linkifies plan/, docs/, build-notes/ and the README, so all
    four must be servable (build-notes was referenced by insights and 404'd)."""
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    client = TestClient(app)
    for path in (
        "README.md",
        "plan/09-deployment-map.md",
        "docs/lab-guide-mcp.md",
        "build-notes/claude/04-claude-code-environment.md",
    ):
        assert client.get(f"/api/docs/{path}").status_code == 200, path

    # Still a whitelist, not a file server: source and config stay closed.
    for path in ("src/console/app.py", "config/targets.yaml", ".env"):
        assert client.get(f"/api/docs/{path}").status_code == 404, path


def test_tmp_docs_is_never_surfaced(tmp_path, monkeypatch):
    """`tmp-docs/` is gitignored scratch space — the author's thinking before it
    becomes a workstream. It is cited by name in plan/07 for provenance, which
    is exactly the kind of mention that invites someone to "helpfully" add it to
    a whitelist. Two doors, both held shut here."""
    from platforms.guide import corpus

    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    client = TestClient(app)
    for path in (
        "tmp-docs/07.25.2026-AD3-mulesoft-agent-fabric.md",
        "tmp-docs/anything.md",
        # ..-escapes resolve before the whitelist check; prove it.
        "plan/../tmp-docs/07.25.2026-arch-thoughts.md",
    ):
        assert client.get(f"/api/docs/{path}").status_code == 404, path

    readable = corpus.CORE_DOCS + corpus.TOOL_DOCS
    assert not [d for d in readable if "tmp-docs" in d], readable


def test_presenter_notes_reach_only_a_reviewer(tmp_path, monkeypatch):
    """Speaker prep lives in the same doc as the map, and the console is served
    on a public hostname — so it is stripped server-side, not hidden in CSS.
    Anything that ships to the browser is published whether or not it renders."""
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    app = make_app(tmp_path, monkeypatch)
    client = TestClient(app)

    # The document itself has the section...
    from console.architecture import load as load_architecture

    assert "Before presenting" in load_architecture()["presenter"]

    # ...a viewer never receives it...
    viewer = client.get("/api/architecture", headers=_viewer_headers(monkeypatch, tmp_path))
    assert viewer.status_code == 200
    assert viewer.json()["presenter"] == ""
    assert "Before presenting" not in viewer.text
    assert viewer.json()["levels"], "the map itself is still public"

    # ...and neither does the shared service token, which identifies no one.
    svc = client.get("/api/architecture", headers={"X-Lab-Token": "sekrit"})
    assert svc.json()["presenter"] == ""


def test_architecture_links_only_repo_files_that_exist(tmp_path, monkeypatch):
    """A link that 404s on GitHub in front of an audience is worse than plain
    text — so the paths are existence-checked, not pattern-matched."""
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    data = TestClient(app).get("/api/architecture").json()

    files = {f["path"]: f for f in data["files"]}
    assert "scripts/identity_preflight.py" in files
    assert files["scripts/identity_preflight.py"]["url"].endswith(
        "/blob/main/scripts/identity_preflight.py"
    )
    assert files["src/bridge"]["kind"] == "dir"
    assert "/tree/main/src/bridge" in files["src/bridge"]["url"]
    for path in files:
        assert Path(path).exists(), f"linked a path that is not in the repo: {path}"


def test_public_endpoints_do_not_publish_console_deep_links(tmp_path, monkeypatch):
    """/api/scenarios and /api/targets are the unauthenticated landing exhibit.
    Component deep links name the Salesforce org's my-domain, the GCP project
    and (once set) an Azure tenant id — the identifiers the repo stopped
    publishing. An anonymous caller gets the titles, not the URLs."""
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    monkeypatch.setenv("SF_MY_DOMAIN", "example.my.salesforce.com")
    app = make_app(tmp_path, monkeypatch)
    client = TestClient(app)

    def component_urls(headers):
        found = set()
        for path, key in (("/api/scenarios", "scenarios"), ("/api/targets", "targets")):
            for row in client.get(path, headers=headers).json()[key]:
                for comp in row.get("components") or []:
                    if comp.get("url"):
                        found.add(comp["url"])
        return found

    anon = component_urls({})
    assert anon == set(), f"public endpoint leaked console links: {anon}"

    # ...but the exhibit itself still renders: titles survive, and the UI is
    # told the link exists rather than claiming the component does not.
    scen = client.get("/api/scenarios").json()["scenarios"]
    comps = [c for s in scen for c in (s.get("components") or [])]
    assert comps, "components disappeared entirely"
    assert any(c.get("url_requires_signin") for c in comps)

    # A known caller gets them. NOTE: these paths are exempt from the token
    # middleware, so the handler verifies the credential itself — this asserts
    # that path works, not just that the middleware would have.
    assert component_urls({"X-Lab-Token": "sekrit"}), "signed-in caller lost the links"


def test_expiry_is_operator_only(tmp_path, monkeypatch):
    """Credential expiry describes the lab's rotation posture — operational
    data, not part of the public exhibit. The shared service token does not
    qualify either: it identifies no one."""
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    monkeypatch.setenv("A2ALAB_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True)
    (tmp_path / "state" / "expiry.json").write_text(
        json.dumps({"credentials": [{"name": "k", "status": "ok", "days_left": 9}]})
    )
    app = make_app(tmp_path, monkeypatch)
    client = TestClient(app)

    assert client.get("/api/expiry", headers={"X-Lab-Token": "sekrit"}).status_code == 403
    viewer = client.get("/api/expiry", headers=_viewer_headers(monkeypatch, tmp_path))
    assert viewer.status_code == 403

    operator = _operator_headers(monkeypatch, tmp_path)
    ok = client.get("/api/expiry", headers=operator)
    assert ok.status_code == 200
    assert ok.json()["credentials"][0]["name"] == "k"


def test_expiry_says_so_when_no_report_has_been_collected(tmp_path, monkeypatch):
    """An absent report must name the command that produces it — a silent empty
    table reads as 'no credentials expire', which is the opposite of true."""
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    monkeypatch.setenv("A2ALAB_STATE_DIR", str(tmp_path / "empty"))
    app = make_app(tmp_path, monkeypatch)
    data = (
        TestClient(app).get("/api/expiry", headers=_operator_headers(monkeypatch, tmp_path)).json()
    )
    assert data["credentials"] == []
    assert "expiry_report.py" in data["error"]


def test_sse_keepalive_fits_inside_common_idle_timeouts():
    """The live tail and the Lab Guide chat are both SSE, and a quiet lab emits
    nothing — which every intermediary reads as a dead connection. The ALB the
    console moves behind (WS13) idles out at 120s and proxies commonly at 30s,
    so the keepalive has to be under the smallest of them.

    The drop itself is recoverable; the data loss is not. EventSource
    reconnects, but the rebuilt generator restarts its per-file offsets at
    current EOF, so hops that arrived during the gap are skipped silently.

    Asserted as a constant rather than by reading the stream: the tail never
    ends, so every 'read until done' idiom hangs the suite. The emission itself
    is exercised in the live console.
    """
    import console.app as console_app

    assert console_app.SSE_KEEPALIVE_S < 30, (
        "keepalive must stay under the smallest common idle timeout (30s)"
    )


def test_healthz_is_open_and_says_nothing_useful(tmp_path, monkeypatch):
    """The ALB health check carries no credentials, so a gated /healthz marks
    every task unhealthy and the service never stabilises (WS13). It must also
    disclose nothing beyond liveness — it is the one unauthenticated endpoint
    added for infrastructure rather than for the exhibit."""
    app = make_app(tmp_path / "traces", monkeypatch)
    resp = TestClient(app).get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy", "app": "console"}


def test_hosted_console_refuses_to_start_without_a_token(monkeypatch):
    """The hosted console must fail CLOSED when its token never arrives.

    Found by deploying it (2026-07-28, WS13 item 1). `deploy_console.sh` wrote
    the runtime secret and passed `A2ALAB_RUNTIME_SECRET_ARN`, but the console —
    unlike the bridge — never called `load_secret_env_and_log`, so `A2ALAB_TOKEN`
    was unset in the container. `TokenAuthMiddleware` treats an absent expected
    token as "auth is off" (correct on a laptop, where `.env` is the only source
    and needing AWS to run locally would be worse). Behind a public ALB it meant
    every /api surface answered 200 to an unauthenticated caller, and a
    deliberately wrong bearer token was accepted too.

    The middleware's open-when-unset behaviour is deliberately left alone; what
    changes is that a container which believes it is hosted refuses to serve
    with authentication disabled.
    """
    import console.app as console_app

    monkeypatch.setattr(console_app, "create_console_app", lambda *a, **k: object())
    monkeypatch.setattr("interop.secret_env.load_secret_env_and_log", lambda source: None)
    # main() calls load_dotenv(), and this repo HAS a .env carrying a token —
    # so without stubbing it the guard cannot be observed here even though it
    # fires in the container, which has no .env at all.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("A2ALAB_RUNTIME_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:x:secret:y")
    monkeypatch.delenv("A2ALAB_TOKEN", raising=False)
    monkeypatch.setattr("sys.argv", ["console"])

    with pytest.raises(SystemExit) as exc:
        console_app.main()
    assert "A2ALAB_TOKEN" in str(exc.value)


def test_local_console_still_starts_without_a_token(monkeypatch):
    """The guard must not break local development, which is the reason the
    middleware fails open in the first place: no runtime secret ARN means a
    laptop reading `.env`, and it must not need AWS to run."""
    import console.app as console_app

    started = {}
    monkeypatch.setattr(console_app, "create_console_app", lambda *a, **k: object())
    monkeypatch.setattr("interop.secret_env.load_secret_env_and_log", lambda source: None)
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: started.update(kw))
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("A2ALAB_RUNTIME_SECRET_ARN", raising=False)
    monkeypatch.delenv("A2ALAB_TOKEN", raising=False)
    monkeypatch.setattr("sys.argv", ["console"])

    console_app.main()
    assert started["port"] == 8200


def test_empty_console_url_env_falls_back_to_the_default(tmp_path, monkeypatch):
    """An empty override means "I have nothing better", not "show no link".

    `.env` carried `AGENTCORE_CONSOLE_URL=` and `AGENT_ENGINE_CONSOLE_URL=`
    with empty values, and the code read them with `os.environ.get(var,
    default)` — where an empty string is a PRESENT key and beats the default.
    Both rows rendered "not yet available" in the running console for as long
    as that was true. test_every_component_has_a_console_url could not catch it
    because the suite does not load `.env` and `run_local.sh` does.
    """
    monkeypatch.setenv("SF_MY_DOMAIN", "example.my.salesforce.com")
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    monkeypatch.setenv("AGENTCORE_CONSOLE_URL", "")
    monkeypatch.setenv("AGENT_ENGINE_CONSOLE_URL", "")
    monkeypatch.setenv("FOUNDRY_CONSOLE_URL", "")
    monkeypatch.setenv("OPENAI_CONSOLE_URL", "")
    monkeypatch.setenv("CLAUDE_AGENT_CONSOLE_URL", "")
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    data = TestClient(app).get("/api/scenarios", headers={"X-Lab-Token": "sekrit"}).json()
    missing = [
        (s["name"], c["title"])
        for s in data["scenarios"]
        for c in s.get("components") or []
        if not c.get("url")
    ]
    assert missing == [], f"empty env var blanked a console link: {missing}"


def test_sign_offs_persist_to_the_hosted_store_when_configured(monkeypatch, tmp_path):
    """D50: the hosted console's filesystem is a layer of the container image,
    so a sign-off written there vanishes on the next restart with no error —
    the worst failure mode for a named human decision. Aurora is the store when
    it is configured."""
    from console import reviews

    saved = {}

    class FakeStore:
        def get_state(self, key):
            return saved.get(key)

        def put_state(self, key, payload):
            saved[key] = payload

        def close(self):
            saved["closed"] = True

    monkeypatch.setattr(reviews, "_hosted_store", lambda: FakeStore())
    monkeypatch.setattr(reviews, "REVIEWS_PATH", tmp_path / "insight_reviews.yaml")

    entry = reviews.record(
        {"id": "a2a-async-at-heart", "headline": "h", "status": "measured"},
        "approved",
        user={"sub": "ryan", "name": "Ryan Cox"},
        comment="checked against the run",
    )
    assert entry["by"] == "ryan"
    assert saved["insight_reviews"]["reviews"]["a2a-async-at-heart"]["state"] == "approved"
    # and it reads back from the store, not the file
    assert reviews.load_reviews()["a2a-async-at-heart"]["name"] == "Ryan Cox"


def test_sign_offs_still_use_the_file_with_no_hosted_store(monkeypatch, tmp_path):
    """A fresh checkout, the unit suite and offline work must not need Aurora."""
    from console import reviews

    monkeypatch.setattr(reviews, "_hosted_store", lambda: None)
    path = tmp_path / "insight_reviews.yaml"
    reviews.record(
        {"id": "managed-vs-self-hosted", "headline": "h"},
        "approved",
        user={"sub": "ryan"},
        path=path,
    )
    assert path.exists()
    assert reviews.load_reviews(path)["managed-vs-self-hosted"]["state"] == "approved"


def test_an_explicit_path_is_never_answered_from_the_store(monkeypatch, tmp_path):
    """scripts/insight_reviews_sync.py compares the two copies, so a caller
    naming a file must get that file — otherwise `diff` compares the store
    with itself and always reports 'in sync'."""
    from console import reviews

    class LoudStore:
        def get_state(self, key):
            raise AssertionError("explicit path must not consult the store")

        def close(self):
            pass

    monkeypatch.setattr(reviews, "_hosted_store", lambda: LoudStore())
    path = tmp_path / "insight_reviews.yaml"
    path.write_text("reviews:\n  x:\n    state: approved\n")
    assert reviews.load_reviews(path)["x"]["state"] == "approved"


def test_a_failing_store_does_not_report_a_silent_success(monkeypatch, tmp_path):
    """The whole point is that a lost sign-off must not look like a saved one."""
    from console import reviews

    class BrokenStore:
        def get_state(self, key):
            return None

        def put_state(self, key, payload):
            raise RuntimeError("aurora is asleep")

        def close(self):
            pass

    monkeypatch.setattr(reviews, "_hosted_store", lambda: BrokenStore())
    monkeypatch.setattr(reviews, "REVIEWS_PATH", tmp_path / "insight_reviews.yaml")
    with pytest.raises(RuntimeError, match="aurora is asleep"):
        reviews.record({"id": "x", "headline": "h"}, "approved", user={"sub": "ryan"})


def test_hosted_harvest_fires_the_lambda_and_returns_at_once(tmp_path, monkeypatch):
    """A full six-platform sweep runs past two minutes. The ALB's idle timeout
    is 120s and Cloudflare's proxy limit is lower, so a synchronous harvest
    cannot finish through the front door whatever we configure — the browser
    got the load balancer's HTML 504 and reported
    `SyntaxError: Unexpected token '<'` (D54).

    Delegating also fixes what the in-process sweep could not do at all: the
    Lambda covers six platforms to this endpoint's four, and holds the GCP key
    and Entra principal the console container does not.
    """
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    monkeypatch.setenv("A2ALAB_HARVEST_FUNCTION", "a2alab-obs-harvest")
    invoked = {}

    class FakeLambda:
        def invoke(self, **kw):
            invoked.update(kw)
            return {"StatusCode": 202}

    monkeypatch.setattr("boto3.client", lambda *a, **k: FakeLambda())
    app = make_app(tmp_path / "traces", monkeypatch)
    resp = TestClient(app).post("/api/obs/harvest", headers={"X-Lab-Token": "sekrit"})
    body = resp.json()
    assert body["ok"] is True and body["async"] is True
    assert body["started_at"] > 0  # the poll contract
    assert invoked["FunctionName"] == "a2alab-obs-harvest"
    assert invoked["InvocationType"] == "Event"  # fire-and-forget, not RequestResponse


def test_local_harvest_still_runs_in_process(tmp_path, monkeypatch):
    """No Lambda configured means a laptop, where nothing is timing the request
    out — running it here keeps the per-platform outcomes in the response."""
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    # Empty, not absent: the endpoint now DEFAULTS to firing the Lambda so the
    # button behaves the same on a laptop as hosted (the in-process sweep wrote
    # with a read-only credential and 500'd). Forcing it is opt-in.
    monkeypatch.setenv("A2ALAB_HARVEST_FUNCTION", "")

    class FakeSource:
        def __init__(self, *a, **k):
            pass

        def harvest(self, store):
            return SimpleNamespace(__dict__={"platform": "claude", "status": "ok", "detail": ""})

    import console.app as console_app

    app = make_app(tmp_path / "traces", monkeypatch)
    monkeypatch.setattr("observability.anthropic_source.AnthropicSource", FakeSource)
    resp = TestClient(app).post(
        "/api/obs/harvest?platform=claude", headers={"X-Lab-Token": "sekrit"}
    )
    assert resp.json().get("async") is None  # synchronous path
    assert console_app is not None


def test_an_unstartable_harvest_reports_rather_than_pretending(tmp_path, monkeypatch):
    """The old failure told the user 'Harvest failed: SyntaxError'. A refusal to
    start must say so in words the panel can render."""
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    monkeypatch.setenv("A2ALAB_HARVEST_FUNCTION", "a2alab-obs-harvest")

    def boom(*a, **k):
        raise RuntimeError("AccessDeniedException: lambda:InvokeFunction")

    monkeypatch.setattr("boto3.client", boom)
    app = make_app(tmp_path / "traces", monkeypatch)
    body = TestClient(app).post("/api/obs/harvest", headers={"X-Lab-Token": "sekrit"}).json()
    assert body["ok"] is False
    assert "lambda:InvokeFunction" in body["error"]


def test_hosted_mode_keeps_the_managed_and_self_hosted_cells_distinct():
    """D55. `claude-to-agentforce` targets claude-rest and is titled "Claude
    Managed Agent"; `claude-aws-to-agentforce` targets claude-agentcore, the
    SELF-HOSTED SDK twin. The pair exists to compare the two.

    Hosted mode used to remap claude-rest → claude-agentcore, so both ran the
    same backend and the comparison silently compared nothing. Proven from a
    trace hop before the fix: claude-rest resolved to
    `claude-agentcore (agentcore-http)` reporting `"backend": "sdk"`.
    """
    from interop.registry import Registry

    registry = Registry.load()
    hosted = registry.modes.get("hosted", {})
    assert hosted.get("claude-rest") != "claude-agentcore", (
        "hosted mode must not send the Managed Agent cell to the self-hosted runtime"
    )
    assert hosted.get("openai-rest") != "openai-agentcore"
    # and the two cells must still resolve somewhere DIFFERENT from each other
    assert hosted.get("claude-rest", "claude-rest") != hosted.get(
        "claude-agentcore", "claude-agentcore"
    )


def test_every_hosted_remap_points_at_a_real_target():
    """A mode entry naming a target that does not exist fails at request time
    with an unknown-target KeyError, long after the typo."""
    from interop.registry import Registry

    registry = Registry.load()
    for mode, mapping in registry.modes.items():
        for src, dst in mapping.items():
            assert src in registry.targets, f"{mode}: unknown source target {src}"
            assert dst in registry.targets, f"{mode}: unknown destination target {dst}"


def test_the_briefs_endpoint_returns_every_kind_within_the_window(tmp_path, monkeypatch):
    """D56. Two agents write to lab.obs_briefs — the observability analyst and
    the WS12 cost sentinel — separated only by `kind`. /api/cost-brief always
    filtered; /api/obs/briefs never did, so it returned whatever was newest and
    the Observability section rendered a build-COST brief. It read as the
    analyst having changed subject to coding telemetry, when in fact its own
    last brief was eleven days old and it was paused.
    """
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    asked = {}

    class FakeStore:
        def list_briefs(self, limit=20, kind=None, days=None):
            asked.update(kind=kind, days=days)
            return []

        def close(self):
            pass

    monkeypatch.setattr("observability.pg.PgClient.configured", classmethod(lambda cls: True))
    monkeypatch.setattr("observability.pg.PgObsStore", lambda *a, **k: FakeStore())
    app = make_app(tmp_path / "traces", monkeypatch)
    TestClient(app).get("/api/obs/briefs", headers={"X-Lab-Token": "sekrit"})
    # It asks for EVERY kind in the window and the console splits them into
    # one sub-tab per kind, so a future analysis agent gets a tab with no
    # server change. What must not return is the old behaviour: an unfiltered
    # list under ONE heading, where the cost sentinel's brief appeared in the
    # Observability section and read as the analyst changing subject.
    assert asked["kind"] is None
    # and it asks for a WINDOW, so a week with no analyst run looks empty
    # rather than showing an eleven-day-old brief as if it were current.
    assert asked["days"] == 7


def test_track_accepts_anonymous_beacon_without_pg(tmp_path, monkeypatch):
    """WS18: /api/track is exempt from auth (an unauthenticated visit must log
    before sign-in) and no-ops gracefully when Aurora is not configured — it
    returns 204 and never 500s a beacon."""
    monkeypatch.delenv("A2ALAB_PG_CLUSTER_ARN", raising=False)
    monkeypatch.delenv("A2ALAB_PG_DSN", raising=False)
    monkeypatch.delenv("A2ALAB_LOGGING_API_URL", raising=False)
    app = make_app(tmp_path / "traces", monkeypatch)
    client = TestClient(app)
    r = client.post("/api/track", json={"event": "site_visit", "visitor_id": "v1"})
    assert r.status_code == 204


def test_track_drops_unknown_event(tmp_path, monkeypatch):
    """The event name is a closed set; an unknown one is dropped (still 204),
    so the table cannot fill with typos."""
    app = make_app(tmp_path / "traces", monkeypatch)
    client = TestClient(app)
    r = client.post("/api/track", json={"event": "definitely-not-real"})
    assert r.status_code == 204


def test_track_records_row_and_stamps_headers(tmp_path, monkeypatch):
    """When Aurora is configured the beacon writes one row; country comes from
    CF-IPCountry (not an IP) and persona is NOT taken from the body."""
    monkeypatch.setenv("A2ALAB_PG_CLUSTER_ARN", "arn:aws:rds:us-east-1:x:cluster:c")
    monkeypatch.setenv("A2ALAB_PG_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:x:secret:r")
    monkeypatch.setenv("A2ALAB_PG_WRITER_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:x:secret:w")
    monkeypatch.delenv("A2ALAB_LOGGING_API_URL", raising=False)

    import console.app as console_app

    make_app(tmp_path / "traces", monkeypatch)  # reload with env set
    importlib.reload(console_app)

    recorded = {}

    class FakeStore:
        def record_usage(self, event, **kw):
            recorded["event"] = event
            recorded.update(kw)

        def close(self):
            recorded["closed"] = True

    # Intercept the writer-store construction so no AWS call happens.
    import observability.pg as pg

    monkeypatch.setattr(pg.PgClient, "configured", classmethod(lambda cls: True))
    monkeypatch.setattr(pg.PgClient, "__init__", lambda self, **kw: None)
    monkeypatch.setattr(pg, "PgObsStore", lambda client=None: FakeStore())

    app = console_app.create_console_app()
    client = TestClient(app)
    r = client.post(
        "/api/track",
        json={
            "event": "nav",
            "section": "experiment",
            "visitor_id": "v9",
            "persona": "SPOOFED",
            "detail": {"name": "x"},
        },
        headers={"CF-IPCountry": "gb", "Accept-Language": "en-GB,en;q=0.9"},
    )
    assert r.status_code == 204
    assert recorded["event"] == "nav"
    assert recorded["section"] == "experiment"
    assert recorded["country"] == "GB"  # uppercased CF header
    assert recorded["locale"] == "en-GB"  # first Accept-Language tag
    assert recorded["persona"] is None  # body value ignored, no JWT
    assert recorded.get("closed") is True


def test_monitoring_requires_sign_in(tmp_path, monkeypatch):
    """The aggregates are lab operating data, not the public exhibit — 401
    without a credential, unlike /api/track."""
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    app = make_app(tmp_path / "traces", monkeypatch)
    client = TestClient(app)
    assert client.get("/api/monitoring").status_code == 401
    ok = client.get("/api/monitoring", headers={"Authorization": "Bearer sekrit"})
    # Signed in: 200 with a "not configured" note when there is no Aurora.
    assert ok.status_code == 200
    assert ok.json()["stats"] is None
