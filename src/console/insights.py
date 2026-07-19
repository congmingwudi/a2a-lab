"""Loader + markdown renderer for config/insights.yaml — the trusted-advisor
insights published in the console's Insights section and exported for
presentation work (Claude Design imports the markdown directly).

Shared by the console API (GET /api/insights, /api/insights.md) and
scripts/export_insights.py so the app and the export can never drift.
"""

from __future__ import annotations

from pathlib import Path

import yaml

INSIGHTS_PATH = Path("config/insights.yaml")

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


def load_insights(path: str | Path = INSIGHTS_PATH) -> list[dict]:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return raw.get("insights") or []


def by_category(insights: list[dict]) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = {}
    for ins in insights:
        groups.setdefault(ins.get("category", "Uncategorized"), []).append(ins)
    known = [c for c in CATEGORY_ORDER if c in groups]
    extra = [c for c in groups if c not in CATEGORY_ORDER]
    return [(c, groups[c]) for c in known + extra]


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
                f"**What the lab showed:** {' '.join(str(ins.get('evidence', '')).split())}",
                "",
                f"**Advisor take:** {' '.join(str(ins.get('advisory', '')).split())}",
                "",
            ]
    return "\n".join(lines)
