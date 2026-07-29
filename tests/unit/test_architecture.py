"""The deployment map parses into the console's Architecture section.

The point of parsing plan/09-deployment-map.md rather than duplicating its
mermaid into config/diagrams.yaml is that there is only ever one copy. These
tests hold that seam: the doc keeps its conventions, and the console keeps
getting levels out of it.
"""

from __future__ import annotations

from pathlib import Path

from console.architecture import DOC_PATH, load, parse

REPO = Path(__file__).resolve().parents[2]

SAMPLE = """\
Intro prose about the estate.

## L0 — First level

```mermaid
flowchart TB
  A --> B
```

What you are looking at.

## L1 — Second level

No diagram here, just prose.

## Checking reality

```sh
aws lambda list-functions
```

## Why not, in one place

Because.
"""


def test_parse_splits_levels_diagrams_and_appendices():
    doc = parse(SAMPLE)

    assert doc["intro"] == "Intro prose about the estate."
    assert [x["id"] for x in doc["levels"]] == ["L0", "L1"]
    assert doc["levels"][0]["title"] == "First level"
    assert doc["levels"][0]["mermaid"] == "flowchart TB\n  A --> B"
    # the fence is removed from the prose, not left in it twice
    assert "mermaid" not in doc["levels"][0]["prose"]
    assert doc["levels"][0]["prose"] == "What you are looking at."
    # a level may legitimately have no diagram
    assert doc["levels"][1]["mermaid"] == ""

    # The appendices end the last level rather than being swallowed into it —
    # otherwise "Checking reality" would render as part of L1.
    assert "Checking reality" not in doc["levels"][1]["prose"]
    assert [s["title"] for s in doc["sections"]] == [
        "Checking reality",
        "Why not, in one place",
    ]


def test_missing_file_degrades_to_an_error_not_an_exception():
    """The console asks for this on every page load; a missing doc must render
    a message, not 500 the section."""
    doc = load(Path("plan/does-not-exist.md"))
    assert doc["levels"] == []
    assert "not found" in doc["error"]


def test_the_real_deployment_map_parses():
    """Guards the doc's own conventions: if someone renames a heading out of
    the `## L<n> — title` shape, the console silently loses that level."""
    doc = load(DOC_PATH)

    assert doc.get("error") is None
    ids = [x["id"] for x in doc["levels"]]
    assert ids == sorted(ids), f"levels out of order: {ids}"
    assert len(ids) >= 5, f"expected the full progression, got {ids}"
    assert ids[0] == "L0", "the map must open at estate level"

    # Every level earns its place with a picture and an explanation. A level
    # with a diagram and no prose is the failure mode this doc exists to avoid:
    # a picture nobody can read the intent off.
    for level in doc["levels"]:
        assert level["mermaid"], f"{level['id']} has no diagram"
        assert level["prose"].strip(), f"{level['id']} has no prose"
        assert level["mermaid"].startswith(("flowchart", "graph", "sequenceDiagram")), (
            f"{level['id']} diagram is not a supported mermaid type"
        )

    # The file-level mapping is the detail the map was asked for; it is a table.
    deepest = doc["levels"][-1]
    assert "|" in deepest["prose"], "the deepest level should carry the code→deploy table"


def test_links_point_only_at_files_that_exist_on_the_published_branch():
    """A link is a promise about the REMOTE, not about this laptop.

    Existence-on-disk produced links that 404'd for every file created in a
    session and not yet pushed — real here, absent from `blob/main`. The check
    has to ask git what is on the branch the URL names.
    """
    import subprocess

    doc = load(DOC_PATH)
    published = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "origin/main"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if published.returncode != 0:
        import pytest

        pytest.skip("no origin/main to compare against")
    on_branch = set(published.stdout.split("\n"))
    for entry in doc["files"]:
        if entry["kind"] == "file":
            assert entry["path"] in on_branch, (
                f"{entry['path']} is linked but not on origin/main — the link would 404"
            )
        else:
            assert any(p.startswith(entry["path"] + "/") for p in on_branch), entry["path"]


def test_link_urls_match_their_kind():
    doc = load(DOC_PATH)
    for entry in doc["files"]:
        expected = "/tree/" if entry["kind"] == "dir" else "/blob/"
        assert expected in entry["url"], entry


def test_a_decimal_level_is_a_level_not_an_appendix():
    """L5.5 (DNS) was inserted between the observability level and the
    code→deployment table. That table is referred to as "L6" in CLAUDE.md and
    in the console's own copy, so renumbering to make room would have broken
    references outside this file.

    Without the decimal in the pattern the section is silently DEMOTED to an
    appendix — the page still renders and the Architecture section just quietly
    loses a level, which is the kind of failure nobody reports.
    """
    from console.architecture import parse

    doc = parse(
        "# Map\n\nintro\n\n"
        "## L5 — Observability\n\nfive interiors\n\n"
        "## L5.5 — DNS: the four hostnames\n\nfour CNAMEs\n\n"
        "## L6 — Code to deployment\n\nthe table\n\n"
        "## Checking reality\n\ncommands\n"
    )
    assert [level["id"] for level in doc["levels"]] == ["L5", "L5.5", "L6"]
    assert doc["levels"][1]["title"] == "DNS: the four hostnames"
    # and the appendix after it is still an appendix
    assert [s["title"] for s in doc["sections"]] == ["Checking reality"]


def test_the_real_map_carries_the_dns_level():
    """Every public entrance is a hand-made Cloudflare record — the one part of
    the estate no script creates. If the level goes missing, the four hostnames
    have no home in the plan."""
    from console.architecture import load

    doc = load()
    dns = [level for level in doc["levels"] if level["id"] == "L5.5"]
    assert dns, f"no L5.5 in {[level['id'] for level in doc['levels']]}"
    body = dns[0]["prose"]
    for host in ("bridge-lab", "console-lab", "faces-lab"):
        assert host in body, f"{host} not recorded in the DNS level"
