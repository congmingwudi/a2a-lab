"""Secrets Manager -> process env for the hosted runtimes (F1).

The lab's hosted seams — the two AgentCore runtimes and the Agentforce A2A
shim Lambda — used to carry their credentials as plain environment variables
on the runtime/function config: API keys, the Salesforce connected-app
client id/secret, the shim's bearer token. Anyone with read access to the
runtime description could read them, and `update-agent-runtime` echoes them
back on every deploy.

They now travel the way the harvest Lambda's already did (D23,
`observability/lambda_handlers.py`): one Secrets Manager secret per runtime
holding a JSON object of env vars, with only its ARN on the runtime config.
The container loads it at startup, before anything reads `os.environ`.

Deliberately a no-op when ``A2ALAB_RUNTIME_SECRET_ARN`` is unset — that is
local development, where the same processes read the same names from `.env`
and must not need AWS at all. When the ARN *is* set, a failed fetch raises:
a hosted runtime that silently started without its credentials would answer
every request with an auth error, which is far harder to read in a trace
than a container that refuses to boot.

``setdefault`` semantics, matching the harvest Lambda: an explicitly-set
environment variable wins over the secret, so a one-off override on the
runtime config is still possible without editing the secret.
"""

from __future__ import annotations

import json
import os

ARN_VAR = "A2ALAB_RUNTIME_SECRET_ARN"

_loaded = False


def load_secret_env(arn: str | None = None) -> list[str]:
    """Merge the runtime secret's JSON object into os.environ.

    Returns the names (never the values) of the keys the secret carried, for
    startup logging. Idempotent: the fetch happens once per process.
    """
    global _loaded
    arn = arn or os.environ.get(ARN_VAR)
    if _loaded or not arn:
        return []

    import boto3

    raw = boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"{ARN_VAR} secret must be a JSON object of env vars")
    for key, value in payload.items():
        os.environ.setdefault(key, str(value))
    _loaded = True
    return sorted(payload)


def load_secret_env_and_log(source: str) -> None:
    """load_secret_env() with a one-line, value-free startup log."""
    keys = load_secret_env()
    if keys:
        print(f"[secret-env] {source}: loaded {len(keys)} keys from secret: {', '.join(keys)}")
