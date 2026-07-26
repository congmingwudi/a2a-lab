"""WS9 coding-agent telemetry source — canned CloudWatch payloads, no AWS."""

from __future__ import annotations

import datetime as dt
import json

from observability.coding_source import (
    CodingSource,
    _tool_of,
    summarize_series,
)
from observability.store import ObsStore

DAY = dt.datetime(2026, 7, 25, 12, 0, tzinfo=dt.timezone.utc)
NEXT_DAY = dt.datetime(2026, 7, 26, 12, 0, tzinfo=dt.timezone.utc)


class FakeCloudWatch:
    """Minimal stand-in for the two CloudWatch calls the source makes."""

    def __init__(self, metrics, datapoints):
        self._metrics = metrics
        self._datapoints = datapoints
        self.calls = []

    def list_metrics(self, **kwargs):
        self.calls.append(("list_metrics", kwargs))
        ns = kwargs.get("Namespace")
        metrics = [m for m in self._metrics if ns is None or m["Namespace"] == ns]
        return {"Metrics": metrics}

    def get_metric_statistics(self, **kwargs):
        self.calls.append(("get_metric_statistics", kwargs))
        key = (kwargs["Namespace"], kwargs["MetricName"])
        return {"Datapoints": self._datapoints.get(key, [])}


def _metric(name, namespace="ClaudeCode", **dims):
    return {
        "Namespace": namespace,
        "MetricName": name,
        "Dimensions": [{"Name": k, "Value": v} for k, v in dims.items()],
    }


# ---- pure rollup ----------------------------------------------------------


def test_summarize_buckets_by_tool_and_day():
    rows = [
        {
            "metric": "claude_code.cost.usage",
            "tool": "claude-code",
            "dimensions": {"model": "opus"},
            "timestamp": DAY,
            "value": 1.25,
        },
        {
            "metric": "claude_code.cost.usage",
            "tool": "claude-code",
            "dimensions": {"model": "haiku"},
            "timestamp": DAY,
            "value": 0.25,
        },
        {
            "metric": "claude_code.token.usage",
            "tool": "claude-code",
            "dimensions": {"type": "input", "model": "opus"},
            "timestamp": DAY,
            "value": 10_000,
        },
        {
            "metric": "claude_code.token.usage",
            "tool": "claude-code",
            "dimensions": {"type": "output", "model": "opus"},
            "timestamp": DAY,
            "value": 2_000,
        },
        {
            "metric": "claude_code.session.count",
            "tool": "claude-code",
            "dimensions": {},
            "timestamp": DAY,
            "value": 3,
        },
        {
            "metric": "claude_code.active_time.total",
            "tool": "claude-code",
            "dimensions": {},
            "timestamp": DAY,
            "value": 7200,
        },
        # a different day, and a different tool, must not merge into the above
        {
            "metric": "claude_code.cost.usage",
            "tool": "claude-code",
            "dimensions": {},
            "timestamp": NEXT_DAY,
            "value": 0.50,
        },
        {
            "metric": "codex.cost.usage",
            "tool": "codex",
            "dimensions": {},
            "timestamp": DAY,
            "value": 0.75,
        },
    ]
    buckets = summarize_series(rows)

    assert set(buckets) == {
        "claude-code:2026-07-25",
        "claude-code:2026-07-26",
        "codex:2026-07-25",
    }
    day = buckets["claude-code:2026-07-25"]
    assert day["cost_usd"] == 1.50
    assert day["tokens"] == {"input": 10_000, "output": 2_000}
    assert day["sessions"] == 3
    assert day["active_time_s"] == 7200
    # per-model split preserved so "which model cost what" is answerable
    assert day["by_model"]["opus"]["cost_usd"] == 1.25
    assert day["by_model"]["haiku"]["cost_usd"] == 0.25
    assert buckets["codex:2026-07-25"]["cost_usd"] == 0.75


def test_tool_detection_by_prefix():
    assert _tool_of("claude_code.cost.usage") == "claude-code"
    assert _tool_of("codex.token.usage") == "codex"
    # an unrelated CloudWatch metric must not be swept in
    assert _tool_of("AWS/Lambda.Invocations") is None
    assert _tool_of("my_app.claude_code.cost") is None


# ---- harvest --------------------------------------------------------------


def test_harvest_writes_sessions_and_events(tmp_path):
    metrics = [
        _metric("claude_code.cost.usage", model="opus"),
        _metric("claude_code.token.usage", type="input"),
        # noise in the same namespace must be ignored, not harvested
        _metric("SomeOtherApp.requests"),
    ]
    datapoints = {
        ("ClaudeCode", "claude_code.cost.usage"): [{"Timestamp": DAY, "Sum": 2.0}],
        ("ClaudeCode", "claude_code.token.usage"): [{"Timestamp": DAY, "Sum": 5000}],
    }
    store = ObsStore(db_path=tmp_path / "lab.db")
    result = CodingSource(client=FakeCloudWatch(metrics, datapoints)).harvest(store)

    assert result.status == "ok"
    assert result.sessions == 1
    assert "modelled build cost at list price" in result.detail

    sessions = store.list_sessions("coding")
    assert sessions[0]["native_id"] == "claude-code:2026-07-25"
    usage = json.loads(sessions[0]["usage_json"])
    assert usage["cost_usd_estimated"] == 2.0
    assert usage["input_tokens"] == 5000
    raw = json.loads(sessions[0]["raw_json"])
    # the honesty caveat travels with the data, not just the docs
    assert "not an invoice" in raw["cost_note"]
    store.close()


def test_harvest_blocked_when_no_coding_metrics_exist(tmp_path):
    """The state this will be in until the exporters are switched on.

    It must read as 'blocked, here is what to do' rather than as an error —
    the coverage panel renders this string.
    """
    store = ObsStore(db_path=tmp_path / "lab.db")
    result = CodingSource(client=FakeCloudWatch([_metric("AWS/Lambda.Invocations")], {})).harvest(
        store
    )
    assert result.status == "blocked"
    assert "not retroactive" in result.detail
    store.close()


def test_harvest_reports_error_without_raising(tmp_path):
    class Exploding:
        def list_metrics(self, **kwargs):
            raise RuntimeError("expired token")

    store = ObsStore(db_path=tmp_path / "lab.db")
    result = CodingSource(client=Exploding()).harvest(store)
    assert result.status == "error"
    assert "expired token" in result.detail
    store.close()


def test_explicit_namespaces_skip_discovery(monkeypatch, tmp_path):
    monkeypatch.setenv("A2ALAB_CODING_NAMESPACES", "Custom/Coding")
    metrics = [_metric("claude_code.cost.usage", namespace="Custom/Coding")]
    datapoints = {("Custom/Coding", "claude_code.cost.usage"): [{"Timestamp": DAY, "Sum": 1.0}]}
    cw = FakeCloudWatch(metrics, datapoints)
    store = ObsStore(db_path=tmp_path / "lab.db")
    result = CodingSource(client=cw).harvest(store)
    assert result.status == "ok"
    assert "Custom/Coding" in result.detail
    store.close()
