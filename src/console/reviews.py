"""Sign-off state for published insights — who approved a claim, and when.

An insight is a claim the lab makes in public, so approving one is a named
act, not a flag. `config/insights.yaml` marks an entry `review: required`
when it needs a human decision; this module records that decision —
approver, timestamp, comment — in `config/insight_reviews.yaml`, beside the
claims it governs, where it is diffable and reviewable like everything else
in plan/.

The record pins the CONTENT it approved: a hash over headline, evidence,
advisory, status and refs. Edit an approved insight and its approval goes
`stale` rather than silently carrying over — an approval is of words, not of
an id. That is the same honesty rule the matrix and the insight `status`
field already follow.

Sign-off is reserved to directory users carrying `reviewer: true` in
config/users.yaml (the console enforces it; see app.py). A service token
carries no person, so it can never approve.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REVIEWS_PATH = Path("config/insight_reviews.yaml")

# The fields an approval is OF. A diagram attachment or a new id elsewhere in
# the file must not invalidate a sign-off; a changed claim must.
REVIEWED_FIELDS = ("headline", "evidence", "advisory", "status", "refs")

STATES = ("approved", "rejected")

_HEADER = """\
# Insight sign-off (console: Insights → Approve / Request changes).
#
# Written by the console when the lab's reviewer decides on an insight that
# config/insights.yaml marks `review: required`. `content` is a hash of the
# text that was approved — edit the insight afterwards and the console shows
# the approval as stale rather than pretending it still applies.
#
# Hand-editing is fine; keep the shape.
"""


def _norm(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    return value


def content_hash(insight: dict) -> str:
    """Stable short hash of the claim's reviewable text."""
    payload = {field: _norm(insight.get(field)) for field in REVIEWED_FIELDS}
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _path(path: str | Path | None) -> Path:
    # Resolved per call, not bound as a default, so the store can be pointed
    # at a tmp file (tests) without the module having been imported late.
    return Path(path) if path is not None else REVIEWS_PATH


def load_reviews(path: str | Path | None = None) -> dict[str, dict]:
    p = _path(path)
    if not p.exists():  # no sign-offs yet — everything reads as pending
        return {}
    raw = yaml.safe_load(p.read_text()) or {}
    return raw.get("reviews") or {}


def save_reviews(reviews: dict[str, dict], path: str | Path | None = None) -> None:
    p = _path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump({"reviews": reviews}, sort_keys=True, allow_unicode=True, width=88)
    p.write_text(_HEADER + body)


def record(
    insight: dict,
    decision: str,
    *,
    user: dict,
    comment: str = "",
    path: str | Path | None = None,
) -> dict:
    """Write one decision and return the stored entry.

    `user` is the verified lab-JWT claims dict — the sign-off carries the
    person, not the role, because "operator approved it" answers nobody's
    question about a published claim.
    """
    if decision not in STATES:
        raise ValueError(f"decision must be one of {STATES}, got {decision!r}")
    reviews = load_reviews(path)
    entry = {
        "state": decision,
        "by": user.get("sub") or "unknown",
        "name": user.get("name") or user.get("sub") or "unknown",
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "comment": " ".join((comment or "").split()),
        "content": content_hash(insight),
    }
    reviews[str(insight.get("id"))] = entry
    save_reviews(reviews, path)
    return entry


def review_state(insight: dict, reviews: dict[str, dict]) -> dict:
    """The review block the console renders on a tile.

    `required` drives whether the tile shows the control at all; `stale` is
    the interesting one — approved text that has since been edited is NOT
    approved text.
    """
    required = str(insight.get("review") or "").lower() == "required"
    entry = reviews.get(str(insight.get("id")))
    if not entry:
        return {"required": required, "state": "pending", "stale": False}
    stale = entry.get("content") != content_hash(insight)
    return {
        "required": required,
        "state": entry.get("state", "pending"),
        "by": entry.get("by"),
        "name": entry.get("name"),
        "at": entry.get("at"),
        "comment": entry.get("comment") or "",
        "stale": stale,
    }


def attach_reviews(insights: list[dict], path: str | Path | None = None) -> list[dict]:
    reviews = load_reviews(path)
    return [{**ins, "review_state": review_state(ins, reviews)} for ins in insights]
