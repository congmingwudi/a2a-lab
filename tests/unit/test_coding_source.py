"""WS9 coding-agent telemetry source — canned PromQL payloads, no AWS."""

from __future__ import annotations

import datetime as dt
import json

from observability.coding_source import (
    CodingSource,
    _tool_of,
    metric_names,
    normalize_repos,
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
    assert _tool_of("cursor_session_total") == "cursor"
    assert _tool_of("AWS/Lambda.Invocations") is None
    assert _tool_of("my_app.claude_code.cost") is None


def test_codex_exec_folds_into_codex():
    """`codex exec` (headless) reports @resource.service.name=codex_exec while the
    interactive CLI reports `codex`. Same tool, two launch modes — they must roll
    into ONE bucket, not two rows, and sum their sessions."""
    rows = [
        {
            "metric": "codex.thread.started",
            "tool": "codex",
            "repo": "acme/lab",
            "project": "lab",
            "dimensions": {},
            "timestamp": DAY,
            "value": 3,
        },
        {
            "metric": "codex.thread.started",
            "tool": "codex_exec",
            "repo": "acme/lab",
            "project": "lab",
            "dimensions": {},
            "timestamp": DAY,
            "value": 1,
        },
    ]
    buckets = summarize_series(rows)
    assert "codex_exec:2026-07-25" not in buckets
    assert buckets["codex:2026-07-25"]["sessions"] == 4


def test_cursor_sessions_summarize_with_no_cost():
    """Cursor (via cursorscope) publishes counters but no cost or consumable
    token metric, so it is read for sessions only — the same footing as Codex,
    which the model breakdown and cross-tool total already handle."""
    rows = [
        {
            "metric": "cursor_session_total",
            "tool": "cursor",
            "repo": "acme/lab",
            "project": "lab",
            "dimensions": {"model": "claude-opus-5"},
            "timestamp": DAY,
            "value": 4,
        }
    ]
    bucket = summarize_series(rows)["cursor:2026-07-25"]
    assert bucket["sessions"] == 4
    assert bucket["cost_usd"] == 0.0
    assert bucket["tokens"] == {}
    assert bucket["by_model"]["claude-opus-5"]["sessions"] == 4
    assert bucket["by_repo"]["acme/lab"]["sessions"] == 4


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
    # through its lookback. See _metric_rows for the measured comparison. Scoped
    # to the delta metrics because the same sweep also queries Cursor's
    # CUMULATIVE counters, which correctly use increase() — see
    # test_cursor_cumulative_counters_are_queried_with_increase.
    delta_qs = [q for q in client.queries if "claude_code." in q or "codex." in q]
    assert delta_qs and all(q.startswith("sum_over_time(") for q in delta_qs)

    sessions = store.list_sessions("coding")
    assert sessions[0]["native_id"] == "claude-code:2026-07-25"
    usage = json.loads(sessions[0]["usage_json"])
    assert usage["cost_usd_estimated"] == 2.0
    assert usage["input_tokens"] == 5000
    raw = json.loads(sessions[0]["raw_json"])
    assert "not an invoice" in raw["cost_note"]
    store.close()


def test_cursor_cumulative_counters_are_queried_with_increase(tmp_path):
    """Cursor's counters are CUMULATIVE (cursorscope pins cumulative temporality
    and the names carry the `_total` suffix), unlike the two native exporters'
    delta Sums. sum_over_time on a cumulative counter adds the running total at
    every step and wildly over-counts, so these must go through increase().
    Delta metrics must still use sum_over_time — the two forms are not
    interchangeable."""
    client = FakePromQL(
        {
            "cursor_session_total": [
                _series(
                    {"__name__": "cursor_session_total", "@resource.tool": "cursor"},
                    [(DAY, 2.0)],
                )
            ],
            "claude_code.cost.usage": [
                _series(
                    {"__name__": "claude_code.cost.usage", "@resource.tool": "claude-code"},
                    [(DAY, 1.0)],
                )
            ],
        }
    )
    store = ObsStore(db_path=tmp_path / "lab.db")
    CodingSource(client=client).harvest(store)
    cursor_qs = [q for q in client.queries if "cursor_session_total" in q]
    delta_qs = [q for q in client.queries if "claude_code.cost.usage" in q]
    assert cursor_qs and all(q.startswith("increase(") for q in cursor_qs)
    assert delta_qs and all(q.startswith("sum_over_time(") for q in delta_qs)
    # and the same [step] window on both, so buckets tile the range once
    assert all(f"[{300}s]" in q for q in cursor_qs + delta_qs)
    store.close()


def test_cursor_attribution_from_service_labels(tmp_path):
    """Real cursorscope metrics carry NO @resource.tool / .repo / .project —
    only @resource.service.name / .service.namespace / .deployment.environment
    (build-notes/cursor/01 §3, verified live 2026-07-31). _metric_rows must fall
    back to those, or every Cursor row lands `unattributed` and the tool defaults
    to the name-prefix guess. This is the shape the harvest actually sees."""
    client = FakePromQL(
        {
            "cursor_hook_events_total": [
                _series(
                    {
                        "__name__": "cursor_hook_events_total",
                        "@resource.service.name": "cursor",
                        "@resource.service.namespace": "a2a-lab",
                        "@resource.deployment.environment": "acme/a2a-lab",
                        "cursor_hook_name": "sessionStart",
                    },
                    [(DAY, 3.0)],
                )
            ],
        }
    )
    rows = CodingSource(client=client)._metric_rows(client)
    assert rows, "cursor_hook_events_total must be queried and returned"
    row = rows[0]
    assert row["tool"] == "cursor"  # from @resource.service.name
    assert row["repo"] == "acme/a2a-lab"  # from @resource.deployment.environment
    assert row["project"] == "a2a-lab"  # from @resource.service.namespace
    # cursor_hook_name is a datapoint label, so it stays in dimensions
    assert row["dimensions"].get("cursor_hook_name") == "sessionStart"


def test_cursor_hook_events_do_not_inflate_sessions():
    """cursor_hook_events_total counts every lifecycle hook, not sessions. Its
    suffix (`hook_events_total`) is not in SESSION_SUFFIX, so it must land in
    `metrics` verbatim and leave the session count alone — otherwise every hook
    fires would be miscounted as a session."""
    rows = [
        {
            "metric": "cursor_hook_events_total",
            "tool": "cursor",
            "repo": "acme/lab",
            "project": "lab",
            "dimensions": {"cursor_hook_name": "preToolUse"},
            "timestamp": DAY,
            "value": 12,
        }
    ]
    bucket = summarize_series(rows)["cursor:2026-07-25"]
    assert bucket["sessions"] == 0
    assert bucket["metrics"]["cursor_hook_events_total"] == 12


def test_resource_tool_label_wins_over_the_name_prefix(tmp_path):
    """A tool that renames its metrics still attributes correctly if it sets
    the tool resource attribute."""
    # codex.thread.started, not codex.cost.usage: Codex emits no cost metric at
    # all (observed live 2026-07-26), and only names in CODEX_METRICS are ever
    # queried, so a fixture keyed on a non-existent metric tests nothing.
    client = FakePromQL(
        {
            "codex.thread.started": [
                _series(
                    {"__name__": "codex.thread.started", "@resource.tool": "codex-cli"},
                    [(DAY, 1.0)],
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


# ---- per-repository attribution -------------------------------------------


def test_summarize_splits_cost_per_repo_and_sums_to_the_total():
    """The whole point of setting @resource.repo: cost per codebase.

    The per-repo split must reconcile exactly with the bucket total — a
    breakdown that does not add up is worse than no breakdown.
    """
    rows = [
        {
            "metric": "claude_code.cost.usage",
            "tool": "claude-code",
            "repo": "acme/lab",
            "project": "lab",
            "dimensions": {"model": "opus"},
            "timestamp": DAY,
            "value": 3.0,
        },
        {
            "metric": "claude_code.cost.usage",
            "tool": "claude-code",
            "repo": "acme/logging-service",
            "project": "logging-service",
            "dimensions": {"model": "opus"},
            "timestamp": DAY,
            "value": 1.0,
        },
        {
            "metric": "claude_code.token.usage",
            "tool": "claude-code",
            "repo": "acme/logging-service",
            "project": "logging-service",
            "dimensions": {"type": "input"},
            "timestamp": DAY,
            "value": 500,
        },
    ]
    bucket = summarize_series(rows)["claude-code:2026-07-25"]
    by_repo = bucket["by_repo"]

    assert set(by_repo) == {"acme/lab", "acme/logging-service"}
    assert by_repo["acme/lab"]["cost_usd"] == 3.0
    assert by_repo["acme/logging-service"]["cost_usd"] == 1.0
    assert by_repo["acme/logging-service"]["tokens"]["input"] == 500
    assert by_repo["acme/logging-service"]["project"] == "logging-service"
    # reconciliation
    assert sum(r["cost_usd"] for r in by_repo.values()) == bucket["cost_usd"] == 4.0


def test_datapoints_without_a_repo_label_are_kept_as_unattributed():
    """Dropping them would make the per-repo view disagree with the total."""
    rows = [
        {
            "metric": "claude_code.cost.usage",
            "tool": "claude-code",
            "repo": "unattributed",
            "project": "unattributed",
            "dimensions": {},
            "timestamp": DAY,
            "value": 2.5,
        }
    ]
    bucket = summarize_series(rows)["claude-code:2026-07-25"]
    assert bucket["by_repo"]["unattributed"]["cost_usd"] == 2.5
    assert sum(r["cost_usd"] for r in bucket["by_repo"].values()) == bucket["cost_usd"]


def _cost_row(repo, value, project=None):
    return {
        "metric": "claude_code.cost.usage",
        "tool": "claude-code",
        "repo": repo,
        "project": project or repo.rsplit("/", 1)[-1],
        "dimensions": {},
        "timestamp": DAY,
        "value": value,
    }


def test_model_is_recorded_for_a_tool_with_no_cost_metric():
    """Codex labels every datapoint with `model` but publishes no cost metric.

    Keying by_model off cost alone reported an empty model breakdown for it —
    the attribution was on the wire the whole time. Sessions are the unit that
    tool can answer in.
    """
    rows = [
        {
            "metric": "codex.thread.started",
            "tool": "codex",
            "repo": "acme/lab",
            "project": "lab",
            "dimensions": {"model": "gpt-5.6-sol"},
            "timestamp": DAY,
            "value": 3,
        }
    ]
    bucket = summarize_series(rows)["codex:2026-07-25"]
    assert bucket["by_model"]["gpt-5.6-sol"]["sessions"] == 3
    assert bucket["sessions"] == 3


def test_placeholder_repo_owner_folds_into_the_real_repo():
    """`<owner>/x` and `acme/x` are one codebase whose exporter was wrong for a
    while. Merging keeps the cost attributed; dropping it would quietly shrink
    the measured total, which is the failure this section exists to avoid."""
    rows = [_cost_row("acme/logging-service", 1.5), _cost_row("<owner>/logging-service", 2.5)]
    bucket = summarize_series(rows)["claude-code:2026-07-25"]

    assert set(bucket["by_repo"]) == {"acme/logging-service"}
    assert bucket["by_repo"]["acme/logging-service"]["cost_usd"] == 4.0
    assert bucket["cost_usd"] == 4.0  # total unchanged by the merge


def test_placeholder_with_no_real_counterpart_is_left_alone():
    """It is still the only record of that work, and the odd name is the signal
    that a checkout needs configuring — silently renaming it would hide that."""
    rows = normalize_repos([_cost_row("<owner>/orphan", 1.0)])
    assert rows[0]["repo"] == "<owner>/orphan"


def test_explicit_repo_alias_from_env(monkeypatch):
    monkeypatch.setenv("A2ALAB_CODING_REPO_ALIASES", "old/name=new/name, junk=acme/lab")
    rows = normalize_repos([_cost_row("old/name", 1.0), _cost_row("junk", 2.0)])
    assert [r["repo"] for r in rows] == ["new/name", "acme/lab"]


def test_harvest_keeps_the_resource_repo_label_off_the_wire(tmp_path):
    """Regression: an earlier version stripped every '@' label in _metric_rows,
    throwing away the attribution the exporters were configured to produce."""
    client = FakePromQL(
        {
            "claude_code.cost.usage": [
                _series(
                    {
                        "__name__": "claude_code.cost.usage",
                        "@resource.tool": "claude-code",
                        "@resource.repo": "acme/lab",
                        "@resource.project": "lab",
                        "model": "opus",
                    },
                    [(DAY, 7.0)],
                )
            ]
        }
    )
    store = ObsStore(db_path=tmp_path / "lab.db")
    CodingSource(client=client).harvest(store)
    raw = json.loads(store.list_sessions("coding")[0]["raw_json"])
    assert raw["by_repo"]["acme/lab"]["cost_usd"] == 7.0
    assert raw["by_repo"]["acme/lab"]["project"] == "lab"
    store.close()
