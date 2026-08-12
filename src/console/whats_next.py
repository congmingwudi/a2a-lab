"""Loader for config/whats_next.yaml — the roadmap-horizon plans published in
the console's "What's Next" section.

Deliberately thin: unlike insights.py there is no markdown export and no
diagram attachment. The section is the answer to "where do you go from here?",
so the plans are read straight from config on every request (the
file is the source of truth, and an operator editing it should see the change
on refresh, not after a restart). config/ is COPYd into the console image, so a
new plan ships with the next rebuild — no code change needed.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WHATS_NEXT_PATH = Path("config/whats_next.yaml")

# Presentation order for the horizon buckets; anything unlisted sorts after.
# "done" is last on purpose: the console renders it below a "What's Done"
# divider at the BOTTOM of the section, so the roadmap reads idea → shipped.
HORIZON_ORDER = ["in-flight", "planned", "exploring", "done"]


def load_plans(path: str | Path = WHATS_NEXT_PATH) -> list[dict]:
    p = Path(path)
    if not p.exists():  # a lab that never wrote the file shows the empty state
        return []
    raw = yaml.safe_load(p.read_text()) or {}
    return raw.get("plans") or []
