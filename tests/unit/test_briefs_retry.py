"""A4/F07 — the brief runner must retry a FAILED Salesforce delivery on a later
watcher tick, and must never re-write one that already succeeded.

The defect (D81): the failure path sent a `user.custom_tool_result`, which the
next tick's `answered` set treated as "done", so the writer was never called
again. The fix is a per-call delivery ledger: the save tool is gated by the
ledger's per-call status, not by whether the managed session already has a
tool-result for it.
"""

from __future__ import annotations

import types

import pytest

from briefs import runner as runner_mod
from briefs.__main__ import _service_once
from briefs.runner import SAVE_TOOL_NAME, BriefRunner


class FakeEvents:
    def __init__(self):
        self.sent: list = []

    async def send(self, *, session_id, events):
        self.sent.append(events)


def _client():
    ev = FakeEvents()
    return types.SimpleNamespace(
        beta=types.SimpleNamespace(sessions=types.SimpleNamespace(events=ev))
    ), ev


def _save_event(tool_use_id="tuse_1", account="Apple Inc."):
    return types.SimpleNamespace(
        type="agent.custom_tool_use",
        id=tool_use_id,
        name=SAVE_TOOL_NAME,
        input={"account_name": account, "headline": "h", "brief_markdown": "b"},
    )


class FakeWriter:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = 0

    async def save_brief(self, **kw):
        self.calls += 1
        if self.fail:
            raise RuntimeError("Salesforce 503")
        return {
            "account_id": "001A",
            "account_name": kw["account_name"],
            "brief_id": "a0X1",
            "task_id": "00T1",
            "delivery_key": f"{kw['research_session_id']}#001A",
            "notified": False,
        }


async def _run(runner, event, *, prior_calls=None):
    calls: list[dict] = []
    await runner._handle_event(
        event,
        "sess1",
        "trace1",
        [],
        calls,
        set(),
        skip_tool_ids=set(),
        prior_calls=prior_calls,
    )
    return calls


async def test_new_save_call_invokes_the_writer_and_records_ok():
    client, ev = _client()
    runner = BriefRunner(client)
    runner._writer = FakeWriter()
    calls = await _run(runner, _save_event())
    assert runner._writer.calls == 1
    assert calls == [
        {
            "tool_use_id": "tuse_1",
            "status": "ok",
            "account": "Apple Inc.",
            "account_id": "001A",
            "account_name": "Apple Inc.",
            "brief_id": "a0X1",
            "task_id": "00T1",
            "delivery_key": "sess1#001A",
            "notified": False,
        }
    ]
    assert ev.sent  # a tool-result was still returned to the managed session


async def test_failed_save_records_failed_and_still_answers_the_session():
    client, ev = _client()
    runner = BriefRunner(client)
    runner._writer = FakeWriter(fail=True)
    calls = await _run(runner, _save_event())
    assert runner._writer.calls == 1
    assert calls[0]["status"] == "failed"
    assert "Salesforce 503" in calls[0]["error"]
    # the managed turn still gets a tool-result so it can complete
    assert ev.sent


async def test_a_prior_ok_call_is_not_rewritten():
    """The idempotency invariant: a call the ledger already marks delivered is
    surfaced but never re-sent to Salesforce."""
    client, ev = _client()
    runner = BriefRunner(client)
    runner._writer = FakeWriter()
    prior = {
        "tuse_1": {
            "tool_use_id": "tuse_1",
            "status": "ok",
            "account": "Apple Inc.",
            "brief_id": "a0X1",
        }
    }
    calls = await _run(runner, _save_event(), prior_calls=prior)
    assert runner._writer.calls == 0  # not re-written
    assert calls == [prior["tuse_1"]]


async def test_a_prior_failed_call_is_retried():
    """The retry invariant: a previously-failed call IS attempted again."""
    client, ev = _client()
    runner = BriefRunner(client)
    runner._writer = FakeWriter()  # succeeds this time
    prior = {"tuse_1": {"tool_use_id": "tuse_1", "status": "failed", "error": "old"}}
    calls = await _run(runner, _save_event(), prior_calls=prior)
    assert runner._writer.calls == 1  # retried
    assert calls[0]["status"] == "ok"


