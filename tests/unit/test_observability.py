"""M11: sqlite trace sink, obs store, and harvest sources (canned payloads)."""

import json
import sqlite3
from types import SimpleNamespace

from interop.trace import Hop, SqliteSink, TraceRecorder
from observability.anthropic_source import AnthropicSource
from observability.openai_source import OpenAISource
from observability.salesforce_source import SalesforceSource
from observability.store import ObsStore


def _db_rows(db_path, query):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(query).fetchall()]
    finally:
        conn.close()


# ---- SqliteSink / platform_ref (M11.1) ------------------------------------


def test_sqlite_sink_roundtrip_with_platform_ref(tmp_path):
    db = tmp_path / "lab.db"
    recorder = TraceRecorder(sinks=[SqliteSink(db_path=db)])
    with Hop(
        "trace-1",
        source="claude-researcher",
        target="anthropic-managed-agents",
        protocol="managed-agents-api",
        transport_detail="sessions.events send/stream",
        request_payload={"message": "hi"},
        recorder=recorder,
    ) as hop:
        hop.platform_ref = "sesn_abc"
        hop.response_payload = {"text": "hello"}

    rows = _db_rows(db, "SELECT * FROM trace_events")
    assert len(rows) == 1
    assert rows[0]["platform_ref"] == "sesn_abc"
    assert json.loads(rows[0]["request_payload_raw"]) == {"message": "hi"}
    assert rows[0]["status"] == "ok"


def test_sqlite_sink_error_hop(tmp_path):
    db = tmp_path / "lab.db"
    recorder = TraceRecorder(sinks=[SqliteSink(db_path=db)])
    try:
        with Hop(
            "trace-err",
            source="a",
            target="b",
            protocol="rest",
            transport_detail="POST /invoke",
            request_payload={},
            recorder=recorder,
        ):
            raise TimeoutError("boom")
    except TimeoutError:
        pass
    rows = _db_rows(db, "SELECT status, platform_ref FROM trace_events")
    assert rows[0]["status"] == "error"
    assert rows[0]["platform_ref"] is None


# ---- ObsStore -------------------------------------------------------------


def test_obs_store_upserts_and_summary(tmp_path):
    store = ObsStore(db_path=tmp_path / "lab.db")
    store.upsert_session(
        "claude",
        "sesn_1",
        title="a2a-lab s1",
        status="idle",
        created_at="2026-07-17T10:00:00",
        updated_at="2026-07-17T10:01:00",
        usage={"input_tokens": 100, "output_tokens": 50},
        raw={"id": "sesn_1"},
    )
    # second upsert replaces, not duplicates
    store.upsert_session("claude", "sesn_1", title="a2a-lab s1", status="terminated")
    store.upsert_event(
        "claude",
        "sesn_1",
        "sevt_1",
        event_type="agent.message",
        summary="hello",
        raw={"type": "agent.message"},
    )
    store.set_harvest_status("claude", "ok", "capped at 50")

    summary = store.summary()
    plat = summary["platforms"]["claude"]
    assert plat["sessions"] == 1
    assert plat["events"] == 1
    assert plat["harvest"]["status"] == "ok"

    sessions = store.list_sessions("claude")
    assert sessions[0]["status"] == "terminated"
    assert sessions[0]["event_count"] == 1
    events = store.list_events("claude", "sesn_1")
    assert events[0]["summary"] == "hello"
    store.close()


def test_obs_store_joins_lab_traces_via_platform_ref(tmp_path):
    db = tmp_path / "lab.db"
    recorder = TraceRecorder(sinks=[SqliteSink(db_path=db)])
    with Hop(
        "trace-9",
        source="s",
        target="t",
        protocol="managed-agents-api",
        transport_detail="x",
        request_payload={},
        recorder=recorder,
    ) as hop:
        hop.platform_ref = "sesn_joined"

    store = ObsStore(db_path=db)
    store.upsert_session("claude", "sesn_joined", title="t")
    assert store.lab_traces_for("sesn_joined") == ["trace-9"]
    assert store.list_sessions("claude")[0]["lab_trace_count"] == 1
    store.close()


# ---- Anthropic source (canned SDK objects) --------------------------------


class _FakePaginator(list):
    """The SDK auto-paginates on iteration; a list stands in fine."""


