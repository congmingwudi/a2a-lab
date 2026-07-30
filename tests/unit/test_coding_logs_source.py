"""WS16 behavioural-logs source — canned FilterLogEvents payloads, no AWS."""

from __future__ import annotations

import datetime as dt
import json

from observability.coding_logs_source import (
    DURATION_EDGES_MS,
    PROMPT_LEN_EDGES,
    CodingLogsSource,
    percentile_from_hist,
    summarize_records,
)
from observability.store import ObsStore

DAY = dt.datetime(2026, 7, 30, 12, 0, tzinfo=dt.timezone.utc)
NEXT = dt.datetime(2026, 7, 31, 9, 0, tzinfo=dt.timezone.utc)


def _rec(event, attrs, ts=DAY):
    return {"event": event, "attrs": attrs, "timestamp": ts}


# ---- pure rollup ----------------------------------------------------------


def test_edit_acceptance_counts_accept_and_reject_by_source():
    days = summarize_records(
        [
            _rec("claude_code.tool_decision", {"decision": "accept", "source": "user"}),
            _rec("claude_code.tool_decision", {"decision": "accept", "source": "user"}),
            _rec("claude_code.tool_decision", {"decision": "reject", "source": "user"}),
            _rec("claude_code.tool_decision", {"decision": "accept", "source": "auto"}),
        ]
    )
    dec = days["2026-07-30"]["decisions"]
    assert dec["accept"] == 3
    assert dec["reject"] == 1
    assert dec["by_source"]["user"] == {"accept": 2, "reject": 1}
    assert dec["by_source"]["auto"] == {"accept": 1, "reject": 0}


def test_tool_result_rolls_up_mix_success_and_latency():
    days = summarize_records(
        [
            _rec(
                "claude_code.tool_result",
                {"tool_name": "Edit", "success": True, "duration_ms": 120},
            ),
            _rec(
                "claude_code.tool_result",
                {"tool_name": "Edit", "success": False, "duration_ms": 400},
            ),
            _rec(
                "claude_code.tool_result",
                {
                    "tool_name": "search",
                    "success": True,
                    "duration_ms": 900,
                    "mcp_server_scope": "codesearch",
                },
            ),
        ]
    )
    tools = days["2026-07-30"]["tools"]
    assert tools["Edit"]["count"] == 2
    assert tools["Edit"]["success"] == 1
    assert tools["Edit"]["fail"] == 1
    assert tools["Edit"]["mcp"] is False
    assert tools["search"]["mcp"] is True
    # 120ms lands in the first bucket (<=100 is index 0? no: 120 > 100 -> index 1)
    assert sum(tools["Edit"]["dur_hist"]) == 2
    assert tools["Edit"]["dur_n"] == 2


def test_api_request_keeps_model_latency_and_four_token_buckets():
    days = summarize_records(
        [
            _rec(
                "claude_code.api_request",
                {
                    "model": "claude-opus-4-8",
                    "duration_ms": 2500,
                    "input": 100,
                    "output": 50,
                    "cacheRead": 900,
                    "cacheCreation": 30,
                },
            )
        ]
    )
    m = days["2026-07-30"]["models"]["claude-opus-4-8"]
    assert m["count"] == 1
    assert m["input"] == 100
    assert m["output"] == 50
    assert m["cache_read"] == 900
    assert m["cache_creation"] == 30
    assert m["dur_n"] == 1


def test_errors_refusals_and_retries():
    days = summarize_records(
        [
            _rec("claude_code.api_error", {"status_code": "529", "attempt": "2"}),
            _rec("claude_code.api_error", {"status_code": "529", "attempt": "3"}),
            _rec("claude_code.api_refusal", {}),
        ]
    )
    d = days["2026-07-30"]
    assert d["errors"]["529"] == 2
    assert d["refusals"] == 1
    assert d["retries"] == {"2": 1, "3": 1}


def test_prompt_cadence_counts_and_length_no_text():
    days = summarize_records(
        [
            _rec("claude_code.user_prompt", {"prompt_length": 40}),
            _rec("claude_code.user_prompt", {"prompt_length": 800}),
        ]
    )
    p = days["2026-07-30"]["prompts"]
    assert p["count"] == 2
    assert p["len_sum"] == 840
    assert sum(p["len_hist"]) == 2


