#!/usr/bin/env bash
# Host the fourteen protocol faces on ECS Fargate, behind the ALB the bridge and
# console already share (WS13 item 2).
#
# One service, one port, fourteen mounted ASGI apps addressed by PATH
# (https://<FACES_HOSTNAME>/<target-name>/...). See src/faces/__init__.py for
# why that rather than fourteen services on fourteen hostnames.
#
#   deploy/faces/deploy_faces.sh                    # build, push, create-or-update
#   deploy/faces/deploy_faces.sh --skip-build       # redeploy the current image
#
# ⚠️  NOT YET RUN. Written 2026-07-28 from the proven deploy/bridge script but
# never executed — the first run needs a person watching. Its risky step is the
# listener rule (below), which touches a load balancer Salesforce depends on.
#
# WHY THE BRIDGE'S ALB rather than one per service: a second load balancer is
# ~$16/month for nothing. The bridge ALB already terminates TLS on :443 with
# the imported Cloudflare Origin certificate for *.agenticthings.com, which
# covers every lab hostname — so an additional face costs a target group, a
# listener RULE and a Fargate task. Host-based routing keeps the bridge on the
# listener's DEFAULT action, so a mistake in this rule cannot break Path A:
# unmatched traffic still falls through to the bridge exactly as today.
#
# DNS IS NOT TOUCHED HERE, deliberately, exactly as the bridge cutover was
# done. The rule is verified against the ALB's own hostname with a Host header
# first; pointing console-lab at the ALB is a separate, reversible one-field
# edit in Cloudflare. See the note printed at the end.
#
# Requires: .env populated, an authenticated AWS session (aws sso login,
# Zscaler ON), and Docker signed in to the container registry.
set -euo pipefail
cd "$(dirname "$0")/../.."
set -a; source .env; set +a
source deploy/aws_preflight.sh

REGION="${AWS_REGION:-us-east-1}"
NAME=a2alab-faces
CLUSTER=a2alab
BRIDGE_NAME=a2alab-bridge             # whose ALB and cluster this joins
TASK_ROLE=a2alab-faces-task
EXEC_ROLE=a2alab-faces-exec
CONTAINER_PORT=8300
# The hostname this face answers on. No fallback default: a ${VAR:-...} here
# would route someone else's checkout at the author's hostname.
HOSTNAME_="${FACES_HOSTNAME:?set FACES_HOSTNAME in .env - e.g. faces-lab.example.com}"
# Rule priority on the shared listener. Low numbers match first; the bridge is
# the default action and has no rule, so any free priority works.
RULE_PRIORITY="${FACES_RULE_PRIORITY:-30}"
SKIP_BUILD="${1:-}"

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$NAME"

# ---- image ------------------------------------------------------------------
if [ "$SKIP_BUILD" != "--skip-build" ]; then
  aws ecr describe-repositories --repository-names "$NAME" --region "$REGION" >/dev/null 2>&1 \
    || aws ecr create-repository --repository-name "$NAME" --region "$REGION" >/dev/null
  aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com" >/dev/null
  docker build --platform linux/arm64 -f deploy/faces/Dockerfile -t "$NAME" .
  docker tag "$NAME:latest" "$ECR:latest"
  docker push "$ECR:latest" >/dev/null
  echo "pushed $ECR:latest"
fi