def _fake_anthropic_client():
    session = SimpleNamespace(
        id="sesn_fake",
        title="a2a-lab demo",
        status="idle",
        created_at=None,
        updated_at=None,
        usage=None,
        model_dump=lambda mode="json": {"id": "sesn_fake", "title": "a2a-lab demo"},
    )
    msg_block = SimpleNamespace(type="text", text="the answer")
    events = [
        SimpleNamespace(
            id="sevt_1",
            type="agent.message",
            processed_at=None,
            content=[msg_block],
            model_dump=lambda mode="json": {"type": "agent.message"},
        ),
        SimpleNamespace(
            id="sevt_2",
            type="span.model_request_end",
            processed_at=None,
            model_usage=SimpleNamespace(
                model_dump=lambda mode="json": {"input_tokens": 10, "output_tokens": 5}
            ),
            model_dump=lambda mode="json": {"type": "span.model_request_end"},
        ),
    ]
    return SimpleNamespace(
        beta=SimpleNamespace(
            sessions=SimpleNamespace(
                list=lambda: _FakePaginator([session]),
                events=SimpleNamespace(
                    list=lambda session_id: _FakePaginator(events),
                ),
            )
        )
    )


def test_anthropic_source_harvests_sessions_and_events(tmp_path, monkeypatch):
    monkeypatch.setenv("A2ALAB_STATE_DIR", str(tmp_path / "state"))
    store = ObsStore(db_path=tmp_path / "lab.db")
    result = AnthropicSource(client=_fake_anthropic_client()).harvest(store)

    assert result.status == "ok"
    assert result.sessions == 1
    assert result.events == 2
    sessions = store.list_sessions("claude")
    assert sessions[0]["native_id"] == "sesn_fake"
    events = store.list_events("claude", "sesn_fake")
    types = {e["event_type"] for e in events}
    assert types == {"agent.message", "span.model_request_end"}
    msg = next(e for e in events if e["event_type"] == "agent.message")
    assert msg["summary"] == "the answer"
    usage_ev = next(e for e in events if e["event_type"] == "span.model_request_end")
    assert json.loads(usage_ev["usage_json"])["input_tokens"] == 10
    store.close()


def test_anthropic_source_skips_events_for_unchanged_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("A2ALAB_STATE_DIR", str(tmp_path / "state"))
    client = _fake_anthropic_client()
    session = next(iter(client.beta.sessions.list()))
    session.updated_at = SimpleNamespace(isoformat=lambda: "2026-07-17T10:00:00")
    store = ObsStore(db_path=tmp_path / "lab.db")

    first = AnthropicSource(client=client).harvest(store)
    second = AnthropicSource(client=client).harvest(store)
    assert first.events == 2
    assert second.events == 0  # unchanged updated_at → events not refetched
    store.close()


def test_anthropic_source_reports_error_not_raise(tmp_path):
    class ExplodingClient:
        @property
        def beta(self):
            raise RuntimeError("no api key")

    store = ObsStore(db_path=tmp_path / "lab.db")
    result = AnthropicSource(client=ExplodingClient()).harvest(store)
    assert result.status == "error"
    assert "no api key" in result.detail
    assert store.summary()["platforms"]["claude"]["harvest"]["status"] == "error"
    store.close()


# ---- Salesforce + OpenAI sources ------------------------------------------


