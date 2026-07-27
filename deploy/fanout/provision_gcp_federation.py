"""Let the fan-out MCP Lambda call Vertex AI without a Google key (WS7 item 4).

    uv run python deploy/fanout/provision_gcp_federation.py

This is D40 in the mirror. That one let an Agent Engine container call Bedrock
AgentCore by minting a Google OIDC token and trading it at AWS STS. This one
lets an AWS Lambda call Agent Engine — and the two are not symmetric, which is
the point worth recording:

    GCP -> AWS   AWS trusts `accounts.google.com` as a web identity provider
                 natively. Nothing is created on the AWS side except a role
                 whose trust policy pins the Google subject and audience.
    AWS -> GCP   Google trusts nobody until you build the trust: a workload
                 identity pool, an AWS provider inside it, an attribute mapping,
                 an attribute condition, and an impersonation binding on a
                 service account. Five objects to AWS's one.

So the "keyless federation" both clouds advertise costs very different amounts
depending on which way you are going, and Google's side is where the identity
gets *shaped* — the attribute mapping decides what a principal even is before
any policy can mention it.

**How the Lambda proves who it is.** Not with a token. google-auth builds a
signed-but-unsent `sts:GetCallerIdentity` request from the function's ambient
role credentials and hands that to Google, which replays it against AWS STS.
The credential is therefore something AWS vouches for, Google verifies, and
nobody can replay usefully once the Lambda's own short-lived credentials
expire. There is no key in the function and nothing to rotate.

**Why a service account at all.** The federated principal holds no Google
permissions and Agent Engine authorizes service accounts, so the pool grants
the AWS role permission to impersonate exactly one service account, and that
account holds the `aiplatform.user` grant. Two hops, deliberately: revoking the
Lambda's access is a binding change, not a permissions audit.

Needs: gcloud ADC with project-owner-ish rights on the GCP side (`gcloud auth
application-default login`; long-lived, rarely needs redoing) — no AWS session,
because nothing is created in AWS here. Idempotent: re-run after changing the
Lambda's role name.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from dotenv import load_dotenv  # noqa: E402

POOL_ID = "a2alab-aws"
PROVIDER_ID = "a2alab-lambda"
SA_NAME = "a2alab-fanout-mcp"
# The Lambda execution role, by NAME: the attribute mapping extracts the role
# name out of the assumed-role ARN, so this is the value the principalSet
# member is keyed on. Session names differ per invocation and must not appear.
DEFAULT_LAMBDA_ROLE = "a2alab-fanout-lambda"
AWS_ACCOUNT = "REDACTED-AWS-ACCOUNT"


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(args)}")
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        # "already exists" is the normal path on re-run, not a failure.
        if "ALREADY_EXISTS" in proc.stderr or "already exists" in proc.stderr:
            print("  (exists)")
            return proc
        if check:
            raise SystemExit(f"failed: {proc.stderr.strip()[:500]}")
    return proc


def gcloud(*args: str) -> subprocess.CompletedProcess:
    return run(["gcloud", *args])


def gcloud_eventually(*args: str, attempts: int = 6, delay_s: float = 5.0) -> None:
    """gcloud, retried through IAM's eventual consistency.

    A service account is not immediately visible to the policy APIs after
    `service-accounts create` returns success: the very next
    `add-iam-policy-binding` fails with INVALID_ARGUMENT "Service account ...
    does not exist". Measured here on the first provisioning run. It is a
    propagation delay wearing a not-found error's clothes, which is worth
    absorbing rather than leaving as a re-run instruction — a provisioning
    script that only works the second time trains you to ignore its failures.
    """
    for attempt in range(1, attempts + 1):
        proc = run(list(("gcloud", *args)), check=False)
        if proc.returncode == 0 or "ALREADY_EXISTS" in proc.stderr:
            return
        if "does not exist" in proc.stderr and attempt < attempts:
            print(f"  (not visible yet — retry {attempt}/{attempts - 1} in {delay_s:.0f}s)")
            time.sleep(delay_s)
            continue
        raise SystemExit(f"failed: {proc.stderr.strip()[:500]}")


def write_env_var(var: str, value: str) -> None:
    env_path = REPO / ".env"
    lines = env_path.read_text().splitlines()
    hit = False
    for i, ln in enumerate(lines):
        if ln.startswith(f"{var}="):
            lines[i] = f"{var}={value}"
            hit = True
    if not hit:
        lines.append(f"{var}={value}")
    env_path.write_text("\n".join(lines) + "\n")
    print(f".env: {var} set")


def main() -> None:
    load_dotenv(REPO / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="a2a-lab-d441")
    parser.add_argument("--pool", default=POOL_ID)
    parser.add_argument("--provider", default=PROVIDER_ID)
    parser.add_argument("--lambda-role", default=DEFAULT_LAMBDA_ROLE)
    parser.add_argument("--aws-account", default=AWS_ACCOUNT)
    args = parser.parse_args()

    number = subprocess.run(
        ["gcloud", "projects", "describe", args.project, "--format=value(projectNumber)"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    print(f"project {args.project} ({number})")

    sa_email = f"{SA_NAME}@{args.project}.iam.gserviceaccount.com"

    # ---- the pool and its AWS provider -------------------------------------
    gcloud(
        "iam",
        "workload-identity-pools",
        "create",
        args.pool,
        f"--project={args.project}",
        "--location=global",
        "--display-name=A2A lab AWS workloads",
        "--description=AWS workloads (fan-out MCP Lambda) calling Vertex AI (WS7 item 4)",
    )

    # attribute.aws_role extracts the ROLE NAME from an assumed-role ARN, which
    # is what makes a stable principalSet member possible: the full ARN carries
    # a per-invocation session name, so pinning the ARN would pin one Lambda
    # invocation and nothing else.
    mapping = (
        "google.subject=assertion.arn,"
        "attribute.account=assertion.account,"
        "attribute.aws_role=assertion.arn.extract('assumed-role/{role_name}/')"
    )
    # Belt and braces with the principalSet below. The condition keeps non-role
    # AWS identities (an IAM user, the account root) out of the pool entirely,
    # so a misconfigured binding cannot silently widen access beyond roles.
    condition = f"assertion.arn.startsWith('arn:aws:sts::{args.aws_account}:assumed-role/')"
    gcloud(
        "iam",
        "workload-identity-pools",
        "providers",
        "create-aws",
        args.provider,
        f"--project={args.project}",
        "--location=global",
        f"--workload-identity-pool={args.pool}",
        f"--account-id={args.aws_account}",
        f"--attribute-mapping={mapping}",
        f"--attribute-condition={condition}",
    )
    # Re-run safety: create is a no-op once the provider exists, so the mapping
    # and condition would silently stay stale after an edit here.
    gcloud(
        "iam",
        "workload-identity-pools",
        "providers",
        "update-aws",
        args.provider,
        f"--project={args.project}",
        "--location=global",
        f"--workload-identity-pool={args.pool}",
        f"--attribute-mapping={mapping}",
        f"--attribute-condition={condition}",
    )

    # ---- the service account the Lambda impersonates ------------------------
    gcloud(
        "iam",
        "service-accounts",
        "create",
        SA_NAME,
        f"--project={args.project}",
        "--display-name=A2A lab fan-out MCP (AWS Lambda)",
    )
    # aiplatform.user is what Agent Engine's A2A endpoint authorizes against.
    # Scoped to the project rather than the single engine because Agent Engine
    # resource-level IAM is not exposed on the preview surface — worth stating
    # rather than implying least privilege we did not achieve.
    gcloud_eventually(
        "projects",
        "add-iam-policy-binding",
        args.project,
        f"--member=serviceAccount:{sa_email}",
        "--role=roles/aiplatform.user",
        "--condition=None",
    )

    member = (
        f"principalSet://iam.googleapis.com/projects/{number}/locations/global/"
        f"workloadIdentityPools/{args.pool}/attribute.aws_role/{args.lambda_role}"
    )
    gcloud_eventually(
        "iam",
        "service-accounts",
        "add-iam-policy-binding",
        sa_email,
        f"--project={args.project}",
        "--role=roles/iam.workloadIdentityUser",
        f"--member={member}",
    )

    audience = (
        f"//iam.googleapis.com/projects/{number}/locations/global/"
        f"workloadIdentityPools/{args.pool}/providers/{args.provider}"
    )
    # These are the LAMBDA's variable names, and they go to the function's
    # environment, not the laptop's — same reasoning as D40's
    # A2ALAB_ADK_AWS_* pair. Setting them locally would put the laptop into
    # federation mode, where there is no AWS role to present and every ADK call
    # would start failing for a reason unrelated to Google.
    write_env_var("A2ALAB_FANOUT_GCP_AUDIENCE", audience)
    write_env_var("A2ALAB_FANOUT_GCP_SA", sa_email)

    print(
        "\n"
        + json.dumps(
            {"audience": audience, "service_account": sa_email, "member": member}, indent=1
        )
    )
    print(
        "\nnext: deploy/fanout/build_zip.sh && deploy/fanout/deploy_fanout.sh\n"
        f"  (the Lambda's execution role MUST be named {args.lambda_role} — the "
        "principalSet member above is keyed on that name)"
    )


if __name__ == "__main__":
    main()