# ---- credentials -> Secrets Manager (D39/F1) --------------------------------
# Same rule as every other hosted seam: secrets are fetched at container start
# from one secret, never carried on the task definition.
SECRET_NAME=a2alab/runtime/faces
SECRET_JSON=$(python3 - <<'PY'
import json, os
keys = [
    "A2ALAB_TOKEN",          # the console's own service token
    "ANTHROPIC_API_KEY",     # the Lab Guide answers in-process
    "OPENAI_API_KEY",
    "SF_CLIENT_ID", "SF_CLIENT_SECRET",
    # The _OBS connected app is a SECOND credential pair (D37/F6, per-caller
    # identity) and was missed on the first run: both landed in plaintext on
    # the task definition, which is exactly what this secret exists to prevent.
    "SF_CLIENT_ID_OBS", "SF_CLIENT_SECRET_OBS",
    "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET",
    "BRIDGE_TOKEN",
    "A2ALAB_FANOUT_MCP_TOKEN",   # bearer for the remote fan-out server (D41)
]
print(json.dumps({k: os.environ[k] for k in keys if os.environ.get(k)}))
PY
)
aws secretsmanager describe-secret --region "$REGION" --secret-id "$SECRET_NAME" >/dev/null 2>&1 \
  && aws secretsmanager put-secret-value --region "$REGION" --secret-id "$SECRET_NAME" \
       --secret-string "$SECRET_JSON" >/dev/null \
  || aws secretsmanager create-secret --region "$REGION" --name "$SECRET_NAME" \
       --secret-string "$SECRET_JSON" >/dev/null
SECRET_ARN=$(aws secretsmanager describe-secret --region "$REGION" \
  --secret-id "$SECRET_NAME" --query 'ARN' --output text)
echo "secret ready: $SECRET_NAME"

# ---- roles -------------------------------------------------------------------
TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

EXEC_ARN=$(aws iam get-role --role-name "$EXEC_ROLE" --query 'Role.Arn' --output text 2>/dev/null) || {
  EXEC_ARN=$(aws iam create-role --role-name "$EXEC_ROLE" \
    --assume-role-policy-document "$TRUST" --query 'Role.Arn' --output text)
  aws iam attach-role-policy --role-name "$EXEC_ROLE" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
  sleep 8
}
# The EXECUTION role pulls the image and resolves secrets at start; the TASK
# role is what the running code uses. Both need Secrets Manager here, for
# different reasons and at different times.
aws iam put-role-policy --role-name "$EXEC_ROLE" --policy-name read-console-secret \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"$SECRET_ARN\"}]}"

TASK_ARN=$(aws iam get-role --role-name "$TASK_ROLE" --query 'Role.Arn' --output text 2>/dev/null) || {
  TASK_ARN=$(aws iam create-role --role-name "$TASK_ROLE" \
    --assume-role-policy-document "$TRUST" --query 'Role.Arn' --output text)
  sleep 8
}

