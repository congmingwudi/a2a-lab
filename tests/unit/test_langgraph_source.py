"""Unit tests for the LangGraph / LangSmith harvest source (WS4 item 7).

LangSmith is LangGraph's framework-native observability surface, so this source
reads the LangSmith runs API — the same "observe each platform through its own
telemetry" shape as adk_source (Vertex) and strands_source (CloudWatch). Unlike
those runtime-rollup sources, LangSmith exposes PER-TURN run trees, so the
mapping is one obs session per root run (a turn) with the LLM/tool child spans
as events. HTTP is faked with httpx.MockTransport — no network, no LangSmith.
"""

from __future__ import annotations

import httpx

from observability.langgraph_source import (
    LangGraphSource,
    event_rows,
    group_by_trace,
    summarize_session,
)
from observability.store import ObsStore

PROJECT_ID = "a63f89b0-27d4-4897-8075-916432ac5985"

# A canned run tree for one turn (trace T1): the root chain, one LLM span, one
# tool span (ask_agentforce), and one internal scaffolding chain that must NOT
# become an event. Shapes mirror a real LangSmith /runs/query payload.
ROOT = {
    "id": "T1",
    "parent_run_id": None,
    "trace_id": "T1",
    "name": "LangGraph",
    "run_type": "chain",
    "status": "success",
    "error": None,
    "prompt_tokens": 766,
    "completion_tokens": 63,
    "total_tokens": 829,
    "start_time": "2026-08-17T13:41:45.421731",
    "end_time": "2026-08-17T13:41:46.782996",  # 1.361265s -> 1361ms
    "tags": [],
    "extra": {"metadata": {"lab_trace_id": "lab-abc123"}},
}
LLM_CHILD = {
    "id": "C1",
    "parent_run_id": "T1",
    "trace_id": "T1",
    "name": "ChatAnthropic",
    "run_type": "llm",
    "status": "success",
    "prompt_tokens": 766,
    "completion_tokens": 63,
    "total_tokens": 829,
    "start_time": "2026-08-17T13:41:45.436571",
    "end_time": "2026-08-17T13:41:46.780695",
    "extra": {"metadata": {"ls_model_name": "claude-haiku-4-5-20251001"}},
}
TOOL_CHILD = {
    "id": "C2",
    "parent_run_id": "T1",
    "trace_id": "T1",
    "name": "ask_agentforce",
    "run_type": "tool",
    "status": "success",
    "start_time": "2026-08-17T13:41:45.500000",
    "end_time": "2026-08-17T13:41:46.000000",  # 500ms
    "extra": {"metadata": {}},
}
SCAFFOLD_CHILD = {
    "id": "C3",
    "parent_run_id": "T1",
    "trace_id": "T1",
    "name": "should_continue",
    "run_type": "chain",
    "status": "success",
    "start_time": "2026-08-17T13:41:46.781705",
    "end_time": "2026-08-17T13:41:46.782286",
    "extra": {"metadata": {}},
}
RUNS = [SCAFFOLD_CHILD, LLM_CHILD, TOOL_CHILD, ROOT]  # deliberately unordered


def make_store(tmp_path):
    return ObsStore(tmp_path / "lab.db")


def _mock_http(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---- pure helpers ----------------------------------------------------------


def test_group_by_trace_splits_root_and_children():
    grouped = group_by_trace(RUNS)
    assert set(grouped) == {"T1"}
    grp = grouped["T1"]
    assert grp["root"]["id"] == "T1"
    assert {c["id"] for c in grp["children"]} == {"C1", "C2", "C3"}


def test_summarize_session_rolls_up_and_finds_join():
    s = summarize_session(ROOT, [LLM_CHILD, TOOL_CHILD, SCAFFOLD_CHILD])
    assert s["model"] == "claude-haiku-4-5-20251001"
    assert s["total_tokens"] == 829
    assert s["prompt_tokens"] == 766 and s["completion_tokens"] == 63
    assert s["llm_calls"] == 1 and s["tool_calls"] == 1
    assert s["latency_ms"] == 1361  # 46.782996 - 45.421731
    assert s["lab_trace_id"] == "lab-abc123"  # the wire-trace join
    assert s["status"] == "success"


def test_event_rows_keeps_only_llm_and_tool_spans():
    rows = event_rows([LLM_CHILD, TOOL_CHILD, SCAFFOLD_CHILD])
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {"C1", "C2"}  # scaffolding chain dropped
    assert by_id["C1"]["event_type"] == "llm"
    assert "829" in by_id["C1"]["summary"]  # tokens surfaced
    assert by_id["C2"]["event_type"] == "tool"
    assert "ask_agentforce" in by_id["C2"]["summary"]


# ---- harvest orchestration -------------------------------------------------


def test_harvest_blocked_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    result = LangGraphSource().harvest(make_store(tmp_path))
    assert result.status == "blocked"
    assert "LANGSMITH_API_KEY" in result.detail


def test_harvest_ok_maps_session_events_and_join(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_fake")
    monkeypatch.setenv("LANGCHAIN_PROJECT", "a2a-lab")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sessions"):
            assert request.headers.get("x-api-key") == "lsv2_pt_fake"
            return httpx.Response(200, json=[{"id": PROJECT_ID, "name": "a2a-lab"}])
        if request.url.path.endswith("/runs/query"):
            return httpx.Response(200, json={"runs": RUNS})
        return httpx.Response(404)

    store = make_store(tmp_path)
    result = LangGraphSource(http=_mock_http(handler)).harvest(store)

    assert result.status == "ok"
    assert result.sessions == 1 and result.events == 2

    (session,) = store.list_sessions("langgraph")
    assert session["native_id"] == "T1"
    assert session["lab_session_id"] == "lab-abc123"  # join stored

    events = store.list_events("langgraph", "T1")
    assert {e["event_type"] for e in events} == {"llm", "tool"}


def test_harvest_ok_but_project_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_fake")
    monkeypatch.setenv("LANGCHAIN_PROJECT", "nope")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sessions"):
            return httpx.Response(200, json=[])  # no such project yet
        return httpx.Response(404)

    result = LangGraphSource(http=_mock_http(handler)).harvest(make_store(tmp_path))
    assert result.status == "ok"
    assert result.sessions == 0
    assert "nope" in result.detail  # says which project had nothing


def test_harvest_error_on_api_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_fake")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    result = LangGraphSource(http=_mock_http(handler)).harvest(make_store(tmp_path))
    assert result.status == "error"
