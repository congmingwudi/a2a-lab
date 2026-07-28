#!/usr/bin/env bash
# Host the scheduled-brief watcher on ECS Fargate (WS13 item 3).
#
#   deploy/briefs/deploy_briefs.sh
#
# WHAT THIS IS. Anthropic's scheduled deployment fires a brief session on its
# own cron. The session then STALLS awaiting the result of a host-side custom
# tool (`save_account_brief`), because Salesforce delivery happens on our side —
# credentials never enter the managed sandbox (D16/D27). Something has to be
# watching to service it. That something was `python -m briefs --watch` inside
# `scripts/run_local.sh`, i.e. the operator's laptop, and it was the last
# runtime dependency on that machine.
#
# WHY A SERVICE, NOT AN EVENTBRIDGE LAMBDA (which is what WS13 item 3 assumed).
# The watcher's work is a poll loop, and a Lambda would need a zip carrying the
# Anthropic SDK, httpx and the Salesforce client — a third bundle to build and
# keep in step. The faces image ALREADY contains this code and every dependency
# it needs, so this is the same image with a different command, at ~$4/month for
# 0.25 vCPU. No ALB, no target group, no listener rule: it serves nothing and
# needs no inbound path.
#
# NOTHING IS LOST WHILE IT IS DOWN. Sessions fired with no watcher simply idle
# awaiting the tool result and are picked up on the next poll — which is why a
# restart during a deploy is not an incident.
#
# Requires: deploy/faces/deploy_faces.sh run first (this reuses its image).
set -euo pipefail
cd "$(dirname "$0")/../.."
set -a; source .env; set +a
source deploy/aws_preflight.sh

REGION="${AWS_REGION:-us-east-1}"
NAME=a2alab-briefs
CLUSTER=a2alab
IMAGE_FROM=a2alab-faces          # same image, different command
TASK_ROLE=a2alab-briefs-task
EXEC_ROLE=a2alab-briefs-exec

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$IMAGE_FROM"

