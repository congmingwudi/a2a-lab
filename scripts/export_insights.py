"""Export config/insights.yaml to plan/08-insights.md — the checked-in
markdown twin of the console's Insights section, ready to drop into
Claude Design (or any deck tooling) as presentation source material.

    uv run python scripts/export_insights.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from console.insights import load_insights, to_markdown  # noqa: E402

OUT = Path("plan/08-insights.md")


def main() -> None:
    insights = load_insights()
    OUT.write_text(to_markdown(insights) + "\n")
    print(f"wrote {OUT} ({len(insights)} insights)")


if __name__ == "__main__":
    main()
