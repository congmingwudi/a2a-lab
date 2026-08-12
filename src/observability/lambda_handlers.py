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
    from observability.coding_logs_source import CodingLogsSource
    from observability.coding_source import CodingSource
    from observability.foundry_source import FoundrySource
    from observability.infra_source import (
        AwsInfraSource,
        AzureInfraSource,
        GcpInfraSource,
    )
    from observability.openai_source import OpenAISource
    from observability.pg import PgObsStore
    from observability.salesforce_source import SalesforceSource
    from observability.strands_source import StrandsSource

    sources = {
        "claude": AnthropicSource,
        "salesforce": SalesforceSource,
        "openai": OpenAISource,
        # WS9 build telemetry — reads CloudWatch in this same account, so the
        # Lambda's execution role needs cloudwatch:GetMetricData +
        # cloudwatch:ListMetrics (the documented pair for the PromQL
        # QueryMetrics operation — this comment said GetMetricStatistics until
        # 2026-07-27, a leftover from the pre-PromQL version of coding_source,
        # and the grant was never applied at all). Owned by
        # deploy/obs/deploy_harvest.sh. No new secret (D39).
        "coding": CodingSource,
        # WS16 behavioural telemetry — reads the OTLP log group over SigV4
        # FilterLogEvents in this same account, so the Lambda's execution role
        # needs logs:FilterLogEvents on /a2alab/coding-agents/otlp (owned by
        # deploy/obs/deploy_harvest.sh). No new secret (D39): the read is
        # signed with the Lambda's role, the ingest side (bearer token) is the
        # developer's launch wrapper, not this function.
        "coding-logs": CodingLogsSource,
        # adk reads Cloud Logging/Monitoring with a service-account key from the
        # secret (see _materialize_gcp_key); foundry reads App Insights with the
        # Entra SP already in the secret. Both were absent here until 2026-07-25,
        # which is why Aurora held no ADK or Foundry rows while local sqlite did.
        "adk": AdkSource,
        "foundry": FoundrySource,
        # strands reads AWS/Bedrock model metrics (cloudwatch:GetMetricStatistics)
        # and the AgentCore runtime access log (logs:FilterLogEvents on
        # /aws/bedrock-agentcore/runtimes/*-DEFAULT) in THIS account — so the
        # Lambda's execution role needs both grants (owned by
        # deploy/obs/deploy_harvest.sh). No new secret (D39): both reads are
        # signed with the Lambda's role.
        "strands": StrandsSource,
        # Track B (plan/explore-moirai-timeseries-forecasting.md): cross-cloud
        # INFRASTRUCTURE metrics to lab.infra_metrics. Reads CloudWatch in this
        # account with the Lambda's role (needs cloudwatch:GetMetricData, the
        # same grant coding already carries), GCP Cloud Monitoring with the
        # secret's service-account key, and Azure Monitor with the Entra SP —
        # every identity this function already holds. Opt-in, never in the
        # default sweep: infra is a different cadence, so it runs only when
        # asked for by name or the `infra` group.
        "infra-aws": AwsInfraSource,
        "infra-gcp": GcpInfraSource,
        "infra-azure": AzureInfraSource,
    }
    # The default sweep is the agent platforms + the coding agents, matching
    # scripts/obs_harvest.py — infra is opt-in (a different cadence).
    INFRA = ("infra-aws", "infra-gcp", "infra-azure")
    default_sweep = [n for n in sources if n not in INFRA]

    asked = event.get("platform") if isinstance(event, dict) else None
    if asked == "anthropic":  # legacy alias for hosted invokes
        asked = "claude"
    if asked == "infra":  # group alias: all three infra sources
        wanted = list(INFRA)
    elif asked:
        wanted = [asked]
    else:
        wanted = default_sweep
    if any(w not in sources for w in wanted):
        return {"ok": False, "error": f"unknown platform(s): {wanted}"}

    store = PgObsStore()
    results = [sources[name]().harvest(store).__dict__ for name in wanted]
    ok = all(r["status"] != "error" for r in results)

    # WS14: refresh the credential expiry snapshot on the same schedule. It is
    # not a "platform", so it is not in `sources` and never appears in the
    # coverage panel — it is a second, unrelated artifact that happens to need
    # exactly the identities this function already holds (prepare() above put
    # the GCP key on disk and the Entra app's secret in the environment), plus
    # IAM and ACM through this Lambda's own role.
    #
    # It ran only on the operator's laptop until now, so the console's
    # Credentials panel was as current as the last time somebody remembered to
    # run a script. Skipped when asked for one platform: `{"platform": "adk"}`
    # means a targeted harvest, not a full sweep.
    expiry = None
    if asked is None:
        try:
            expiry = _refresh_expiry(store)
        except Exception as exc:  # noqa: BLE001 - never fail the harvest for this
            expiry = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": ok, "results": results, "expiry": expiry}


def _refresh_expiry(store) -> dict:
    """Collect credential expiries and publish them to lab.lab_state.

    Imports the collector from scripts/ — it is the SAME code the operator runs
    locally, deliberately, so the hosted snapshot and a hand-run one cannot
    disagree about what is tracked.
    """
    import sys
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[2]
    for candidate in (root / "scripts", _Path("/var/task/scripts"), _Path("scripts")):
        if candidate.exists() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
    import expiry_report

    from observability.pg import STATE_EXPIRY

    report = expiry_report.collect()
    store.put_state(STATE_EXPIRY, report)
    creds = report.get("credentials") or []
    bad = [c for c in creds if c.get("status") in ("error", "expired")]
    return {"ok": not bad, "credentials": len(creds), "problems": [c["name"] for c in bad]}
