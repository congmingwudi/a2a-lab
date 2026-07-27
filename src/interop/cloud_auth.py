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

Then WS7 item 4 pushed the same problem back the other way. The fan-out MCP
server runs as an AWS Lambda and has to reach Vertex AI Agent Engine, so an AWS
workload now needs a *Google* identity — the exact mirror of the case above.
Both directions are here, and comparing them is the point:

    GCP -> AWS   the container mints a Google OIDC token; AWS trusts
                 ``accounts.google.com`` natively, so nothing is registered on
                 the AWS side and the role's trust policy does the pinning
    AWS -> GCP   the Lambda presents a *signed GetCallerIdentity request* as
                 its credential; Google will not trust AWS until you create a
                 workload identity pool and provider, then bind them to a
                 service account to impersonate

Same guarantee reached two ways, and the asymmetry is a real finding: Google
requires standing infrastructure per trust relationship where AWS requires
none, and Google's side is where the identity is *shaped* (attribute mapping,
attribute conditions) rather than merely accepted.

Both helpers degrade to the ambient behaviour when unconfigured, so the laptop
and the Lambda keep working unchanged: ``aws_session()`` without a role ARN is
just ``boto3.Session()``, and ``google_credentials()`` without a pool is plain
ADC.
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


# ---- Google, reached from AWS ----------------------------------------------

GOOGLE_AUDIENCE_VAR = "A2ALAB_GCP_WORKLOAD_AUDIENCE"
GOOGLE_SA_VAR = "A2ALAB_GCP_IMPERSONATE_SA"
GOOGLE_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)

_google_lock = threading.Lock()
_google_cache: dict[tuple[str, str], Any] = {}


def google_federation_configured() -> bool:
    return bool(os.environ.get(GOOGLE_AUDIENCE_VAR) and os.environ.get(GOOGLE_SA_VAR))


def _aws_region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"


def _botocore_supplier():
    """Hand google-auth botocore's credential resolution instead of its own.

    google-auth's built-in AWS supplier looks in exactly two places: the
    ``AWS_ACCESS_KEY_ID`` env vars, then the **EC2 metadata service** at
    169.254.169.254. That covers Lambda, which sets the env vars — and it does
    NOT cover ECS/Fargate, which sets neither: a task's credentials come from
    the container credentials endpoint at ``$AWS_CONTAINER_CREDENTIALS_RELATIVE_URI``.
    So the identical code path that federated fine from the fan-out Lambda
    (D41) failed on the bridge's Fargate task with a TransportError trying to
    reach an IMDS address that does not answer there.

    botocore already resolves every one of these — env vars, container
    endpoint, IMDS, profiles, assumed roles — so delegating to it makes this
    work on any AWS compute rather than on the two shapes we happened to test.
    Credentials are fetched per call because botocore refreshes them underneath
    us; caching the frozen values here would federate happily until the task's
    role credentials rotated and then fail for a reason far from the cause.
    """
    from google.auth import aws as google_aws

    class BotocoreSupplier(google_aws.AwsSecurityCredentialsSupplier):
        def get_aws_security_credentials(self, context, request):
            import boto3

            frozen = boto3.Session().get_credentials()
            if frozen is None:
                raise RuntimeError(
                    "no AWS credentials for GCP federation — the task or function "
                    "has no role attached"
                )
            frozen = frozen.get_frozen_credentials()
            return google_aws.AwsSecurityCredentials(
                frozen.access_key, frozen.secret_key, frozen.token
            )

        def get_aws_region(self, context, request):
            return _aws_region()

    return BotocoreSupplier()


def _external_account_config(audience: str, service_account: str) -> dict[str, Any]:
    """The external_account credential Google's client library expects.

    The subject token is not a bearer token at all: it is a *signed but
    unsent* ``sts:GetCallerIdentity`` request. Google replays it against AWS
    STS to learn who the caller is. So the credential presented to Google is
    one AWS can vouch for and Google can verify, and it expires with the
    workload's own short-lived role credentials.

    ``service_account_impersonation_url`` is what makes this usable: the
    federated principal itself has no Google permissions, and Agent Engine
    authorizes service accounts. The pool grants the AWS role the right to
    impersonate one service account, and that account holds the actual
    ``aiplatform.reasoningEngines`` grant.

    Note there is no ``credential_source``: the supplier above replaces it, and
    google-auth rejects a config carrying both.
    """
    return {
        "type": "external_account",
        "audience": audience,
        "subject_token_type": "urn:ietf:params:aws:token-type:aws4_request",
        "token_url": "https://sts.googleapis.com/v1/token",
        "service_account_impersonation_url": (
            "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
            f"{service_account}:generateAccessToken"
        ),
    }


def google_credentials(scopes: tuple[str, ...] = GOOGLE_SCOPES) -> Any:
    """Google credentials for wherever this is running.

    With the workload-identity vars set (the fan-out MCP Lambda), federates the
    ambient AWS identity into a Google service account. Without them, plain
    ADC — which is what the laptop and the Agent Engine container want, and why
    every existing caller keeps working untouched.
    """
    audience = os.environ.get(GOOGLE_AUDIENCE_VAR)
    service_account = os.environ.get(GOOGLE_SA_VAR)
    if not (audience and service_account):
        from google.auth import default as google_default

        credentials, _ = google_default(scopes=list(scopes))
        return credentials

    key = (audience, service_account)
    with _google_lock:
        cached = _google_cache.get(key)
        if cached is None:
            # google.auth.aws, NOT google.auth.identity_pool: identity_pool is
            # for file- and URL-sourced subject tokens, and it rejects an
            # `environment_id` credential source. The AWS class is the one that
            # knows how to build and sign the GetCallerIdentity request.
            from google.auth import aws as google_aws

            try:
                # from_info rather than the constructor: it strips `type` and
                # threads the supplier through for us.
                cached = google_aws.Credentials.from_info(
                    _external_account_config(audience, service_account),
                    aws_security_credentials_supplier=_botocore_supplier(),
                ).with_scopes(list(scopes))
            except Exception as exc:
                raise RuntimeError(
                    f"Google workload identity federation failed to initialise for "
                    f"audience {audience} impersonating {service_account} "
                    f"({type(exc).__name__}: {exc}). Provision the pool with "
                    "deploy/fanout/provision_gcp_federation.py."
                ) from exc
            _google_cache[key] = cached
    return cached


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
