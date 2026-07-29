"""The plan → Jira parser (WS15/D58).

What is worth testing here is not the Jira API — it is the reading of the plan,
because every failure mode is silent: a shape the regex stops matching produces
an epic with no stories, which looks like a workstream with nothing in it rather
than like a broken import.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import jira_sync  # noqa: E402

TABLE_WS = """## WS99 — A workstream with a table

| # | Item | State |
|---|---|---|
| 1 | Ship the thing | **done 2026-07-28** (D41) |
| 2 | Grant the permission | **operator action — still open** |
"""

STATUS_WS = """## WS98 — A workstream written as narrative

Status 2026-07-19 (first leg live):
1. ✅ `src/interop/` built and verified — with a trailing clause (D27).
2. ⏳ Remaining: the other half.

**Credentials:** nothing new.
"""

PROSE_WS = """## WS97 — A workstream with no work items at all

**Goal:** something.

1. **A design element.** Not a status line, so not a story.
2. **Another one.** Still not a story.
"""


def parse(text: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    plan = tmp_path / "07-workstreams.md"
    plan.write_text(text, encoding="utf-8")
    monkeypatch.setattr(jira_sync, "PLAN", plan)
    return jira_sync.parse_plan()


def test_table_rows_become_items_with_their_real_state(tmp_path, monkeypatch):
    (ws,) = parse(TABLE_WS, tmp_path, monkeypatch)
    assert [i["done"] for i in ws["items"]] == [True, False]
    # "operator action" must NOT read as done just because the row is filled in.
    assert ws["items"][1]["summary"] == "Grant the permission"
    assert ws["done"] is False  # one item open ⇒ epic open


def test_statused_numbered_lines_become_items(tmp_path, monkeypatch):
    (ws,) = parse(STATUS_WS, tmp_path, monkeypatch)
    assert [i["done"] for i in ws["items"]] == [True, False]
    # The summary is the first clause; the evidence stays in the description.
    assert ws["items"][0]["summary"] == "src/interop/ built and verified"
    assert "adr-D27" in ws["adrs"]


def test_narrative_numbered_lines_are_not_items(tmp_path, monkeypatch):
    """The refusal that keeps the board honest: prose is not a work item."""
    (ws,) = parse(PROSE_WS, tmp_path, monkeypatch)
    assert ws["items"] == []
    assert ws["done"] is False  # no stories ⇒ never auto-closed


def test_epic_never_closes_on_the_word_done_in_prose(tmp_path, monkeypatch):
    """ "Everything that does not need AWS is done" is not a finished workstream."""
    text = """## WS96 — Half of it

**Status 2026-07-26.** Everything that does not need AWS is done; the one step
that does is the one that matters most.
"""
    (ws,) = parse(text, tmp_path, monkeypatch)
    assert ws["done"] is False
    assert "does not need AWS is done" in ws["status"]


def test_status_paragraph_does_not_swallow_the_items_under_it(tmp_path, monkeypatch):
    (ws,) = parse(STATUS_WS, tmp_path, monkeypatch)
    assert ws["status"] == "Status 2026-07-19 (first leg live):"


def test_every_workstream_in_the_real_plan_parses(tmp_path, monkeypatch):
    """Guards the live document: headings and item shapes still match."""
    plan = jira_sync.parse_plan()
    assert len(plan) >= 15
    numbers = [int(w["ws"][2:]) for w in plan]
    assert numbers == sorted(numbers), "workstreams must import in WS order"
    assert sum(len(w["items"]) for w in plan) >= 40, "item shapes stopped matching"
    # A workstream with neither a status paragraph nor items is one that has not
    # started (WS4 LangGraph, WS5 Strands). That is a real state and it imports
    # as a labelled epic — but it must never import as DONE, which is the way
    # this could quietly lie.
    for w in plan:
        if not w["status"] and not w["items"]:
            assert not w["done"], f"{w['ws']} has no recorded work yet but imports as done"


def test_repo_links_only_point_at_paths_that_exist(tmp_path, monkeypatch):
    root = Path.cwd()
    links = jira_sync.repo_links("built src/interop/ and scripts/nope_not_real.py", root)
    assert any(link.endswith("/src/interop") for link in links)
    assert not any("nope_not_real" in link for link in links)
