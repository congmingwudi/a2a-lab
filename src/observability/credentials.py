"""Service credentials for the harvest, from one place, for local and hosted.

The rule this module enforces (2026-07-25): **AWS auth is the only human login
in the stack.** Everything the harvest touches afterwards — Salesforce, GCP,
Azure, the model vendors — is a service identity whose secret lives in the
`a2alab/obs/harvest` Secrets Manager secret and is fetched *with* that AWS
auth. No credential should ever be picked up from a developer's `az login`,
`gcloud auth`, or a value typed into a laptop `.env`.

That rule exists because of a real failure. Foundry's harvest passed locally
and failed hosted with `InsufficientAccessError`, and both results were
"correct": `DefaultAzureCredential` walks a chain, found the developer's
Azure CLI login on the laptop, and found the service principal in Lambda —
which had never been granted `Log Analytics Reader`. The laptop run was
proving that a human had access. A credential chain that can fall back to a
person does not test production, and worse, it hides the gap until the code
is somewhere a person isn't.

So: `azure_credential()` builds an explicit ClientSecretCredential and refuses
to guess; `materialize_gcp_key()` turns the service-account JSON into ADC for
this process, displacing any ambient `gcloud` login; and both local and hosted
entrypoints load the same secret through `load_harvest_secret()`.
"""

from __future__ import annotations

import json
import os
import tempfile

SECRET_ARN_VAR = "A2ALAB_HARVEST_SECRET_ARN"
GCP_KEY_JSON_VAR = "GOOGLE_APPLICATION_CREDENTIALS_JSON"
AZURE_VARS = ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")

_secret_loaded = False
_gcp_key_path: str | None = None


def load_harvest_secret(arn: str | None = None, *, override: bool = True) -> list[str]:
    """Merge the harvest secret's JSON object into os.environ.

    Returns the key NAMES (never values) it carried. No-op when the ARN is
    unset, so a contributor without AWS can still run the sources that need
    no cloud credentials.

    `override=True` deliberately inverts `interop.secret_env`'s setdefault
    semantics. That module lets an explicitly-set variable win, which is right
    for a hosted runtime you may need to poke at. Here the whole point is that
    the secret is the single source of truth: a stale value in someone's .env
    quietly beating the managed one is exactly the drift this replaces.
    """
    global _secret_loaded
    arn = arn or os.environ.get(SECRET_ARN_VAR)
    if _secret_loaded or not arn:
        return []
    import boto3

    # Region from the ARN, not from the environment. This laptop has
    # AWS_REGION=us-east-1 in .env and AWS_DEFAULT_REGION=us-west-2 ambient in
    # the shell; boto3 resolved the latter and reported the secret as
    # ResourceNotFound — which reads as "wrong ARN", not "right ARN, wrong
    # region". A fully-qualified ARN already names its region, so use it.
    parts = arn.split(":")
    region = parts[3] if len(parts) > 4 and parts[0] == "arn" else None
    client = (
        boto3.client("secretsmanager", region_name=region)
        if region
        else boto3.client("secretsmanager")
    )
    raw = client.get_secret_value(SecretId=arn)["SecretString"]
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"{SECRET_ARN_VAR} secret must be a JSON object of env vars")
    for key, value in payload.items():
        if override or key not in os.environ:
            os.environ[key] = str(value)
    _secret_loaded = True
    return sorted(payload)


def materialize_gcp_key() -> bool:
    """Point ADC at the service-account key carried in the secret.

    google.auth wants a FILE path in GOOGLE_APPLICATION_CREDENTIALS, but the
    key arrives as a secret string, so write it out 0600 and point at it.
    Setting the variable also *displaces* any ambient `gcloud auth` login,
    which is the point: the harvest reads GCP as the service account whether
    it runs in Lambda or on a laptop that happens to have gcloud configured.
    """
    global _gcp_key_path
    raw = os.environ.get(GCP_KEY_JSON_VAR)
    if not raw:
        return False
    if _gcp_key_path and os.path.exists(_gcp_key_path):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _gcp_key_path
        return True
    # /tmp is the only writable path in Lambda; locally this is the temp dir.
    fd, path = tempfile.mkstemp(prefix="a2alab-gcp-", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        fh.write(raw)
    os.chmod(path, 0o600)
    _gcp_key_path = path
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path
    return True


def azure_missing() -> list[str]:
    return [v for v in AZURE_VARS if not os.environ.get(v)]


def azure_credential():
    """The lab's Entra service principal — explicitly, or not at all.

    The constructor lives in ``interop.cloud_auth`` so the agents that ship
    without ``src/observability`` (the ADK container's Foundry leg) get the
    same rule from the same code rather than a copy that can drift. This
    wrapper only adds the harvest-specific hint about where the secret lives.
    """
    from interop.cloud_auth import azure_credential as _explicit

    if azure_missing():
        raise RuntimeError(
            f"Azure service principal not configured — missing {', '.join(azure_missing())}. "
            f"They belong in the {SECRET_ARN_VAR} secret; the lab does not fall "
            "back to an interactive az login."
        )
    return _explicit()


def prepare() -> list[str]:
    """One call for an entrypoint: load the secret, then wire GCP ADC."""
    names = load_harvest_secret()
    materialize_gcp_key()
    return names
