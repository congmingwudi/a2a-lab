"""Unit tests for the obs MCP server's tools (D23) — fake PgClient, no AWS."""

from __future__ import annotations

import json

from obs_mcp.tools import MAX_ROWS, ObsTools, build_registry


class FakePg:
    def __init__(self, rows=None, raises=None):
        self.rows = rows if rows is not None else []
        self.raises = raises
        self.calls: list[tuple[str, dict]] = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params or {}))
        if self.raises:
            raise self.raises
        return self.rows


def make_tools(reader=None, writer=None):
    return ObsTools(reader=reader or FakePg(), writer=writer or FakePg())


def test_registry_exposes_both_tools():
    registry = build_registry(make_tools())
    names = {t.name for t in registry.all()}
    assert names == {"query_obs_store", "save_brief"}


def test_query_rejects_non_select():
    tools = make_tools()
    out = json.loads(tools.query_obs_store({"sql": "DELETE FROM lab.obs_briefs"}))
    assert "error" in out
    assert tools._reader.calls == []  # never reached the DB


def test_query_rejects_multiple_statements():
    tools = make_tools()
    out = json.loads(tools.query_obs_store({"sql": "SELECT 1; DROP TABLE lab.obs_briefs"}))
    assert "error" in out


def test_query_allows_select_and_with_and_trailing_semicolon():
    reader = FakePg(rows=[{"n": 1}])
    tools = make_tools(reader=reader)
    for sql in ("SELECT 1;", "WITH x AS (SELECT 1) SELECT * FROM x", "  select 2"):
        out = json.loads(tools.query_obs_store({"sql": sql}))
        assert out["rows"] == [{"n": 1}], sql
        assert out["capped_at"] == MAX_ROWS


def test_query_caps_rows():
    reader = FakePg(rows=[{"n": i} for i in range(MAX_ROWS + 50)])
    tools = make_tools(reader=reader)
    out = json.loads(tools.query_obs_store({"sql": "SELECT 1"}))
    assert out["row_count"] == MAX_ROWS


def test_query_error_returned_for_self_correction():
    reader = FakePg(raises=RuntimeError("relation does not exist"))
    tools = make_tools(reader=reader)
    out = json.loads(tools.query_obs_store({"sql": "SELECT * FROM nope"}))
    assert "RuntimeError" in out["error"]


def test_save_brief_requires_content():
    tools = make_tools()
    out = json.loads(tools.save_brief({"brief_md": "  "}))
    assert "error" in out


def test_save_brief_inserts_and_reports():
    writer = FakePg()
    tools = make_tools(writer=writer)
    out = json.loads(tools.save_brief({"brief_md": "# findings", "queries_run": 7}))
    assert out["saved"] is True
    insert = writer.calls[0]
    assert "obs_briefs" in insert[0]
    assert insert[1]["queries_run"] == 7


def test_trace_hop_failure_does_not_break_tool():
    reader = FakePg(rows=[{"n": 1}])
    writer = FakePg(raises=RuntimeError("no grants"))
    tools = make_tools(reader=reader, writer=writer)
    out = json.loads(tools.query_obs_store({"sql": "SELECT 1"}))
    assert out["rows"] == [{"n": 1}]
