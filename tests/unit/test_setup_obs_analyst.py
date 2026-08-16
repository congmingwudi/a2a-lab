"""Idempotency backstop for the obs-analyst control-plane setup.

The script used to key "already provisioned?" off a laptop-local STATE_FILE
only, so an absent/lost state file spawned a DUPLICATE agent — the orphans that
had to be archived by hand on 2026-08-13. These tests pin the fix: the create
path asks the workspace by name before creating, refuses on a collision, and
--recreate archives the prior agent + its deployment instead of orphaning them.
Fully hermetic — the Anthropic client is faked, no live credentials.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "setup_obs_analyst.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("setup_obs_analyst", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


class _FakeDeployments:
    def __init__(self):
        self.archived: list[str] = []

    def archive(self, deployment_id):
        self.archived.append(deployment_id)


class _FakeBeta:
    def __init__(self, listing):
        self.agents = _FakeAgents(listing)
        self.deployments = _FakeDeployments()


class _FakeClient:
    def __init__(self, listing):
        self.beta = _FakeBeta(listing)


@pytest.fixture
def mod(monkeypatch, tmp_path):
    m = _load_module()
    # Point every laptop-local file at the temp dir.
    monkeypatch.setattr(m, "STATE_DIR", tmp_path)
    monkeypatch.setattr(m, "STATE_FILE", tmp_path / "obs_analyst.json")
    managed = tmp_path / "managed.json"
    managed.write_text(json.dumps({"environment_id": "env_1"}))
    monkeypatch.setattr(m, "MANAGED_FILE", managed)
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
    """State file absent but an identically-named agent already exists on the
    workspace -> refuse (exit 1), never create."""
    client = _FakeClient([_FakeAgent("a_existing", mod.AGENT_NAME)])
    assert not mod.STATE_FILE.exists()

    rc = _run_main(monkeypatch, mod, client, ["setup_obs_analyst.py", "--local"])

    assert rc == 1
    assert client.beta.agents.created == []  # the whole point: no duplicate
    assert client.beta.agents.archived == []
    assert "refusing to create a duplicate" in capsys.readouterr().out


def test_recreate_archives_prior_agent_and_deployment(monkeypatch, mod):
    """--recreate replaces rather than orphans: the outgoing deployment (from
    the state file) and every same-named agent (from the workspace, catching
    orphans too) are archived before the new one is created."""
    mod.STATE_FILE.write_text(json.dumps({"deployment_id": "dep_old", "agent_id": "a_old"}))
    client = _FakeClient(
        [_FakeAgent("a_old", mod.AGENT_NAME), _FakeAgent("a_orphan", mod.AGENT_NAME)]
    )

    rc = _run_main(monkeypatch, mod, client, ["setup_obs_analyst.py", "--recreate", "--local"])

    assert rc == 0
    assert client.beta.deployments.archived == ["dep_old"]
    assert set(client.beta.agents.archived) == {"a_old", "a_orphan"}
    assert len(client.beta.agents.created) == 1  # exactly one fresh agent
    # State file rewritten with the new agent's id.
    assert json.loads(mod.STATE_FILE.read_text())["agent_id"].startswith("agent_new_")


def test_creates_when_nothing_exists(monkeypatch, mod):
    """The clean first-run path is unchanged: no state file, no same-named agent
    -> create exactly one."""
    client = _FakeClient([_FakeAgent("unrelated", "Some Other Agent")])
    assert not mod.STATE_FILE.exists()

    rc = _run_main(monkeypatch, mod, client, ["setup_obs_analyst.py", "--local"])

    assert rc == 0
    assert len(client.beta.agents.created) == 1
    assert client.beta.agents.archived == []
    assert mod.STATE_FILE.exists()
