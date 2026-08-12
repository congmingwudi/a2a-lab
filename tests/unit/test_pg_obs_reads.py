"""PgObsStore's read side (WS13 item 6 / D49) — fake PgClient, no AWS.

Why these exist. The console's Observability section read the sqlite store
unconditionally while the hosted harvest filled Aurora, so the dashboard
rendered the laptop's copy and a container rendered nothing. Postgres is now
the source of truth, which means these six methods carry the section — and two
of them cannot be written the obvious way, because the RDS Data API refuses any
result over 1 MB.
"""

from __future__ import annotations

import json

from observability.pg import CALLER_RIDER_SQL, LAB_TRACE_RIDER_SQL, PgObsStore


class FakePg:
    """Records SQL and replays canned rows, matched by a fragment of the query."""

    def __init__(self, by_fragment: dict[str, list] | None = None):
        self.by_fragment = by_fragment or {}
        self.calls: list[tuple[str, dict]] = []
        self.batch_calls: list[tuple[str, list]] = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params or {}))
        for fragment, rows in self.by_fragment.items():
            if fragment in sql:
                return rows(params) if callable(rows) else rows
        return []

    def execute_batch(self, sql, param_sets):
        self.batch_calls.append((sql, list(param_sets)))
        return len(param_sets)


def test_summary_rolls_up_sessions_events_harvest_and_tokens():
    store = PgObsStore(
        client=FakePg(
            {
                "COUNT(*) AS sessions": [{"platform": "claude", "sessions": 3}],
                "COUNT(*) AS events": [{"platform": "claude", "events": 40}],
                "last_harvest_at": [
                    {
                        "platform": "claude",
                        "last_harvest_at": 1.0,
                        "status": "ok",
                        "detail": "",
                    }
                ],
                "usage_json IS NOT NULL": [
                    {
                        "platform": "claude",
                        "usage_json": json.dumps({"input_tokens": 10, "output_tokens": 5}),
                    },
                    {
                        "platform": "claude",
                        "usage_json": json.dumps({"input_tokens": 1, "est_cost_usd": 0.25}),
                    },
                ],
            }
        )
    )
    out = store.summary()["platforms"]["claude"]
    assert out["sessions"] == 3
    assert out["events"] == 40
    assert out["harvest"]["status"] == "ok"
    assert out["tokens"] == 16  # 10 + 5 + 1, folded across both rows
    assert out["est_cost_usd"] == 0.25


def test_summary_survives_one_unparsable_usage_row():
    """The panel is a coverage report; one bad row must cost that row only."""
    store = PgObsStore(
        client=FakePg(
            {
                "COUNT(*) AS sessions": [{"platform": "adk", "sessions": 1}],
                "usage_json IS NOT NULL": [
                    {"platform": "adk", "usage_json": "not json at all"},
                    {"platform": "adk", "usage_json": json.dumps({"input_tokens": 7})},
                ],
            }
        )
    )
    assert store.summary()["platforms"]["adk"]["tokens"] == 7


def test_list_sessions_orders_by_coalesced_created_at():
    """created_at is nullable and Postgres sorts NULL FIRST under DESC — without
    the COALESCE the null rows would consume the LIMIT and hide real sessions."""
    store = PgObsStore(client=FakePg({"FROM lab.obs_sessions s": [{"platform": "claude"}]}))
    store.list_sessions(limit=5)
    sql, params = store.client.calls[0]
    assert "ORDER BY COALESCE(s.created_at, '') DESC" in sql
    assert params["limit"] == 5
    assert "platform" not in params  # no filter -> no WHERE clause
    assert "WHERE s.platform" not in sql


def test_list_sessions_filters_by_platform_when_given():
    store = PgObsStore(client=FakePg({"FROM lab.obs_sessions s": []}))
    store.list_sessions(platform="foundry")
    sql, params = store.client.calls[0]
    assert "WHERE s.platform = :platform" in sql
    assert params["platform"] == "foundry"


def test_list_sessions_omits_raw_json_by_default():
    """raw_json is unbounded (100k chars/row) and this returns up to `limit`
    rows, so it must stay OUT of the default SELECT or a chatty platform blows
    the Data API's ~1 MB result budget."""
    store = PgObsStore(client=FakePg({"FROM lab.obs_sessions s": [{"platform": "claude"}]}))
    store.list_sessions("claude")
    sql, _ = store.client.calls[0]
    assert "raw_json" not in sql


