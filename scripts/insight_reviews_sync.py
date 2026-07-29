#!/usr/bin/env python
"""Move insight sign-offs between Aurora and the repo file (D50).

    uv run python scripts/insight_reviews_sync.py pull   # store -> config/insight_reviews.yaml
    uv run python scripts/insight_reviews_sync.py push   # config/insight_reviews.yaml -> store
    uv run python scripts/insight_reviews_sync.py diff   # show what differs, change nothing

WHY THIS EXISTS. A sign-off is a named human act on a published claim, so it
has two jobs that pull in opposite directions: it must survive (the hosted
console runs on a container filesystem that does not) and it must be reviewable
in the repo like the claims it governs. Aurora does the first, the file does the
second, and this script is the seam between them.

`pull` is the one you will run: sign off in the console, pull, commit. `push`
exists for the migration — moving sign-offs made before D50 into the store, and
for hand-edits to the file, which the module's docstring explicitly allows.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

from console import reviews  # noqa: E402


def _store():
    from observability.pg import PgClient, PgObsStore

    if not PgClient.configured():
        sys.exit(
            "no hosted store configured — set A2ALAB_PG_CLUSTER_ARN and "
            "A2ALAB_PG_SECRET_ARN in .env (this script is the seam between "
            "Aurora and the repo file; with no Aurora there is nothing to sync)"
        )
    return PgObsStore()


def main() -> int:
    load_dotenv()
    action = (sys.argv[1] if len(sys.argv) > 1 else "diff").lower()
    if action not in {"pull", "push", "diff"}:
        sys.exit(f"unknown action {action!r} — use pull, push or diff")

    from observability.pg import STATE_INSIGHT_REVIEWS

    store = _store()
    try:
        stored = (store.get_state(STATE_INSIGHT_REVIEWS) or {}).get("reviews") or {}
        # Explicit path: never let the file read be answered from the store.
        on_disk = reviews.load_reviews(reviews.REVIEWS_PATH)

        only_store = sorted(set(stored) - set(on_disk))
        only_file = sorted(set(on_disk) - set(stored))
        differing = sorted(k for k in set(stored) & set(on_disk) if stored[k] != on_disk[k])

        print(f"store: {len(stored)} sign-off(s)   file: {len(on_disk)} sign-off(s)")
        for label, ids in (
            ("only in store", only_store),
            ("only in file", only_file),
            ("differ", differing),
        ):
            if ids:
                print(f"  {label}: {', '.join(ids)}")
        if not (only_store or only_file or differing):
            print("  in sync")

        if action == "pull":
            reviews.save_reviews(stored, reviews.REVIEWS_PATH)
            print(f"wrote {len(stored)} sign-off(s) to {reviews.REVIEWS_PATH}")
        elif action == "push":
            store.put_state(STATE_INSIGHT_REVIEWS, {"reviews": on_disk})
            print(f"pushed {len(on_disk)} sign-off(s) to lab.lab_state[{STATE_INSIGHT_REVIEWS}]")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
