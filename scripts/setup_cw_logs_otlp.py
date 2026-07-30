#!/usr/bin/env python
"""Provision the CloudWatch **Logs** OTLP ingestion path (WS16, D59).

Targets the same telemetry AWS account the metrics already reach — a separate
account from the lab's hosting, pinned by `AWS_PROFILE` in `.env` (never named
here; the account id and profile name live only in `.env`/`.a2alab`, D39).

    uv run python scripts/setup_cw_logs_otlp.py            # DRY RUN — prints the plan
    uv run python scripts/setup_cw_logs_otlp.py --apply    # create/verify in AWS

WS9 already exports Claude Code **metrics** to CloudWatch's managed OTLP endpoint
(`monitoring.<region>/v1/metrics`). This script provisions the sibling **logs**
path, and the two do NOT share a credential — proven live 2026-07-30:
the metrics bearer token returns 403 against `logs.<region>/v1/logs`, because a
service-specific credential is scoped at creation to one service and metrics is
`cloudwatch.amazonaws.com` while logs is `logs.amazonaws.com` (build note §"logs
vs metrics", D59). Logs also needs, unlike metrics:

- a **pre-provisioned log group** with bearer-token auth explicitly enabled
  (`put-account-policy`/`put-bearer-token-authentication`); OTLP does not
  auto-create it, and ingestion 4xxs until it exists;
- an IAM user carrying `CloudWatchLogsAPIKeyAccess` (the managed policy that
  grants `logs:PutLogEvents` + `logs:CallWithBearerToken`), mirroring how the
  metrics user carries `CloudWatchAPIKeyAccess`.

It is idempotent — every step checks-then-creates, so a re-run after a partial
provision completes the rest rather than erroring. The bearer token lands in
Secrets Manager (never on disk), the same posture as the metrics key (D39); the
console/exporter fetch it at runtime.

Read-back is NOT here: ingested logs are read over SigV4 (Logs Insights /
FilterLogEvents), which is `src/observability/coding_logs_source.py` (Phase 2),
not this provisioner.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

# The logs user mirrors the metrics user's name so the pair reads as a pair.
LOGS_USER = os.environ.get("A2ALAB_CW_LOGS_USER", "a2alab-cw-logs-otlp")
# AWS-managed policy granting logs:PutLogEvents + logs:CallWithBearerToken —
# the logs analogue of CloudWatchAPIKeyAccess on the metrics user.
LOGS_POLICY_ARN = "arn:aws:iam::aws:policy/CloudWatchLogsAPIKeyAccess"
LOG_GROUP = os.environ.get("A2ALAB_CW_LOGS_GROUP", "/a2alab/coding-agents/otlp")
SECRET_ID = os.environ.get("A2ALAB_CW_LOGS_SECRET", "a2alab/telemetry/cw-logs-api-key")


def _boto(service: str):
    import boto3

    return boto3.client(service, region_name=os.environ.get("AWS_REGION", "us-east-1"))


def ensure_user(iam, apply: bool) -> bool:
    try:
        iam.get_user(UserName=LOGS_USER)
        print(f"  user {LOGS_USER}: exists")
        exists = True
    except iam.exceptions.NoSuchEntityException:
        print(f"  user {LOGS_USER}: MISSING → create")
        exists = False
        if apply:
            iam.create_user(
                UserName=LOGS_USER,
                Tags=[{"Key": "project", "Value": "a2a-lab"}, {"Key": "signal", "Value": "logs"}],
            )
    attached = (
        {
            p["PolicyArn"]
            for p in iam.list_attached_user_policies(UserName=LOGS_USER)["AttachedPolicies"]
        }
        if exists
        else set()
    )
    if LOGS_POLICY_ARN in attached:
        print("  policy CloudWatchLogsAPIKeyAccess: attached")
    else:
        print("  policy CloudWatchLogsAPIKeyAccess: MISSING → attach")
        if apply:
            iam.attach_user_policy(UserName=LOGS_USER, PolicyArn=LOGS_POLICY_ARN)
    return exists


def ensure_log_group(logs, apply: bool) -> None:
    groups = logs.describe_log_groups(logGroupNamePrefix=LOG_GROUP).get("logGroups", [])
    if any(g["logGroupName"] == LOG_GROUP for g in groups):
        print(f"  log group {LOG_GROUP}: exists")
    else:
        print(f"  log group {LOG_GROUP}: MISSING → create")
        if apply:
            logs.create_log_group(
                logGroupName=LOG_GROUP,
                tags={"project": "a2a-lab", "signal": "logs"},
            )
    # Bearer-token auth must be enabled on the group or OTLP ingestion 401s even
    # with a valid logs credential. The API name has moved between SDK versions;
    # try the current one and fall back, reporting rather than crashing.
    if apply:
        try:
            logs.put_bearer_token_authentication(
                logGroupIdentifier=LOG_GROUP, bearerTokenAuthenticationEnabled=True
            )
            print("  bearer-token auth: enabled")
        except AttributeError:
            print(
                "  bearer-token auth: this botocore lacks put_bearer_token_authentication — "
                "enable it in the console on the log group, or upgrade botocore"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  bearer-token auth: could not enable ({type(exc).__name__}: {exc})")
    else:
        print("  bearer-token auth: would enable on the group")


def ensure_credential(iam, secrets, apply: bool) -> None:
    """Create the logs service-specific credential and store it in Secrets Manager.

    The API key is shown by AWS exactly once at creation, so this only mints one
    when the secret does not already hold a working pair. The metrics path uses
    keys CW_METRICS_API_KEY / CW_METRICS_CREDENTIAL_ID; the logs path mirrors it.
    """
    have_secret = False
    try:
        cur = json.loads(secrets.get_secret_value(SecretId=SECRET_ID)["SecretString"])
        have_secret = "CW_LOGS_API_KEY" in cur
    except secrets.exceptions.ResourceNotFoundException:
        cur = {}
    if have_secret:
        print(f"  secret {SECRET_ID}: already holds CW_LOGS_API_KEY (no new key minted)")
        return
    print(f"  secret {SECRET_ID}: MISSING key → mint logs credential + store")
    if not apply:
        return
    cred = iam.create_service_specific_credential(
        UserName=LOGS_USER, ServiceName="logs.amazonaws.com"
    )["ServiceSpecificCredential"]
    # The OTLP bearer token is ServiceCredentialSecret (the modern API-key form),
    # NOT ServicePassword (the old SMTP-style field, empty for this service). AWS
    # returns the secret exactly once, at creation — so getting this field name
    # wrong stores an empty token and the only fix is to delete and re-mint.
    api_key = cred.get("ServiceCredentialSecret") or cred.get("ServicePassword", "")
    if not api_key:
        raise RuntimeError(
            "credential created but no secret in the response — "
            f"fields were {sorted(cred)}; refusing to store an empty token"
        )
    payload = json.dumps(
        {"CW_LOGS_API_KEY": api_key, "CW_LOGS_CREDENTIAL_ID": cred["ServiceSpecificCredentialId"]}
    )
    try:
        secrets.create_secret(Name=SECRET_ID, SecretString=payload)
    except secrets.exceptions.ResourceExistsException:
        secrets.put_secret_value(SecretId=SECRET_ID, SecretString=payload)
    print("  logs credential minted and stored")


def main() -> int:
    load_dotenv()
    apply = "--apply" in sys.argv
    region = os.environ.get("AWS_REGION", "us-east-1")
    acct = os.environ.get("A2ALAB_AWS_ACCOUNT_ID", "?")
    print(f"{'APPLY' if apply else 'DRY RUN'} — CloudWatch Logs OTLP path")
    print(f"account {acct}  region {region}")
    print(f"  endpoint (write, bearer): https://logs.{region}.amazonaws.com/v1/logs")
    print(f"  log group: {LOG_GROUP}\n")

    iam = _boto("iam")
    logs = _boto("logs")
    secrets = _boto("secretsmanager")

    ensure_user(iam, apply)
    ensure_log_group(logs, apply)
    ensure_credential(iam, secrets, apply)

    if not apply:
        print("\nnothing created — re-run with --apply")
    else:
        print(f"\ndone. token in secret '{SECRET_ID}'. Wire the exporter (Phase 1) next.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
