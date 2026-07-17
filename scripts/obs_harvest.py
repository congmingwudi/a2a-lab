"""Pull each platform's execution logs into the local obs store (M11.2).

    uv run python scripts/obs_harvest.py                 # all platforms
    uv run python scripts/obs_harvest.py anthropic       # one platform

The console's Observability section triggers the same harvest via
POST /api/obs/harvest.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from observability import ObsStore
from observability.anthropic_source import AnthropicSource
from observability.openai_source import OpenAISource
from observability.salesforce_source import SalesforceSource

SOURCES = {
    "anthropic": AnthropicSource,
    "salesforce": SalesforceSource,
    "openai": OpenAISource,
}


def main() -> int:
    wanted = sys.argv[1:] or list(SOURCES)
    unknown = [w for w in wanted if w not in SOURCES]
    if unknown:
        print(f"unknown platform(s): {', '.join(unknown)} — choose from {', '.join(SOURCES)}")
        return 2
    store = ObsStore()
    print(f"obs store: {store.db_path}")
    failed = False
    for name in wanted:
        result = SOURCES[name]().harvest(store)
        line = f"[{result.platform}] {result.status}"
        if result.sessions or result.events:
            line += f" · {result.sessions} sessions, {result.events} events"
        if result.detail:
            line += f" · {result.detail}"
        print(line)
        for err in result.errors[:5]:
            print(f"    ! {err}")
        failed = failed or result.status == "error"
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
