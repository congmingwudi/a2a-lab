import pytest
import yaml

import importlib
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from interop.clients.base import RemoteAgentClient
from interop.models import AgentRequest, AgentResponse
from interop.registry import Registry, Target
from orchestration.agents import LEG_AGENTS


def make_app(trace_dir, monkeypatch, registry=None):
    monkeypatch.setenv("A2ALAB_TRACE_DIR", str(trace_dir))
    import console.app as console_app

    importlib.reload(console_app)
    return console_app.create_console_app(registry)


def unscoped_theme_selectors(css: str) -> list[str]:
    without_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    selectors = re.findall(r"([^{};]+)\{", without_comments)
    bad_selectors = []
    for selector_group in selectors:
        selector_group = selector_group.strip()
        if not selector_group or selector_group.startswith("@"):
            continue
        for selector in selector_group.split(","):
            selector = selector.strip()
            if selector and not selector.startswith('html[data-design="'):
                bad_selectors.append(selector)
    return bad_selectors


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
    for path in ("/", "/guide", "/design/axs"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers.get("cache-control") == "no-cache", path


def test_optional_themes_are_available_from_every_public_shell(tmp_path, monkeypatch):
    """Default stays unmarked while the compatibility alias forces Skyline.

    Like /guide, the data-free shell must load before browser JavaScript can
    attach the persona JWT held in localStorage. Live-data routes stay gated.
    """
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    app = make_app(tmp_path / "traces", monkeypatch)
    client = TestClient(app)

    themed = client.get("/design/axs")
    assert themed.status_code == 200
    assert '<html lang="en" data-design="skyline" data-design-forced="skyline">' in themed.text
    assert re.search(r'href="/static/themes\.css\?v=\d+"', themed.text)

    for path in ("/", "/guide"):
        normal = client.get(path)
        assert normal.status_code == 200
        assert '<html lang="en">' in normal.text
        assert 'data-design-forced="skyline"' not in normal.text
        assert re.search(r'href="/static/themes\.css\?v=\d+"', normal.text)

    assert client.get("/api/traces").status_code == 401


def test_optional_themes_are_scoped_accessible_and_keep_semantic_colors():
    css = Path("src/console/static/themes.css").read_text()
    for design in ("skyline", "canvas", "parchment"):
        assert f'html[data-design="{design}"][data-theme="light"]' in css
        assert f'html[data-design="{design}"][data-theme="dark"]' in css
    assert "prefers-reduced-motion: reduce" in css

    # Every concrete selector, including selectors nested in @media, must be
    # rooted in one of the opt-in design markers. At-rules themselves are the
    # only allowed unscoped block headers.
    assert unscoped_theme_selectors(css) == []

    semantic_tokens = (
        "--rest:",
        "--mcp:",
        "--a2a:",
        "--agentforce-api:",
        "--chip-agentforce:",
        "--chip-claude:",
        "--chip-openai:",
        "--chip-google:",
        "--chip-aws:",
        "--chip-foundry:",
    )
    assert not [token for token in semantic_tokens if token in css]


def test_shared_lab_mark_is_fixed_accessible_and_not_theme_overridden():
    html = Path("src/console/static/index.html").read_text()
    css = Path("src/console/static/themes.css").read_text()

    mark = re.search(r'<svg class="brandmark".*?</svg>', html, flags=re.S)
    assert mark
    markup = mark.group(0)
    assert 'width="20"' in markup
    assert 'height="20"' in markup
    assert 'viewBox="0 0 20 20"' in markup
    assert 'aria-hidden="true"' in markup
    assert 'focusable="false"' in markup
    assert markup.count('data-part="agent"') == 2
    assert markup.count('data-part="message"') == 2
    assert 'stroke="#0b1f33"' in markup
    assert 'stroke-width="1.5"' in markup
    assert 'stroke-linecap="round"' in markup

    base_rule = re.search(r"\.brandmark\s*\{(.*?)\}", html, flags=re.S)
    assert base_rule
    declarations = base_rule.group(1)
    for expected in ("display: block", "width: 20px", "height: 20px", "flex: none"):
        assert expected in declarations
    assert "#09decb 0%" in declarations
    assert "#3fc7d8 30%" in declarations
    assert "#7db9e8 55%" in declarations
    assert "#a8a2ec 78%" in declarations
    assert "#c99df0 100%" in declarations

    # The mark is lab identity, not a theme surface. Inspect all selector
    # headers so overrides nested in media queries and pseudo-elements fail.
    without_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    selector_groups = re.findall(r"([^{};]+)\{", without_comments)
    mark_selectors = [
        selector.strip()
        for group in selector_groups
        if not group.strip().startswith("@")
        for selector in group.split(",")
        if ".brandmark" in selector
    ]
    assert mark_selectors == []


def test_landing_title_reuses_the_shared_lab_mark():
    html = Path("src/console/static/index.html").read_text()

    assert 'function landingMarkHtml()' in html
    assert 'document.querySelector("header .brandmark")' in html
    assert 'mark.classList.add("landing-brandmark")' in html
    assert '<div class="landing-title">${landingMarkHtml()}<h1 class="wordmark">' in html
    assert html.count('<h1 class="wordmark">A2A Interop Lab</h1>') == 1

    wrapper = re.search(r"\.landing-title\s*\{(.*?)\}", html, flags=re.S)
    assert wrapper
    for expected in (
        "display: flex",
        "align-items: baseline",
        "gap: 12px",
        "font-family: var(--font-head)",
        "font-size: var(--landing-title-size)",
    ):
        assert expected in wrapper.group(1)

    landing_mark = re.search(
        r"\.landing-title \.landing-brandmark\s*\{(.*?)\}", html, flags=re.S
    )
    assert landing_mark
    for expected in (
        "width: 1cap",
        "height: 1cap",
    ):
        assert expected in landing_mark.group(1)

    assert "font-size: var(--landing-title-size)" in html


def test_every_design_sizes_the_landing_mark_and_title_from_one_token():
    html = Path("src/console/static/index.html").read_text()
    css = Path("src/console/static/themes.css").read_text()

    assert "--landing-title-size: clamp(30px, 4.4vw, 46px)" in html
    for design, size in (
        ("skyline", "clamp(40px, 6vw, 70px)"),
        ("canvas", "clamp(44px, 7vw, 82px)"),
        ("parchment", "clamp(45px, 6.7vw, 78px)"),
    ):
        rule = re.search(
            rf'html\[data-design="{design}"\] \.landing-title\s*\{{(.*?)\}}',
            css,
            flags=re.S,
        )
        assert rule
        assert f"--landing-title-size: {size}" in rule.group(1)

    # Theme CSS may choose the shared size token, but never redraw or
    # independently resize the lab mark itself.
    assert ".brandmark" not in css


def test_landing_intro_uses_a_responsive_logo_platform_list():
    html = Path("src/console/static/index.html").read_text()

    removed_inline_list = (
        "— Salesforce Agentforce,\n"
        "          Claude, OpenAI, Google Gemini on Vertex AI Agent Engine"
    )
    assert removed_inline_list not in html
    assert "Agent platforms across the hyperscalers answer the same questions by" in html
    assert '${landingPlatformListHtml()}' in html
    assert '<ul class="hero-platforms" aria-label="Agent platforms">' in html

    expected_platforms = (
        '["salesforce", "Salesforce Agentforce"]',
        '["anthropic", "Claude"]',
        '["openai", "OpenAI"]',
        '["gcloud", "Google Gemini on Vertex AI Agent Engine"]',
        '["microsoft", "Microsoft Foundry"]',
        '["aws", "AWS Strands on Bedrock AgentCore"]',
        '["langgraph", "LangGraph on Heroku"]',
    )
    for platform in expected_platforms:
        assert platform in html

    list_rule = re.search(r"\.hero-platforms\s*\{(.*?)\}", html, flags=re.S)
    assert list_rule
    for expected in (
        "display: grid",
        "repeat(auto-fit, minmax(230px, 1fr))",
        "list-style: none",
    ):
        assert expected in list_rule.group(1)


def test_control_panel_uses_category_icons_without_disclosure_bullets():
    html = Path("src/console/static/index.html").read_text()
    css = Path("src/console/static/themes.css").read_text()

    assert "details.acc > summary::before" not in html
    assert "details.acc[open] > summary::before" not in html
    assert 'details.acc > summary::before' not in css
    assert ".nav-group .caret" not in html

    scenario_nav = re.search(
        r"function renderScenarios\(\)\s*\{(.*?)\n\}\n\nfunction renderTargets",
        html,
        flags=re.S,
    )
    assert scenario_nav
    assert 'class="caret"' not in scenario_nav.group(1)


def test_optional_theme_scope_check_catches_first_rule_inside_media():
    mutated = '@media (max-width: 1px) { body { color: red; } }'
    assert unscoped_theme_selectors(mutated) == ["body"]


def test_optional_theme_token_contrast_and_distinct_primary_colors():
    """Root surfaces remain readable and chrome does not impersonate a platform."""
    css = Path("src/console/static/themes.css").read_text()
    html = Path("src/console/static/index.html").read_text()

    def rgb(value):
        value = value.lstrip("#")
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))

    def contrast(a, b):
        def luminance(value):
            channels = []
            for channel in rgb(value):
                level = channel / 255
                channels.append(level / 12.92 if level <= 0.04045 else ((level + 0.055) / 1.055) ** 2.4)
            return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

        high, low = sorted((luminance(a), luminance(b)), reverse=True)
        return (high + 0.05) / (low + 0.05)

    base_dark = re.search(r":root\s*\{(.*?)\}", html, flags=re.S)
    base_light = re.search(r':root\[data-theme="light"\]\s*\{(.*?)\}', html, flags=re.S)
    assert base_dark and base_light
    platform_colors = {}
    for mode, block in (("dark", base_dark), ("light", base_light)):
        values = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", block.group(1)))
        platform_colors[mode] = (values["chip-openai"].lower(), values["chip-claude"].lower())

    for design in ("skyline", "canvas", "parchment"):
        for mode in ("light", "dark"):
            pattern = rf'html\[data-design="{design}"\]\[data-theme="{mode}"\]\s*\{{(.*?)\}}'
            block = re.search(pattern, css, flags=re.S)
            assert block, (design, mode)
            tokens = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", block.group(1)))
            for surface in ("bg", "card", "card2"):
                assert contrast(tokens["fg"], tokens[surface]) >= 4.5, (design, mode, surface)
                assert contrast(tokens["muted"], tokens[surface]) >= 4.5, (design, mode, surface)
            assert tokens["primary"].lower() not in platform_colors[mode]
            if design in ("canvas", "parchment"):
                focus_token = {
                    "canvas": "canvas-focus",
                    "parchment": "parchment-focus",
                }[design]
                assert contrast(tokens["border-hi"], tokens["card"]) >= 3.0, (design, mode)
                assert contrast(tokens[focus_token], tokens["card"]) >= 3.0, (design, mode)