def test_list_sessions_includes_raw_json_when_opted_in():
    """The coding-telemetry tiles read sessions/metrics from raw_json. sqlite's
    `SELECT s.*` always returned it, so those fields worked locally and silently
    read empty against Aurora — this is the query that has to opt in."""
    store = PgObsStore(client=FakePg({"FROM lab.obs_sessions s": [{"platform": "coding"}]}))
    store.list_sessions("coding", include_raw=True)
    sql, _ = store.client.calls[0]
    assert "s.raw_json::text AS raw_json" in sql


def test_list_events_pages_to_fit_the_data_api_size_cap():
    """A 1 MB cap against 100_000-char rows means 9 rows per statement. The page
    size is derived from the widest row in THIS session, because payload sizes
    differ by two orders of magnitude between platforms."""
    pages: list[dict] = []

    def event_rows(params):
        # 20 events exist; hand back one page at a time.
        start, limit = params["offset"], params["limit"]
        pages.append({"offset": start, "limit": limit})
        return [
            {"event_id": f"e{i}", "raw_json": "x"} for i in range(start, min(start + limit, 20))
        ]

    store = PgObsStore(
        client=FakePg(
            {"MAX(LENGTH(raw_json::text))": [{"mx": 100_000}], "LIMIT :limit": event_rows}
        )
    )
    out = store.list_events("claude", "sesn_1")
    assert len(out) == 20
    assert pages[0]["limit"] == 9  # 900_000 // 100_000
    assert [p["offset"] for p in pages] == [0, 9, 18]


def test_list_events_handles_an_empty_session_without_dividing_by_zero():
    store = PgObsStore(
        client=FakePg({"MAX(LENGTH(raw_json::text))": [{"mx": 0}], "LIMIT :limit": []})
    )
    assert store.list_events("claude", "nope") == []


def test_riders_extract_in_sql_not_in_python():
    """The matching raw_json totals ~3.6 MB, so pulling it back to regex locally
    fails outright. Only the short captured value may cross the wire."""
    store = PgObsStore(
        client=FakePg(
            {
                "MIN(substring": [
                    {
                        "platform": "claude",
                        "native_session_id": "sesn_1",
                        "rider": "agentforce-twin-via-bridge",
                    }
                ]
            }
        )
    )
    assert store.session_callers() == {"claude:sesn_1": "agentforce-twin-via-bridge"}
    sql, params = store.client.calls[0]
    assert "raw_json::text AS raw_json" not in sql  # never fetch the payload
    assert params["pattern"] == CALLER_RIDER_SQL
    assert params["needle"] == "%caller-agent%"


def test_lab_trace_riders_use_their_own_pattern():
    store = PgObsStore(client=FakePg({"MIN(substring": []}))
    store.session_lab_traces()
    _, params = store.client.calls[0]
    assert params["pattern"] == LAB_TRACE_RIDER_SQL
    assert params["needle"] == "%lab-trace%"


def test_riders_drop_sessions_whose_capture_came_back_null():
    store = PgObsStore(
        client=FakePg(
            {
                "MIN(substring": [
                    {"platform": "claude", "native_session_id": "a", "rider": None},
                    {"platform": "claude", "native_session_id": "b", "rider": "caller-x"},
                ]
            }
        )
    )
    assert store.session_callers() == {"claude:b": "caller-x"}


def test_the_two_rider_patterns_agree_with_their_python_twins():
    """Two dialects of the same rule (Postgres ARE and Python re) exist only
    because of the size cap. If they drift, the console and any local sqlite
    fallback silently disagree about who called whom."""
    from observability.store import CALLER_RIDER_RE, LAB_TRACE_RIDER_RE

    assert CALLER_RIDER_SQL == CALLER_RIDER_RE.pattern
    assert LAB_TRACE_RIDER_SQL == LAB_TRACE_RIDER_RE.pattern


