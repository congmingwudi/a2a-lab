#!/usr/bin/env bash
# Host the Path A bridge on ECS Fargate behind an ALB (WS7 item 7).
#
#   deploy/bridge/deploy_bridge.sh                # build, push, create-or-update
#   deploy/bridge/deploy_bridge.sh --skip-build   # redeploy the current image
#
# WHY NOT API GATEWAY, since every other hosted lab component uses it (D23,
# D28, D41): an HTTP API's integration timeout maxes at 30s and is not
# adjustable — the 29s account quota that IS adjustable governs REST APIs
# only. The bridge's client timeout is 45s (plan/01-architecture.md, Path A
# budget chain: action ~85-90s -> Apex 110s -> bridge 45s), so the gateway
# pattern would silently cut Path A's sync research depth by 15s. An ALB's
# idle timeout is a configurable attribute, so the measured budget survives
# the move off the laptop. The shim keeps API Gateway because its work fits
# (10-19s measured); same cloud, different ceiling, different answer.
#
# Requires: .env populated, an authenticated AWS session (aws sso login
# Zscaler ON), and Docker signed in to the container registry.
#
# TLS/DNS is deliberately NOT automated here — see the note this prints at the
# end. The ALB comes up on HTTP so the whole path can be verified before any
# Salesforce-visible DNS changes.
set -euo pipefail
cd "$(dirname "$0")/../.."
set -a; source .env; set +a
source deploy/aws_preflight.sh

REGION="${AWS_REGION:-us-east-1}"
NAME=a2alab-bridge
CLUSTER=a2alab
TASK_ROLE=a2alab-bridge-task          # must match deploy/bridge/gcp_federation.sh
EXEC_ROLE=a2alab-bridge-exec
CONTAINER_PORT=8100
# Above the bridge's own 45s client timeout with headroom for connection
# setup, and still well under Apex's 110s. The whole point of this host.
ALB_IDLE_TIMEOUT=120
SKIP_BUILD="${1:-}"

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$NAME"

# ---- image ------------------------------------------------------------------
if [ "$SKIP_BUILD" != "--skip-build" ]; then
  aws ecr describe-repositories --repository-names "$NAME" --region "$REGION" >/dev/null 2>&1 \
    || aws ecr create-repository --repository-name "$NAME" --region "$REGION" >/dev/null
  aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com" >/dev/null
  # arm64 (Graviton) — cheaper per vCPU-hour and the same arch the rest of the
  # lab's images already target.
  docker build --platform linux/arm64 -f deploy/bridge/Dockerfile -t "$NAME" .
  docker tag "$NAME:latest" "$ECR:latest"
  docker push "$ECR:latest" >/dev/null
  echo "pushed $ECR:latest"
fi

# ---- credentials -> Secrets Manager (D39/F1) --------------------------------
# The bridge fans out to every platform, so it needs every platform's caller
# credential. None of them ride the task definition; interop.secret_env loads
# them at container start from this one secret.
SECRET_NAME=a2alab/runtime/bridge
SECRET_JSON=$(python3 - <<'PY'
import json, os
keys = [
    "BRIDGE_TOKEN", "A2ALAB_TOKEN",
    "SF_CLIENT_ID", "SF_CLIENT_SECRET",
    # The _OBS connected app is a SECOND credential pair (D37/F6, per-caller
    # identity). It postdates this list, so it rode the task definition in
    # cleartext from the first hosted bridge until D48 — the exact exposure
    # this secret exists to prevent.
    "SF_CLIENT_ID_OBS", "SF_CLIENT_SECRET_OBS",
    "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET",
    "ANTHROPIC_API_KEY",
    "A2ALAB_FANOUT_MCP_TOKEN",   # bearer for the remote fan-out server (D41)
]
print(json.dumps({k: os.environ[k] for k in keys if os.environ.get(k)}))
PY
)
if SECRET_ARN=$(aws secretsmanager describe-secret --region "$REGION" \
      --secret-id "$SECRET_NAME" --query ARN --output text 2>/dev/null); then
  aws secretsmanager put-secret-value --region "$REGION" \
    --secret-id "$SECRET_NAME" --secret-string "$SECRET_JSON" >/dev/null
  echo "updated secret $SECRET_NAME"
