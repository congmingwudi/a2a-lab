"""Outbound cloud identity for lab code running somewhere that is not a laptop.

D39 says AWS auth is the only interactive human login in the runtime path and
every other platform credential is a service identity, constructed explicitly.
This module is where the *outbound* half of that rule lives — the credentials a
lab agent needs to call SOMEONE ELSE'S cloud.

It exists because of the ADK fan-out orchestrator (WS8). That agent runs inside
a Vertex AI Agent Engine container and has to reach three clouds:

    Logistics       -> Agent Engine A2A     (Google, same cloud: ADC just works)
    Commercial      -> Foundry A2A          (Entra, needs an SP)
    Customer comms  -> Bedrock AgentCore    (SigV4, needs AWS)

The first is free. The second and third are not, and they fail *differently*,
which is the finding: an agent that leaves its own cloud needs an identity in
the destination cloud, and every hyperscaler spells that differently.

Two rules, both learned the hard way:

- **Never a credential chain that can fall back to a person.** Azure gets an
  explicit ClientSecretCredential, never DefaultAzureCredential. See the
  narrative in ``observability/credentials.py`` — a chain that finds a
  developer's ``az login`` proves a human has access, not the service.
- **Never a long-lived key in a container.** AWS is reached by *federating* the
  container's own Google identity: the runtime mints a Google-signed OIDC token
  and trades it at STS for short-lived AWS credentials. No AWS access key ever
  exists to leak, rotate, or commit.

Both helpers degrade to the ambient behaviour when unconfigured, so the laptop
and the Lambda keep working unchanged: ``aws_session()`` without a role ARN is
just ``boto3.Session()``.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

# ---- Azure -----------------------------------------------------------------

AZURE_VARS = ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")


def azure_missing() -> list[str]:
    return [v for v in AZURE_VARS if not os.environ.get(v)]


def azure_credential():
    """The lab's Entra service principal — explicitly, or not at all.

    Deliberately NOT DefaultAzureCredential: that walks a chain ending in
    developer logins, so it answers "can *someone here* read this?" when the
    only useful question is "can the service principal do this?".
    """
    missing = azure_missing()
    if missing:
        raise RuntimeError(
            f"Azure service principal not configured — missing {', '.join(missing)}. "
            "The lab does not fall back to an interactive az login (D39)."
        )
    from azure.identity import ClientSecretCredential

    return ClientSecretCredential(
        tenant_id=os.environ["AZURE_TENANT_ID"],
        client_id=os.environ["AZURE_CLIENT_ID"],
        client_secret=os.environ["AZURE_CLIENT_SECRET"],
    )


# ---- AWS, reached from another cloud ---------------------------------------

AWS_ROLE_VAR = "A2ALAB_AWS_ROLE_ARN"
AWS_AUDIENCE_VAR = "A2ALAB_AWS_WEB_IDENTITY_AUDIENCE"
DEFAULT_AUDIENCE = "a2a-interop-lab"

# Refresh this far before expiry — a leg call can take ~2 min, so a token that
# is merely "not expired yet" is not good enough.
_SKEW_S = 600.0
_lock = threading.Lock()
_cache: dict[tuple[str, str], tuple[dict[str, str], float]] = {}


def federation_configured() -> bool:
    return bool(os.environ.get(AWS_ROLE_VAR))


def _google_id_token(audience: str) -> str:
    """A Google-signed OIDC token identifying THIS container's service account.

    On Agent Engine this comes from the GCP metadata server, so nothing is
    stored anywhere and the token lives about an hour. AWS trusts
    ``accounts.google.com`` as a web identity provider natively, so no IAM OIDC
    provider has to be registered on the AWS side — the role's trust policy
    pins the audience and the service account's numeric subject.
    """
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token as google_id_token

    return google_id_token.fetch_id_token(Request(), audience)


def _claims(token: str) -> dict[str, Any]:
    """The token's own claims, unverified, for error messages only.

    AWS's trust policy has to pin ``accounts.google.com:sub`` to the calling
    service account's numeric id, and that id is not discoverable from outside
    a Google-managed service agent's own token. So the first federated call is
    allowed to fail loudly and *tell you what to pin* — the error names the
    subject, audience and email instead of saying AccessDenied and stopping.
    Never used for authorization; STS verifies the signature, not this.
    """
    import base64
    import json

    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        # azp matters as much as aud here: AWS pins `accounts.google.com:aud`
        # to azp and `:oaud` to aud whenever azp is present, which it always is
        # on a service-account token. Reporting aud without azp is what makes
        # the resulting AccessDenied look unexplainable.
        return {k: data.get(k) for k in ("sub", "aud", "azp", "email", "iss") if data.get(k)}
    except Exception:
        return {}


def _assume_role(role_arn: str, audience: str) -> tuple[dict[str, str], float]:
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config

    token = _google_id_token(audience)
    # AssumeRoleWithWebIdentity is an unsigned STS operation by definition —
    # the whole point is that the caller has no AWS credentials yet. Signing it
    # would require the credentials we are here to obtain.
    sts = boto3.client(
        "sts",
        region_name=os.environ.get("AWS_REGION") or "us-east-1",
        config=Config(signature_version=UNSIGNED),
    )
    try:
        resp = sts.assume_role_with_web_identity(
            RoleArn=role_arn,
            RoleSessionName="a2alab-adk-fanout",
            WebIdentityToken=token,
        )
    except Exception as exc:
        raise RuntimeError(
            f"AssumeRoleWithWebIdentity on {role_arn} failed ({type(exc).__name__}: {exc}). "
            f"Caller identity was {_claims(token)} — pin accounts.google.com:sub and "
            "accounts.google.com:aud to those values in the role's trust policy "
            "(deploy/adk/provision_aws_federation.py)."
        ) from exc
    creds = resp["Credentials"]
    return (
        {
            "aws_access_key_id": creds["AccessKeyId"],
            "aws_secret_access_key": creds["SecretAccessKey"],
            "aws_session_token": creds["SessionToken"],
        },
        creds["Expiration"].timestamp(),
    )


def aws_session(region: str | None = None) -> Any:
    """A boto3 Session for calling AWS from wherever this is running.

    With ``A2ALAB_AWS_ROLE_ARN`` set (the Agent Engine container), federates
    the container's Google identity into that role. Without it, the ordinary
    boto3 chain — which is what the laptop and the harvest Lambda want.
    """
    import boto3

    role_arn = os.environ.get(AWS_ROLE_VAR)
    if not role_arn:
        return boto3.Session(region_name=region) if region else boto3.Session()

    audience = os.environ.get(AWS_AUDIENCE_VAR) or DEFAULT_AUDIENCE
    key = (role_arn, audience)
    with _lock:
        cached = _cache.get(key)
        if cached is None or time.time() > cached[1] - _SKEW_S:
            cached = _assume_role(role_arn, audience)
            _cache[key] = cached
    return boto3.Session(region_name=region, **cached[0])
