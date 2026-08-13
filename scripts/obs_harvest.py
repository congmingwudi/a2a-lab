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
from observability.infra_source import AwsInfraSource, AzureInfraSource, GcpInfraSource
from observability.openai_source import OpenAISource
from observability.salesforce_otel_source import SalesforceOtelSource
from observability.salesforce_source import SalesforceSource
from observability.strands_source import StrandsSource

# The five agent-platform sources — the coverage sweep and its "harvested from
# all platforms" claim. Everything below the divider shares only the harvest
# seam and the store; each is reachable by name but NEVER in the unqualified
# sweep, so the five columns keep meaning what they say.
PLATFORM_SOURCES = {
    "claude": AnthropicSource,
    "salesforce": SalesforceSource,
    "openai": OpenAISource,
    "adk": AdkSource,
    "foundry": FoundrySource,
    "strands": StrandsSource,
}

# WS9/WS16: the coding agents that BUILT the lab, rendered in their own console
# section. `coding` is the metrics (cost/tokens); `coding-logs` the behavioural
# log signal (edit-acceptance, tool mix, latency, reliability, prompt cadence).
BUILD_SOURCES = {
    "coding": CodingSource,
    "coding-logs": CodingLogsSource,
}

# Track B (plan/explore-moirai-timeseries-forecasting.md): cross-cloud
# INFRASTRUCTURE metrics — the SRE-grade runtime telemetry sibling of the
# agent-log harvest, written to lab.infra_metrics. Not agent platforms, so kept
# out of the coverage sweep; harvest with `infra` (all three clouds) or by name
# (infra-aws / infra-gcp / infra-azure).
INFRA_SOURCES = {
    "infra-aws": AwsInfraSource,
    "infra-gcp": GcpInfraSource,
    "infra-azure": AzureInfraSource,
}

# WS23/D73: the Agentforce Session Trace OTel API — a SECOND route to the SAME
# Data 360 record the live `salesforce` source assembles from four STDM DMOs,
# read as pre-joined OTLP instead. Built so the live path can switch to it when
# the API leaves beta and grows a bulk read; until then it is reachable by name
# (`salesforce-otel`) only and NEVER in the unqualified sweep, so it neither
# doubles the Agentforce column nor changes what "harvested from all platforms"
# means. See src/observability/salesforce_otel_source.py.
OTEL_SOURCES = {
    "salesforce-otel": SalesforceOtelSource,
}

SOURCES = {**PLATFORM_SOURCES, **BUILD_SOURCES, **INFRA_SOURCES, **OTEL_SOURCES}

# `infra` is a convenience alias expanding to all three infra sources.
GROUP_ALIASES = {"infra": list(INFRA_SOURCES)}


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

    # No args = the prior default sweep (agent platforms + the coding agents),
    # NOT infra: Track B is dense runtime telemetry with a different cadence and
    # is opt-in via `infra` or a named infra-* source, so a bare harvest keeps
    # its old scope. Expand any group alias (`infra`) to its members.
    args = sys.argv[1:]
    wanted: list[str] = []
    for a in args or list(PLATFORM_SOURCES) + list(BUILD_SOURCES):
        wanted.extend(GROUP_ALIASES.get(a, [a]))
    choices = list(SOURCES) + list(GROUP_ALIASES)
    unknown = [w for w in wanted if w not in SOURCES]
    if unknown:
        print(f"unknown platform(s): {', '.join(unknown)} — choose from {', '.join(choices)}")
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