def test_store_selection_defaults_to_postgres(monkeypatch, tmp_path):
    """The bug D49 fixes was two selectors with OPPOSITE defaults: obs_harvest
    defaulted to sqlite, the console hardcoded sqlite, and the hosted harvest
    wrote Aurora. One function decides now, and it defaults to the source of
    truth."""
    from observability import make_obs_store
    from observability.pg import PgObsStore

    monkeypatch.delenv("A2ALAB_OBS_STORE", raising=False)
    monkeypatch.setenv("A2ALAB_PG_CLUSTER_ARN", "arn:aws:rds:us-east-1:x:cluster:y")
    monkeypatch.setenv("A2ALAB_PG_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:x:secret:z")
    monkeypatch.setattr("observability.pg.PgClient.from_env", classmethod(lambda cls: object()))
    assert isinstance(make_obs_store(), PgObsStore)


def test_store_selection_honours_an_explicit_sqlite_request(monkeypatch, tmp_path):
    """Kept deliberately: working on a harvested snapshot with no AWS session."""
    from observability import ObsStore, make_obs_store

    monkeypatch.setenv("A2ALAB_TRACE_DIR", str(tmp_path))
    monkeypatch.setenv("A2ALAB_OBS_STORE", "sqlite")
    store = make_obs_store()
    assert isinstance(store, ObsStore)
    store.close()


def test_store_selection_falls_back_when_postgres_is_not_configured(monkeypatch, tmp_path):
    """A fresh checkout with no Aurora must still run."""
    from observability import ObsStore, make_obs_store

    monkeypatch.setenv("A2ALAB_TRACE_DIR", str(tmp_path))
    monkeypatch.delenv("A2ALAB_OBS_STORE", raising=False)
    for var in ("A2ALAB_PG_CLUSTER_ARN", "A2ALAB_PG_SECRET_ARN", "A2ALAB_PG_DSN"):
        monkeypatch.delenv(var, raising=False)
    store = make_obs_store()
    assert isinstance(store, ObsStore)
    store.close()


def test_list_briefs_windows_by_days_without_backfilling():
    """The console shows a rolling 7 days (D56). `days` is separate from
    `limit` on purpose: a quiet week must show FEW briefs rather than reaching
    back for older ones to fill a count — "nothing was written this week" is
    the state the panel most needs to be able to say, and the one a
    latest-brief view hid for eleven days."""
    store = PgObsStore(client=FakePg({"FROM lab.obs_briefs": []}))
    store.list_briefs(kind="observability", days=7)
    sql, params = store.client.calls[0]
    assert "kind = :kind" in sql
    assert "created_at >= now() - (:days * INTERVAL '1 day')" in sql
    assert params["days"] == 7 and params["kind"] == "observability"


def test_list_briefs_without_a_window_is_unbounded_by_date():
    """The analyst's own feed asks "what have I written before" and wants
    history, not this week."""
    store = PgObsStore(client=FakePg({"FROM lab.obs_briefs": []}))
    store.list_briefs()
    sql, params = store.client.calls[0]
    assert "make_interval" not in sql
    assert "WHERE" not in sql
    assert "days" not in params


# ---- usage analytics (WS18) ------------------------------------------------


def test_record_usage_inserts_pii_free_row():
    """record_usage writes exactly the columns the schema holds, clipping over-
    long fields; occurred_at is the DB's now(), never a client clock."""
    store = PgObsStore(client=FakePg({"INSERT INTO lab.usage_events": []}))
    store.record_usage(
        "nav",
        visitor_id="v1",
        persona="ryan",
        role="operator",
        country="US",
        locale="en-US",
        section="experiment",
        detail={"name": "sf-consult"},
    )
    sql, params = store.client.calls[0]
    assert "INSERT INTO lab.usage_events" in sql
    assert "occurred_at" not in sql  # defaulted to now() by the DDL
    assert params["event"] == "nav"
    assert params["section"] == "experiment"
    assert params["country"] == "US"
    assert '"name": "sf-consult"' in params["detail"]


def test_record_usage_nulls_empty_optional_fields():
    """An anonymous visit has no persona/role; those must be NULL, not ''."""
    store = PgObsStore(client=FakePg({"INSERT INTO lab.usage_events": []}))
    store.record_usage("site_visit", visitor_id="v2", persona=None, role="")
    _sql, params = store.client.calls[0]
    assert params["persona"] is None
    assert params["role"] is None
    assert params["detail"] is None


