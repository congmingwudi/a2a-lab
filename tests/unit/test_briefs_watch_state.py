"""The brief watcher's outcome ledger (WS13 item 3 + WS25 b3 / A4·F07·D81).

The watcher services scheduled brief sessions that stall awaiting a host-side
Salesforce write. It ran on the operator's laptop and read two files from
`.a2alab/` — the provisioned ids, and the record of what it had serviced. A
container has neither, and the second is the dangerous one: it is what stops a
brief being delivered twice AND what lets a *failed* delivery be retried.

Before b3 that record was an id-only set and a session was marked serviced
unconditionally — so a failed Salesforce write was never retried, and there was
no terminal-failure state. b3 replaces it with a per-session outcome ledger
carrying `status`/`attempts`/`calls` (the per-call delivery ledger, keyed by
tool_use_id), migrating the old list to `delivered` entries deterministically.
"""

from __future__ import annotations

import json

import pytest


# --- provisioned ids (unchanged from WS13) ----------------------------------


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


# --- the outcome ledger: persistence -----------------------------------------


def _ok_entry(calls=None, **kw):
    entry = {
        "status": "delivered",
        "attempts": 1,
        "at": "2026-09-09T00:00:00",
        "calls": calls or {},
    }
    entry.update(kw)
    return entry


def test_ledger_round_trips_through_the_hosted_store(monkeypatch):
    """THE important one. The ledger is what stops a brief being delivered twice
    and what lets a failed one be retried. Written to a container filesystem it
    dies with the task, and the next poll re-delivers every brief still listed in
    recent deployment runs — duplicate A2ALab_Account_Brief__c records in a
    production org, which is worse than missing one."""
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
    ledger = {"sesn_a": _ok_entry(), "sesn_b": _ok_entry()}
    watcher._save_ledger(ledger)
    assert set(saved[watcher.STATE_KEY]["sessions"]) == {"sesn_a", "sesn_b"}
    assert set(watcher._load_ledger()) == {"sesn_a", "sesn_b"}


def test_ledger_still_uses_the_file_with_no_store(monkeypatch, tmp_path):
    """A laptop with no Aurora keeps working exactly as before."""
    import briefs.__main__ as watcher

    monkeypatch.setattr(watcher, "_state_store", lambda: None)
    monkeypatch.setattr(watcher, "WATCH_STATE", tmp_path / "brief_state.json")
    watcher._save_ledger({"sesn_x": _ok_entry()})
    assert set(watcher._load_ledger()) == {"sesn_x"}


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
        watcher._save_ledger({"sesn_a": _ok_entry()})


def test_the_ledger_stays_bounded_evicting_oldest_first(monkeypatch):
    """One lab_state row, so the ledger is capped. Eviction is deterministic —
    by (at or ordinal) — so mixed legacy(no-`at`)/new records evict oldest
    first and the newest 500 survive."""
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
    # 900 timestamped entries with ascending `at`; only the newest 500 survive.
    ledger = {
        f"sesn_{i:04d}": _ok_entry(at=f"2026-09-09T00:{i // 60:02d}:{i % 60:02d}")
        for i in range(900)
    }
    watcher._save_ledger(ledger)
    kept = set(saved[watcher.STATE_KEY]["sessions"])
    assert len(kept) == 500
    assert "sesn_0899" in kept  # newest survives
    assert "sesn_0000" not in kept  # oldest evicted


# --- migration + terminal states ---------------------------------------------


def test_legacy_id_list_migrates_to_delivered_and_is_skipped(monkeypatch):
    """The old id-only `serviced_sessions` list must not be re-serviced. Each id
    loads as `delivered`, attempts=1, so the watcher's skip test passes."""
    import briefs.__main__ as watcher

    saved = {watcher.STATE_KEY: {"serviced_sessions": ["old_a", "old_b"]}}

    class FakeStore:
        def get_state(self, key):
            return saved.get(key)

        def put_state(self, key, payload):
            saved[key] = payload

        def close(self):
            pass

    monkeypatch.setattr(watcher, "_state_store", lambda: FakeStore())
    ledger = watcher._load_ledger()
    assert ledger["old_a"]["status"] == "delivered"
    assert ledger["old_b"]["status"] == "delivered"
    assert watcher._is_terminal(ledger["old_a"])  # skipped, never re-serviced


def test_legacy_entries_evict_before_timestamped_ones(monkeypatch):
    """Legacy entries carry no `at`; an ascending ordinal from list position
    gives them a defined order, and they rank below any timestamped entry so a
    freshly serviced session is never evicted in favour of a migrated one."""
    import briefs.__main__ as watcher

    legacy = watcher._ledger_from_payload({"serviced_sessions": [f"old_{i}" for i in range(600)]})
    legacy["fresh"] = _ok_entry(at="2026-09-09T12:00:00")
    bounded = watcher._bound(legacy, cap=500)
    assert "fresh" in bounded  # the timestamped survivor is never evicted
    assert "old_0" not in bounded  # the oldest legacy id goes first