# Aurora through the Data API, both roles: the console READS traces, obs rows
# and briefs, and WRITES hops for runs it fires. Writer secret included because
# a run started from the console records its own trace.
PG_STMTS=""
if [ -n "${A2ALAB_PG_CLUSTER_ARN:-}" ]; then
  PG_STMTS="{\"Effect\":\"Allow\",\"Action\":[\"rds-data:ExecuteStatement\",\"rds-data:BatchExecuteStatement\"],\"Resource\":\"$A2ALAB_PG_CLUSTER_ARN\"},
    {\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"arn:aws:secretsmanager:$REGION:$ACCOUNT:secret:a2alab/*\"},"
fi
# bedrock-agentcore:InvokeAgentRuntime — the Run buttons reach the hosted
# Claude/OpenAI runtimes, which have an IAM data plane and no URL (D26).
aws iam put-role-policy --role-name "$TASK_ROLE" --policy-name console-access \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[
    $PG_STMTS
    {\"Effect\":\"Allow\",\"Action\":\"bedrock-agentcore:InvokeAgentRuntime\",\"Resource\":\"*\"}
  ]}"

# ---- network: join the bridge's VPC, subnets and ALB -------------------------
VPC=$(aws ec2 describe-vpcs --region "$REGION" --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' --output text)
SUBNETS=$(aws ec2 describe-subnets --region "$REGION" --filters Name=vpc-id,Values="$VPC" \
  --query 'Subnets[].SubnetId' --output text | tr '\t' ',')

ALB_ARN=$(aws elbv2 describe-load-balancers --region "$REGION" --names "$BRIDGE_NAME" \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text 2>/dev/null) || {
  echo "no ALB named $BRIDGE_NAME — deploy the bridge first (deploy/bridge/deploy_bridge.sh)"
  exit 1
}
ALB_DNS=$(aws elbv2 describe-load-balancers --region "$REGION" --load-balancer-arns "$ALB_ARN" \
  --query 'LoadBalancers[0].DNSName' --output text)

# The task security group must accept traffic from the ALB's. Reuse the
# bridge's ALB SG as the source rather than opening the port to the world.
ALB_SG=$(aws elbv2 describe-load-balancers --region "$REGION" --load-balancer-arns "$ALB_ARN" \
  --query 'LoadBalancers[0].SecurityGroups[0]' --output text)
sg_id() { aws ec2 describe-security-groups --region "$REGION" \
  --filters Name=group-name,Values="$1" Name=vpc-id,Values="$VPC" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null; }
TASK_SG=$(sg_id "$NAME-task")
if [ "$TASK_SG" = "None" ] || [ -z "$TASK_SG" ]; then
  TASK_SG=$(aws ec2 create-security-group --region "$REGION" --group-name "$NAME-task" \
    --description "console tasks, ALB only" --vpc-id "$VPC" --query 'GroupId' --output text)
  aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$TASK_SG" \
    --protocol tcp --port "$CONTAINER_PORT" --source-group "$ALB_SG" >/dev/null
fi

TG_ARN=$(aws elbv2 describe-target-groups --region "$REGION" --names "$NAME" \
  --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null) || {
  TG_ARN=$(aws elbv2 create-target-group --region "$REGION" --name "$NAME" \
    --protocol HTTP --port "$CONTAINER_PORT" --vpc-id "$VPC" --target-type ip \
    --health-check-path /healthz --health-check-interval-seconds 30 \
    --healthy-threshold-count 2 --unhealthy-threshold-count 3 \
    --query 'TargetGroups[0].TargetGroupArn' --output text)
}

# ---- the listener rule — the one step that touches Path A's load balancer ----
# A RULE, never a change to the default action: unmatched traffic keeps going
# to the bridge, so the worst case for a wrong host pattern is that the console
# is unreachable, not that Salesforce is.
add_rule() {
  local listener=$1
  local existing
  existing=$(aws elbv2 describe-rules --region "$REGION" --listener-arn "$listener" \
    --query "Rules[?Conditions[?Field=='host-header' && contains(Values, '$HOSTNAME_')]].RuleArn | [0]" \
    --output text 2>/dev/null)
  if [ "$existing" != "None" ] && [ -n "$existing" ]; then
    aws elbv2 modify-rule --region "$REGION" --rule-arn "$existing" \
      --actions Type=forward,TargetGroupArn="$TG_ARN" >/dev/null
    echo "  updated rule on $(basename "$listener")"
  else
    aws elbv2 create-rule --region "$REGION" --listener-arn "$listener" \
      --priority "$RULE_PRIORITY" \
      --conditions Field=host-header,Values="$HOSTNAME_" \
      --actions Type=forward,TargetGroupArn="$TG_ARN" >/dev/null
    echo "  created rule on $(basename "$listener")"
  fi
}
for port in 443 80; do
  L=$(aws elbv2 describe-listeners --region "$REGION" --load-balancer-arn "$ALB_ARN" \
    --query "Listeners[?Port==\`$port\`].ListenerArn | [0]" --output text 2>/dev/null)
  [ "$L" = "None" ] || [ -z "$L" ] || add_rule "$L"
done

# ---- task definition + service -----------------------------------------------
aws logs create-log-group --region "$REGION" --log-group-name "/ecs/$NAME" >/dev/null 2>&1 || true

ENV_JSON=$(A2ALAB_RUNTIME_SECRET_ARN="$SECRET_ARN" \
           DEPLOY_REGION="$REGION" python3 - <<'PY'
import json, os, pathlib, re

# Same derivation as the bridge: targets.yaml's ${VAR}s plus what the code
# actually reads, because a client can read os.environ for something that
# appears in no config file (SF_AGENT_ID is the case that taught this).
targets = pathlib.Path("config/targets.yaml").read_text()
body = "\n".join(ln.split("#", 1)[0] for ln in targets.splitlines())
referenced = set(re.findall(r"\$\{([A-Z0-9_]+)\}", body))
for path in pathlib.Path("src").rglob("*.py"):
    for m in re.finditer(r"""os\.environ(?:\.get)?[(\[]\s*["']([A-Z0-9_]{3,})["']""", path.read_text()):
        referenced.add(m.group(1))

SECRETS = {"A2ALAB_TOKEN", "BRIDGE_TOKEN", "SF_CLIENT_ID", "SF_CLIENT_SECRET",
           "SF_CLIENT_ID_OBS", "SF_CLIENT_SECRET_OBS",
           "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET",
           "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "A2ALAB_FANOUT_MCP_TOKEN"}


def is_secret(name):
    """An enumerated list only excludes the secrets someone remembered.

    The first run put SF_CLIENT_ID_OBS, SF_CLIENT_SECRET_OBS and
    A2ALAB_FANOUT_MCP_TOKEN on the task definition in the clear because the
    list predated them. Names are a reliable signal here, so treat any
    SECRET/TOKEN/KEY/PASSWORD variable as sensitive by default — EXCEPT the
    `*_ARN` pointers (A2ALAB_PG_SECRET_ARN, A2ALAB_RUNTIME_SECRET_ARN), which
    name a secret rather than being one and MUST stay in plain env or the
    container cannot find what to fetch.
    """
    if name in SECRETS:
        return True
    if name.endswith("_ARN"):
        return False
    return any(w in name for w in ("SECRET", "TOKEN", "KEY", "PASSWORD"))
# The task has its own identity and region; the laptop's must not ride along.
# AWS_DEFAULT_REGION especially: this machine exports a different region than
# .env sets, and boto3 prefers it — the container then looks for its secret in
# the wrong region and fails with AccessDenied, which reads as a policy bug.
AMBIENT = {"AWS_PROFILE", "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_ACCESS_KEY_ID",
           "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "PATH", "HOME", "PWD"}

env = {k: os.environ[k] for k in sorted(referenced)
       if os.environ.get(k) and not is_secret(k) and k not in AMBIENT}
env["A2ALAB_RUNTIME_SECRET_ARN"] = os.environ["A2ALAB_RUNTIME_SECRET_ARN"]
env["AWS_REGION"] = os.environ["DEPLOY_REGION"]
# Hosted console: no traces/ directory, no .a2alab. Aurora is the only source.
env["A2ALAB_FACES_BASE"] = f"https://{os.environ['FACES_HOSTNAME']}"
env["A2ALAB_TRACE_SINK"] = "postgres"
env["A2ALAB_OBS_STORE"] = "postgres"
# Aurora, set EXPLICITLY — the scan above cannot see these. observability/pg.py
# reads them as os.environ.get(SECRET_ARN_ENV), i.e. through a module constant,
# and the regex only matches a string literal inside the call. The first run
# shipped A2ALAB_PG_CLUSTER_ARN (it appears literally elsewhere) but not
# A2ALAB_PG_SECRET_ARN, so PgClient.configured() was False and /api/traces,
# /api/obs/*, /api/cost-brief and build-telemetry all returned empty with the
# console otherwise healthy. deploy_bridge.sh sets these the same explicit way.
#
# The WRITER secret, matching the bridge and for the same reason: with
# A2ALAB_TRACE_SINK=postgres, PostgresSink INSERTs through PgClient.from_env(),
# which reads A2ALAB_PG_SECRET_ARN. Handing it the lab_reader ARN would leave
# the console reading fine and failing every trace write.
# No GCP federation here, unlike the console: these faces consult Agentforce
# (both shims, and the Claude adapter's ask_agentforce tool). Nothing in them
# calls Agent Engine, so there is no Google identity to present.

# The Managed Agents ids, set EXPLICITLY — the third instance of the D48 blind
# spot. managed_backend.py reads them as os.environ.get(AGENT_ID_ENV), through
# module constants, and the scan above only matches string literals. Locally
# they come from .a2alab/managed.json, a file no container has, so without
# these the Claude faces start healthy and answer every call with "Managed
# Agents backend is not provisioned".
for _var in ("CLAUDE_MANAGED_AGENT_ID", "CLAUDE_MANAGED_ENV_ID"):
    if os.environ.get(_var):
        env[_var] = os.environ[_var]

_cluster = os.environ.get("A2ALAB_PG_CLUSTER_ARN")
_writer = os.environ.get("A2ALAB_PG_WRITER_SECRET_ARN")
if _cluster and _writer:
    env["A2ALAB_PG_CLUSTER_ARN"] = _cluster
    env["A2ALAB_PG_SECRET_ARN"] = _writer
if os.environ.get("A2ALAB_PG_DATABASE"):
    env["A2ALAB_PG_DATABASE"] = os.environ["A2ALAB_PG_DATABASE"]
print(json.dumps([{"name": k, "value": str(v)} for k, v in env.items()]))
PY
)

TASKDEF=$(python3 - "$ENV_JSON" <<PY
import json, sys
print(json.dumps({
    "family": "$NAME",
    "networkMode": "awsvpc",
    "requiresCompatibilities": ["FARGATE"],
    "cpu": "512", "memory": "1024",
    "runtimePlatform": {"cpuArchitecture": "ARM64", "operatingSystemFamily": "LINUX"},
    "executionRoleArn": "$EXEC_ARN",
    "taskRoleArn": "$TASK_ARN",
    "containerDefinitions": [{
        "name": "$NAME",
        "image": "$ECR:latest",
        "essential": True,
        "portMappings": [{"containerPort": $CONTAINER_PORT, "protocol": "tcp"}],
        "environment": json.loads(sys.argv[1]),
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": "/ecs/$NAME",
                "awslogs-region": "$REGION",
                "awslogs-stream-prefix": "ecs",
            },
        },
    }],
}))
PY
)
TD_ARN=$(aws ecs register-task-definition --region "$REGION" --cli-input-json "$TASKDEF" \
  --query 'taskDefinition.taskDefinitionArn' --output text)

NET="awsvpcConfiguration={subnets=[${SUBNETS}],securityGroups=[${TASK_SG}],assignPublicIp=ENABLED}"
if aws ecs describe-services --region "$REGION" --cluster "$CLUSTER" --services "$NAME" \
     --query 'services[0].status' --output text 2>/dev/null | grep -q ACTIVE; then
  aws ecs update-service --region "$REGION" --cluster "$CLUSTER" --service "$NAME" \
    --task-definition "$TD_ARN" --force-new-deployment >/dev/null
  echo "service updated"
else
  aws ecs create-service --region "$REGION" --cluster "$CLUSTER" --service-name "$NAME" \
    --task-definition "$TD_ARN" --desired-count 1 --launch-type FARGATE \
    --network-configuration "$NET" \
    --load-balancers "targetGroupArn=$TG_ARN,containerName=$NAME,containerPort=$CONTAINER_PORT" \
    >/dev/null
  echo "service created"
fi

cat <<EOF

console service deploying on cluster $CLUSTER.

Verify BEFORE touching DNS — the ALB's own hostname with a Host header, which
is exactly how the bridge cutover was checked:

  aws ecs wait services-stable --region $REGION --cluster $CLUSTER --services $NAME
  curl -s -o /dev/null -w '%{http_code}\\n' -H 'Host: $HOSTNAME_' http://$ALB_DNS/healthz

Expect 200. If it is 503 the target group has no healthy task — read the logs:
  aws logs tail /ecs/$NAME --since 10m --region $REGION

Only then point $HOSTNAME_ at the ALB in Cloudflare (proxied, Full strict):
  CNAME $HOSTNAME_ -> $ALB_DNS
Rollback is the same field back to the tunnel. The tunnel keeps running
throughout, so DNS alone decides which origin serves the hostname.
EOF