def test_theme_controls_bootstrap_and_experiment_heading_identity():
    html = Path("src/console/static/index.html").read_text()

    assert '<select id="designTheme"' in html
    assert '<option value="default">Default</option>' in html
    assert '<option value="skyline">Skyline</option>' in html
    assert '<option value="canvas">Canvas</option>' in html
    assert '<option value="parchment">Parchment</option>' in html
    assert 'aria-label="Color mode"' in html
    assert 'aria-label="Theme"' not in html

    # Pre-paint and runtime paths share the same literal allow-list contract.
    # Garbage storage values therefore leave the default marker absent.
    assert html.count('["default", "skyline", "canvas", "parchment"]') == 2
    assert 'DESIGNS.includes(savedDesign)' in html
    assert 'DESIGNS.includes(design)' in html
    assert 'forcedDesign === "skyline"' in html
    assert 'localStorage.getItem("a2a-design-v1")' in html
    assert 'localStorage.setItem(DESIGN_KEY, normalized)' in html
    assert 'window.location.assign("/")' in html
    assert 'normalized !== "skyline"' in html

    assert 'LangGraph|Claude|Agentforce' in html
    assert '"LangGraph": "langgraph"' in html
    assert "scenarioTitleHtml(exp) || esc(view.name)" in html
    assert "targetTitleHtml(exp) || esc(view.name)" in html
    assert 'includes("agentcore") ? `<span class="badge aws"' not in html


def test_d99_platform_identity_contract_and_shared_renderers():
    scenarios = yaml.safe_load(Path("config/scenarios.yaml").read_text())["scenarios"]
    targets = yaml.safe_load(Path("config/targets.yaml").read_text())["targets"]
    html = Path("src/console/static/index.html").read_text()

    # Account Brief identity describes the actual run roles: Claude researches,
    # Salesforce receives the brief. It must not imply an Agentforce call.
    assert scenarios["account-brief-async"]["title_identity"] == [
        {"platform": "claude", "role": "researcher"},
        {"platform": "agentforce", "role": "delivery-org"},
    ]

    expected_legs = {agent.platform for agent in LEG_AGENTS}
    assert expected_legs == {"adk", "foundry", "openai"}
    for name in (
        "supplier-disruption-cma",
        "supplier-disruption-adk",
        "supplier-disruption-agentforce",
    ):
        scenario = scenarios[name]
        assert set(scenario["fanout_platforms"]) == expected_legs
        agent_platforms = {
            targets[agent["target"]]["platform"]
            for agent in scenario["agents"]
            if not agent["title"].startswith("Orchestrator")
        }
        assert agent_platforms == expected_legs

    configured_platforms = {target["platform"] for target in targets.values()}
    expected_marks = {
        "claude": "anthropic",
        "agentforce": "salesforce",
        "openai": "openai",
        "adk": "gcloud",
        "foundry": "microsoft",
        "strands": "aws",
        "langgraph": "langgraph",
        "guide": "aws",
        "mulesoft": None,
    }
    assert configured_platforms == set(expected_marks)
    mark_block = html.split("const PLATFORM_MARK = {", 1)[1].split("};", 1)[0]
    for platform, mark in expected_marks.items():
        value = "null" if mark is None else f'"{mark}"'
        assert f'{platform}: {value}' in mark_block

    declared = {
        item["platform"] for item in scenarios["account-brief-async"]["title_identity"]
    }
    declared.update(scenarios["supplier-disruption-cma"]["fanout_platforms"])
    assert declared <= configured_platforms

    assert 'function scenarioTitleHtml(s)' in html
    assert '<h3>${scenarioTitleHtml(s)}</h3>' in html
    assert '<div class="tile-head"><h3>${scenarioTitleHtml(s)}</h3>' in html
    assert 'function targetTitleHtml(t)' in html
    assert '<span class="tname mono" title="platform: ${esc(t.platform)}">${targetTitleHtml(t)}' in html
    assert 'role="img" aria-label="${esc(label)}"' in html
    assert 'class="title-icons${lead ? " lead" : ""}"' in html
    assert ".tabbar h2 > .title-icons.lead" in html
    assert ".title-icons:first-child" not in html

    assert 'const seen = new Set();' in html
    assert 'if (!mark || seen.has(mark)) return "";' in html
    assert "PLATFORM_MARK[a.platform]" in html
    assert 'PLATFORM_MARK[t.platform]' in html
    assert "WARM_LOGO" not in html

    label_block = html.split("const PLATFORM_LABEL = {", 1)[1].split("};", 1)[0]
    role_block = html.split("const TITLE_ROLE_LABEL = {", 1)[1].split("};", 1)[0]
    assert '\\"' not in label_block + role_block

    assert scenarios["strands-to-agentforce"]["title"] == "AWS Strands → Agentforce"
    assert scenarios["agentforce-to-strands"]["title"] == "Agentforce → AWS Strands (AWS)"