# --- the per-tick decision ---------------------------------------------------


def _entry(calls, attempts=1, last_error=None):
    return {"attempts": attempts, "calls": calls, "last_error": last_error}


def test_status_delivered_only_when_every_call_is_ok():
    import briefs.__main__ as watcher

    calls = {"t1": {"status": "ok"}, "t2": {"status": "ok"}}
    assert watcher._session_status(_entry(calls), max_attempts=3) == "delivered"


def test_status_pending_while_a_call_failed_and_attempts_remain():
    import briefs.__main__ as watcher

    calls = {"t1": {"status": "ok"}, "t2": {"status": "failed"}}
    assert watcher._session_status(_entry(calls, attempts=1), max_attempts=3) == "pending"


def test_status_failed_terminal_once_attempts_hit_the_cap():
    import briefs.__main__ as watcher

    calls = {"t1": {"status": "failed"}}
    assert watcher._session_status(_entry(calls, attempts=3), max_attempts=3) == "failed"


def test_a_zero_save_session_is_never_marked_delivered():
    """No save calls and no error is anomalous, not success — it must retry, not
    be silently serviced (the old unconditional-serviced bug)."""
    import briefs.__main__ as watcher

    assert watcher._session_status(_entry({}, attempts=1), max_attempts=3) == "pending"
    # ...but it still goes terminal at the cap rather than looping forever.
    assert watcher._session_status(_entry({}, attempts=3), max_attempts=3) == "failed"


def test_a_poll_error_keeps_the_session_pending_then_terminal():
    import briefs.__main__ as watcher

    err = _entry({}, attempts=1, last_error="managed session error")
    assert watcher._session_status(err, max_attempts=3) == "pending"
    err_capped = _entry({}, attempts=3, last_error="managed session error")
    assert watcher._session_status(err_capped, max_attempts=3) == "failed"


# --- requeue: re-arm terminal failures once the cause is fixed (2026-11-04) ---


def test_requeue_rearms_only_failed_sessions_and_keeps_their_calls():
    import briefs.__main__ as watcher

    ok_call = {"t0": {"status": "ok", "brief_id": "a01"}}
    ledger = {
        "s_failed": {"status": "failed", "attempts": 3,
                     "calls": {**ok_call, "t1": {"status": "failed"}}},
        "s_done": {"status": "delivered", "attempts": 1, "calls": ok_call},
        "s_pending": {"status": "pending", "attempts": 1, "calls": {}},
    }  # fmt: skip
    out, requeued = watcher.requeue_failed(ledger)
    assert requeued == ["s_failed"]
    assert out["s_failed"]["status"] == "pending" and out["s_failed"]["attempts"] == 0
    # the ok call survives, so the replay never re-writes a brief that landed
    assert out["s_failed"]["calls"]["t0"]["status"] == "ok"
    assert out["s_done"] == ledger["s_done"] and out["s_pending"] == ledger["s_pending"]
    assert ledger["s_failed"]["status"] == "failed"  # input not mutated


async def test_watch_rereads_the_ledger_every_tick(monkeypatch):
    """A requeue written to the store between ticks must be seen — a ledger
    loaded once at boot would skip the session as terminal forever and then
    write the stale copy back over the requeue."""
    import sys
    import types

    import briefs.__main__ as watcher

    loads = []
    states = iter([{"s1": {"status": "failed", "attempts": 3, "calls": {}}},
                   {"s1": {"status": "pending", "attempts": 0, "calls": {}}}])  # fmt: skip

    def fake_load():
        loads.append(1)
        return next(states)

    serviced = []

    async def fake_service(client, sid, prior):
        serviced.append((sid, prior["status"]))
        return {**prior, "status": "delivered"}

    class Runs:
        def __init__(self):
            self._items = [types.SimpleNamespace(session_id="s1")]

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._items:
                raise StopAsyncIteration
            return self._items.pop()

    client = types.SimpleNamespace(beta=types.SimpleNamespace(
        deployment_runs=types.SimpleNamespace(list=lambda **kw: Runs())))  # fmt: skip
    monkeypatch.setitem(sys.modules, "anthropic",
                        types.SimpleNamespace(AsyncAnthropic=lambda: client))  # fmt: skip
    monkeypatch.setattr(watcher, "load_brief_ids", lambda: {"deployment_id": "dep"})
    monkeypatch.setattr(watcher, "_load_ledger", fake_load)
    monkeypatch.setattr(watcher, "_save_ledger", lambda ledger: None)
    monkeypatch.setattr(watcher, "_service_once", fake_service)

    ticks = 0

    async def fake_sleep(_):
        nonlocal ticks
        ticks += 1
        if ticks >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(watcher.asyncio, "sleep", fake_sleep)
    with pytest.raises(KeyboardInterrupt):
        await watcher.watch()
    assert len(loads) == 2
    assert serviced == [("s1", "pending")]  # skipped as terminal tick 1, retried tick 2