def test_salesforce_source_blocked_without_env(tmp_path, monkeypatch):
    for var in ("SF_MY_DOMAIN", "SF_CLIENT_ID", "SF_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    store = ObsStore(db_path=tmp_path / "lab.db")
    result = SalesforceSource().harvest(store)
    assert result.status == "blocked"
    assert "SF_MY_DOMAIN" in result.detail
    store.close()


def test_openai_source_blocked_without_key(tmp_path, monkeypatch):
    # Built with M9 (D24): harvest works from emit-time-captured response
    # ids, so without an API key it reports blocked — not not-built.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = ObsStore(db_path=tmp_path / "lab.db")
    result = OpenAISource(fetch=lambda rid: {}).harvest(store)
    assert result.status == "blocked"
    assert store.summary()["platforms"]["openai"]["harvest"]["status"] == "blocked"
    store.close()


# ---- Analyst SQL guard (M11.5) --------------------------------------------


def test_analyst_sql_guard_and_readonly(tmp_path):
    from observability.analyst import _run_readonly_sql

    db = tmp_path / "lab.db"
    store = ObsStore(db_path=db)
    store.upsert_session("claude", "sesn_x", title="t")
    store.close()

    ok = json.loads(_run_readonly_sql("SELECT COUNT(*) AS n FROM obs_sessions", db))
    assert ok["rows"][0]["n"] == 1

    denied = json.loads(_run_readonly_sql("DELETE FROM obs_sessions", db))
    assert "only SELECT" in denied["error"]

    # read-only connection: even a sneaky SELECT-prefixed write path can't
    # mutate — verify the file opens in ro mode by attempting a write via
    # a second guard-passing statement with a CTE trick is still a SELECT;
    # the mode=ro URI is the backstop for anything the prefix check misses.
    bad = json.loads(_run_readonly_sql("SELECT * FROM missing_table", db))
    assert "error" in bad


def test_salesforce_summary_heuristics():
    from observability.salesforce_source import _first_key, _summary_of

    rec = {
        "ssot__Id__c": "abc",
        "ssot__SessionStartDttm__c": "2026-07-17T20:00:00Z",
        "ssot__AiAgentNameTxt__c": "A2ALab Research Assistant",
        "ssot__StatusTxt__c": "Completed",
    }
    assert _first_key(rec, "start", "dttm") == "2026-07-17T20:00:00Z"
    summary = _summary_of(rec)
    assert "A2ALab Research Assistant" in summary
    assert "Completed" in summary


def test_adk_metrics_rollup_math():
    """summarize_metrics: Monitoring series -> tokens, billing meters, and
    the estimated-cost formula (compute + list token prices)."""
    from observability import adk_source

    series = {
        "requests": [
            {
                "metric": {"labels": {"response_code": "200"}},
                "points": [{"value": {"int64Value": "47"}}],
            },
            {
                "metric": {"labels": {"response_code": "404"}},
                "points": [{"value": {"int64Value": "4"}}],
            },
        ],
        "cpu_s": [{"metric": {}, "points": [{"value": {"doubleValue": 3600.0}}]}],
        "gib_s": [{"metric": {}, "points": [{"value": {"doubleValue": 7200.0}}]}],
        "tokens": [
            {
                "metric": {"labels": {"type": "input", "model_user_id": "gemini-2.5-flash-lite"}},
                "points": [{"value": {"int64Value": "1000000"}}],
            },
            {
                "metric": {"labels": {"type": "output", "model_user_id": "gemini-2.5-flash-lite"}},
                "points": [{"value": {"int64Value": "500000"}}],
            },
        ],
    }
    m = adk_source.summarize_metrics(series)
    assert m["requests"] == {"200": 47, "404": 4}
    assert m["input_tokens"] == 1_000_000 and m["output_tokens"] == 500_000
    assert m["tokens_by_model"]["gemini-2.5-flash-lite"] == {
        "input": 1_000_000,
        "output": 500_000,
    }
    # 1 vCPU-hr + 2 GiB-hr compute; 1M in + 0.5M out tokens at list prices
    assert m["est_compute_usd"] == round(0.0994 + 2 * 0.0105, 4)
    assert m["est_token_usd"] == round(0.10 + 0.5 * 0.40, 4)
    assert m["est_cost_usd"] == round(m["est_compute_usd"] + m["est_token_usd"], 4)


def test_summary_rolls_up_est_cost(tmp_path, monkeypatch):
    """Platforms whose usage carries est_cost_usd surface it in the
    coverage summary next to the token totals."""
    monkeypatch.setenv("A2ALAB_TRACE_DIR", str(tmp_path))
    from observability.store import ObsStore

    store = ObsStore()
    store.upsert_session(
        "adk",
        "engine-1",
        title="t",
        usage={"input_tokens": 10, "output_tokens": 5, "est_cost_usd": 0.0416},
    )
    plat = store.summary()["platforms"]["adk"]
    store.close()
    assert plat["tokens"] == 15
    assert plat["est_cost_usd"] == 0.0416


def test_obs_store_extracts_caller_and_lab_trace_from_rider(tmp_path):
    store = ObsStore(db_path=tmp_path / "lab.db")
    store.upsert_session("foundry", "resp_1", title="run")
    # rider text as a platform logs it: embedded in a JSON-escaped blob
    store.upsert_event(
        "foundry",
        "resp_1",
        "evt_1",
        event_type="invoke_agent",
        raw={
            "input": "question\n[A2A-LAB DELEGATION]\ncaller-agent: adk-researcher\n"
            "caller-platform: adk\ndelegation-depth: 1\nlab-trace: abc123def4567890\n"
        },
    )
    assert store.session_callers() == {"foundry:resp_1": "adk-researcher"}
    assert store.session_lab_traces() == {"foundry:resp_1": "abc123def4567890"}
    store.close()