async def test_result_reports_per_call_delivered_and_failed_counts():
    """_result must expose per-call outcome, not one delivered integer."""
    runner = BriefRunner(_client()[0])
    ok = {"tool_use_id": "a", "status": "ok", "brief_id": "b1"}
    bad = {"tool_use_id": "b", "status": "failed", "error": "boom"}
    result = runner._result("sess1", ["text"], [ok, bad], 2, 0.0)
    assert result["delivered"] == 1
    assert result["failed"] == 1
    assert result["error"] and "boom" in result["error"]
    assert result["calls"] == [ok, bad]
    assert result["deliveries"] == [ok]  # ok subset, for the summary line


# --- A4/F07 two-tick replay through _service_once ---------------------------
#
# The prior tests drove `_handle_event` directly with a synthetic `skip_tool_ids`
# and `prior_calls`. Codex's Round-1 finding is that this bypasses the real
# defect: on a genuine scheduled replay the failed call ALREADY carries a
# `user.custom_tool_result` in session history, and re-answering it is a
# duplicate the managed API rejects — which throws away the now-successful writer
# outcome. These tests span TWO ticks through `_service_once` (briefs.__main__)
# so the answer is written into history by tick 1 organically, and use a fake
# managed session that REJECTS a second result for the same tool id — proving the
# `already_answered` guard, not a permissive fake, is what makes the retry safe.


class _FakeIter:
    """Minimal async-iterable over a fixed list (events.list page / stream)."""

    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        async def gen():
            for it in self._items:
                yield it

        return gen()

    async def close(self):  # only the stream is closed; harmless on the page
        return None


class FakeSession:
    """A managed session as history: the initial `agent.custom_tool_use` events,
    plus every `user.custom_tool_result` the runner sends back. `send` REJECTS a
    duplicate result for an already-answered tool id — the managed API's
    behaviour, and the thing the runner's replay guard must avoid triggering."""

    def __init__(self, tool_uses: list[tuple[str, str]]):
        self.events: list = []
        for tid, account in tool_uses:
            self.events.append(
                types.SimpleNamespace(
                    type="agent.custom_tool_use",
                    id=tid,
                    name=SAVE_TOOL_NAME,
                    input={"account_name": account, "headline": "h", "brief_markdown": "b"},
                )
            )
        self.answered: set[str] = set()

    def send(self, events):
        for ev in events:
            if ev.get("type") != "user.custom_tool_result":
                continue
            tid = ev.get("custom_tool_use_id")
            if tid in self.answered:
                raise RuntimeError(f"managed API rejects duplicate result for {tid}")
            self.answered.add(tid)
            self.events.append(
                types.SimpleNamespace(type="user.custom_tool_result", custom_tool_use_id=tid)
            )


class FakeClient:
    """AsyncAnthropic surface `_drive` touches: events.list/stream/send + a
    non-terminal retrieve. The stream yields a single non-action idle so `_drive`
    breaks right after processing history — the whole exchange rides the history
    replay, which is exactly the scheduled path under test."""

    def __init__(self, session: FakeSession):
        outer = self
        self.session = session

        class _Events:
            async def list(self, *, session_id):
                return _FakeIter(outer.session.events)

            async def stream(self, *, session_id):
                return _FakeIter(
                    [types.SimpleNamespace(type="session.status_idle", stop_reason=None)]
                )

            async def send(self, *, session_id, events):
                outer.session.send(events)

        class _Sessions:
            def __init__(self):
                self.events = _Events()

            async def retrieve(self, session_id):
                return types.SimpleNamespace(status="idle")

        self.beta = types.SimpleNamespace(sessions=_Sessions())


class FakeAccountWriter:
    """save_brief keyed by account: an account in `fail_accounts` fails that many
    times, then succeeds. Records every call so a test can assert the failed
    account is retried while the delivered one is never re-written."""

    def __init__(self, *, fail_accounts: dict[str, int] | None = None):
        self.fail_accounts = dict(fail_accounts or {})
        self.calls: list[str] = []

    async def save_brief(
        self, *, account_name, headline, brief_markdown, research_session_id, trace_id
    ):
        self.calls.append(account_name)
        if self.fail_accounts.get(account_name, 0) > 0:
            self.fail_accounts[account_name] -= 1
            raise RuntimeError("Salesforce 503")
        tag = account_name[:3]
        return {
            "account_id": f"001{tag}",
            "account_name": account_name,
            "brief_id": f"a0X_{tag}",
            "task_id": f"00T_{tag}",
            "delivery_key": f"{research_session_id}#001{tag}",
            "notified": True,
        }

    async def aclose(self):
        return None


