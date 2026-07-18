"""AWS Lambda entrypoint for the hosted harvest (EventBridge-fired, D23).

Runs the same platform sources as scripts/obs_harvest.py against the Aurora
store. Platform credentials (ANTHROPIC_API_KEY, SF_*) live in one Secrets
Manager secret — a JSON object of env vars — loaded at cold start via
A2ALAB_HARVEST_SECRET_ARN. Invoke with {"platform": "anthropic"} to harvest
one source; default is all.
"""

from __future__ import annotations

import json
import os

_secret_loaded = False


def _load_secret_env() -> None:
    global _secret_loaded
    arn = os.environ.get("A2ALAB_HARVEST_SECRET_ARN")
    if _secret_loaded or not arn:
        return
    import boto3

    raw = boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
    for key, value in json.loads(raw).items():
        os.environ.setdefault(key, str(value))
    _secret_loaded = True


def handler(event, context):  # noqa: ARG001 - AWS signature
    _load_secret_env()
    from observability.anthropic_source import AnthropicSource
    from observability.openai_source import OpenAISource
    from observability.pg import PgObsStore
    from observability.salesforce_source import SalesforceSource

    sources = {
        "anthropic": AnthropicSource,
        "salesforce": SalesforceSource,
        "openai": OpenAISource,
    }
    wanted = [event.get("platform")] if isinstance(event, dict) and event.get("platform") else None
    wanted = wanted or list(sources)
    if any(w not in sources for w in wanted):
        return {"ok": False, "error": f"unknown platform(s): {wanted}"}

    store = PgObsStore()
    results = [sources[name]().harvest(store).__dict__ for name in wanted]
    ok = all(r["status"] != "error" for r in results)
    return {"ok": ok, "results": results}
