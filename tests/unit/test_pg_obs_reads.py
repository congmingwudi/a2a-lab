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

    def execute(self, sql, params=None):
        self.calls.append((sql, params or {}))
        for fragment, rows in self.by_fragment.items():
            if fragment in sql:
                return rows(params) if callable(rows) else rows
        return []


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