else
  SECRET_ARN=$(aws secretsmanager create-secret --region "$REGION" --name "$SECRET_NAME" \
    --description "A2A lab: caller credentials for the hosted Path A bridge (WS7 item 7)" \
    --secret-string "$SECRET_JSON" --query ARN --output text)
  echo "created secret $SECRET_NAME"
fi

# ---- IAM --------------------------------------------------------------------
ecs_trust='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

EXEC_ARN=$(aws iam get-role --role-name "$EXEC_ROLE" --query 'Role.Arn' --output text 2>/dev/null) || {
  EXEC_ARN=$(aws iam create-role --role-name "$EXEC_ROLE" \
    --assume-role-policy-document "$ecs_trust" \
    --description "A2A lab: ECS pulls the bridge image and its secret" \
    --query 'Role.Arn' --output text)
  aws iam attach-role-policy --role-name "$EXEC_ROLE" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
  sleep 10
}

TASK_ARN=$(aws iam get-role --role-name "$TASK_ROLE" --query 'Role.Arn' --output text 2>/dev/null) || {
  TASK_ARN=$(aws iam create-role --role-name "$TASK_ROLE" \
    --assume-role-policy-document "$ecs_trust" \
    --description "A2A lab: the bridge process itself, calling out to every platform" \
    --query 'Role.Arn' --output text)
  sleep 10
}

# The task reads its own secret (secret_env at start).
aws iam put-role-policy --role-name "$TASK_ROLE" --policy-name read-runtime-secret \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"$SECRET_ARN\"}]}"
# ...and so does the execution role, which is a DIFFERENT principal: exec pulls
# the image and starts the container, task is the running process. Granting one
# and not the other fails at a different stage with a different error.
aws iam put-role-policy --role-name "$EXEC_ROLE" --policy-name read-runtime-secret \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"$SECRET_ARN\"}]}"

# Targets the bridge routes to that authorize with AWS IAM rather than a bearer.
AGENTCORE_ARNS=$(python3 - <<'PY'
import json, os
arns = [a for a in (os.environ.get("OPENAI_AGENTCORE_ARN"), os.environ.get("CLAUDE_AGENTCORE_ARN")) if a]
print(json.dumps(sorted({r for a in arns for r in (a, a + "/*")})))
PY
)
if [ "$AGENTCORE_ARNS" != "[]" ]; then
  aws iam put-role-policy --role-name "$TASK_ROLE" --policy-name invoke-agentcore \
    --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"bedrock-agentcore:InvokeAgentRuntime\",\"Resource\":$AGENTCORE_ARNS}]}"