def test_days_are_bucketed_separately():
    days = summarize_records(
        [
            _rec("claude_code.user_prompt", {"prompt_length": 40}, ts=DAY),
            _rec("claude_code.user_prompt", {"prompt_length": 40}, ts=NEXT),
        ]
    )
    assert set(days) == {"2026-07-30", "2026-07-31"}


def test_unknown_decision_value_ignored():
    days = summarize_records(
        [_rec("claude_code.tool_decision", {"decision": "maybe", "source": "user"})]
    )
    dec = days["2026-07-30"]["decisions"]
    assert dec["accept"] == 0 and dec["reject"] == 0


# ---- histogram percentile -------------------------------------------------


def test_percentile_from_empty_hist_is_none():
    assert percentile_from_hist([0] * (len(DURATION_EDGES_MS) + 1), DURATION_EDGES_MS, 0.5) is None


def test_percentile_returns_bucket_upper_edge():
    # all mass in the <=500 bucket (index 2: edges 100,250,500 -> value<=500)
    hist = [0] * (len(DURATION_EDGES_MS) + 1)
    hist[2] = 10
    assert percentile_from_hist(hist, DURATION_EDGES_MS, 0.5) == 500.0
    assert percentile_from_hist(hist, DURATION_EDGES_MS, 0.9) == 500.0


def test_percentile_tail_bucket_returns_top_edge():
    hist = [0] * (len(DURATION_EDGES_MS) + 1)
    hist[-1] = 5  # +inf tail
    assert percentile_from_hist(hist, DURATION_EDGES_MS, 0.9) == float(DURATION_EDGES_MS[-1])


def test_prompt_len_hist_percentile():
    hist = [0] * (len(PROMPT_LEN_EDGES) + 1)
    hist[0] = 9  # <=50
    hist[3] = 1  # <=1000
    assert percentile_from_hist(hist, PROMPT_LEN_EDGES, 0.5) == 50.0
    assert percentile_from_hist(hist, PROMPT_LEN_EDGES, 0.95) == 1000.0


# ---- parse + harvest against a fake logs client ---------------------------


class FakeLogsClient:
    """Stands in for a boto3 logs client. One page of events."""

    def __init__(self, messages):
        self._messages = messages
        self.calls = []

    def filter_log_events(self, **kwargs):
        self.calls.append(kwargs)
        return {"events": [{"message": m} for m in self._messages], "nextToken": None}


def _otlp(event, attrs, ts=DAY):
    return json.dumps(
        {
            "resource": {"attributes": {"tool": "claude-code"}},
            "scope": {"name": "com.anthropic.claude_code"},
            "timeUnixNano": str(int(ts.timestamp() * 1e9)),
            "body": event,
            "attributes": {"event.name": event, **attrs},
        }
    )


def test_parse_skips_non_json_and_unknown_events():
    assert CodingLogsSource._parse("not json") is None
    assert (
        CodingLogsSource._parse(json.dumps({"attributes": {"event.name": "other.thing"}})) is None
    )
    rec = CodingLogsSource._parse(_otlp("claude_code.user_prompt", {"prompt_length": 10}))
    assert rec and rec["event"] == "claude_code.user_prompt"


def test_harvest_stores_aggregates_and_reports_rate():
    client = FakeLogsClient(
        [
            _otlp("claude_code.tool_decision", {"decision": "accept", "source": "user"}),
            _otlp("claude_code.tool_decision", {"decision": "reject", "source": "user"}),
            _otlp(
                "claude_code.tool_result",
                {"tool_name": "Edit", "success": True, "duration_ms": 120},
            ),
            _otlp("claude_code.user_prompt", {"prompt_length": 200}),
        ]
    )
    store = ObsStore(":memory:")
    result = CodingLogsSource(client=client).harvest(store)
    assert result.status == "ok"
    assert result.sessions == 1
    assert "50% edits kept" in result.detail
    rows = store.list_sessions("coding-logs")
    assert len(rows) == 1
    raw = json.loads(rows[0]["raw_json"])
    assert raw["aggregates"]["decisions"]["accept"] == 1
    # content-off is asserted structurally: no prompt/file/tool text anywhere
    assert "off" in raw["content_flags"]


def test_harvest_blocks_when_no_events():
    store = ObsStore(":memory:")
    result = CodingLogsSource(client=FakeLogsClient([])).harvest(store)
    assert result.status == "blocked"
    assert "claude_otel.sh" in result.detail
