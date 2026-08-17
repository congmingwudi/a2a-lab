"""Idempotency backstop for the cost-sentinel control-plane setup.

Same fix and same rationale as tests/unit/test_setup_obs_analyst.py — this
script mirrored the analyst's non-idempotent create path (keyed off a
laptop-local STATE_FILE only, so an absent file spawned a duplicate agent).
These tests pin the guard: refuse on a same-named collision, and --recreate
archives the prior agent + deployment before creating. Fully hermetic — the
Anthropic client (agents, vaults, deployments) is faked, no live credentials.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "setup_cost_sentinel.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("setup_cost_sentinel", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeAgent:
    def __init__(self, id_, name, version=1, archived_at=None):
        self.id = id_
        self.name = name
        self.version = version
        self.archived_at = archived_at


class _FakeAgents:
    def __init__(self, listing):
        self._listing = listing
        self.archived: list[str] = []
        self.created: list[dict] = []

    def list(self):
        return list(self._listing)

    def archive(self, agent_id):
        self.archived.append(agent_id)

    def create(self, **kwargs):
        self.created.append(kwargs)
        return _FakeAgent(f"agent_new_{len(self.created)}", kwargs["name"])


class _FakeCredentials:
    def create(self, **kwargs):
        return _Obj(id="cred_new")


class _FakeVaults:
    def __init__(self):
        self.credentials = _FakeCredentials()

    def create(self, **kwargs):
        return _Obj(id="vault_new")


class _FakeDeployments:
    def __init__(self):
        self.archived: list[str] = []
        self.created: list[dict] = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return _Obj(id="dep_new", schedule=None)

    def pause(self, deployment_id):
        pass

    def archive(self, deployment_id):
        self.archived.append(deployment_id)


class _FakeBeta:
    def __init__(self, listing):
        self.agents = _FakeAgents(listing)
        self.vaults = _FakeVaults()
        self.deployments = _FakeDeployments()


class _FakeClient:
    def __init__(self, listing):
        self.beta = _FakeBeta(listing)


@pytest.fixture
def mod(monkeypatch, tmp_path):
    m = _load_module()
    monkeypatch.setattr(m, "STATE_DIR", tmp_path)
    monkeypatch.setattr(m, "STATE_FILE", tmp_path / "cost_sentinel.json")
    managed = tmp_path / "managed.json"
    managed.write_text(json.dumps({"environment_id": "env_1"}))
    monkeypatch.setattr(m, "MANAGED_FILE", managed)
    mcp = tmp_path / "obs_mcp.json"
    mcp.write_text(json.dumps({"url": "https://mcp.example/", "token": "tok"}))
    monkeypatch.setattr(m, "MCP_FILE", mcp)
    return m


def _run_main(monkeypatch, mod, client, argv):
    monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: client)
    monkeypatch.setattr("sys.argv", argv)
    return mod.main()


def test_agents_named_filters_by_name_and_skips_archived(mod):
    listing = [
        _FakeAgent("a1", mod.AGENT_NAME),
        _FakeAgent("a2", "Some Other Agent"),
        _FakeAgent("a3", mod.AGENT_NAME, archived_at="2026-08-13T00:00:00Z"),
    ]
    found = mod._agents_named(_FakeClient(listing), mod.AGENT_NAME)
    assert [a.id for a in found] == ["a1"]


def test_refuses_duplicate_when_state_file_missing(monkeypatch, mod, capsys):
    client = _FakeClient([_FakeAgent("a_existing", mod.AGENT_NAME)])
    assert not mod.STATE_FILE.exists()

    rc = _run_main(monkeypatch, mod, client, ["setup_cost_sentinel.py"])

    assert rc == 1
    assert client.beta.agents.created == []
    assert client.beta.agents.archived == []
    assert "refusing to create a duplicate" in capsys.readouterr().out


def test_recreate_archives_prior_agent_and_deployment(monkeypatch, mod):
    mod.STATE_FILE.write_text(json.dumps({"deployment_id": "dep_old", "agent_id": "a_old"}))
    client = _FakeClient(
        [_FakeAgent("a_old", mod.AGENT_NAME), _FakeAgent("a_orphan", mod.AGENT_NAME)]
    )

    rc = _run_main(monkeypatch, mod, client, ["setup_cost_sentinel.py", "--recreate"])

    assert rc == 0
    assert client.beta.deployments.archived == ["dep_old"]
    assert set(client.beta.agents.archived) == {"a_old", "a_orphan"}
    assert len(client.beta.agents.created) == 1
    assert json.loads(mod.STATE_FILE.read_text())["agent_id"].startswith("agent_new_")


def test_creates_when_nothing_exists(monkeypatch, mod):
    client = _FakeClient([_FakeAgent("unrelated", "Some Other Agent")])
    assert not mod.STATE_FILE.exists()

    rc = _run_main(monkeypatch, mod, client, ["setup_cost_sentinel.py"])

    assert rc == 0
    assert len(client.beta.agents.created) == 1
    assert client.beta.agents.archived == []
    assert mod.STATE_FILE.exists()