def test_usage_stats_windowed_query_bounds_by_interval():
    """A windowed call bounds every aggregate by the multiplied interval (the
    Data API rejects make_interval on a bigint — same trap as list_briefs)."""
    store = PgObsStore(client=FakePg({"FROM lab.usage_events": []}))
    store.usage_stats(days=7)
    joined = " ".join(sql for sql, _ in store.client.calls)
    assert "occurred_at >= now() - (:days * INTERVAL '1 day')" in joined
    assert "make_interval" not in joined
    assert all(p.get("days") == 7 for _sql, p in store.client.calls)


def test_usage_stats_all_time_has_no_date_bound():
    store = PgObsStore(client=FakePg({"FROM lab.usage_events": []}))
    out = store.usage_stats(days=None)
    joined = " ".join(sql for sql, _ in store.client.calls)
    assert "INTERVAL '1 day'" not in joined
    assert out["window_days"] is None


def test_usage_stats_shapes_totals_from_rows():
    """The totals block is coerced to ints and merged with the returning
    count, so a caller renders numbers even when a sub-query returns nothing."""
    store = PgObsStore(
        client=FakePg(
            {
                "AS visits": [
                    {
                        "visits": 12,
                        "logins": 3,
                        "navs": 40,
                        "unique_visitors": 5,
                    }
                ],
                "HAVING count(DISTINCT": [{"n": 2}],
            }
        )
    )
    out = store.usage_stats(days=30)
    assert out["totals"]["visits"] == 12
    assert out["totals"]["unique_visitors"] == 5
    assert out["totals"]["returning_visitors"] == 2
    assert out["by_country"] == []  # no canned rows -> empty, not a crash
    # Login breakdowns are always present, empty when no rows match.
    assert out["logins_by_role"] == []
    assert out["logins_by_country"] == []


def test_usage_stats_breaks_logins_down_by_role_and_country():
    """The Monitoring Visitors tab shows who signed in and from where — both
    aggregates filter to persona_login, and role/country default to a label
    (never dropped) so an older null row still counts."""
    store = PgObsStore(
        client=FakePg(
            {
                "ORDER BY logins DESC, role": [
                    {"role": "master of the universe", "logins": 4, "people": 1},
                    {"role": "operator", "logins": 2, "people": 2},
                ],
                "ORDER BY logins DESC, country": [
                    {"country": "US", "logins": 5},
                    {"country": "GB", "logins": 1},
                ],
            }
        )
    )
    out = store.usage_stats(days=7)
    joined = " ".join(sql for sql, _ in store.client.calls)
    assert joined.count("event = 'persona_login'") >= 2  # one per login aggregate
    assert out["logins_by_role"][0]["role"] == "master of the universe"
    assert out["logins_by_role"][0]["people"] == 1
    assert out["logins_by_country"][0]["country"] == "US"


# --------------------------------------------------------------------------- #
# infra_metrics write side (Track B) — batched to fit the Data API budget
# --------------------------------------------------------------------------- #


def test_upsert_metrics_batches_one_call_with_shaped_param_sets():
    """The write goes through execute_batch (one round trip), not a per-row
    execute loop that overran a two-minute budget over the Data API. Each row's
    labels are JSON-clipped and harvested_at is stamped once."""
    store = PgObsStore(client=FakePg())
    rows = [
        {
            "cloud": "aws",
            "resource": "obs-aurora",
            "metric": "ACUUtilization",
            "ts_at": "2026-08-11T00:00:00Z",
            "value": 12.5,
            "unit": "Percent",
            "labels": {"stat": "Average"},
        },
        {
            "cloud": "gcp",
            "resource": "agent-engine",
            "metric": "x/cpu",
            "ts_at": "2026-08-11T00:00:00Z",
            "value": 1.0,
        },
    ]
    n = store.upsert_metrics(rows)
    assert n == 2
    assert len(store.client.batch_calls) == 1  # one batched call, not two executes
    sql, param_sets = store.client.batch_calls[0]
    assert "INSERT INTO lab.infra_metrics" in sql and "ON CONFLICT" in sql
    assert [p["cloud"] for p in param_sets] == ["aws", "gcp"]
    assert '"stat": "Average"' in param_sets[0]["labels"]
    assert param_sets[1]["labels"] is None and param_sets[1]["unit"] is None
    # one harvested_at, stamped once for the whole grid
    assert param_sets[0]["harvested_at"] == param_sets[1]["harvested_at"]


def test_upsert_metrics_empty_is_a_noop():
    store = PgObsStore(client=FakePg())
    assert store.upsert_metrics([]) == 0
    assert store.client.batch_calls == []