def test_d102_supplier_memory_controls_are_scoped_and_accessible():
    scenarios = yaml.safe_load(Path("config/scenarios.yaml").read_text())["scenarios"]
    html = Path("src/console/static/index.html").read_text()
    assert scenarios["supplier-disruption-cma"]["fanout_memory_toggle"] is True
    assert not scenarios["supplier-disruption-adk"].get("fanout_memory_toggle")
    assert not scenarios["supplier-disruption-agentforce"].get("fanout_memory_toggle")
    assert 'name="fanoutMemoryMode"' in html
    assert 'aria-label="Case-memory readers"' in html
    assert 'aria-label="Case-memory key"' in html
    assert 'maxlength="64"' in html
    assert "const CASE_MEMORY_HELP = {" in html
    for setting in ("section", "off", "seed", "recall", "readers", "case"):
        assert f"qhelp(CASE_MEMORY_HELP.{setting})" in html
    assert "Use the same exact key to revisit a case" in html
    assert "coordinator only during final synthesis" in html
    assert 'exp.fanout_memory_toggle ? fanoutMemoryRowHtml(key)' in html
    assert "This run does not claim recall" in html
    assert "fanoutMemoryDetailHtml(s)" in html
    assert 'm.state === "unavailable"' in html
    assert "memory: seeded ${written}/${nominated}" in html
    assert '"memory: empty"' in html
    deploy = Path("deploy/console/deploy_console.sh").read_text()
    assert '"SF_CLIENT_ID_MEMORY"' in deploy
    assert 'payload["A2ALAB_MEMORY_JWT_KEY"]' in deploy
    assert 'memory_jwt_private.pem' in deploy


def test_customer_memory_owner_map_is_secret_backed_and_validated_at_deploy():
    deploy = Path("deploy/faces/deploy_faces.sh").read_text()
    assert '"A2ALAB_MEMORY_OWNER_MAP"' in deploy
    assert '"A2ALAB_MEMORY_CUSTOMER_MAP"' in deploy
    assert "json.loads(payload[map_key])" in deploy
    example = Path(".env.example").read_text()
    assert "#A2ALAB_MEMORY_OWNER_MAP='{" in example
    assert "#A2ALAB_MEMORY_CUSTOMER_MAP='{" in example

    console_deploy = Path("deploy/console/deploy_console.sh").read_text()
    assert '"A2ALAB_MEMORY_CUSTOMER_MAP"' in console_deploy
    assert 'json.loads(payload["A2ALAB_MEMORY_CUSTOMER_MAP"])' in console_deploy


def test_customer_memory_management_is_persona_scoped_and_operator_only(tmp_path, monkeypatch):
    from interop import memory as memory_mod
    from interop.memory import MemoryEntry, MemoryListing

    class FakeManagementMemory:
        def __init__(self):
            self.items = {
                "profile-maya": {
                    "sys_PROFILE_FACT": [MemoryEntry("fact-1", "Keeps bees.")],
                    "sys_PROFILE_PREFERENCES": [],
                },
                "profile-jordan": {
                    "sys_PROFILE_FACT": [MemoryEntry("fact-j", "Keeps koi.")],
                    "sys_PROFILE_PREFERENCES": [],
                },
            }
            self.fact_delete_attempts = 0

        async def list_memories(self, trace_id, **kw):
            entries = tuple(self.items[kw["owner_id"]][kw["category"]])
            return MemoryListing(entries, len(entries))

        async def create_memories(self, trace_id, **kw):
            entry = MemoryEntry("generation-new", kw["contents"][0])
            self.items[kw["owner_id"]][kw["category"]].append(entry)
            return MemoryListing((entry,), 1)

        async def delete_memories(self, trace_id, **kw):
            if kw["category"] == "sys_PROFILE_FACT":
                self.fact_delete_attempts += 1
                if self.fact_delete_attempts == 1:
                    from interop.memory import MemoryCallError
                    raise MemoryCallError("already changing", status=404)
            ids = set(kw["memory_ids"])
            self.items[kw["owner_id"]][kw["category"]] = [
                entry for entry in self.items[kw["owner_id"]][kw["category"]]
                if entry.memory_id not in ids
            ]
            return tuple(ids)

        async def aclose(self):
            pass

    fake_memory = FakeManagementMemory()
    monkeypatch.setattr(memory_mod, "MemoryClient", lambda **_: fake_memory)
    monkeypatch.setenv(
        "A2ALAB_MEMORY_CUSTOMER_MAP", '{"maya":"profile-maya","jordan":"profile-jordan"}'
    )
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    client = TestClient(app)
    operator = _operator_headers(monkeypatch, tmp_path)
    response = client.get("/api/memory/customer/maya", headers=operator)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["customer"] == "maya" and data["complete"] is True
    assert data["memories"] == [
        {"category": "sys_PROFILE_FACT", "memory_id": "fact-1", "content": "Keeps bees."}
    ]
    deleted = client.post(
        "/api/memory/customer/maya", headers=operator,
        json={"action": "delete", "memories": data["memories"]},
    )
    assert deleted.status_code == 200 and deleted.json()["ok"] is True
    assert fake_memory.fact_delete_attempts == 2
    fresh = client.post(
        "/api/memory/customer/maya", headers=operator, json={"action": "fresh"}
    )
    assert fresh.status_code == 200 and fresh.json()["ok"] is True
    assert fresh.json()["generation"].startswith("v1:")
    jordan = client.get("/api/memory/customer/jordan", headers=operator)
    assert jordan.status_code == 200 and jordan.json()["memories"] == [
        {"category": "sys_PROFILE_FACT", "memory_id": "fact-j", "content": "Keeps koi."}
    ]
    rejected = client.post(
        "/api/memory/customer/maya", headers=operator,
        json={"action": "delete", "memories": [
            {"category": "sys_PROFILE_FACT", "memory_id": "not-governed"}
        ]},
    )
    assert rejected.status_code == 400
    assert client.get(
        "/api/memory/customer/maya", headers=_viewer_headers(monkeypatch, tmp_path)
    ).status_code == 403
    assert client.get(
        "/api/memory/customer/maya", headers={"x-lab-token": "sekrit"}
    ).status_code == 403
    assert client.get(
        "/api/memory/customer/maya", headers=_machine_headers(monkeypatch, tmp_path)
    ).status_code == 403


