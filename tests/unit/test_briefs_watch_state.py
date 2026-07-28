"""The brief watcher's two container-hostile dependencies (WS13 item 3).

The watcher services scheduled brief sessions that stall awaiting a host-side
Salesforce write. It ran on the operator's laptop and read two files from
`.a2alab/` — the provisioned ids, and the set of sessions it had already
serviced. A container has neither, and the second one is the dangerous one: it
is what stops a brief being delivered twice.
"""

from __future__ import annotations

import json

import pytest


def test_deployment_id_comes_from_the_environment_when_set(monkeypatch, tmp_path):
    """A hosted watcher has no `.a2alab/brief.json`. The ids are configuration,
    not secrets, so the environment supplies them — same shape as
    CLAUDE_MANAGED_AGENT_ID for the Claude backend."""
    from briefs import runner

    monkeypatch.setattr(runner, "STATE_FILE", tmp_path / "brief.json")
    monkeypatch.setenv("A2ALAB_BRIEF_DEPLOYMENT_ID", "depl_123")
    monkeypatch.setenv("A2ALAB_BRIEF_AGENT_ID", "agent_456")
    ids = runner.load_brief_ids()
    assert ids["deployment_id"] == "depl_123"
    assert ids["agent_id"] == "agent_456"


def test_the_env_merges_over_the_file_rather_than_replacing_it(monkeypatch, tmp_path):
    """Locally the file also carries accounts/cron/model, which the watcher and
    the console both read. Overriding the ids must not throw those away."""
    from briefs import runner

    state = tmp_path / "brief.json"
    state.write_text(
        json.dumps({"deployment_id": "old", "accounts": "Omega, Inc.", "cron": "0 7 * * *"})
    )
    monkeypatch.setattr(runner, "STATE_FILE", state)
    monkeypatch.setenv("A2ALAB_BRIEF_DEPLOYMENT_ID", "new")
    ids = runner.load_brief_ids()
    assert ids["deployment_id"] == "new"
    assert ids["accounts"] == "Omega, Inc."


def test_missing_ids_name_the_hosted_route_too(monkeypatch, tmp_path):
    from briefs import runner

    monkeypatch.setattr(runner, "STATE_FILE", tmp_path / "nope.json")
    monkeypatch.delenv("A2ALAB_BRIEF_DEPLOYMENT_ID", raising=False)
    with pytest.raises(RuntimeError, match="A2ALAB_BRIEF_DEPLOYMENT_ID"):
        runner.load_brief_ids()


def test_serviced_sessions_round_trip_through_the_hosted_store(monkeypatch):
    """THE important one. The serviced set is what stops a brief being
    delivered twice. Written to a container filesystem it dies with the task,
    and the next poll re-delivers every brief still listed in recent deployment
    runs — duplicate A2ALab_Account_Brief__c records in a production org, which
    is worse than missing one."""
    import briefs.__main__ as watcher

    saved: dict = {}

    class FakeStore:
        def get_state(self, key):
            return saved.get(key)

        def put_state(self, key, payload):
            saved[key] = payload

        def close(self):
            pass

    monkeypatch.setattr(watcher, "_state_store", lambda: FakeStore())
    watcher._save_serviced({"sesn_a", "sesn_b"})
    assert set(saved["brief_serviced_sessions"]["serviced_sessions"]) == {"sesn_a", "sesn_b"}
    assert watcher._load_serviced() == {"sesn_a", "sesn_b"}


def test_serviced_sessions_still_use_the_file_with_no_store(monkeypatch, tmp_path):
    """A laptop with no Aurora keeps working exactly as before."""
    import briefs.__main__ as watcher

    monkeypatch.setattr(watcher, "_state_store", lambda: None)
    monkeypatch.setattr(watcher, "WATCH_STATE", tmp_path / "brief_state.json")
    watcher._save_serviced({"sesn_x"})
    assert watcher._load_serviced() == {"sesn_x"}


def test_a_failing_state_write_is_not_swallowed(monkeypatch):
    """If the write is lost the next poll re-delivers briefs that already
    landed. That must surface, not be absorbed like a read would be."""
    import briefs.__main__ as watcher

    class BrokenStore:
        def get_state(self, key):
            return None

        def put_state(self, key, payload):
            raise RuntimeError("aurora is asleep")

        def close(self):
            pass

    monkeypatch.setattr(watcher, "_state_store", lambda: BrokenStore())
    with pytest.raises(RuntimeError, match="aurora is asleep"):
        watcher._save_serviced({"sesn_a"})


def test_the_serviced_set_stays_bounded(monkeypatch):
    """Old sessions cannot reappear in recent runs, so the set is capped — it
    lives in one lab_state row and must not grow without limit."""
    import briefs.__main__ as watcher

    saved: dict = {}

    class FakeStore:
        def get_state(self, key):
            return saved.get(key)

        def put_state(self, key, payload):
            saved[key] = payload

        def close(self):
            pass

    monkeypatch.setattr(watcher, "_state_store", lambda: FakeStore())
    watcher._save_serviced({f"sesn_{i:04d}" for i in range(900)})
    assert len(saved["brief_serviced_sessions"]["serviced_sessions"]) == 500