fi
if [ -n "${A2ALAB_PG_CLUSTER_ARN:-}" ] && [ -n "${A2ALAB_PG_WRITER_SECRET_ARN:-}" ]; then
  aws iam put-role-policy --role-name "$TASK_ROLE" --policy-name write-trace-store \
    --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[
      {\"Effect\":\"Allow\",\"Action\":[\"rds-data:ExecuteStatement\",\"rds-data:BatchExecuteStatement\"],\"Resource\":\"$A2ALAB_PG_CLUSTER_ARN\"},
      {\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"$A2ALAB_PG_WRITER_SECRET_ARN\"}]}"
fi
echo "roles: exec=$EXEC_ROLE task=$TASK_ROLE"

# ---- network ----------------------------------------------------------------
VPC=$(aws ec2 describe-vpcs --region "$REGION" --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' --output text)
SUBNETS=$(aws ec2 describe-subnets --region "$REGION" --filters Name=vpc-id,Values="$VPC" \
  --query 'Subnets[?MapPublicIpOnLaunch==`true`].SubnetId' --output text | tr '\t' ',')

sg_id () {
  aws ec2 describe-security-groups --region "$REGION" \
    --filters Name=vpc-id,Values="$VPC" Name=group-name,Values="$1" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null
}
ALB_SG=$(sg_id "$NAME-alb"); [ "$ALB_SG" != "None" ] || ALB_SG=$(aws ec2 create-security-group \
  --region "$REGION" --group-name "$NAME-alb" --vpc-id "$VPC" \
  --description "A2A lab bridge ALB" --query GroupId --output text)
TASK_SG=$(sg_id "$NAME-task"); [ "$TASK_SG" != "None" ] || TASK_SG=$(aws ec2 create-security-group \
  --region "$REGION" --group-name "$NAME-task" --vpc-id "$VPC" \
  --description "A2A lab bridge task" --query GroupId --output text)

# Idempotent: the duplicate-rule error is the normal path on re-run.
for port in 80 443; do
  aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$ALB_SG" \
    --protocol tcp --port $port --cidr 0.0.0.0/0 >/dev/null 2>&1 || true
done
# The task accepts traffic ONLY from the load balancer — never 0.0.0.0/0, or
# the bridge is reachable on a public IP with the ALB bypassed entirely.
aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$TASK_SG" \
  --protocol tcp --port "$CONTAINER_PORT" --source-group "$ALB_SG" >/dev/null 2>&1 || true

# ---- load balancer ----------------------------------------------------------
ALB_ARN=$(aws elbv2 describe-load-balancers --region "$REGION" --names "$NAME" \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text 2>/dev/null) || {
  ALB_ARN=$(aws elbv2 create-load-balancer --region "$REGION" --name "$NAME" \
    --type application --scheme internet-facing \
    --subnets ${SUBNETS//,/ } --security-groups "$ALB_SG" \
    --query 'LoadBalancers[0].LoadBalancerArn' --output text)
  aws elbv2 wait load-balancer-available --region "$REGION" --load-balancer-arns "$ALB_ARN"
}
# THE reason this component is not on API Gateway. Set every run so a manual
# console edit cannot quietly reintroduce the 30s-class ceiling.
aws elbv2 modify-load-balancer-attributes --region "$REGION" --load-balancer-arn "$ALB_ARN" \
  --attributes Key=idle_timeout.timeout_seconds,Value=$ALB_IDLE_TIMEOUT >/dev/null

TG_ARN=$(aws elbv2 describe-target-groups --region "$REGION" --names "$NAME" \
  --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null) || {
  # target-type ip: Fargate tasks have ENIs, not instance ids.
  TG_ARN=$(aws elbv2 create-target-group --region "$REGION" --name "$NAME" \
    --protocol HTTP --port "$CONTAINER_PORT" --vpc-id "$VPC" --target-type ip \
    --health-check-path /healthz --health-check-interval-seconds 30 \
    --healthy-threshold-count 2 --unhealthy-threshold-count 3 \
    --query 'TargetGroups[0].TargetGroupArn' --output text)
}

LISTENER=$(aws elbv2 describe-listeners --region "$REGION" --load-balancer-arn "$ALB_ARN" \
  --query 'Listeners[?Port==`80`].ListenerArn | [0]' --output text 2>/dev/null)
if [ "$LISTENER" = "None" ] || [ -z "$LISTENER" ]; then
  aws elbv2 create-listener --region "$REGION" --load-balancer-arn "$ALB_ARN" \
    --protocol HTTP --port 80 \
    --default-actions Type=forward,TargetGroupArn="$TG_ARN" >/dev/null
fi
ALB_DNS=$(aws elbv2 describe-load-balancers --region "$REGION" --load-balancer-arns "$ALB_ARN" \
  --query 'LoadBalancers[0].DNSName' --output text)

# ---- task definition + service ----------------------------------------------
aws logs create-log-group --region "$REGION" --log-group-name "/ecs/$NAME" >/dev/null 2>&1 || true
aws ecs describe-clusters --region "$REGION" --clusters "$CLUSTER" \
  --query 'clusters[0].status' --output text 2>/dev/null | grep -q ACTIVE \
  || aws ecs create-cluster --region "$REGION" --cluster-name "$CLUSTER" >/dev/null

# Env vars DERIVED, from TWO sources — because one is not enough, learned here.
#
# Deriving only targets.yaml's ${VAR}s (the fan-out server's approach) covers
# every endpoint the registry expands, and still missed SF_AGENT_ID: the
# Agentforce CLIENT reads it straight from os.environ, so it appears nowhere in
# targets.yaml. The route 500'd with "Agentforce is not configured" while every
# endpoint in the manifest was correct.
#
# So also scan the source for what the code itself reads. Mechanical, and it
# cannot drift the way a hand-maintained list does — a new client that reads a
# new var ships it automatically. Over-inclusion is harmless: a var absent from
# .env is simply not set, and secrets are excluded below.
ENV_JSON=$(A2ALAB_RUNTIME_SECRET_ARN="$SECRET_ARN" ECR_IMAGE="$ECR:latest" \
           DEPLOY_REGION="$REGION" python3 - <<'PY'
import json, os, pathlib, re, sys
targets = pathlib.Path("config/targets.yaml").read_text()
body = "\n".join(ln.split("#", 1)[0] for ln in targets.splitlines())
referenced = sorted(set(re.findall(r"\$\{([A-Z0-9_]+)\}", body)))

read_by_code = set()
for path in pathlib.Path("src").rglob("*.py"):
    for match in re.finditer(r"""os\.environ(?:\.get)?[(\[]\s*["']([A-Z0-9_]{3,})["']""",
                             path.read_text()):
        read_by_code.add(match.group(1))

# Secrets ride Secrets Manager, never the task definition (D39/F1). They are
# read via os.environ too, so the scan above would otherwise put them straight
# back into the config it took them out of.
SECRETS = {"BRIDGE_TOKEN", "A2ALAB_TOKEN", "SF_CLIENT_ID", "SF_CLIENT_SECRET",
           "SF_CLIENT_ID_OBS", "SF_CLIENT_SECRET_OBS",
           "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET",
           "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "A2ALAB_FANOUT_MCP_TOKEN"}


def is_secret(name):
    """An enumerated list only excludes the secrets someone remembered.

    SF_CLIENT_ID_OBS, SF_CLIENT_SECRET_OBS and A2ALAB_FANOUT_MCP_TOKEN all
    postdate the list above and rode the task definition in cleartext until
    D48. Names are a reliable signal, so treat any SECRET/TOKEN/KEY/PASSWORD
    variable as sensitive by default — EXCEPT `*_ARN` pointers
    (A2ALAB_PG_SECRET_ARN, A2ALAB_RUNTIME_SECRET_ARN), which NAME a secret
    rather than being one and must stay in plain env or the container cannot
    find what to fetch.
    """
    if name in SECRETS:
        return True
    if name.endswith("_ARN"):
        return False
    return any(w in name for w in ("SECRET", "TOKEN", "KEY", "PASSWORD"))

# The task has its OWN AWS identity and region; the laptop's must never ride
# along. These come from the AMBIENT SHELL, not .env, so the source scan picks
# up values nobody wrote down. AWS_DEFAULT_REGION is the specific landmine:
# this machine exports us-west-2 while .env sets AWS_REGION=us-east-1, and
# boto3 PREFERS AWS_DEFAULT_REGION — so the container looked for its secret in
# the wrong region and failed with AccessDenied, which reads as a policy bug
# and is not one (the IAM simulator says `allowed`). observability/promql.py
# documents the same variable misdirecting two other lab components; this is
# the third. AWS_REGION is re-set explicitly below to the deploy region.
AMBIENT_AWS = {"AWS_DEFAULT_REGION", "AWS_PROFILE", "AWS_ACCESS_KEY_ID",
               "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_DEFAULT_PROFILE"}

keys = [k for k in (referenced + sorted(read_by_code))
        if not is_secret(k) and k not in AMBIENT_AWS]
keys += ["GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION",
         "A2ALAB_RUNTIME_SECRET_ARN", "A2ALAB_MODE"]
env = {k: os.environ[k] for k in dict.fromkeys(keys) if os.environ.get(k)}
# AWS -> GCP federation for the google-adk-a2a target, same mechanism as the
# fan-out Lambda (D41) and the same pool — deploy/bridge/gcp_federation.sh
# binds THIS task role into it.
if os.environ.get("A2ALAB_BRIDGE_GCP_AUDIENCE"):
    env["A2ALAB_GCP_WORKLOAD_AUDIENCE"] = os.environ["A2ALAB_BRIDGE_GCP_AUDIENCE"]
    env["A2ALAB_GCP_IMPERSONATE_SA"] = os.environ["A2ALAB_BRIDGE_GCP_SA"]
# Hops must leave the container; a jsonl-only sink writes into a filesystem
# that disappears with the task.
# Explicit, after the ambient exclusion above: the task's region is the region
# it was deployed into, not whatever the operator's shell happened to say.
env["AWS_REGION"] = os.environ.get("DEPLOY_REGION") or "us-east-1"
env["A2ALAB_TRACE_DIR"] = "/tmp/traces"
env["A2ALAB_TRACE_SINK"] = "jsonl"
cluster, writer = os.environ.get("A2ALAB_PG_CLUSTER_ARN"), os.environ.get("A2ALAB_PG_WRITER_SECRET_ARN")
if cluster and writer:
    env["A2ALAB_TRACE_SINK"] = "jsonl,postgres"
    env["A2ALAB_PG_CLUSTER_ARN"] = cluster
    env["A2ALAB_PG_SECRET_ARN"] = writer
missing = [k for k in referenced if not os.environ.get(k)]
if missing:
    print(f"warning: targets.yaml references unset vars: {', '.join(missing)}", file=sys.stderr)
print(json.dumps([{"name": k, "value": v} for k, v in sorted(env.items())]))
PY
)

TASKDEF=$(python3 - "$ENV_JSON" <<PY
import json, sys
print(json.dumps({
    "family": "$NAME",
    "networkMode": "awsvpc",
    "requiresCompatibilities": ["FARGATE"],
    "runtimePlatform": {"cpuArchitecture": "ARM64", "operatingSystemFamily": "LINUX"},
    "cpu": "512", "memory": "1024",
    "executionRoleArn": "$EXEC_ARN",
    "taskRoleArn": "$TASK_ARN",
    "containerDefinitions": [{
        "name": "$NAME",
        "image": "$ECR:latest",
        "essential": True,
        "portMappings": [{"containerPort": $CONTAINER_PORT, "protocol": "tcp"}],
        "environment": json.loads(sys.argv[1]),
        "logConfiguration": {"logDriver": "awslogs", "options": {
            "awslogs-group": "/ecs/$NAME",
            "awslogs-region": "$REGION",
            "awslogs-stream-prefix": "ecs",
        }},
    }],
}))
PY
)
TD_ARN=$(aws ecs register-task-definition --region "$REGION" --cli-input-json "$TASKDEF" \
  --query 'taskDefinition.taskDefinitionArn' --output text)
echo "task definition: $TD_ARN"

NET="awsvpcConfiguration={subnets=[${SUBNETS}],securityGroups=[${TASK_SG}],assignPublicIp=ENABLED}"
if aws ecs describe-services --region "$REGION" --cluster "$CLUSTER" --services "$NAME" \
     --query 'services[0].status' --output text 2>/dev/null | grep -q ACTIVE; then
  aws ecs update-service --region "$REGION" --cluster "$CLUSTER" --service "$NAME" \
    --task-definition "$TD_ARN" --force-new-deployment >/dev/null
  echo "updated service $NAME"
else
  aws ecs create-service --region "$REGION" --cluster "$CLUSTER" --service-name "$NAME" \
    --task-definition "$TD_ARN" --desired-count 1 --launch-type FARGATE \
    --network-configuration "$NET" \
    --load-balancers "targetGroupArn=$TG_ARN,containerName=$NAME,containerPort=$CONTAINER_PORT" \
    --health-check-grace-period-seconds 60 >/dev/null
  echo "created service $NAME"
fi

python3 - "$ALB_DNS" "$ALB_ARN" <<'PY'
import json, pathlib, sys
p = pathlib.Path(".a2alab/bridge_host.json")
state = json.loads(p.read_text()) if p.exists() else {}
state["alb_dns"], state["alb_arn"] = sys.argv[1], sys.argv[2]
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(state, indent=1))
PY

cat <<EOF

ALB: http://$ALB_DNS  (idle timeout ${ALB_IDLE_TIMEOUT}s — the reason this is not API Gateway)
watch:  aws ecs describe-services --cluster $CLUSTER --services $NAME --region $REGION --query 'services[0].deployments'
logs:   aws logs tail /ecs/$NAME --region $REGION --follow
smoke:  curl -s http://$ALB_DNS/healthz

STILL MANUAL — TLS and DNS, deliberately:
  Salesforce's A2ALab_Bridge named credential points at
  https://bridge-lab.agenticthings.com, today the Cloudflare tunnel to your
  laptop. Cutting Path A over needs a certificate the ALB can serve and a DNS
  change, both of which touch a Salesforce-visible hostname — so they are not
  scripted. Verify everything on the ALB hostname above FIRST, then cut over.
EOF