# ---- credentials -> Secrets Manager (D39/F1) --------------------------------
SECRET_NAME=a2alab/runtime/briefs
SECRET_JSON=$(python3 - <<'PY'
import json, os
keys = [
    "ANTHROPIC_API_KEY",     # attaches to the scheduled session
    "A2ALAB_TOKEN",
    # Salesforce delivery is the whole point: the tool writes an
    # A2ALab_Account_Brief__c record as the lab's connected app.
    "SF_CLIENT_ID", "SF_CLIENT_SECRET",
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
aws iam put-role-policy --role-name "$EXEC_ROLE" --policy-name read-briefs-secret \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"$SECRET_ARN\"}]}"

TASK_ARN=$(aws iam get-role --role-name "$TASK_ROLE" --query 'Role.Arn' --output text 2>/dev/null) || {
  TASK_ARN=$(aws iam create-role --role-name "$TASK_ROLE" \
    --assume-role-policy-document "$TRUST" --query 'Role.Arn' --output text)
  sleep 8
}
# Aurora: the watcher records trace hops for each serviced session, and keeps
# its serviced-session set in lab.lab_state — the thing that stops a restart
# re-delivering briefs that already landed in Salesforce.
PG_STMTS=""
if [ -n "${A2ALAB_PG_CLUSTER_ARN:-}" ]; then
  PG_STMTS="{\"Effect\":\"Allow\",\"Action\":[\"rds-data:ExecuteStatement\",\"rds-data:BatchExecuteStatement\"],\"Resource\":\"$A2ALAB_PG_CLUSTER_ARN\"},
    {\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"arn:aws:secretsmanager:$REGION:$ACCOUNT:secret:a2alab/*\"},"
fi
aws iam put-role-policy --role-name "$TASK_ROLE" --policy-name briefs-access \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[
    $PG_STMTS
    {\"Effect\":\"Allow\",\"Action\":\"logs:CreateLogStream\",\"Resource\":\"*\"}
  ]}"

# ---- network -----------------------------------------------------------------
VPC=$(aws ec2 describe-vpcs --region "$REGION" --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' --output text)
SUBNETS=$(aws ec2 describe-subnets --region "$REGION" --filters Name=vpc-id,Values="$VPC" \
  --query 'Subnets[].SubnetId' --output text | tr '\t' ',')
sg_id() { aws ec2 describe-security-groups --region "$REGION" \
  --filters Name=group-name,Values="$1" Name=vpc-id,Values="$VPC" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null; }
TASK_SG=$(sg_id "$NAME-task")
if [ "$TASK_SG" = "None" ] || [ -z "$TASK_SG" ]; then
  # Egress only, no ingress rules at all: this task calls out and is never
  # called. That is the whole security posture of a watcher.
  TASK_SG=$(aws ec2 create-security-group --region "$REGION" --group-name "$NAME-task" \
    --description "brief watcher, egress only" --vpc-id "$VPC" --query 'GroupId' --output text)
fi

# ---- task definition + service -----------------------------------------------
aws logs create-log-group --region "$REGION" --log-group-name "/ecs/$NAME" >/dev/null 2>&1 || true

ENV_JSON=$(A2ALAB_RUNTIME_SECRET_ARN="$SECRET_ARN" \
           DEPLOY_REGION="$REGION" python3 - <<'PY'
import json, os

# Small and explicit rather than scanned: this task runs ONE module, and the
# scan's constant blind spot (D48) has now cost three deploys.
env = {
    "A2ALAB_RUNTIME_SECRET_ARN": os.environ["A2ALAB_RUNTIME_SECRET_ARN"],
    "AWS_REGION": os.environ["DEPLOY_REGION"],
    "A2ALAB_TRACE_SINK": "postgres",
    "A2ALAB_OBS_STORE": "postgres",
}
# The ids that normally come from .a2alab/brief.json, which no container has.
for src, dst in (("A2ALAB_BRIEF_DEPLOYMENT_ID", "A2ALAB_BRIEF_DEPLOYMENT_ID"),
                 ("A2ALAB_BRIEF_AGENT_ID", "A2ALAB_BRIEF_AGENT_ID"),
                 ("A2ALAB_BRIEF_ENV_ID", "A2ALAB_BRIEF_ENV_ID")):
    if os.environ.get(src):
        env[dst] = os.environ[src]
for var in ("A2ALAB_PG_CLUSTER_ARN", "A2ALAB_BRIEF_POLL_S", "SF_MY_DOMAIN", "SF_AGENT_ID"):
    if os.environ.get(var):
        env[var] = os.environ[var]
if os.environ.get("A2ALAB_PG_WRITER_SECRET_ARN"):
    env["A2ALAB_PG_SECRET_ARN"] = os.environ["A2ALAB_PG_WRITER_SECRET_ARN"]
print(json.dumps([{"name": k, "value": str(v)} for k, v in sorted(env.items())]))
PY
)

TASKDEF=$(python3 - "$ENV_JSON" <<PY
import json, sys
print(json.dumps({
    "family": "$NAME",
    "networkMode": "awsvpc",
    "requiresCompatibilities": ["FARGATE"],
    "cpu": "256", "memory": "512",
    "runtimePlatform": {"cpuArchitecture": "ARM64", "operatingSystemFamily": "LINUX"},
    "executionRoleArn": "$EXEC_ARN",
    "taskRoleArn": "$TASK_ARN",
    "containerDefinitions": [{
        "name": "$NAME",
        "image": "$ECR:latest",
        "essential": True,
        "command": ["sh", "-c", "uv run python -m briefs --watch"],
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
    --network-configuration "$NET" >/dev/null
  echo "service created"
fi

cat <<EOF

brief watcher deploying on cluster $CLUSTER (no load balancer — it serves nothing).

Confirm it is watching:
  aws logs tail /ecs/$NAME --since 5m --region $REGION --follow

Expect a line like: [briefs] watching deployment <id> every 60s
Then stop the local one — 'python -m briefs --watch' in scripts/run_local.sh is
now redundant, and two watchers racing for the same session is the one way to
deliver a brief twice.
EOF