class _FakeRds:
    """Captures batch_execute_statement calls to prove chunking."""

    def __init__(self):
        self.batches: list[list] = []

    def batch_execute_statement(self, **kw):
        self.batches.append(kw["parameterSets"])
        return {}


def test_execute_batch_chunks_under_the_data_api_ceiling():
    from observability.pg import PgClient

    client = PgClient(cluster_arn="arn:aws:rds:us-east-1:x:cluster:c", secret_arn="s")
    client._rds = _FakeRds()
    param_sets = [{"cloud": "aws", "n": i} for i in range(450)]
    n = client.execute_batch("INSERT ...", param_sets)
    assert n == 450
    # 450 rows / 200-row chunks -> 3 calls (200, 200, 50)
    assert [len(b) for b in client._rds.batches] == [200, 200, 50]
    # each parameter set is Data-API-typed name/value pairs
    first = client._rds.batches[0][0]
    assert {"name": "cloud", "value": {"stringValue": "aws"}} in first


# --------------------------------------------------------------------------- #
# infra_metrics read side (Track B) — SQL-side downsample, label-only from jsonb
# --------------------------------------------------------------------------- #


def test_infra_metrics_series_groups_and_shapes_from_rows():
    """Rows arrive already grouped/ordered (SELECT ... ORDER BY cloud, resource,
    metric, ts_at); the shared shaper packages them into one entry per series
    with the metadata the Metrics tab needs — no second query."""
    store = PgObsStore(
        client=FakePg(
            {
                "FROM lab.infra_metrics": [
                    {
                        "cloud": "aws",
                        "resource": "obs-aurora",
                        "metric": "ACUUtilization",
                        "ts_at": "2026-08-11T00:00:00Z",
                        "value": 10.0,
                        "unit": "Percent",
                        "label": "ACU utilization",
                        "harvested_at": 1.0,
                    },
                    {
                        "cloud": "aws",
                        "resource": "obs-aurora",
                        "metric": "ACUUtilization",
                        "ts_at": "2026-08-11T00:05:00Z",
                        "value": 12.0,
                        "unit": "Percent",
                        "label": "ACU utilization",
                        "harvested_at": 1.0,
                    },
                    {
                        "cloud": "gcp",
                        "resource": "agent-engine",
                        "metric": "x/cpu",
                        "ts_at": "2026-08-11T00:00:00Z",
                        "value": None,
                        "unit": None,
                        "label": None,
                        "harvested_at": 1.0,
                    },
                ]
            }
        )
    )
    out = store.infra_metrics_series()
    assert [s["cloud"] for s in out] == ["aws", "gcp"]
    aws = out[0]
    assert aws["count"] == 2
    assert aws["first_at"] == "2026-08-11T00:00:00Z"
    assert aws["last_at"] == "2026-08-11T00:05:00Z"
    assert aws["last_value"] == 12.0
    assert aws["label"] == "ACU utilization"
    assert aws["points"] == [
        {"t": "2026-08-11T00:00:00Z", "v": 10.0},
        {"t": "2026-08-11T00:05:00Z", "v": 12.0},
    ]
    # a null-only series still appears, with last_value None
    assert out[1]["last_value"] is None


def test_infra_metrics_series_downsamples_in_sql_not_twice():
    """The Data API refuses results over 1 MB, so the SELECT strides each series
    to ~max_points server-side. The Python shaper must NOT thin again — that
    would drop the evenly-strided rows to a second, misaligned grid. The SQL
    carries a stride expression and binds max_points."""
    store = PgObsStore(client=FakePg({"FROM lab.infra_metrics": []}))
    store.infra_metrics_series(max_points=50)
    sql, params = store.client.calls[0]
    assert "row_number() OVER" in sql
    assert "PARTITION BY cloud, resource, metric" in sql
    assert params["maxpoints"] == 50
    assert "since" not in params  # no window -> no WHERE
    assert "ts_at >=" not in sql


def test_infra_metrics_series_windows_when_given_a_since():
    store = PgObsStore(client=FakePg({"FROM lab.infra_metrics": []}))
    store.infra_metrics_series(since_iso="2026-08-10T00:00:00Z")
    sql, params = store.client.calls[0]
    assert "ts_at >= :since" in sql
    assert params["since"] == "2026-08-10T00:00:00Z"
