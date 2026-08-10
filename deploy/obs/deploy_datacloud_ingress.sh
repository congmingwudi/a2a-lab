#!/usr/bin/env bash
# WS19/D69 — open the ONE ingress path the hosted obs store deliberately never
# had: a scoped, TLS-only 5432 rule on the Aurora cluster's security group for
# the Salesforce Data Cloud Zero Copy connector, and nothing wider.
#
#   deploy/obs/deploy_datacloud_ingress.sh            # apply the 5432 allowlist
#   deploy/obs/deploy_datacloud_ingress.sh --tls      # + enforce server-side TLS
#                                                      #   (custom param group,
#                                                      #   ONE-TIME instance reboot)
#   deploy/obs/deploy_datacloud_ingress.sh --verify   # print the pinned CIDRs +
#                                                      #   provenance for a manual
#                                                      #   re-check against the article
#   deploy/obs/deploy_datacloud_ingress.sh --revoke   # remove the 5432 rules
#
# WHY THIS IS NOT LIKE THE OTHER DEPLOY SCRIPTS. Every hosted lab component
# reaches Aurora over the RDS Data API (IAM-authed HTTPS), so the cluster's 5432
# ingress is closed to all but the lab host (pg.py said "stays closed to them
# entirely"). Zero Copy federation cannot use the Data API — the connector logs
# in with user/password to the cluster endpoint over 5432 from Salesforce's
# Data 360 egress IPs. So this is the successor to that posture note, and it is
# the reason D69 is an ADR: it changes the store's network exposure. The MCP
# server is NOT in the allowlist despite the plan's shorthand — it too uses the
# Data API, so 5432 opens to the Data Cloud CIDRs alone.
#
# WHICH SALESFORCE IP SOURCE (the WS19 correction, 2026-08-08, D70). NOT
# ip-ranges.salesforce.com — that is the Salesforce app-fabric range
# (155.226.152.0/23) and the external-database connector does NOT egress from it
# (item 2 pinned it and Test Connection failed: "could not connect", every other
# layer fine). The connector egresses from the AWS-native Hyperforce NAT IPs
# published in the "IP Addresses Used by Data 360 Services" help article — proven
# by VPC flow logs, and confirmed for this tenant's eu-central-1 probe egressing
# from 3.64.2.81 / 18.198.9.100 (both ACCEPTed once pinned).
# config/salesforce_ip_ranges.yaml pins that article's /32 set. The article has
# no JSON manifest, so --verify cannot auto-diff — see below.
#
# WHICH REGION (the D69/D70 lesson). The allowlist is the DATA CLOUD TENANT's
# region, read from A2ALAB_DATACLOUD_REGION (its home-org instance name), NOT the
# core org's instance and NOT where the cluster lives. This lab's tenant is in
# eu-central-1 (co-located with the EU55 org), federating cross-region to the
# us-east-1 cluster. Confirm which org/tenant you are pointed at before pinning —
# a first capture against a different org's ca-central-1 tenant cost real time
# (D70). The CIDRs are pinned in config/salesforce_ip_ranges.yaml with article
# provenance.
set -euo pipefail
cd "$(dirname "$0")/../.."
set -a; source .env; set +a
source deploy/aws_preflight.sh

MODE="${1:-apply}"
REGION="${AWS_REGION:-us-east-1}"                       # where the CLUSTER lives
CLUSTER_ID="${A2ALAB_PG_CLUSTER_ID:-a2alab-obs}"
DC_REGION="${A2ALAB_DATACLOUD_REGION:?set A2ALAB_DATACLOUD_REGION in .env — the Data Cloud tenant region (D69)}"
RANGES_FILE="config/salesforce_ip_ranges.yaml"
SG_NAME="a2alab-aurora-sg"
PORT=5432