def test_customer_memory_reset_reports_partial_delete_and_selected_generation(tmp_path, monkeypatch):
    from interop import memory as memory_mod
    from interop.memory import MemoryCallError, MemoryEntry, MemoryListing

    class FailingMemory:
        def __init__(self, *, retain_marker=False):
            self.fact = MemoryEntry("fact-1", "Keeps bees.")
            self.marker = None
            self.retain_marker = retain_marker

        async def list_memories(self, trace_id, **kw):
            if kw["category"] == "sys_PROFILE_FACT":
                entries = ([self.fact] if self.fact and self.fact.memory_id else []) + (
                    [MemoryEntry("marker-new", self.marker)] if self.marker else []
                )
                return MemoryListing(tuple(entries), len(entries))
            return MemoryListing((), 0)

        async def create_memories(self, trace_id, **kw):
            if self.retain_marker:
                self.marker = kw["contents"][0]
            return MemoryListing((MemoryEntry("marker-new", kw["contents"][0]),), 1)

        async def delete_memories(self, trace_id, **kw):
            if kw["category"] == "sys_PROFILE_FACT":
                raise MemoryCallError("delete refused", status=500, body={"error": "no"})
            return tuple(kw["memory_ids"])

        async def aclose(self):
            pass

    fake = FailingMemory(retain_marker=True)
    monkeypatch.setattr(memory_mod, "MemoryClient", lambda **_: fake)
    monkeypatch.setenv("A2ALAB_MEMORY_CUSTOMER_MAP", '{"maya":"profile-maya"}')
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    client = TestClient(app)
    response = client.post(
        "/api/memory/customer/maya", headers=_operator_headers(monkeypatch, tmp_path),
        json={"action": "fresh"},
    )
    assert response.status_code == 200 and response.headers["content-type"].startswith("application/json")
    data = response.json()
    assert data["ok"] is False and data["trace_id"] and data["errors"] == ["delete refused"]
    assert data["memories"] == [
        {"category": "sys_PROFILE_FACT", "memory_id": "fact-1", "content": "Keeps bees."}
    ]
    assert any(step["ok"] is False for step in data["steps"])

    mismatch = FailingMemory(retain_marker=False)
    mismatch.fact = None
    monkeypatch.setattr(memory_mod, "MemoryClient", lambda **_: mismatch)
    response = client.post(
        "/api/memory/customer/maya", headers=_operator_headers(monkeypatch, tmp_path),
        json={"action": "fresh"},
    )
    assert response.status_code == 200 and response.json()["ok"] is False
    assert response.json()["generation"] is None


