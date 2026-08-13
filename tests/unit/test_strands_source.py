"""Unit tests for the Strands / Bedrock AgentCore harvest source (WS5/D66).

Injected boto3 clients — no AWS. Covers the blocked path (no ARN), the pure
rollups, and a full harvest against canned CloudWatch payloads.
"""

from __future__ import annotations

from observability.store import ObsStore
from observability.strands_source import (
    StrandsSource,
    log_group_for,
    runtime_id_from_arn,
    summarize_bedrock,
    summarize_requests,
)

# Account segment is a non-numeric placeholder on purpose: the repo commits no
# 12-digit account id (test_no_account_identifiers), and runtime_id_from_arn
# only reads the part after the last "/", so the account is irrelevant here.
ARN = "arn:aws:bedrock-agentcore:us-east-1:ACCOUNT_ID:runtime/a2alab_strands-a07goY5qhK"
MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def make_store(tmp_path):
    return ObsStore(tmp_path / "lab.db")


def test_pure_helpers():
    assert runtime_id_from_arn(ARN) == "a2alab_strands-a07goY5qhK"
    assert log_group_for("r-1") == "/aws/bedrock-agentcore/runtimes/r-1-DEFAULT"
    b = summarize_bedrock(
        {
            "input_tokens": 1_000_000,
            "output_tokens": 500_000,
            "invocations": 10,
            "latency_ms_avg": 900.0,
        }
    )
    # $0.80/M in + $4.00/M out -> 0.80 + 2.00
    assert b["est_cost_usd"] == 2.8
    assert b["invocations"] == 10
    r = summarize_requests(
        [
            'INFO: - "POST /invocations HTTP/1.1" 200 OK',
            '"POST /invocations HTTP/1.1" 500 Internal Server Error',
            'GET /ping HTTP/1.1" 200 OK',  # not an invocation -> ignored
        ]
    )
    assert r == {"ok": 1, "error": 1}


def test_harvest_blocked_without_arn(tmp_path, monkeypatch):
    monkeypatch.delenv("STRANDS_AGENTCORE_ARN", raising=False)
    result = StrandsSource().harvest(make_store(tmp_path))
    assert result.status == "blocked"
    assert "STRANDS_AGENTCORE_ARN" in result.detail


class _FakeCw:
    """Returns a fixed Datapoint per (MetricName, stat)."""

    _VALUES = {
        ("InputTokenCount", "Sum"): 8000.0,
        ("OutputTokenCount", "Sum"): 1000.0,
        ("Invocations", "Sum"): 9.0,
        ("InvocationLatency", "Average"): 1800.0,
    }

    def get_metric_statistics(self, **kw):
        key = (kw["MetricName"], kw["Statistics"][0])
        stat = kw["Statistics"][0]
        return {"Datapoints": [{stat: self._VALUES[key]}]}


class _FakeLogs:
    def filter_log_events(self, **kw):
        return {
            "events": [
                {"message": 'INFO: 127.0.0.1 - "POST /invocations HTTP/1.1" 200 OK'},
                {"message": 'INFO: 127.0.0.1 - "POST /invocations HTTP/1.1" 200 OK'},
                {
                    "message": 'INFO: 127.0.0.1 - "POST /invocations HTTP/1.1" 500 Internal Server Error'
                },
            ]
        }


def test_harvest_ok_with_metrics_and_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("STRANDS_AGENTCORE_ARN", ARN)
    monkeypatch.setenv("STRANDS_MODEL_ID", MODEL)
    store = make_store(tmp_path)
    result = StrandsSource(cw_client=_FakeCw(), logs_client=_FakeLogs()).harvest(store)

    assert result.status == "ok"
    assert result.sessions == 1 and result.events == 1

    (session,) = store.list_sessions("strands")
    assert session["native_id"] == "a2alab_strands-a07goY5qhK"

    events = store.list_events("strands", "a2alab_strands-a07goY5qhK")
    assert len(events) == 1
    summary = events[0]["summary"]
    assert "2 ok / 1 error" in summary
    assert "8000 in / 1000 out tokens" in summary


def test_harvest_soft_fails_metrics_but_keeps_requests(tmp_path, monkeypatch):
    """A CloudWatch metrics hiccup must not lose the runtime request rollup."""
    monkeypatch.setenv("STRANDS_AGENTCORE_ARN", ARN)
    monkeypatch.setenv("STRANDS_MODEL_ID", MODEL)

    class _BadCw:
        def get_metric_statistics(self, **kw):
            raise RuntimeError("throttled")

    store = make_store(tmp_path)
    result = StrandsSource(cw_client=_BadCw(), logs_client=_FakeLogs()).harvest(store)
    assert result.status == "ok"  # request rollup still succeeded
    assert "Bedrock model metrics unavailable" in result.detail


def test_harvest_errors_when_log_group_unreadable(tmp_path, monkeypatch):
    monkeypatch.setenv("STRANDS_AGENTCORE_ARN", ARN)
    monkeypatch.setenv("STRANDS_MODEL_ID", MODEL)

    class _BadLogs:
        def filter_log_events(self, **kw):
            raise RuntimeError("AccessDenied")

    store = make_store(tmp_path)
    result = StrandsSource(cw_client=_FakeCw(), logs_client=_BadLogs()).harvest(store)
    assert result.status == "error"
    assert "AccessDenied" in result.detail
