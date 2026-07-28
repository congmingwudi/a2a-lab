# Morning steps — 2026-07-28

Everything from the overnight session that needs your hands, in order. Each step
says what to expect and how to back out.

**Nothing here is urgent.** The lab is in a working state: 322 tests pass, the
hosted harvest is green on all six platforms, and the cost sentinel has fired
once successfully. These are the next moves, not repairs.

---

## 1. Delete the CloudFront distribution (2 minutes)

You asked for the CloudFront work to be removed rather than left lingering. The
code, config and docs are gone; the **distribution itself is disabled but still
exists**, because AWS requires a disabled distribution to finish propagating
before it can be deleted.

```sh
set -a; source .env; set +a
aws cloudfront get-distribution --id E1E3KZ4W1DYMBU --region us-east-1 \
  --query 'Distribution.[Status,DistributionConfig.Enabled]' --output text
```

Expect `Deployed  False`. Then:

```sh
ETAG=$(aws cloudfront get-distribution --id E1E3KZ4W1DYMBU --region us-east-1 --query ETag --output text)
aws cloudfront delete-distribution --id E1E3KZ4W1DYMBU --if-match "$ETAG" --region us-east-1
aws cloudfront delete-function --name a2alab-console-gate --region us-east-1 \
  --if-match "$(aws cloudfront describe-function --name a2alab-console-gate --region us-east-1 --query ETag --output text)"
```

If `Status` still says `InProgress`, wait and retry — it takes ~15 minutes.
Leaving it disabled costs nothing, so this is tidiness rather than spend.

---

## 2. Deploy the console to Fargate (WS13 item 1) — the real work

`deploy/console/deploy_console.sh` is **written and never run**. It is modelled
line-for-line on `deploy/bridge/deploy_bridge.sh`, which is proven, but a first
run needs you watching because one step touches the load balancer Salesforce
depends on.

**Before you start:** Zscaler ON (AWS SSO), Docker Desktop signed in, and
`aws sso login` if the token has expired.

```sh
deploy/console/deploy_console.sh
```

**What it does, and the one risky part.** It builds an arm64 image, pushes to a
new ECR repo, creates a target group, and adds a **host-header rule** to the
bridge's existing 443 listener for `console-lab.agenticthings.com`. The bridge
stays the listener's *default action* and carries no rule, so a wrong host
pattern can only make the console unreachable — it cannot break Path A. That
property is why this is safe to try.

**Verify before touching DNS** — the same way the bridge cutover was verified:

```sh
aws ecs wait services-stable --region us-east-1 --cluster a2alab --services a2alab-console
ALB=$(aws elbv2 describe-load-balancers --region us-east-1 --names a2alab-bridge \
  --query 'LoadBalancers[0].DNSName' --output text)
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: console-lab.agenticthings.com' "http://$ALB/healthz"
```

Expect `200`. If it is `503`, the target group has no healthy task:

```sh
aws logs tail /ecs/a2alab-console --since 10m --region us-east-1
```

**Expect at least one thing to be missing on the first run.** The console was
never designed to run in a container, and I found three gaps by inspection
(`/healthz`, the expiry file, the doc trees in the image). Anything that shells
out, reads `.a2alab/`, or assumes a local `traces/` directory will surface here
one at a time. That is the expected shape of this step, not a failure.

**Only then** point DNS at the ALB in Cloudflare — CNAME
`console-lab.agenticthings.com` → the ALB hostname, proxied, Full strict.
Rollback is the same field back to the tunnel. `cloudflared` keeps running
throughout (you asked to keep it for local dev), so DNS alone decides which
origin serves the hostname.

---

## 3. Optional — resume the cost sentinel's weekly schedule

It is provisioned and **paused**. The first scheduled firing would be Monday
2026-08-03 07:00 America/New_York, which is the first date with two real weeks
of telemetry behind it.

```sh
uv run python scripts/cost_sentinel.py resume
```

Each firing bills a real session. Leave it paused if you would rather fire it by
hand.

---

## 4. Optional — port the session-id fix to the observability analyst

`cost_sentinel.py reconcile` links each cost brief to the deployment run that
produced it (the agent cannot know its own session id, so it is a join after the
fact). The **observability analyst's brief from 2026-07-18 is still unlinked** —
the same fix ports to `scripts/obs_analysis.py` unchanged. Not done, because you
did not ask for that one.

---

## What ran unattended, and what to distrust

**Verified end to end:**

- The `kind` column, the coding-telemetry PromQL grant, and the harvest bundle —
  all six platforms now report `ok` hosted (`coding`: 4 tool-days, $369.96).
- The cost sentinel's first brief, checked figure by figure against
  `lab.obs_sessions`. Every number matches.
- `lab.lab_state` — 14 credentials round-tripped through Aurora, and
  `/api/expiry` now reads the store before the file.
- WS11's A2A submit/poll, against live platforms and a deterministic slow
  adapter. 322 tests pass, ruff clean.

**Written but NOT executed — treat as drafts:**

- `deploy/console/deploy_console.sh` and its Dockerfile (step 2 above).
- `src/fanout_mcp/tasks.py` — the table is migrated and the logic is unit-tested
  against a fake client, but no worker has ever run in Lambda. The submit/check
  MCP tools are **not registered yet**, so nothing in the deployed fan-out
  server changed.

**The honesty sweeps found 9 things and I fixed all of them** — 7 insight
entries, 1 matrix paragraph, 1 targets.yaml comment. All were the record
lagging the work rather than overclaiming, and **two were falsified by work done
in the same 24 hours** (the A2A async insight, by WS11). Three insights are now
`review: required` and waiting for you in the console: `a2a-async-at-heart`,
`managed-vs-self-hosted`, `fabricated-attribution`. Details in
plan/03-results.md.

**One test is weaker than it looks:** `test_sse_keepalive_fits_inside_common_idle_timeouts`
asserts the constant, not the emission. Two attempts to read the live stream
hung the suite (the tail is an infinite generator), so the keepalive itself is
verified by inspection. If the live tail misbehaves after the console moves
behind the ALB, start there.
