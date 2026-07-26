"""AWS Lambda entrypoint for the hosted harvest (EventBridge-fired, D23).

Runs the same platform sources as scripts/obs_harvest.py against the Aurora
store. Platform credentials (ANTHROPIC_API_KEY, SF_*) live in one Secrets
Manager secret — a JSON object of env vars — loaded at cold start via
A2ALAB_HARVEST_SECRET_ARN. Invoke with {"platform": "anthropic"} to harvest
one source; default is all.
"""

from __future__ import annotations


def handler(event, context):  # noqa: ARG001 - AWS signature
    from observability.credentials import prepare

    # Secret -> env, then the GCP key -> a file ADC can read. Identical call in
    # scripts/obs_harvest.py, so local and hosted harvests authenticate as the
    # same service identities rather than as whoever is logged in.
    prepare()

    from observability.adk_source import AdkSource
    from observability.anthropic_source import AnthropicSource
    from observability.coding_source import CodingSource
    from observability.foundry_source import FoundrySource
    from observability.openai_source import OpenAISource
    from observability.pg import PgObsStore
    from observability.salesforce_source import SalesforceSource

    sources = {
        "claude": AnthropicSource,
        "salesforce": SalesforceSource,
        "openai": OpenAISource,
        # WS9 build telemetry — reads CloudWatch in this same account, so the
        # Lambda's execution role needs cloudwatch:ListMetrics +
        # cloudwatch:GetMetricStatistics. No new secret (D39).
        "coding": CodingSource,
        # adk reads Cloud Logging/Monitoring with a service-account key from the
        # secret (see _materialize_gcp_key); foundry reads App Insights with the
        # Entra SP already in the secret. Both were absent here until 2026-07-25,
        # which is why Aurora held no ADK or Foundry rows while local sqlite did.
        "adk": AdkSource,
        "foundry": FoundrySource,
    }
    asked = event.get("platform") if isinstance(event, dict) else None
    if asked == "anthropic":  # legacy alias for hosted invokes
        asked = "claude"
    wanted = [asked] if asked else None
    wanted = wanted or list(sources)
    if any(w not in sources for w in wanted):
        return {"ok": False, "error": f"unknown platform(s): {wanted}"}

    store = PgObsStore()
    results = [sources[name]().harvest(store).__dict__ for name in wanted]
    ok = all(r["status"] != "error" for r in results)
    return {"ok": ok, "results": results}