# The pinned IPv4 CIDRs for the tenant region, and the manifest they came from.
# One Python read so a malformed file or an unknown region fails loudly here,
# not as an empty allowlist that silently lets nobody through.
read_pins() {
  A2ALAB_DC_REGION="$DC_REGION" A2ALAB_RANGES_FILE="$RANGES_FILE" python3 - <<'PY'
import os, sys, yaml
region = os.environ["A2ALAB_DC_REGION"]
doc = yaml.safe_load(open(os.environ["A2ALAB_RANGES_FILE"]))
regions = doc.get("regions") or {}
if region not in regions:
    sys.exit(f"{region} not in {os.environ['A2ALAB_RANGES_FILE']} (regions: {', '.join(regions)})")
cidrs = regions[region].get("ipv4") or []
if not cidrs:
    sys.exit(f"no ipv4 CIDRs pinned for {region}")
# CIDRs on the first line, the source url + fetched date on the second — the
# caller reads two lines.
print(" ".join(cidrs))
print(f"{doc.get('source_url','')} fetched={doc.get('fetched','')}")
PY
}

PINS_OUT="$(read_pins)"
CIDRS="$(echo "$PINS_OUT" | sed -n '1p')"
PROVENANCE="$(echo "$PINS_OUT" | sed -n '2p')"

SG_ID=$(aws ec2 describe-security-groups --region "$REGION" \
  --filters "Name=group-name,Values=$SG_NAME" \
  --query 'SecurityGroups[0].GroupId' --output text)
[ "$SG_ID" != "None" ] && [ -n "$SG_ID" ] \
  || { echo "security group $SG_NAME not found in $REGION" >&2; exit 1; }

