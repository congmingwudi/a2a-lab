"""Loader + markdown renderer for config/insights.yaml — the trusted-advisor
insights published in the console's Insights section and exported for
presentation work (Claude Design imports the markdown directly).

Shared by the console API (GET /api/insights, /api/insights.md) and
scripts/export_insights.py so the app and the export can never drift.

config/diagrams.yaml attaches mermaid diagrams to insights: each diagram
names the insight ids whose tiles should carry its chip, so one diagram can
serve several insights without being duplicated. The attachment happens here,
once, which is why a chip in the console and a ```mermaid block in the
exported markdown always agree.
"""

from __future__ import annotations

from pathlib import Path

import yaml

INSIGHTS_PATH = Path("config/insights.yaml")
DIAGRAMS_PATH = Path("config/diagrams.yaml")

# Presentation order for categories; anything unlisted sorts after, as-found.
CATEGORY_ORDER = [
    "Federation vs consolidation",
    "Delegation patterns",
    "Protocols",
    "Hosting models",
    "Security & trust",
    "Observability",
    "Method",
]


def load_diagrams(path: str | Path = DIAGRAMS_PATH) -> list[dict]:
    p = Path(path)
    if not p.exists():  # diagrams are optional — insights render without them
        return []
    raw = yaml.safe_load(p.read_text()) or {}
    return raw.get("diagrams") or []


def load_insights(
    path: str | Path = INSIGHTS_PATH,
    diagrams_path: str | Path = DIAGRAMS_PATH,
) -> list[dict]:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    insights = raw.get("insights") or []
    return attach_diagrams(insights, load_diagrams(diagrams_path))


def attach_diagrams(insights: list[dict], diagrams: list[dict]) -> list[dict]:
    """Give each insight a `diagrams` list, in config order.

    The mapping lives on the diagram (`insights: [id, ...]`) rather than on the
    insight, so adding a diagram that serves four tiles is one edit in one
    file. Insight dicts are copied — the caller's loaded yaml stays untouched.
    """
    by_id: dict[str, list[dict]] = {}
    for diagram in diagrams:
        entry = {k: v for k, v in diagram.items() if k != "insights"}
        for insight_id in diagram.get("insights") or []:
            by_id.setdefault(insight_id, []).append(entry)
    out = []
    for insight in insights:
        attached = by_id.get(insight.get("id"))
        out.append({**insight, "diagrams": attached} if attached else dict(insight))
    return out


def by_category(insights: list[dict]) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = {}
    for ins in insights:
        groups.setdefault(ins.get("category", "Uncategorized"), []).append(ins)
    known = [c for c in CATEGORY_ORDER if c in groups]
    extra = [c for c in groups if c not in CATEGORY_ORDER]
    return [(c, groups[c]) for c in known + extra]


def _prose(value: object) -> str:
    """Collapse a folded YAML scalar to clean prose, preserving paragraphs.

    Mirrors the console's `prose()` helper: `evidence: >` folds a wrapped
    paragraph to one line and a blank line to a single "\\n", so splitting on
    newlines recovers the author's paragraphs. Each is whitespace-collapsed and
    rejoined with a blank line, which is a markdown paragraph break. A
    single-paragraph value passes through as one line, exactly as before.
    """
    paras = [" ".join(p.split()) for p in str(value or "").split("\n")]
    return "\n\n".join(p for p in paras if p)


def to_markdown(insights: list[dict]) -> str:
    """One self-contained markdown doc, shaped as talking points: claim,
    what the lab showed, what to tell the customer."""
    lines = [
        "# A2A Interop Lab — field insights",
        "",
        "Distilled findings from running the same agent-to-agent scenarios across",
        "Salesforce Agentforce, Claude, and OpenAI over REST, MCP, and A2A — with",
        "every hop's raw wire payload recorded. Status marks the evidence level:",
        "**measured** (recorded lab runs), **observed** (documented in the lab),",
        "**hypothesis** (measurement planned).",
        "",
    ]
    for category, items in by_category(insights):
        lines += [f"## {category}", ""]
        for ins in items:
            lines += [
                f"### {ins.get('headline', ins.get('id', 'untitled'))}",
                "",
                f"*Status: {ins.get('status', 'observed')}"
                + (f" · refs: {', '.join(ins['refs'])}" if ins.get("refs") else "")
                + "*",
                "",
                f"**What the lab showed:** {_prose(ins.get('evidence', ''))}",
                "",
                f"**Advisor take:** {_prose(ins.get('advisory', ''))}",
                "",
            ]
            # Diagrams ride as ```mermaid fences: GitHub and Claude Design both
            # render them, so the readout material carries the same picture the
            # console chip opens.
            for diagram in ins.get("diagrams") or []:
                lines += [
                    f"**{diagram.get('title', diagram.get('id', 'diagram'))}** — "
                    f"{' '.join(str(diagram.get('caption', '')).split())}",
                    "",
                    "```mermaid",
                    str(diagram.get("mermaid", "")).rstrip(),
                    "```",
                    "",
                ]
    return "\n".join(lines)