def test_customer_memory_reset_refuses_incomplete_listing(tmp_path, monkeypatch):
    from interop import memory as memory_mod
    from interop.memory import MemoryEntry, MemoryListing

    class IncompleteMemory:
        async def list_memories(self, trace_id, **kw):
            if kw["category"] == "sys_PROFILE_FACT":
                return MemoryListing((MemoryEntry("fact-1", "Known"),), 2)
            return MemoryListing((), 0)

        async def aclose(self):
            pass

    monkeypatch.setattr(memory_mod, "MemoryClient", lambda **_: IncompleteMemory())
    monkeypatch.setenv("A2ALAB_MEMORY_CUSTOMER_MAP", '{"maya":"profile-maya"}')
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    response = TestClient(app).post(
        "/api/memory/customer/maya", headers=_operator_headers(monkeypatch, tmp_path),
        json={"action": "fresh"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["trace_id"]


def test_customer_memory_panel_has_selection_reset_and_retention_copy():
    html = Path("src/console/static/index.html").read_text()
    assert "Manage memories" in html and "Delete selected" in html
    assert "Start fresh for" in html
    assert "Deletion is permanent" in html
    assert "conversation history is not erased" in html
    assert "Automatic extraction is asynchronous" in html
    assert "20 entries capped into an agent prompt" in html
    assert "memory: normalized retry" in html
    assert "stale_memory_generation" in html
    assert "Fork to a new chat" in html
    assert 'body.memory_generation = chat.memory_generation' in html
    assert 'userTurn.toggles.memory_generation = chat.memory_generation' in html
    assert "delete ${memories.length} selected memories for" in html
    assert "delete ${count} curated memories for" in html
    assert 'memory: "off"' in html
    assert "Regression sweeps are always the memory-off control" in html


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


def test_customer_memory_run_forwards_the_chat_generation_precondition(tmp_path, monkeypatch):
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    registry = FakeRegistry()
    app = make_app(tmp_path / "traces", monkeypatch, registry)
    import console.app as console_app

    monkeypatch.setattr(console_app, "load_scenarios", lambda: {
        "customer-test": {
            "status": "live", "target": "claude-rest", "memory_toggle": True,
            "memory_customer_selector": True, "default_memory_customer": "maya",
        }
    })
    monkeypatch.setattr(console_app, "load_memory_customers", lambda: {"maya": {"name": "Maya"}})
    response = TestClient(app).post(
        "/api/run", headers=_operator_headers(monkeypatch, tmp_path), json={
            "scenario": "customer-test", "message": "continue", "memory": True,
            "memory_customer": "maya", "memory_generation": "v1:old-chat",
        },
    )
    assert response.status_code == 200 and response.json()["ok"] is True
    assert registry.fake_client.requests[-1].metadata["memory_generation"] == "v1:old-chat"


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
    """The two-level Experiments nav: yaml-ordered groups, every scenario
    bucketed into one. A group appears once its platform has a live agent:
    langgraph-agentforce joined when the LangGraph agent went live on Heroku
    (WS4/D77). Its forward cell (langgraph-to-agentforce) is `live` — validated
    end to end 2026-08-19 (ask_agentforce → the D25 twin, traces in Aurora);
    its reverse cell (agentforce-to-langgraph) stays `coming-soon` until Path A
    is cut over to the hosted bridge. CrewAI/AutoGen stay roadmap-only
    (config/whats_next.yaml: other-agent-frameworks) — no live agent, no group.
    No group carries an `upcoming` flag."""
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    client = TestClient(app)
    data = client.get("/api/scenarios").json()
    assert [g["id"] for g in data["groups"]] == [
        "claude-agentforce",
        "openai-agentforce",
        "adk-agentforce",
        "foundry-agentforce",
        "strands-agentforce",
        "langgraph-agentforce",
        "cross-cloud",
        "fan-out",
        "agent-memory",
    ]
    # Every PAIR group reads first — claude/openai/adk/foundry, then
    # strands-agentforce (WS5) and langgraph-agentforce (WS4, Heroku) — then
    # cross-cloud (Cross-hyperscalers), fan-out as the first 1:many group, then
    # Agent Memory as a separate continuity category. No upcoming placeholders.
    assert [bool(g.get("upcoming")) for g in data["groups"]] == ([False] * 9)
    group_ids = {g["id"] for g in data["groups"]}
    for s in data["scenarios"]:
        assert s["group"] in group_ids, s["name"]


def test_customer_memory_experiments_are_a_two_platform_seeded_pair(tmp_path, monkeypatch):
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    scenarios = {
        s["name"]: s for s in TestClient(app).get("/api/scenarios").json()["scenarios"]
    }
    claude = scenarios["customer-memory-claude"]
    openai = scenarios["customer-memory-openai"]
    assert claude["group"] == openai["group"] == "agent-memory"
    assert claude["target"] == "claude-rest" and openai["target"] == "openai-rest"
    assert claude["memory_toggle"] is openai["memory_toggle"] is True
    assert claude["default_memory"] == openai["default_memory"] == "on"
    assert claude["memory_customer_selector"] is openai["memory_customer_selector"] is True
    assert claude["default_memory_customer"] == openai["default_memory_customer"] == "maya"
    assert claude["seed_prompts"] and openai["seed_prompts"]
    assert "do not call Agentforce" in claude["question"]
    assert "create a CRM Case" in openai["question"]
    html = Path("src/console/static/index.html").read_text()
    assert "seedPromptsHtml(exp)" in html and "useSeedPrompt" in html
    assert 'aria-label="Customer persona"' in html
    assert "Data 360 memory supplied to this agent" in html
    assert "function memoryModeFor(key, exp)" in html
    assert 'exp && exp.default_memory === "on" ? "on" : "off"' in html
    assert "memoryRowHtml(key, exp)" in html
    assert "(overrides && overrides.memory) || memoryModeFor(key, spec)" in html
    assert "memory-evidence-lines" in html
    assert "memory-evidence-line" in html
    assert '.split("\\n")' in html
    assert "memory: stored" in html and "memory: recalled" in html
    for scenario in (claude, openai):
        assert "common customer-centered substrate" in scenario["description"]
        assert "customer profile × category" in scenario["description"]
        assert "A2ALab_Memory_Case" in scenario["description"]
    assert "Shared customer memory, platform provenance" in html
    assert "provenance, not storage partitions" in html
    assert "function memoryHopScope(h, personaLabel)" in html
    assert "function memoryScopeMapHtml(hops, personaLabel, participants)" in html
    assert "memory-hop-path" in html
    assert "Unified Individual · shared identity spine" in html
    assert '["Claude", "OpenAI"]' in html
    assert "Shared case memory" in html
    assert "real-time</i> data graphs" in html
    monkeypatch.setenv("SF_MY_DOMAIN", "example.my.salesforce.com")
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    monkeypatch.setenv(
        "CLAUDE_AGENT_CONSOLE_URL",
        "https://platform.claude.com/workspaces/a2alab/agents/customer-memory",
    )
    signed_in = TestClient(make_app(tmp_path / "signed-traces", monkeypatch, FakeRegistry())).get(
        "/api/scenarios", headers={"X-Lab-Token": "sekrit"}
    ).json()
    signed_names = {s["name"]: s for s in signed_in["scenarios"]}
    for name in ("customer-memory-claude", "customer-memory-openai"):
        memory_components = [
            c for c in signed_names[name]["components"] if c["kind"] == "data-360"
        ]
        assert len(memory_components) == 1
        assert memory_components[0]["url"] == (
            "https://example.lightning.force.com/lightning/app/"
            "c__A2A_Lab/n/standard-Agents"
        )

    claude_components = [
        c for c in signed_names["customer-memory-claude"]["components"]
        if c["kind"] == "claude"
    ]
    assert len(claude_components) == 1
    assert claude_components[0]["url"] == (
        "https://platform.claude.com/workspaces/a2alab/agents/customer-memory"
    )

    app_metadata = Path(
        "salesforce/force-app/main/default/applications/A2A_Lab.app-meta.xml"
    ).read_text()
    assert "<navType>Standard</navType>" in app_metadata
    assert "<tabs>A2A_Lab_Traffic</tabs>" in app_metadata
    assert "<tabs>standard-Agents</tabs>" in app_metadata

    # The Details explanation is also in a Lab Guide-readable source, not only
    # frontend HTML: config/scenarios.yaml is an on-demand guide document.
    from platforms.guide import corpus

    assert "config/scenarios.yaml" in corpus.TOOL_DOCS
    guide_source = corpus.read_doc(
        "config/scenarios.yaml"
    )
    assert "customer profile × category" in guide_source
    assert "A2ALab_Memory_Case" in guide_source


def test_config_exposes_safe_customer_personas_without_profile_ids(tmp_path, monkeypatch):
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    customers = TestClient(app).get("/api/config").json()["memory_customers"]
    assert customers["maya"]["name"] == "Maya Chen"
    assert customers["jordan"]["name"] == "Jordan Alvarez"
    assert all("id" not in field.lower() for customer in customers.values() for field in customer)


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
    assert "a2a-native" in data["af_channel"]["routing_blocks"]


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
    # D106: the native channel injects its own block, naming its own tool
    data = client.post(
        "/api/run",
        json={"scenario": "claude-to-agentforce", "message": "hi", "af_channel": "a2a-native"},
    ).json()
    assert data["af_channel"] == "a2a-native"
    msg = registry.fake_client.requests[3].message
    assert "agentforce-channel: a2a-native" in msg and "ask_agentforce_a2a_native" in msg


def test_run_memory_toggle_is_per_run(tmp_path, monkeypatch):
    """D91: memory is a per-run toggle on the experiment, not a lab setting —
    only memory=true on a memory_toggle scenario asks the target for it."""
    registry = FakeRegistry()
    app = make_app(tmp_path / "traces", monkeypatch, registry)
    client = TestClient(app)
    on = client.post(
        "/api/run", json={"scenario": "claude-to-agentforce", "message": "hi", "memory": True}
    ).json()
    assert on["ok"] is True and on["memory"] is True
    assert registry.fake_client.requests[0].metadata.get("memory") is True
    off = client.post("/api/run", json={"scenario": "claude-to-agentforce", "message": "hi"})
    assert off.json()["memory"] is False
    assert "memory" not in registry.fake_client.requests[1].metadata
    # a scenario without the toggle never asks, whatever the body says
    other = client.post(
        "/api/run", json={"scenario": "agentforce-to-claude", "message": "hi", "memory": True}
    ).json()
    assert not other.get("memory")
    assert all(r.metadata.get("memory") is not True for r in registry.fake_client.requests[2:])


def test_customer_memory_run_selects_an_allowlisted_persona(tmp_path, monkeypatch):
    registry = FakeRegistry()
    app = make_app(tmp_path / "traces", monkeypatch, registry)
    client = TestClient(app)
    response = client.post(
        "/api/run",
        json={
            "scenario": "customer-memory-claude",
            "message": "remember this",
            "memory": True,
            "memory_customer": "jordan",
        },
    )
    assert response.status_code == 200
    assert registry.fake_client.requests[-1].metadata["memory_customer"] == "jordan"
    rejected = client.post(
        "/api/run",
        json={
            "scenario": "customer-memory-claude",
            "message": "remember this",
            "memory": True,
            "memory_customer": "unknown",
        },
    )
    assert rejected.status_code == 400


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


def test_warmup_clear_hides_display_but_keeps_the_file(tmp_path, monkeypatch):
    """Clear is a display reset, never a delete: the panel goes blank but every
    recorded row survives in warmups.jsonl (the cold-start comparison + the
    Moirai forecast series). A later warm re-appears above the watermark."""
    trace_dir = tmp_path / "traces"
    app = make_app(trace_dir, monkeypatch, WarmupRegistry())
    client = TestClient(app)

    rec = client.post("/api/warmup/claude-agentcore").json()
    assert client.get("/api/warmup").json()["targets"][0]["last"] == rec

    cleared = client.post("/api/warmup/clear")
    assert cleared.status_code == 200 and cleared.json()["cleared"] >= rec["ts"]

    # Display: every row reset to "never warmed" …
    listed = client.get("/api/warmup").json()["targets"]
    assert all(t["last"] is None and t["history"] == [] for t in listed)
    # … but the raw record is untouched (comparison / forecast data preserved).
    assert json.loads((trace_dir / "warmups.jsonl").read_text().splitlines()[-1]) == rec

    # A fresh warm lands above the watermark and shows again.
    time.sleep(0.005)  # round(ts, 3) is ms; guarantee a strictly-later stamp
    rec2 = client.post("/api/warmup/claude-agentcore").json()
    shown = next(
        t for t in client.get("/api/warmup").json()["targets"] if t["name"] == "claude-agentcore"
    )
    assert shown["last"] == rec2 and rec2 != rec
    # and BOTH attempts remain on disk — clear never truncated anything.
    assert len((trace_dir / "warmups.jsonl").read_text().splitlines()) == 2


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


def test_workstreams_parsed_and_served_for_reference_chips(tmp_path, monkeypatch):
    """Details WS refs open the exact plan section, not the whole workstream file."""
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    workstreams = TestClient(app).get("/api/workstreams").json()["workstreams"]

    assert "WS11" in workstreams and "WS27" in workstreams
    assert workstreams["WS27"]["id"] == "WS27"
    assert "Data 360 Agentic Memory" in workstreams["WS27"]["title"]
    assert "D102" in workstreams["WS27"]["markdown"]
    assert "## WS28" not in workstreams["WS27"]["markdown"]


def test_workstreams_missing_file_empty(tmp_path):
    import console.app as console_app

    assert console_app.load_workstreams(tmp_path / "nope.md") == {}


def test_details_reference_chips_cover_workstreams_and_three_digit_decisions():
    html = Path("src/console/static/index.html").read_text()

    assert "let workstreamLog = {};" in html
    assert 'fetch(apiUrl("/api/workstreams"))' in html
    assert "const SOURCE_REF_RE = /\\b(?:D\\d+|WS\\d+)\\b/;" in html
    assert "decisionLog[part] || workstreamLog[part]" in html
    # The reported Details example exercises both paths: WS11/WS27 and D102.
    assert "(WS27, D102)" in html
    assert "how each leg is called (WS11)" in html


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


def test_supplier_memory_owner_comes_from_verified_user_before_fanout(
    tmp_path, monkeypatch
):
    """D102's CMA branch returns early, so identity must be resolved before it."""
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    monkeypatch.setenv("A2ALAB_MODE", "hosted")
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    captured = {}

    from orchestration.memory import MemoryEvidence
    import orchestration.cma as cma

    class FakeOrchestrator:
        def __init__(self, **kw):
            captured.update(kw)

        async def run(self, message, trace_id):
            memory = MemoryEvidence(mode=captured["memory_run"].mode, reader="coordinator")
            fan = SimpleNamespace(
                ok_count=3,
                results=[1, 2, 3],
                dispatch_summary="",
                gate=None,
                memory=memory,
            )
            return {
                "brief": "brief",
                "trace_id": trace_id,
                "wall_ms": 1,
                "fanout": fan,
                "dispatch_mode": "sync",
            }

    monkeypatch.setattr(cma, "CmaOrchestrator", FakeOrchestrator)
    body = {
        "scenario": "supplier-disruption-cma",
        "message": "strike",
        "variant": "tool",
        "memory_mode": "seed",
        "memory_reader": "coordinator",
        "memory_case": "rotterdam-1",
        "owner_id": "attacker-chosen",
    }
    response = TestClient(app).post(
        "/api/run", json=body, headers=_operator_headers(monkeypatch, tmp_path)
    )
    assert response.status_code == 200, response.text
    run = captured["memory_run"]
    assert run.owner_id == "a2alab-ryan"
    assert run.case_key == "rotterdam-1"
    assert response.json()["memory"]["mode"] == "seed"


def test_supplier_memory_rejects_untrusted_shape_before_agent_work(tmp_path, monkeypatch):
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    monkeypatch.setenv("A2ALAB_MODE", "hosted")
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    headers = _operator_headers(monkeypatch, tmp_path)
    client = TestClient(app)
    base = {
        "scenario": "supplier-disruption-cma",
        "message": "strike",
        "variant": "tool",
        "memory_mode": "seed",
        "memory_case": "bad/slash",
    }
    assert client.post("/api/run", json=base, headers=headers).status_code == 400
    assert client.post(
        "/api/run", json={**base, "memory_case": "case", "variant": "mcp"}, headers=headers
    ).status_code == 409
    assert client.post(
        "/api/run", json={**base, "memory_case": "case", "memory_reader": "everyone"},
        headers=headers,
    ).status_code == 400


def test_supplier_memory_rejects_unsigned_and_machine_callers_before_agent_work(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    monkeypatch.setenv("A2ALAB_MODE", "hosted")
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    body = {
        "scenario": "supplier-disruption-cma",
        "message": "strike",
        "variant": "tool",
        "memory_mode": "seed",
        "memory_case": "case",
    }
    client = TestClient(app)
    assert client.post("/api/run", json=body).status_code in (401, 403)
    assert client.post(
        "/api/run", json=body, headers=_machine_headers(monkeypatch, tmp_path)
    ).status_code == 403


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
        ("/api/briefs/outcomes", "get"),
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
        "/api/workstreams",
        "/api/scenarios",
        "/api/config",
        "/api/traces",  # dummy demo data only — the wire record IS the exhibit
    ):
        r = client.get(path, headers=headers)
        assert r.status_code == 200, f"{path} blocked a viewer: {r.status_code}"


def _machine_headers(monkeypatch, tmp_path):
    """The MuleSoft Omni Gateway's client-credentials identity (WS10 SP1):
    role 'machine' in the real config/users.yaml directory — an attributed
    faces caller, never an operator of this console."""
    from interop import identity

    monkeypatch.setenv(identity.KEY_DIR_ENV, str(tmp_path / "keys"))
    token = identity.issue_service_token("mulesoft-omni-gateway")
    return {"authorization": f"Bearer {token}"}


def test_machine_role_403_on_operator_surfaces(tmp_path, monkeypatch):
    """The bug this fix closes: _viewer_forbidden used to deny ONLY
    role == 'viewer', so the machine gateway token (neither viewer nor
    operator) fell through to the spend-incurring endpoints. It must be
    denied exactly like a viewer."""
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    app = make_app(tmp_path, monkeypatch)
    client = TestClient(app)
    headers = _machine_headers(monkeypatch, tmp_path)
    r = client.post("/api/run", headers=headers, json={})
    assert r.status_code == 403, f"machine role let through: {r.status_code}"
    assert "operator-only" in r.json()["detail"]


def test_operator_role_reaches_operator_surfaces(tmp_path, monkeypatch):
    from interop import identity

    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    registry = FakeRegistry()
    app = make_app(tmp_path / "traces", monkeypatch, registry)
    client = TestClient(app)
    monkeypatch.setenv(identity.KEY_DIR_ENV, str(tmp_path / "keys"))
    # ana holds role 'operator' in the real config/users.yaml directory.
    headers = {"authorization": f"Bearer {identity.issue_token('ana')}"}
    r = client.post("/api/run", headers=headers, json={"target": "claude-rest", "message": "hi"})
    assert r.status_code != 403, f"operator role blocked: {r.status_code}"
    assert r.json()["ok"] is True


def test_brief_outcomes_lists_failed_first_and_hides_nothing(tmp_path, monkeypatch):
    """A4/F07: the console's delivery-outcome reader surfaces terminal-`failed`
    sessions first (action needed), carries `last_error`, and still counts the
    delivered/pending ones in the summary."""
    import briefs.__main__ as watcher
    from observability.pg import PgClient

    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    monkeypatch.setattr(PgClient, "configured", classmethod(lambda cls: False))
    app = make_app(tmp_path / "traces", monkeypatch)
    client = TestClient(app)

    ledger_file = tmp_path / "brief_state.json"
    ledger_file.write_text(
        json.dumps(
            {
                "sessions": {
                    "s_ok": {
                        "status": "delivered",
                        "attempts": 1,
                        "at": "2026-09-09T01:00:00",
                        "calls": {"t1": {"status": "ok"}},
                    },
                    "s_bad": {
                        "status": "failed",
                        "attempts": 3,
                        "at": "2026-09-09T02:00:00",
                        "last_error": "Salesforce 503",
                        "calls": {"t1": {"status": "failed"}},
                    },
                    "s_wait": {
                        "status": "pending",
                        "attempts": 1,
                        "at": "2026-09-09T03:00:00",
                        "calls": {},
                    },
                }
            }
        )
    )
    monkeypatch.setattr(watcher, "WATCH_STATE", ledger_file)

    headers = _operator_headers(monkeypatch, tmp_path)
    r = client.get("/api/briefs/outcomes", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["summary"] == {"delivered": 1, "pending": 1, "failed": 1}
    assert [s["session_id"] for s in data["sessions"]] == ["s_bad", "s_wait", "s_ok"]
    assert data["sessions"][0]["last_error"] == "Salesforce 503"


def test_brief_outcomes_empty_state_explains_itself(tmp_path, monkeypatch):
    import briefs.__main__ as watcher
    from observability.pg import PgClient

    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    monkeypatch.setattr(PgClient, "configured", classmethod(lambda cls: False))
    monkeypatch.setattr(watcher, "WATCH_STATE", tmp_path / "nope.json")
    app = make_app(tmp_path / "traces", monkeypatch)
    client = TestClient(app)
    headers = _operator_headers(monkeypatch, tmp_path)
    r = client.get("/api/briefs/outcomes", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["sessions"] == []
    assert "watcher" in body["note"].lower()


def test_brief_outcomes_distinguishes_a_store_outage_from_empty(tmp_path, monkeypatch):
    """A configured Aurora store that will not answer is an OUTAGE, not an empty
    ledger. The route must return `error` (never `note`) and an empty summary, so
    the console shows a broken hosted watcher rather than a reassuring 0/0/0
    (Codex WS25-b3 rd1, A4/F07)."""
    import observability.pg as pg

    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    monkeypatch.setattr(pg.PgClient, "configured", classmethod(lambda cls: True))

    class BrokenStore:
        def __init__(self, *a, **k):
            pass

        def get_state(self, key):
            raise RuntimeError("connection refused")

        def close(self):
            pass

    monkeypatch.setattr(pg, "PgObsStore", BrokenStore)
    app = make_app(tmp_path / "traces", monkeypatch)
    client = TestClient(app)
    headers = _operator_headers(monkeypatch, tmp_path)
    r = client.get("/api/briefs/outcomes", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sessions"] == []
    assert body["summary"] == {"delivered": 0, "pending": 0, "failed": 0}
    assert "note" not in body  # not the empty-ledger message
    assert "outage" in body.get("error", "").lower()
    assert "connection refused" in body["error"]


def test_shared_service_token_still_reaches_operator_surfaces(tmp_path, monkeypatch):
    """The header-borne shared token (no persona) is the operator's own
    legacy credential and must stay allowed on the operator-only paths."""
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    registry = FakeRegistry()
    app = make_app(tmp_path / "traces", monkeypatch, registry)
    client = TestClient(app)
    r = client.post(
        "/api/run",
        headers={"x-lab-token": "sekrit"},
        json={"target": "claude-rest", "message": "hi"},
    )
    assert r.status_code != 403
    assert r.json()["ok"] is True


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
    for path in (
        "/api/scenarios",
        "/api/targets",
        "/api/decisions",
        "/api/workstreams",
        "/api/users",
    ):
        assert client.get(path).status_code == 200, path
    assert client.get("/api/docs/plan/02-matrix.md").status_code == 200
    assert client.get("/api/docs/docs/lab-guide-mcp.md").status_code == 200
    for path in ("/api/traces", "/api/insights", "/api/obs/sessions", "/api/config"):
        assert client.get(path).status_code == 401, path


def test_users_picker_omits_machine_principals(tmp_path, monkeypatch):
    # WS10 SP1: the sign-in picker (/api/users) must not list machine
    # principals (e.g. the MuleSoft Omni Gateway) — they have no console
    # password and authenticate only via /oauth/token, so showing one reads
    # as a login with a missing password.
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    app = make_app(tmp_path, monkeypatch)
    client = TestClient(app)
    users = client.get("/api/users").json()["users"]
    roles = {u["role"] for u in users}
    assert "machine" not in roles
    assert "mulesoft-omni-gateway" not in {u["username"] for u in users}
    # Test principals marked `listed: false` are not personas either.
    assert "memprobe" not in {u["username"] for u in users}
    # The human personas are still listed.
    assert {"ryan", "ana", "vic"} <= {u["username"] for u in users}


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
    must be servable (build-notes was referenced by insights and 404'd). Since
    cf88743 the two SOURCE exhibits — config/*.yaml (the lab's own inputs) and
    .claude/workflows/*.js (the WS20 workflow exhibit) — are also served as
    public repo content, so a raw-source chip renders instead of 404ing."""
    app = make_app(tmp_path / "traces", monkeypatch, FakeRegistry())
    client = TestClient(app)
    for path in (
        "README.md",
        "plan/09-deployment-map.md",
        "docs/lab-guide-mcp.md",
        "build-notes/claude/04-claude-code-environment.md",
        # public source exhibits — config inputs and the workflow scripts
        "config/targets.yaml",
    ):
        assert client.get(f"/api/docs/{path}").status_code == 200, path

    # Still a whitelist, not a file server: application source and secrets stay
    # closed, and .claude is scoped to workflows/*.js so settings never leak.
    for path in ("src/console/app.py", ".env", ".claude/settings.local.json"):
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


# ---- WS9: /log forward auth (apikey default, opt-in SigV4) ------------------


def test_logger_headers_apikey_default(monkeypatch):
    """Default mode is the unchanged X-Api-Key behaviour: with a key set, the
    forward carries it; with no key it returns None (skip, not an error)."""
    from console.app import _logger_request_headers

    monkeypatch.delenv("A2ALAB_LOGGING_AUTH", raising=False)
    monkeypatch.setenv("A2ALAB_LOGGING_API_KEY", "sekrit-key")
    url = "https://abc123.execute-api.us-west-2.amazonaws.com/prod/log"

    headers = _logger_request_headers(url, b'{"x":1}')
    assert headers == {"Content-Type": "application/json", "X-Api-Key": "sekrit-key"}

    monkeypatch.delenv("A2ALAB_LOGGING_API_KEY", raising=False)
    assert _logger_request_headers(url, b'{"x":1}') is None


def test_logger_headers_iam_signs_and_parses_region(monkeypatch):
    """A2ALAB_LOGGING_AUTH=iam SigV4-signs for execute-api with the caller's AWS
    session — no X-Api-Key, and the region is parsed from the host when not set
    explicitly (the logger lives in a different region than the console)."""
    pytest.importorskip("botocore")
    from console.app import _logger_request_headers

    monkeypatch.setenv("A2ALAB_LOGGING_AUTH", "iam")
    # Env creds beat any profile in botocore's default chain -> deterministic.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("A2ALAB_LOGGING_REGION", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("A2ALAB_LOGGING_API_KEY", raising=False)

    url = "https://abc123.execute-api.us-west-2.amazonaws.com/prod/log"
    headers = _logger_request_headers(url, b'{"x":1}')

    assert headers is not None
    assert "X-Api-Key" not in headers
    assert headers["Authorization"].startswith("AWS4-HMAC-SHA256")
    # Region parsed from the host is what the signature is scoped to.
    assert "/us-west-2/execute-api/aws4_request" in headers["Authorization"]


def test_logger_headers_iam_no_creds_returns_none(monkeypatch):
    """iam mode with no resolvable AWS credentials skips the forward (None),
    the same fail-quiet contract as a missing API key."""
    pytest.importorskip("botocore")
    import botocore.session

    from console.app import _logger_request_headers

    monkeypatch.setenv("A2ALAB_LOGGING_AUTH", "iam")
    monkeypatch.setenv("A2ALAB_LOGGING_REGION", "us-west-2")
    monkeypatch.setattr(
        botocore.session,
        "get_session",
        lambda: SimpleNamespace(get_credentials=lambda: None),
    )

    url = "https://abc123.execute-api.us-west-2.amazonaws.com/prod/log"
    assert _logger_request_headers(url, b'{"x":1}') is None


# ---- WS23: durable experiment->session map (Session Trace picker) ----------


def test_otel_session_picker_prefers_recorded_labels(tmp_path, monkeypatch):
    """With lab-recorded Agentforce sessions present, the picker lists THOSE
    (labeled by experiment, newest-first) — not the raw org DMO."""
    monkeypatch.setenv("A2ALAB_OBS_STORE", "sqlite")
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    from observability.store import ObsStore

    store = ObsStore(db_path=trace_dir / "lab.db")
    store.upsert_session(
        "salesforce-otel",
        "0199-older",
        title="Supplier Disruption — orchestrated by Agentforce · serial · async",
        status="recorded",
        created_at="2026-08-13T12:00:00+00:00",
    )
    store.upsert_session(
        "salesforce-otel",
        "0199-newer",
        title="Supplier Disruption — orchestrated by Agentforce · delegated · async",
        status="recorded",
        created_at="2026-08-13T14:00:00+00:00",
    )
    store.close()

    app = make_app(trace_dir, monkeypatch)
    client = TestClient(app)
    d = client.get("/api/obs/otel-sessions").json()
    assert d["sessions"][0] == "0199-newer"  # newest first
    assert set(d["sessions"]) == {"0199-newer", "0199-older"}
    assert d["labels"]["0199-newer"]["label"].endswith("delegated · async")
    assert d["labels"]["0199-newer"]["ts"] > d["labels"]["0199-older"]["ts"]
    assert "recorded by lab runs" in d["detail"]


def test_agentforce_run_records_labeled_otel_session(tmp_path, monkeypatch):
    """An Agentforce-primary run persists its OTel-eligible session id + a label,
    so the picker names it afterwards (the client-side map is per-visit only)."""
    monkeypatch.setenv("A2ALAB_OBS_STORE", "sqlite")
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()

    class _AFClient(RemoteAgentClient):
        protocol = "rest"

        async def ask(self, req: AgentRequest) -> AgentResponse:
            return AgentResponse(
                text="ok",
                session_id=req.session_id,
                latency_ms=9,
                metadata={"agent_session_id": "af-session-uuid7"},
            )

    class _AFRegistry(Registry):
        def __init__(self):
            super().__init__(
                {
                    "agentforce-orchestrator-rest": Target(
                        name="agentforce-orchestrator-rest",
                        platform="agentforce",
                        protocol="rest",
                        status="native",
                    )
                }
            )
            self._c = _AFClient()

        def client_for(self, name):
            return self._c

    app = make_app(trace_dir, monkeypatch, _AFRegistry())
    client = TestClient(app)
    r = client.post(
        "/api/run", json={"target": "agentforce-orchestrator-rest", "message": "hi"}
    ).json()
    assert r["ok"] is True
    assert r["session_agent_id"] == "af-session-uuid7"

    d = client.get("/api/obs/otel-sessions").json()
    assert "af-session-uuid7" in d["sessions"]
    assert d["labels"]["af-session-uuid7"]["label"]  # named, not blank


def test_submit_poll_scenarios_target_async_capable_face():
    """Config invariant: any scenario the console drives fire-then-poll
    (console_dispatch: submit_poll) must name a target that resolves to an
    async-capable client — i.e. an A2A face, the only client exposing
    submit()/poll(). A submit_poll scenario pointed at a REST/MCP face would
    500 on the first /api/run because get_client(name).submit does not exist.
    Guards the WS4 langgraph-to-agentforce + WS11 ADK orchestrator wiring."""
    import console.app as console_app
    from interop.clients.a2a import A2AClient

    # submit()/poll() live only on the A2A client.
    assert callable(getattr(A2AClient, "submit", None))
    assert callable(getattr(A2AClient, "poll", None))

    scenarios = console_app.load_scenarios()
    reg = Registry.load()
    submit_poll = {
        name: spec
        for name, spec in scenarios.items()
        if spec.get("console_dispatch") == "submit_poll"
    }
    # The invariant is only meaningful if such scenarios exist.
    assert submit_poll, "expected at least one submit_poll scenario (ADK, langgraph)"
    for name, spec in submit_poll.items():
        target_name = spec["target"]
        target = reg.get(target_name)  # exact, pre-remap: the named face
        assert target.protocol == "a2a", (
            f"scenario '{name}' is console_dispatch: submit_poll but its target "
            f"'{target_name}' speaks '{target.protocol}', not a2a — the console "
            f"would call submit() on a client that has no such method"
        )


# ---- WS25 A2 (D80): deletion is not viewing --------------------------------


def test_viewer_403_on_delete_traces_but_can_still_read_them(tmp_path, monkeypatch):
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    app = make_app(tmp_path, monkeypatch)
    client = TestClient(app)
    headers = _viewer_headers(monkeypatch, tmp_path)
    assert client.get("/api/traces", headers=headers).status_code == 200
    r = client.delete("/api/traces", headers=headers)
    assert r.status_code == 403, f"a viewer deleted the evidence archive: {r.status_code}"
    assert "operator-only" in r.json()["detail"]


def test_operator_can_delete_traces(tmp_path, monkeypatch):
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    app = make_app(tmp_path, monkeypatch)
    client = TestClient(app)
    headers = _operator_headers(monkeypatch, tmp_path)
    assert client.delete("/api/traces", headers=headers).status_code == 200