# --verify: print the pinned CIDRs + provenance and cross-check them against what
# is actually on the SG. The Data 360 IP source is a help ARTICLE, not a JSON
# manifest (see the header), so there is nothing to auto-diff the pins against —
# drift can only be caught by re-reading the article by hand. So --verify proves
# the two things a script CAN prove: (1) the pins parse, and (2) every pinned CIDR
# is actually authorized on the SG (a rule the connector needs but that is missing
# is the failure that started WS19). It exits non-zero if any pin is not applied.
if [[ "$MODE" == "--verify" ]]; then
  echo "pinned ($DC_REGION): $CIDRS"
  echo "provenance: $PROVENANCE"
  echo "source is a help article, not a JSON manifest — re-check ranges by hand:"
  echo "  $(echo "$PROVENANCE" | awk '{print $1}')"
  echo
  SG_ID=$(aws ec2 describe-security-groups --region "$REGION" \
    --filters "Name=group-name,Values=$SG_NAME" \
    --query 'SecurityGroups[0].GroupId' --output text)
  APPLIED="$(aws ec2 describe-security-groups --region "$REGION" --group-ids "$SG_ID" \
    --query "SecurityGroups[0].IpPermissions[?FromPort==\`$PORT\`].IpRanges[].CidrIp" \
    --output text | tr '\t' '\n' | sort)"
  MISSING=""
  for cidr in $CIDRS; do
    echo "$APPLIED" | grep -qx "$cidr" || MISSING="$MISSING $cidr"
  done
  if [[ -z "$MISSING" ]]; then
    echo "OK — all ${DC_REGION} pins are authorized on $SG_NAME:$PORT"
    exit 0
  fi
  echo "MISSING from $SG_NAME:$PORT —$MISSING" >&2
  echo "run this script with no args to apply the pinned set." >&2
  exit 2
fi

# --revoke: take the path back down. Best-effort per CIDR (a rule already gone
# is not an error), so this is safe to re-run.
if [[ "$MODE" == "--revoke" ]]; then
  for cidr in $CIDRS; do
    aws ec2 revoke-security-group-ingress --region "$REGION" --group-id "$SG_ID" \
      --protocol tcp --port "$PORT" --cidr "$cidr" >/dev/null 2>&1 \
      && echo "revoked $cidr -> $SG_NAME:$PORT" \
      || echo "no rule for $cidr (already absent)"
  done
  exit 0
fi

# ---- apply: the scoped 5432 rule -------------------------------------------
# One ingress rule per CIDR, tagged with a description that names WHY it exists
# and its provenance, so an auditor reading the SG sees the reason without this
# script. authorize is idempotent — an existing identical rule returns
# InvalidPermission.Duplicate, which we treat as success.
for cidr in $CIDRS; do
  # AWS SG rule descriptions forbid ? and & (which the article URL in $PROVENANCE
  # carries), so name the source without the URL. The full URL lives in the
  # config file's provenance and in --verify output.
  DESC="Salesforce Data 360 egress (D69/WS19; $DC_REGION; ${PROVENANCE##* })"
  if aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG_ID" \
      --ip-permissions "IpProtocol=tcp,FromPort=$PORT,ToPort=$PORT,IpRanges=[{CidrIp=$cidr,Description=\"$DESC\"}]" \
      >/dev/null 2>&1; then
    echo "authorized $cidr -> $SG_NAME:$PORT"
  else
    # Duplicate is the expected re-run case; anything else is a real failure.
    if aws ec2 describe-security-groups --region "$REGION" --group-ids "$SG_ID" \
        --query "SecurityGroups[0].IpPermissions[?FromPort==\`$PORT\`].IpRanges[].CidrIp" \
        --output text | tr '\t' '\n' | grep -qx "$cidr"; then
      echo "already authorized $cidr -> $SG_NAME:$PORT"
    else
      echo "FAILED to authorize $cidr -> $SG_NAME:$PORT" >&2; exit 1
    fi
  fi
done

echo
echo "5432 ingress for $DC_REGION Data Cloud is scoped on $SG_NAME ($SG_ID):"
aws ec2 describe-security-groups --region "$REGION" --group-ids "$SG_ID" \
  --query "SecurityGroups[0].IpPermissions[?FromPort==\`$PORT\`].IpRanges" --output json

# ---- --tls: enforce server-side TLS ----------------------------------------
# The connector's schema has no TLS toggle (user/password only), so "TLS-only"
# has to be enforced by the SERVER: rds.force_ssl=1. The cluster is on the
# SHARED default.aurora-postgresql16 parameter group, which cannot be edited, so
# enforcing TLS means creating a custom cluster parameter group, setting the
# flag, associating it, and rebooting the instance once. That reboot is why this
# is a deliberate, separate flag and not part of the default apply — opening the
# allowlist is instant and safe; the reboot is a maintenance action the operator
# schedules. rds.force_ssl is 'dynamic', but the parameter-group ASSOCIATION
# needs a reboot to take effect (pending-reboot).
if [[ "$MODE" == "--tls" ]]; then
  FAMILY="aurora-postgresql16"
  PG_NAME="a2alab-obs-force-ssl"
  echo
  aws rds describe-db-cluster-parameter-groups --region "$REGION" \
    --db-cluster-parameter-group-name "$PG_NAME" >/dev/null 2>&1 \
    || aws rds create-db-cluster-parameter-group --region "$REGION" \
        --db-cluster-parameter-group-name "$PG_NAME" \
        --db-parameter-group-family "$FAMILY" \
        --description "a2alab obs: rds.force_ssl=1 for the Data Cloud 5432 path (D69)" \
        --query 'DBClusterParameterGroup.DBClusterParameterGroupName' --output text
  aws rds modify-db-cluster-parameter-group --region "$REGION" \
    --db-cluster-parameter-group-name "$PG_NAME" \
    --parameters "ParameterName=rds.force_ssl,ParameterValue=1,ApplyMethod=immediate" \
    --query 'DBClusterParameterGroupName' --output text
  aws rds modify-db-cluster --region "$REGION" \
    --db-cluster-identifier "$CLUSTER_ID" \
    --db-cluster-parameter-group-name "$PG_NAME" \
    --apply-immediately \
    --query 'DBCluster.[DBClusterIdentifier,DBClusterParameterGroup]' --output text
  # The writer instance carries the cluster's ID with a numeric suffix
  # (a2alab-obs -> a2alab-obs-1); resolve it rather than guessing the suffix.
  INSTANCE_ID=$(aws rds describe-db-clusters --region "$REGION" \
    --db-cluster-identifier "$CLUSTER_ID" \
    --query 'DBClusters[0].DBClusterMembers[?IsClusterWriter==`true`].DBInstanceIdentifier | [0]' \
    --output text)
  echo "associated $PG_NAME (rds.force_ssl=1). REBOOT the instance to apply:"
  echo "  aws rds reboot-db-instance --region $REGION --db-instance-identifier $INSTANCE_ID"
  echo "verify after reboot: SHOW rds.force_ssl; (expect 1) — then non-TLS logins are refused."
fi
