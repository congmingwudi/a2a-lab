#!/usr/bin/env bash
# Sourced by every AWS deploy script. Proves the session is pointed at the
# lab's runtime account BEFORE anything is created.
#
#   set -a; source .env; set +a
#   source deploy/aws_preflight.sh
#   # $AWS_ACCOUNT and $AWS_REGION are now set and verified
#
# WHY THIS EXISTS. The operator has more than one AWS account configured, and
# the lab's runtime account is not the default profile. An expired session, a
# forgotten `AWS_PROFILE`, or an ambient `AWS_DEFAULT_REGION` exported by the
# shell are all silent: the CLI happily authenticates as somebody else and the
# deploy creates real, billable, wrongly-placed infrastructure in a personal
# account. That failure is not loud until you go looking for the resources.
#
# The expected account is NOT in this repo. It lives in `.env`
# (`A2ALAB_AWS_ACCOUNT_ID`), which is gitignored, because the account number
# identifies whose cloud this is — see .a2alab/accounts.md. If the variable is
# unset the deploy still runs, but it prints what it is about to deploy into
# and gives you a chance to stop: an unconfigured guard must not become a
# reason to skip the check entirely.

_pf_die() { echo "aws-preflight: $*" >&2; exit 1; }

# Region: prefer the lab's own setting over whatever the shell exports.
# AWS_DEFAULT_REGION has now misdirected three components (Secrets Manager
# 2026-07-25, PromQL 2026-07-26, the bridge task 2026-07-26) because boto3
# prefers it over AWS_REGION. Pin both so nothing downstream can disagree.
AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"

AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) \
  || _pf_die "no usable AWS session. Run 'aws sso login' (Zscaler ON) and retry."
export AWS_ACCOUNT

_pf_arn=$(aws sts get-caller-identity --query Arn --output text 2>/dev/null || echo "?")

if [ -n "${A2ALAB_AWS_ACCOUNT_ID:-}" ] && [ "$AWS_ACCOUNT" != "$A2ALAB_AWS_ACCOUNT_ID" ]; then
  _pf_die "WRONG ACCOUNT — refusing to deploy.
    expected : $A2ALAB_AWS_ACCOUNT_ID   (A2ALAB_AWS_ACCOUNT_ID in .env)
    session  : $AWS_ACCOUNT
    identity : $_pf_arn
  Set AWS_PROFILE to the lab's profile and run 'aws sso login' for it."
fi

if [ -z "${A2ALAB_AWS_ACCOUNT_ID:-}" ]; then
  echo "aws-preflight: A2ALAB_AWS_ACCOUNT_ID not set — deploying into account" \
       "$AWS_ACCOUNT ($_pf_arn) in $AWS_REGION. Set it in .env to make this a hard check." >&2
else
  echo "aws-preflight: account $AWS_ACCOUNT / $AWS_REGION verified" >&2
fi
