"""Pull each platform's execution logs into the obs store (M11.2).

    uv run python scripts/obs_harvest.py                 # all platforms
    uv run python scripts/obs_harvest.py anthropic       # one platform

Store selection (D23): A2ALAB_OBS_STORE=sqlite (default, traces/lab.db) or
postgres (the hosted Aurora store — needs A2ALAB_PG_* config). The console's
Observability section triggers the same harvest via POST /api/obs/harvest;
the hosted harvest Lambda runs the same sources against Postgres.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

from observability.adk_source import AdkSource
from observability.anthropic_source import AnthropicSource
from observability.coding_logs_source import CodingLogsSource
from observability.coding_source import CodingSource
from observability.foundry_source import FoundrySource
from observability.openai_source import OpenAISource
from observability.salesforce_source import SalesforceSource
from observability.strands_source import StrandsSource

SOURCES = {
    "claude": AnthropicSource,
    "salesforce": SalesforceSource,
    "openai": OpenAISource,
    "adk": AdkSource,
    "foundry": FoundrySource,
    "strands": StrandsSource,
    # WS9/WS16: not agent platforms — the coding agents that BUILT the lab. Kept
    # out of the five-platform coverage panel and rendered in their own console
    # section; they share only this harvest seam and the store. `coding` is the
    # metrics (cost/tokens); `coding-logs` is the behavioural log signal
    # (edit-acceptance, tool mix, latency, reliability, prompt cadence).
    "coding": CodingSource,
    "coding-logs": CodingLogsSource,
}


def make_store():
    # The shared selector (D49). This used to default to sqlite while the
    # console hardcoded sqlite reads, so a local harvest and the hosted one
    # filled two different stores and the dashboard showed whichever the
    # laptop had.
    from observability import make_obs_store

    return make_obs_store()


def main() -> int:
    load_dotenv()
    # Same service identities the hosted harvest uses: the secret's values
    # OVERRIDE .env, and the GCP service-account key displaces any ambient
    # gcloud login. AWS auth is the only human login the harvest depends on.
    from observability.credentials import prepare

    loaded = prepare()
    if loaded:
        print(f"credentials: {len(loaded)} key(s) from the harvest secret (Secrets Manager)")

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