async def test_failed_delivery_is_retried_next_tick_without_reanswering(monkeypatch):
    session = FakeSession([("tuse_1", "Apple Inc.")])
    client = FakeClient(session)
    writer = FakeAccountWriter(fail_accounts={"Apple Inc.": 1})  # fail once, then succeed
    monkeypatch.setattr(runner_mod.BriefWriter, "from_env", lambda: writer)

    # Tick 1: the writer fails, the session gets exactly one result, entry pending.
    entry1 = await _service_once(client, "sess1", None)
    assert entry1["status"] == "pending"
    assert writer.calls == ["Apple Inc."]
    assert session.answered == {"tuse_1"}

    # Tick 2 (replay): the failed call carries a result in history, so re-answering
    # would raise. The writer IS called again and succeeds; no second result is
    # sent, so the delivery is recorded — not stranded on the duplicate rejection.
    entry2 = await _service_once(client, "sess1", entry1)
    assert entry2["status"] == "delivered"
    assert writer.calls == ["Apple Inc.", "Apple Inc."]  # retried
    assert session.answered == {"tuse_1"}  # still one answer -> guard held


async def test_two_account_partial_success_retries_only_the_failure(monkeypatch):
    session = FakeSession([("tuse_a", "Apple Inc."), ("tuse_b", "Beta Co.")])
    client = FakeClient(session)
    writer = FakeAccountWriter(fail_accounts={"Apple Inc.": 1})  # Beta always ok
    monkeypatch.setattr(runner_mod.BriefWriter, "from_env", lambda: writer)

    # Tick 1: Beta delivered, Apple failed -> pending, both answered once.
    entry1 = await _service_once(client, "sess2", None)
    assert entry1["status"] == "pending"
    assert sorted(writer.calls) == ["Apple Inc.", "Beta Co."]
    assert session.answered == {"tuse_a", "tuse_b"}

    # Tick 2: only Apple is retried; the already-ok Beta is surfaced, never
    # re-written, and neither call sends a duplicate result.
    writer.calls.clear()
    entry2 = await _service_once(client, "sess2", entry1)
    assert entry2["status"] == "delivered"
    assert writer.calls == ["Apple Inc."]
    assert session.answered == {"tuse_a", "tuse_b"}


# --- A4/F07 the watch loop persists BEFORE it mutates the live ledger --------


async def test_lost_ledger_write_leaves_session_eligible_next_tick(monkeypatch):
    """The ordering invariant (F07 rd1): `watch()` calls `_save_ledger` BEFORE it
    writes the candidate into the in-memory ledger. So if the state store drops
    the write, the entry stays non-terminal in memory and the next poll
    re-services it. Two ticks: the store fails on tick 1; the SAME session must be
    serviced again on tick 2 (commit-then-save would skip it as terminal)."""
    from briefs import __main__ as m

    run = types.SimpleNamespace(session_id="sessX")

    class _Runs:
        def list(self, *, deployment_id):
            return _FakeIter([run])

    client = types.SimpleNamespace(beta=types.SimpleNamespace(deployment_runs=_Runs()))

    monkeypatch.setattr(m, "load_brief_ids", lambda: {"deployment_id": "dep1"})
    monkeypatch.setattr(m, "_load_ledger", lambda: {})
    monkeypatch.setattr("anthropic.AsyncAnthropic", lambda: client)

    serviced: list[str] = []

    async def fake_service_once(cl, sid, prior):
        serviced.append(sid)
        return {"status": "delivered", "attempts": 1, "at": "t", "last_error": None, "calls": {}}

    monkeypatch.setattr(m, "_service_once", fake_service_once)

    saves = {"n": 0}

    def fake_save(ledger):
        saves["n"] += 1
        if saves["n"] == 1:
            raise RuntimeError("Aurora put_state dropped the write")

    monkeypatch.setattr(m, "_save_ledger", fake_save)

    class _Break(Exception):
        pass

    sleeps = {"n": 0}

    async def fake_sleep(_):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            raise _Break()

    monkeypatch.setattr(m.asyncio, "sleep", fake_sleep)

    with pytest.raises(_Break):
        await m.watch()

    assert serviced == ["sessX", "sessX"]  # re-serviced: the lost write did not commit
    assert saves["n"] == 2  # attempted on both ticks
