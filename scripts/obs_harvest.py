"""Pull each platform's execution logs into the obs store (M11.2).

    uv run python scripts/obs_harvest.py                 # all platforms
    uv run python scripts/obs_harvest.py anthropic       # one platform

Store selection (D23): A2ALAB_OBS_STORE=sqlite (default, traces/lab.db) or
postgres (the hosted Aurora store — needs A2ALAB_PG_* config). The console's
Observability section triggers the same harvest via POST /api/obs/harvest;
the hosted harvest Lambda runs the same sources against Postgres.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

from observability import ObsStore
from observability.adk_source import AdkSource
from observability.foundry_source import FoundrySource
from observability.anthropic_source import AnthropicSource
from observability.openai_source import OpenAISource
from observability.salesforce_source import SalesforceSource

SOURCES = {
    "claude": AnthropicSource,
    "salesforce": SalesforceSource,
    "openai": OpenAISource,
    "adk": AdkSource,
    "foundry": FoundrySource,
}


def make_store():
    if os.environ.get("A2ALAB_OBS_STORE", "sqlite").lower() == "postgres":
        from observability.pg import PgObsStore

        return PgObsStore()
    return ObsStore()


def main() -> int:
    load_dotenv()
    wanted = sys.argv[1:] or list(SOURCES)
    unknown = [w for w in wanted if w not in SOURCES]
    if unknown:
        print(f"unknown platform(s): {', '.join(unknown)} — choose from {', '.join(SOURCES)}")
        return 2
    store = make_store()
    print(f"obs store: {getattr(store, 'db_path', 'postgres (D23 hosted store)')}")
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
