"""config/diagrams.yaml — the readout diagrams attached to insight tiles.

These are demo-critical in a way most config is not: a broken chip or a
diagram that silently disagrees with the README shows up in front of an
audience, so the invariants are checked here rather than discovered live.
"""

from pathlib import Path

import pytest

from console.insights import (
    attach_diagrams,
    load_diagrams,
    load_insights,
    to_markdown,
)

REQUIRED = ("id", "title", "caption", "mermaid", "insights")
# Mermaid graph types the console's renderer is set up for. Anything else is
# probably a typo in the first line, which mermaid reports as a parse error.
GRAPH_TYPES = ("flowchart", "graph", "sequenceDiagram")


@pytest.fixture(scope="module")
def diagrams():
    return load_diagrams()


def test_diagrams_config_is_present(diagrams):
    assert diagrams, "config/diagrams.yaml should carry the readout diagrams"


def test_every_diagram_has_the_required_fields(diagrams):
    for d in diagrams:
        for key in REQUIRED:
            assert d.get(key), f"diagram {d.get('id')!r} is missing {key}"


def test_diagram_ids_are_unique(diagrams):
    ids = [d["id"] for d in diagrams]
    assert len(ids) == len(set(ids)), f"duplicate diagram ids: {ids}"


def test_mermaid_declares_a_supported_graph_type(diagrams):
    for d in diagrams:
        first = d["mermaid"].strip().splitlines()[0].strip()
        assert first.startswith(GRAPH_TYPES), f"{d['id']}: unexpected first line {first!r}"


def test_every_referenced_insight_exists(diagrams):
    known = {i.get("id") for i in load_insights()}
    for d in diagrams:
        for insight_id in d["insights"]:
            assert insight_id in known, (
                f"diagram {d['id']} points at unknown insight {insight_id!r} — "
                "a chip would never render"
            )


def test_readme_diagrams_match_the_config_verbatim(diagrams):
    """The console and README must not drift.

    Diagrams marked `readme: true` are embedded in README.md as well. Editing
    one copy and not the other is the obvious way for the readout to disagree
    with the repo, so it fails here instead.
    """
    readme = Path("README.md").read_text()
    shared = [d for d in diagrams if d.get("readme")]
    assert shared, "expected some diagrams to be shared with the README"
    for d in shared:
        assert d["mermaid"].strip() in readme, (
            f"diagram {d['id']} is marked readme: true but its mermaid is not in "
            "README.md verbatim — update both copies (or clear the flag)"
        )


def test_attach_diagrams_maps_and_does_not_mutate():
    insights = [{"id": "a"}, {"id": "b"}]
    diagrams = [
        {"id": "d1", "title": "One", "mermaid": "flowchart LR\n A --> B", "insights": ["a", "b"]},
        {"id": "d2", "title": "Two", "mermaid": "flowchart LR\n C --> D", "insights": ["b"]},
    ]

    out = attach_diagrams(insights, diagrams)

    assert [d["id"] for d in out[0]["diagrams"]] == ["d1"]
    assert [d["id"] for d in out[1]["diagrams"]] == ["d1", "d2"]
    # The mapping lives on the diagram; it should not ride back out on the
    # attached copy (the console keys chips off the insight it is rendering).
    assert "insights" not in out[0]["diagrams"][0]
    assert insights[0] == {"id": "a"}, "input insights must not be mutated"


def test_insight_without_diagrams_gets_no_key():
    out = attach_diagrams([{"id": "lonely"}], [])
    assert "diagrams" not in out[0]


def test_markdown_export_embeds_mermaid_fences():
    insights = [
        {
            "id": "x",
            "category": "Security & trust",
            "headline": "H",
            "evidence": "E",
            "advisory": "A",
            "diagrams": [
                {"id": "d", "title": "Diag", "caption": "cap", "mermaid": "flowchart LR\n  A --> B"}
            ],
        }
    ]
    md = to_markdown(insights)
    assert "```mermaid" in md
    assert "flowchart LR" in md
    assert "**Diag**" in md and "cap" in md


def test_live_export_carries_the_diagrams():
    md = to_markdown(load_insights())
    assert md.count("```mermaid") >= len([d for d in load_diagrams() for _ in d["insights"]]), (
        "every diagram/insight pairing should appear in the exported markdown"
    )
