"""Let the ADK container call AWS without an AWS key (WS8 / D40).

    uv run python deploy/adk/provision_aws_federation.py --google-sub <numeric-id>

The problem this solves is the fan-out orchestrator's third leg. Logistics and
Commercial are HTTP endpoints the container can reach with a bearer token;
Customer Operations is a **Bedrock AgentCore runtime**, whose data plane is
SigV4-only — there is no public HTTP front door to call instead. So a container
running in Google's cloud has to hold an AWS identity, and the obvious way to
do that (paste an access key into the runtime's env vars) is exactly the
long-lived-secret-in-a-container pattern the lab argues against everywhere else.

Instead: AWS trusts ``accounts.google.com`` as a web identity provider natively.
The Agent Engine container mints a Google-signed OIDC token for its own service
account and trades it at STS for 1-hour credentials. Nothing durable is stored
in the container, and there is no key to rotate or leak. This script creates the
AWS half — the role and the trust policy that pins it to exactly that one Google
identity and audience.

**Finding the subject.** ``sub`` is the service account's numeric id, and for a
Google-*managed* service agent (Agent Engine runs as
``service-<projnum>@gcp-sa-aiplatform-re.iam.gserviceaccount.com``, in a project
you do not own) you cannot look it up. So the first federated call is designed
to fail usefully: set ``A2ALAB_AWS_ROLE_ARN`` to the role this script *will*
create, run the orchestrator once, and read the subject out of the leg's
``[leg unavailable: ...]`` message (interop/cloud_auth.py prints the claims).
Then run this script with that value.

Needs AWS credentials with IAM write access (``aws sso login --profile lab-account``,
Zscaler ON).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from dotenv import load_dotenv  # noqa: E402

ROLE_NAME = "a2alab-adk-fanout"
DEFAULT_AUDIENCE = "a2a-interop-lab"
# Plain ASCII, deliberately: IAM's role description accepts only printable
# ASCII plus Latin-1, so the repo's house em-dash (U+2014) fails CreateRole
# validation -- and nowhere else in the codebase, which makes it a surprising
# one-line failure the first time you provision.
DESCRIPTION = "A2A interop lab: Vertex AI Agent Engine calling Bedrock AgentCore (WS8/D40)"


def trust_policy(google_sub: str, audience: str) -> dict:
    """Both conditions are load-bearing — and the audience one is a trap.

    Why both: ``oaud`` alone is not a control, because any Google principal can
    mint a token for any audience string, so an audience-only trust would
    accept every Google account on earth. ``sub`` alone would accept tokens
    this container minted for some *other* service, which is a confused-deputy
    hole. Pin both.

    The trap is WHICH audience key. AWS's Google integration remaps the claims
    when the token carries an ``azp`` (authorized party) field, which
    service-account tokens always do:

        accounts.google.com:aud   <- the token's azp   (the SA's client id)
        accounts.google.com:oaud  <- the token's aud   (our audience string)

    (IAM condition-keys reference, "Available keys for AWS OIDC federation":
    *"When the azp field is set, the aud field matches the
    accounts.google.com:oaud condition key."*)

    So the intuitive policy — pin ``accounts.google.com:aud`` to the audience
    you asked for — fails with a bare ``AccessDenied`` that names no key and
    looks exactly like a propagation delay or a wrong ``sub``.
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Federated": "accounts.google.com"},
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {
                        "accounts.google.com:sub": google_sub,
                        "accounts.google.com:oaud": audience,
                    }
                },
            }
        ],
    }


def invoke_policy(runtime_arns: list[str]) -> dict:
    """Invoke rights on the named runtimes only — not `bedrock-agentcore:*`.

    The endpoint sub-resource is included because InvokeAgentRuntime authorizes
    against the endpoint ARN when one is targeted, and a runtime-only grant
    fails there with an AccessDenied that names the endpoint, not the runtime.
    """
    resources: list[str] = []
    for arn in runtime_arns:
        resources += [arn, f"{arn}/*"]
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
                "Resource": sorted(set(resources)),
            }
        ],
    }


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
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--google-sub",
        required=True,
        help="numeric 'sub' from the container's own OIDC token (see module docstring)",
    )
    parser.add_argument("--audience", default=DEFAULT_AUDIENCE)
    parser.add_argument("--role-name", default=ROLE_NAME)
    args = parser.parse_args()

    load_dotenv(REPO / ".env")
    runtime_arns = [
        arn
        for arn in (
            os.environ.get("OPENAI_AGENTCORE_ARN"),
            os.environ.get("CLAUDE_AGENTCORE_ARN"),
        )
        if arn
    ]
    if not runtime_arns:
        raise SystemExit("no AgentCore runtime ARNs in .env — deploy the runtimes first")

    import boto3

    iam = boto3.client("iam")
    trust = trust_policy(args.google_sub, args.audience)
    try:
        role = iam.create_role(
            RoleName=args.role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description=DESCRIPTION,
            MaxSessionDuration=3600,
        )["Role"]
        print(f"created role {args.role_name}")
    except iam.exceptions.EntityAlreadyExistsException:
        iam.update_assume_role_policy(RoleName=args.role_name, PolicyDocument=json.dumps(trust))
        role = iam.get_role(RoleName=args.role_name)["Role"]
        print(f"updated trust policy on existing role {args.role_name}")

    iam.put_role_policy(
        RoleName=args.role_name,
        PolicyName="invoke-agentcore-runtimes",
        PolicyDocument=json.dumps(invoke_policy(runtime_arns)),
    )
    print(f"invoke policy set for {len(runtime_arns)} runtime(s)")

    # Deliberately the ADK-scoped names: deploy_adk.py renames them to the
    # A2ALAB_AWS_* pair inside the container. Setting the container's names in
    # .env would put the LAPTOP into federation mode, where ADC is a human
    # login that cannot mint a service-account token — every local AgentCore
    # call would start failing for a reason that has nothing to do with AWS.
    write_env_var("A2ALAB_ADK_AWS_ROLE_ARN", role["Arn"])
    write_env_var("A2ALAB_ADK_AWS_AUDIENCE", args.audience)
    print(
        "\nnext: redeploy the orchestrator so the container picks up the role\n"
        "  A2ALAB_ADK_DISABLE_OTEL=1 uv run python deploy/adk/deploy_adk.py --role orchestrator"
    )


if __name__ == "__main__":
    main()
