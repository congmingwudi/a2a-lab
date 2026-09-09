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
