"""WS9 coding-agent telemetry source — canned PromQL payloads, no AWS."""

from __future__ import annotations

import datetime as dt
import json

from observability.coding_source import (
    CodingSource,
    _tool_of,
    metric_names,
    summarize_series,
)
from observability.store import ObsStore

DAY = dt.datetime(2026, 7, 25, 12, 0, tzinfo=dt.timezone.utc)
NEXT_DAY = dt.datetime(2026, 7, 26, 12, 0, tzinfo=dt.timezone.utc)


class FakePromQL:
    """Stands in for PromQLClient. Keyed by metric name inside the selector."""

    def __init__(self, series_by_metric):
        self._series = series_by_metric
        self.queries = []

    def query_range(self, query, start, end, step_s):
        self.queries.append(query)
        for name, series in self._series.items():
            if f'"{name}"' in query:
                return series
        return []


def _series(labels, points):
    return {"metric": labels, "values": [[ts.timestamp(), str(v)] for ts, v in points]}


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
    assert day["by_model"]["opus"]["cost_usd"] == 1.25
    assert day["by_model"]["haiku"]["cost_usd"] == 0.25
    # regression: Codex metrics are classified by SUFFIX, not full name —
    # comparing full names scored every Codex metric as zero
    assert buckets["codex:2026-07-25"]["cost_usd"] == 0.75


def test_tool_detection_by_prefix():
    assert _tool_of("claude_code.cost.usage") == "claude-code"
    assert _tool_of("codex.token.usage") == "codex"
    assert _tool_of("AWS/Lambda.Invocations") is None
    assert _tool_of("my_app.claude_code.cost") is None


def test_metric_names_are_extendable_and_deduped(monkeypatch):
    """There is no metric enumeration on the PromQL surface, so the list is
    fixed — but it has to be extendable when a tool adds a name."""
    monkeypatch.setenv("A2ALAB_CODING_METRICS", "codex.turn.count, claude_code.cost.usage")
    names = metric_names()
    assert "codex.turn.count" in names
    assert names.count("claude_code.cost.usage") == 1  # already present, not duplicated


# ---- harvest --------------------------------------------------------------


def test_harvest_reads_promql_and_writes_sessions(tmp_path):
    client = FakePromQL(
        {
            "claude_code.cost.usage": [
                _series(
                    {
                        "__name__": "claude_code.cost.usage",
                        "@resource.tool": "claude-code",
                        "model": "opus",
                    },
                    [(DAY, 2.0)],
                )
            ],
            "claude_code.token.usage": [
                _series(
                    {
                        "__name__": "claude_code.token.usage",
                        "@resource.tool": "claude-code",
                        "type": "input",
                    },
                    [(DAY, 5000)],
                )
            ],
        }
    )
    store = ObsStore(db_path=tmp_path / "lab.db")
    result = CodingSource(client=client).harvest(store)

    assert result.status == "ok"
    assert result.sessions == 1
    assert "modelled build cost at list price" in result.detail
    # Delta-temporality Sums are summed, never rate'd: increase() drops
    # single-sample series and under-counts, and a bare selector double-counts
    # through its lookback. See _metric_rows for the measured comparison.
    assert all(q.startswith("sum_over_time(") for q in client.queries)

    sessions = store.list_sessions("coding")
    assert sessions[0]["native_id"] == "claude-code:2026-07-25"
    usage = json.loads(sessions[0]["usage_json"])
    assert usage["cost_usd_estimated"] == 2.0
    assert usage["input_tokens"] == 5000
    raw = json.loads(sessions[0]["raw_json"])
    assert "not an invoice" in raw["cost_note"]
    store.close()


def test_resource_tool_label_wins_over_the_name_prefix(tmp_path):
    """A tool that renames its metrics still attributes correctly if it sets
    the tool resource attribute."""
    client = FakePromQL(
        {
            "codex.cost.usage": [
                _series(
                    {"__name__": "codex.cost.usage", "@resource.tool": "codex-cli"}, [(DAY, 1.0)]
                )
            ]
        }
    )
    store = ObsStore(db_path=tmp_path / "lab.db")
    CodingSource(client=client).harvest(store)
    assert store.list_sessions("coding")[0]["native_id"] == "codex-cli:2026-07-25"
    store.close()


def test_harvest_blocked_when_no_metrics_exist(tmp_path):
    """The true state until the exporters are switched on. Must read as
    'blocked, here is what to do' — the coverage panel renders this string."""
    store = ObsStore(db_path=tmp_path / "lab.db")
    result = CodingSource(client=FakePromQL({})).harvest(store)
    assert result.status == "blocked"
    assert "not retroactive" in result.detail
    store.close()


def test_one_missing_metric_does_not_sink_the_harvest(tmp_path):
    """Most of the fixed name list will legitimately not exist."""

    class PartlyBroken(FakePromQL):
        def query_range(self, query, start, end, step_s):
            if "token.usage" in query:
                raise RuntimeError("no such metric")
            return super().query_range(query, start, end, step_s)

    client = PartlyBroken(
        {"claude_code.cost.usage": [_series({"@resource.tool": "claude-code"}, [(DAY, 3.0)])]}
    )
    store = ObsStore(db_path=tmp_path / "lab.db")
    result = CodingSource(client=client).harvest(store)
    assert result.status == "ok"
    assert json.loads(store.list_sessions("coding")[0]["usage_json"])["cost_usd_estimated"] == 3.0
    store.close()


def test_harvest_reports_error_without_raising(tmp_path):
    class Exploding:
        def query_range(self, *a, **k):
            raise RuntimeError("expired token")

    store = ObsStore(db_path=tmp_path / "lab.db")
    # every metric raises, so _metric_rows swallows each one and yields nothing
    result = CodingSource(client=Exploding()).harvest(store)
    assert result.status == "blocked"
    # ...but "nothing was there" and "nothing could be asked for" must not read
    # the same. Without this, a broken query masquerades as an idle exporter —
    # which it did, for days.
    assert "expired token" in result.detail
    assert "query error" in result.detail
    store.close()


def test_query_step_is_fine_grained_enough_to_see_today(tmp_path):
    """CloudWatch aligns evaluation points to epoch multiples of the step, so a
    daily step cannot see anything recorded since midnight. Measured live
    2026-07-26: 0 series at step 86400/3600/900/600, 4 series at step 300, on
    identical data. A build-cost view that is always a day behind reads as an
    exporter that is switched off."""
    seen: list[int] = []

    class StepRecording(FakePromQL):
        def query_range(self, query, start, end, step_s):
            seen.append(step_s)
            return super().query_range(query, start, end, step_s)

    client = StepRecording(
        {"claude_code.cost.usage": [_series({"@resource.tool": "claude-code"}, [(DAY, 1.0)])]}
    )
    store = ObsStore(db_path=tmp_path / "lab.db")
    CodingSource(client=client).harvest(store)
    assert seen and max(seen) <= 300
    # the window the aggregation covers must equal the step, or the buckets
    # either overlap (double count) or leave gaps (undercount)
    assert all(f"[{step}s]" in q for step, q in zip(seen, client.queries))
    store.close()
