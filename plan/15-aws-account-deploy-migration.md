# AWS account migration — bill of materials, cost model, and cheaper shapes

**Status: exploratory, not scheduled.** Written 2026-08-17 to answer one
question ahead of time: *if the lab's AWS estate had to leave the lab's current
runtime account (D21) and be redeployed into a personal account that the
operator pays for, what would that take, what would it cost, and is there a
cheaper way to host the same experiments?* Nothing here is a decision; when it
becomes one it gets an ADR in `plan/00-decisions.md`.

The trigger is practical rather than architectural: the lab's runtime account
is reachable from exactly one machine, and everything in the AWS column —
deploys, the `.env` pull (D39/D43), harvests, the preflight guard — is gated on
that session. A second machine (see `build-notes/claude/09-secrets-and-environment-identity.md`)
can mirror every *file* and none of the *runtime*.

**Scope.** Only the AWS column moves. GCP (Agent Engine, the AWS→GCP pool),
Azure (Foundry), Salesforce (the production org), Anthropic (Managed Agents),
and Cloudflare (DNS, origin cert) are unchanged by this — they are re-*pointed*,
not re-hosted, and their bills are untouched. That boundary is what keeps the
job to days rather than weeks.

Sources: `plan/09-deployment-map.md` (L1 hosting shapes, L5.7 async inventory,
L6 code→deployment table — the inventory below is that table read for cost),
`plan/04-runbooks.md` §8 (Aurora provisioning), the `deploy/*` scripts (task
sizes, networking, memory), D21, D23, D26, D28, D39, D41, D43, D51, D52, D66.

---

## 1. What is in the account today — the inventory, sized

Everything is `us-east-1`, in the **default VPC**, public subnets,
`assignPublicIp=ENABLED`, no NAT gateway (`deploy/bridge/deploy_bridge.sh`
resolves `isDefault=true`). That last fact matters: NAT is the single most
common surprise on a personal AWS bill and this estate does not carry one.

| # | Resource | Shape / size (from the deploy script) | Deployed by | Runs |
|---|---|---|---|---|
| 1 | ECS cluster `a2alab` | Fargate, no EC2 | every `deploy/*/deploy_*.sh` | — |
| 2 | ECS service `a2alab-bridge` | Fargate **ARM64 0.5 vCPU / 1 GB**, 1 task | `deploy/bridge/deploy_bridge.sh` | 24×7 |
| 3 | ECS service `a2alab-console` | Fargate ARM64 0.5 vCPU / 1 GB, 1 task | `deploy/console/deploy_console.sh` | 24×7 |
| 4 | ECS service `a2alab-faces` (14 protocol faces, one process, D51) | Fargate ARM64 0.5 vCPU / 1 GB, 1 task | `deploy/faces/deploy_faces.sh` | 24×7 |
| 5 | ECS service `a2alab-briefs` (brief watcher poll loop, D52) | Fargate ARM64 **0.25 vCPU / 0.5 GB**, 1 task, no ALB | `deploy/briefs/deploy_briefs.sh` | 24×7 |
| 6 | ALB `a2alab-bridge` + 3 target groups + :443 listener (bridge default, console rule 20, faces rule 30), `idle_timeout=120` | 1 ALB | `deploy_bridge.sh` (+ rules from console/faces) | 24×7 |
| 7 | ACM certificate | **imported** Cloudflare origin cert `*.agenticthings.com` (`.a2alab/cloudflare/`) | `deploy_bridge.sh` | free |
| 8 | AgentCore runtimes `a2alab_claude`, `a2alab_openai`, `a2alab_strands` | container runtimes, `networkMode PUBLIC`, IAM data plane (D26/D66) | `deploy/agentcore/deploy.sh <name>` | on demand |
| 9 | Lambda `a2alab-af-shim` + HTTP API | 1024 MB, 29s timeout (D28) | `deploy/shim/build_zip.sh` + `deploy_shim.sh` | on demand |
| 10 | Lambda `a2alab-fanout-mcp` + HTTP API | 1024 MB (D41) | `deploy/fanout/build_zip.sh` + `deploy_fanout.sh` | on demand |
| 11 | Lambda `a2alab-obs-harvest` + EventBridge **Scheduler** `rate(6 hours)` | reads 8 sources → Aurora (D23) | `deploy/obs/build_zips.sh` + `deploy_harvest.sh` | 4×/day |
| 12 | Lambda `a2alab-obs-mcp` + HTTP API | SQL reads over Aurora, MCP front for the analyst/sentinel | `deploy/obs/expose_mcp.sh` | on demand |
| 13 | Aurora PostgreSQL **Serverless v2** `a2alab-obs`, engine 16.x, **min ACU 0** (scale-to-zero), Data API on, one instance `a2alab-obs-1`, publicly accessible + SG `a2alab-aurora-sg` | store for traces, obs tables, usage events, briefs; the **Zero-Copy** source for Data 360 (WS19/D69–D71) — 5432 open to the Data Cloud tenant CIDRs only, TLS forced | `plan/04-runbooks.md` §8 (hand-provisioned), `scripts/pg_migrate.py`, `deploy/obs/deploy_datacloud_ingress.sh` | idle-paused |
| 14 | Secrets Manager — **14 secrets**: `a2alab/runtime/{bridge,console,faces,briefs,claude,openai,strands,shim,fanout-mcp}`, `a2alab/obs/harvest`, `a2alab/obs/{writer,reader}` + the RDS-managed master, `a2alab/telemetry/{cw-metrics-api-key,cw-logs-api-key}`, `a2alab/env/dev` (= `.env`, D43) | one secret per seam (D39) | each deploy script; `scripts/env_sync.py push` | — |
| 15 | ECR repositories | ~6 images (bridge, console, faces, claude, openai, strands), multi-hundred-MB Python images | each deploy | storage |
| 16 | CloudWatch | log groups `/ecs/*`, `/aws/lambda/*`, `/aws/bedrock-agentcore/runtimes/*`; the **coding-agents OTLP** metrics endpoint + log group `/a2alab/coding-agents/otlp` (WS9/WS16) | `scripts/setup_cw_*_otlp.py` | continuous |
| 17 | IAM | task/exec roles ×4 pairs, `a2alab-agentcore` runtime role, Lambda roles, the **GCP→AWS federation role** (`deploy/adk/provision_aws_federation.py`), the `logs.amazonaws.com` service credential (WS16) | scripts | free |
| 18 | Aurora SG ingress for Data 360 (`config/salesforce_ip_ranges.yaml`, 65 lines of `/32`s) | rules on `a2alab-aurora-sg` | `deploy_datacloud_ingress.sh` | free |
| — | *not in this account:* the operator's `aws-logging-service` (us-west-2, D62) the console's `/api/track` forwards to | external edge — **confirm which account holds it** before assuming it moves or stays | — | — |

Two things the table makes visible that the deployment map does not say
outright: **four Fargate tasks and one ALB run 24×7**, and everything else is
either consumption-billed or asleep. That is the whole cost story.

---

## 2. Like-for-like monthly cost, modelled

Model, not an invoice. **us-east-1 list prices as of writing, rounded; verify
against the pricing pages before committing.** No free tier assumed (see §2.1).
Usage assumptions are the lab's actual pattern: a handful of demo runs a week,
the 6-hourly harvest, the console open an hour or two a day.

| Line | Basis | Est. $/month |
|---|---|---|
| Fargate — bridge, console, faces (3 × 0.5 vCPU/1 GB ARM64, 730 h) | ARM64 ≈ $0.0324/vCPU-h + $0.0036/GB-h → ≈ $14.4 each | **43** |
| Fargate — briefs watcher (0.25 vCPU/0.5 GB ARM64) | same rates | **7** |
| ALB (1, 730 h) + LCUs | $0.0225/h ≈ $16.4; LCU usage negligible at lab traffic | **17** |
| Aurora Serverless v2, min ACU 0 | $0.12/ACU-h **only while awake** (harvest wakes it 4×/day for a few minutes; console/Zero-Copy sessions ~1–2 h/day at ~0.5–1 ACU) + storage $0.10/GB-mo (~5 GB) + I/O + Data API $0.35/M req | **5–15** |
| AgentCore runtimes ×3 | consumption-billed per active second (list ≈ $0.09/vCPU-h + $0.009/GB-h; **no idle charge** — verify current AgentCore pricing) | **1–5** |
| Bedrock model calls (Strands runtime uses Haiku 4.5 via the runtime role, D66) | ~$1/M in, $5/M out; lab volumes are thousands of tokens per run | **<1** |
| Lambdas ×4 (shim, fan-out, harvest, obs-mcp) + 3 HTTP APIs + EventBridge Scheduler | $0.20/M requests + GB-s; $1/M API requests; Scheduler has a large free allowance | **<1** |
| Secrets Manager — 14 secrets | $0.40/secret-mo + $0.05/10k API calls | **6** |
| ECR — ~6 repos, several tagged images each | $0.10/GB-mo; 3–10 GB depending on how many old tags are kept | **1–2** |
| CloudWatch — logs ingest/storage + the coding-agent **custom metrics** | $0.50/GB ingest; custom metrics $0.30/metric-mo **prorated by the hour they receive data**, so per-session series are cheap; the one line to watch if dimension cardinality grows | **2–8** |
| Data transfer out | first 100 GB/mo free, then $0.09/GB; lab payloads are KBs | **<1** |
| **Total, like-for-like** | | **≈ $85–110 / month** |

**Where the money is.** Fargate (~$50) + the ALB (~$17) ≈ **two-thirds of the
bill**, and they are there for four processes that are idle most of the day.
Everything the lab is *about* — the agent runtimes, the shim, the fan-out, the
harvest, the store — costs a few dollars, because it is either
consumption-billed or asleep. Whatever cheaper shape is chosen, it is a
different answer to "where do the four ASGI processes run", nothing else.

### 2.1 Free tier — a caveat that depends on when the personal account was opened

Accounts created **before 2025-07-15** carry the classic 12-month free tier,
which would zero the ALB (750 h/mo for a year) and part of Lambda/API Gateway;
Fargate and Aurora Serverless v2 were never in it. Accounts opened **after**
that date are on the newer credits-based free plan instead. The personal
account referenced by `~/.aws/config` predates the lab, so check its creation
date before counting on either. Do not model around free tier; treat it as a
first-year discount if it applies.

### 2.2 Get the real number before deciding

This model has no measured spend behind it — the cost sentinel (WS12/D44)
reads *build telemetry*, not the AWS bill. One command from the machine that
can still reach the current account gives the truth:

```sh
aws ce get-cost-and-usage --time-period Start=2026-07-01,End=2026-08-01 \
  --granularity MONTHLY --metrics UnblendedCost --group-by Type=DIMENSION,Key=SERVICE \
  --query 'ResultsByTime[0].Groups[].[Keys[0],Metrics.UnblendedCost.Amount]' --output table
```

If it disagrees with the table above by more than ~20%, the table is wrong and
this doc should be corrected before anything is built from it.

---

## 3. Bill of materials — what a like-for-like redeploy actually takes

The good news is structural: **every AWS component is created by a script in
`deploy/` that sources `deploy/aws_preflight.sh`**, and every account-bound
identifier lives in `.env` (`A2ALAB_AWS_ACCOUNT_ID`, the ARNs). Redeploying is
mostly *changing values and re-running the same commands* — the guard that
refuses a wrong-account deploy is the same guard that makes a right-account
redeploy safe. The work is in the seams that reach *out* of AWS.

### 3.1 Prerequisites (one-time, personal account)

| Step | What | Notes |
|---|---|---|
| P1 | An identity to deploy as | The lab assumes an SSO profile (`AWS_PROFILE`, D39). A personal account can run **IAM Identity Center** for the same shape (free) or a single IAM user with MFA + short-lived `sts get-session-token`. Either satisfies `aws_preflight.sh`; the SSO shape keeps `env_sync.py`/runbooks unchanged. |
| P2 | Set `A2ALAB_AWS_ACCOUNT_ID` to the new account in `.env` | The preflight now *refuses the old account*, which is exactly the protection wanted while both exist. |
| P3 | Region `us-east-1`, default VPC present | The scripts resolve it; nothing to build. Pin `AWS_REGION`/`AWS_DEFAULT_REGION` (the preflight does). |
| P4 | Bedrock model access enabled for the models the Strands runtime uses (Haiku 4.5) | Console toggle in a new account; AgentCore is regional GA in us-east-1. |
| P5 | Import the Cloudflare origin cert into the new account's ACM | Free; the cert + key are in `.a2alab/cloudflare/` (chezmoi-tracked, D45). `deploy_bridge.sh` reads `acm_arn.txt` → regenerate. |
| P6 | Budgets + alerts | Two AWS Budgets are free. Set a monthly alert at 1.5× the modelled figure *before* the first deploy. |

### 3.2 Order of operations, and what changes per component

Order follows dependencies: store and secrets first, then things that need
ARNs, then the ALB tier, then the outward re-pointing.

| # | Component | Command | What changes vs. today | Effort |
|---|---|---|---|---|
| 1 | Aurora `a2alab-obs` | hand-provision per `plan/04-runbooks.md` §8, then `scripts/pg_migrate.py` (DDL as owner), then **data**: `pg_dump` from the old cluster (Data API cannot dump; use `psql` over the 5432 path that WS19 opened, or `scripts/pg_backfill.py` from a local `traces/lab.db` snapshot for a rows-only copy) | new cluster/secret ARNs → `A2ALAB_PG_*_ARN`; `deploy_datacloud_ingress.sh` re-run for the Data 360 CIDRs; the **Data 360 Zero-Copy connector re-targeted in Setup UI** (D70 — the one irreducible UI step) | ½ day |
| 2 | Secrets Manager | each deploy script recreates its `a2alab/runtime/*`; `scripts/env_sync.py push` recreates `a2alab/env/dev`; `setup_cw_*_otlp.py --apply` recreates the telemetry credentials | 14 secrets, values unchanged except ARNs | (inside each step) |
| 3 | AgentCore runtimes ×3 | `deploy/agentcore/deploy.sh claude\|openai\|strands` | new runtime ARNs → `CLAUDE_/OPENAI_/STRANDS_AGENTCORE_ARN`, new `AGENTCORE_ROLE_ARN`; ECR repos created on first push | ½ day (three image builds) |
| 4 | Lambdas + HTTP APIs | `deploy/shim/*`, `deploy/fanout/*`, `deploy/obs/build_zips.sh` + `deploy_harvest.sh` + `expose_mcp.sh` (with `--code`, D46) | **new `execute-api` URLs** for shim, fan-out MCP, obs MCP — these are the raw AWS URLs the outside world holds (see 3.3) | ½ day |
| 5 | Bridge (ALB tier) | `deploy/bridge/deploy_bridge.sh` | new ALB DNS name → `.a2alab/bridge_host.json`; task role's `invoke-agentcore` policy picks up the new runtime ARNs | ¼ day |
| 6 | Console, faces, briefs | `deploy/console/…`, `deploy/faces/…`, `deploy/briefs/…` | rules on the new ALB; console secret carries `A2ALAB_LOGGING_API_URL/_KEY` and the Tableau embed key (WS19) unchanged | ¼ day |
| 7 | Cross-cloud identity | `deploy/adk/provision_aws_federation.py` (GCP→AWS role, **new account**); `deploy/fanout/provision_gcp_federation.py` (the pool's provider trusts an **AWS account id** — update), `deploy/bridge/gcp_federation.sh`, `deploy/console/gcp_federation.sh`, `deploy/agentcore/gcp_federation.sh strands` (rebind the new role ARNs as `principalSet` members) | new `A2ALAB_ADK_AWS_ROLE_ARN` into the Agent Engine deployment env (`deploy/adk/deploy_adk.py` redeploy) | ½ day, and the one most likely to fail quietly (D40/D41 lessons: federation is not portable across compute shapes) |
| 8 | Coding-agent telemetry | `scripts/setup_cw_metrics_otlp.py` / `setup_cw_logs_otlp.py --apply`; then `claude_otel.sh`, `codex_otel.sh`, `cursor_otel.sh`, `kiro_otel.sh` re-run per machine (they carry the credential) | account-bound credentials rotate; endpoint hostnames do not change | ¼ day |
| 9 | Verify | `uv run python scripts/identity_preflight.py` (every caller identity), `uv run python scripts/matrix.py` (every runnable cell → `plan/03-results.md`), the D48 **negative** auth test on the console | — | ½ day |
| 10 | Record | `plan/09-deployment-map.md` L1/L6 + "Checking reality"; an ADR; `.a2alab/accounts.md`; `chezmoi re-add` picks up the regenerated `bridge_host.json`/`acm_arn.txt` | the CLAUDE.md rule: a deploy anywhere updates plan/09 in the same change | ¼ day |

**Like-for-like effort: ~3 focused days**, of which roughly a third is the
outward re-pointing in 3.3 and the federation in step 7, not the AWS resources
themselves.

### 3.3 The seams that reach out of AWS — the real checklist

Because every *public entrance* is a **Cloudflare CNAME on the operator's own
domain** (L5.5: `bridge-lab`, `console-lab`, `faces-lab`), the systems that
call the lab by hostname — Salesforce Named Credentials for Path A, Agent
Engine's `A2ALAB_BRIDGE_URL`, the Strands runtime's bridge route — need **no
change at all**: the CNAME target moves from the old ALB to the new one and
Full (strict) TLS keeps working because the origin cert is the same one,
re-imported. That is the payoff of never handing out an AWS hostname.

What *does* hold raw AWS identifiers, and must be updated:

| Holder | Holds | Where it is set |
|---|---|---|
| Foundry `RemoteA2A` connection (Azure) | the **shim's `execute-api` URL** | `deploy/foundry/provision_foundry.py` re-run |
| Anthropic Managed Agent vaults — obs analyst, cost sentinel | the **obs MCP `execute-api` URL** + token (`mcp_url` in `.a2alab/obs_analyst.json`, `cost_sentinel.json`) | `scripts/setup_obs_analyst.py`, `setup_cost_sentinel.py` |
| Fan-out orchestrator (Managed Agent) | the **fan-out MCP `execute-api` URL** (`.a2alab/fanout_mcp_orchestrator.json`) | its setup script (D41) |
| Foundry / ADK / Strands leg agents' *inbound* URLs consumed by the fan-out Lambda | unchanged — they are GCP/Azure/AgentCore ARNs; only the AgentCore ARNs move | fan-out secret |
| GCP workload identity pool provider (`a2alab-aws`) | trusts an **AWS account id**; bindings name **role ARNs** | step 7 |
| Salesforce Data 360 Zero-Copy connector | the **Aurora endpoint** + `lab_reader` credentials | Setup UI (D70) |
| Local: `.env` | `A2ALAB_AWS_ACCOUNT_ID` + ~10 ARN keys | edit, then `env_sync.py push` to the **new** account |
| Local: `.a2alab/{bridge_host.json,cloudflare/acm_arn.txt,accounts.md}` (encrypted, D45) | ALB name, cert ARN, the account mapping | regenerated by the deploys; `chezmoi re-add` |
| Test guard `tests/unit/test_no_account_identifiers.py` | walks `git ls-files` for the account id | unchanged in code; the *value* it checks comes from `.env` |

**Make the next move DNS-only.** The three `execute-api` URLs are the only
reason external systems have to be touched. Putting **API Gateway custom
domains** (free; ACM cert; one Cloudflare CNAME each — `shim-lab`,
`fanout-lab`, `obs-mcp-lab`) in front of them during the migration means a
*future* account move, or a Lambda→something-else move, changes nothing
outside AWS. Cheap to do at the same time; expensive to retrofit later.

---

## 4. Cheaper shapes — because the operator would be paying

The current shapes were chosen by *measured ceiling* (plan/09 L1: the ALB
exists because Path A's budget is 45s and API Gateway's integration timeout is
a hard 30s). A cheaper hosting must not quietly re-introduce a ceiling. Each
option below says what it saves and what it changes about the lab's claims.

### 4.1 Options for "the four ASGI processes" (the $67)

| Option | Shape | Est. $/mo for these four | Ceiling / caveat | What it changes about the lab |
|---|---|---|---|---|
| **A. As-is** | 4 Fargate tasks + ALB | ~67 | none | nothing |
| **B. One Fargate task** | bridge + console + faces (+ the watcher as a thread or sidecar command) in **one** 0.5 vCPU/1 GB task behind the existing ALB, addressed by path/host exactly as the faces already are (D51's argument, applied one level up) | ~14 + 17 ALB = **~31** | shared blast radius: a console bug can now take Path A down, which the console-rule-vs-default-action design deliberately prevented today | plan/09 "Why not… give the console its own load balancer" gains a sibling row |
| **C. Lambda container images + Function URLs, behind Cloudflare** | each service = the **existing ECR image** run on Lambda via the Lambda Web Adapter; a **Function URL** (no API Gateway, so **no 30s ceiling** — Function URLs allow up to 15 min); Cloudflare proxied CNAME → Function URL; the watcher = the faces image invoked by EventBridge every 60s (43k invocations/mo, inside free tier). **No ALB, no Fargate.** | **~0–5** (requests + GB-s at lab volume; ECR storage unchanged) | **cold starts** — a Python image with the lab's deps is 2–5s cold; hurts the console's feel and adds latency to Path A's first call (fits the 45s budget; hurts the "1.2–12.6s" numbers in `plan/03-results.md`). Provisioned concurrency fixes it at ~$3–4/mo per function-GB — for the bridge alone, still far under a task. Cloudflare's own proxy timeout is 100s on the free plan; SSE for the console live-tail needs Lambda **response streaming** (supported on Function URLs, not through the free Cloudflare proxy for long-lived streams — verify or use the tunnel for that one route). | plan/09 L1's four-shapes narrative becomes three: the whole "ALB because 30s" reasoning is **specific to API Gateway**, and Function URLs did not have that ceiling — an honest new finding worth an ADR in itself. D52's objection to a watcher Lambda ("a third zip to keep in step") is answered by running the *same image*, which is the whole reason the watcher reused the faces image. |
| **D. One small VM** | one `t4g.small` (2 vCPU/2 GB ARM ≈ $12) or **Lightsail** ($5–10) running `docker compose` with all four services, fronted by **`cloudflared`** (free tunnel, no ALB, no public IP, no inbound SG) — the shape the lab *started* in (plan/09 "How this got here"), minus the laptop | **~5–15** | a pet, not cattle: patching, disk, restarts are yours; `cloudflared` on the free plan has the same 100s proxy timeout (fine for 45s); no IAM task roles — the VM's instance role does everything, coarser than today's one-role-per-seam | the "earned its way to hosted" story goes backwards a step; the federation lessons (Fargate vs Lambda credential suppliers) become moot on EC2 (IMDS works). Honest, but it makes the estate a demo of Docker on a box. |
| **E. App Runner** | 3 services from the same images; scales to zero *compute* but bills provisioned memory while idle (~$5/GB-mo per service) | ~15–20 + no ALB | 120s request timeout (fine); no ARM; still per-service idle cost | little narrative change; not clearly cheaper than B |

**Recommendation, if it is ever built: C, with the bridge given a small
provisioned-concurrency floor.** It removes the two lines that are two-thirds
of the bill, keeps one identity per seam (each function has its own role, as
each task does today), keeps every public entrance a Cloudflare hostname, and
turns the ALB decision into a documented *finding* rather than a cost. B is
the fallback if cold starts prove unacceptable for the demo — it halves the
bill for an afternoon's work and no new failure modes beyond the shared task.
D is the cheapest and the least like the lab.

### 4.2 The store

Aurora Serverless v2 at **min ACU 0** is already the cheapest Aurora shape and,
at lab usage, one of the cheapest lines on the bill. The alternatives are worse
or change the experiments:

- **RDS PostgreSQL `db.t4g.micro`** (~$12/mo + storage) is *more* than idle
  Aurora at this usage; only wins if the store is kept awake most of the day.
- **A hosted Postgres outside AWS** (Neon/Supabase free tiers) would run the
  console fine, but the Data 360 Zero-Copy connector is the *Aurora* connector
  (`AwsRdsAuroraPostgres`, WS19/D71) — the Tableau Next dashboard and the
  in-org A2A Lab app depend on it. Dropping Aurora drops M10.
- **SQLite on the single VM (option D)** was the shape D49 *removed* because
  the hosted harvest and the console disagreed on the source of truth.

Keep Aurora; the two knobs that matter are the auto-pause delay (leave it at
the minimum) and not letting the console poll it while idle. Budget: $5–15.

### 4.3 The small lines

| Line | Cheaper | Worth it? |
|---|---|---|
| Secrets Manager (14 × $0.40) | SSM Parameter Store **standard** SecureString is free (4 KB cap — fine for the per-seam secrets; `.env` at ~80 keys may need advanced tier or two parameters). ECS task defs' `valueFrom` and Lambda code both read SSM. | ~$5/mo for a half-day of touching every deploy script and `env_sync.py`. **Only if consolidating anyway**; otherwise leave it. |
| ECR | lifecycle policy: keep last 2 tags per repo | yes — one command per repo, and it stops the slow creep |
| CloudWatch logs | 14–30 day retention on every `/ecs/*` and `/aws/lambda/*` group (today: never expires) | yes — trivial, and it is the line most likely to grow unnoticed |
| CloudWatch custom metrics (coding telemetry) | keep session-id dimensions off unless a session is being studied (`OTEL_METRICS_INCLUDE_SESSION_ID`); the WS16 log signal is per-event and cheaper than per-series metrics | watch, don't pre-optimize; the harvest already reads it |
| AgentCore + Bedrock | already consumption-priced; nothing to do | — |

### 4.4 A shape to explicitly *not* choose

Do not put the bridge back on API Gateway to save the ALB — that is the 30s
ceiling the ALB exists to avoid (plan/09 L1, D47's whole reason for
fire-then-poll). If the ALB goes, it goes to a Function URL (C) or a tunnel
(D), both of which keep the 45s budget.

---

## 5. Summary numbers

| Shape | Est. AWS $/month | Build effort beyond like-for-like | Narrative cost |
|---|---|---|---|
| Like-for-like (as today) | **85–110** | — (~3 days) | none |
| B — one Fargate task + ALB | **45–60** | +½ day | small |
| **C — Lambda images + Function URLs, no ALB** | **15–30** | +3–5 days (adapter, cold-start tuning, SSE route, one ADR) | rewrites the "four shapes" section of plan/09 into an honest finding |
| D — one VM + cloudflared | **10–25** | +1–2 days | large — the estate stops being a hosted lab |

Every row keeps: AgentCore ×3, the four Lambdas, Aurora at min-0, Secrets
Manager, the Cloudflare front door, and every non-AWS platform exactly as it is.

---

## 6. What this doc deliberately does not decide

- **Whether to move at all.** The current account is fine while it is
  reachable; this exists so the answer is known before it is needed.
- **Who pays for the non-AWS columns.** Anthropic Managed Agents (three
  scheduled deployments, D16/D23/D44), Vertex Agent Engine (already on a
  personal GCP account per WS2), Foundry, and the Salesforce org are outside
  this migration's scope and this model's totals.
- **Whether option C is worth its narrative cost.** The lab's hosting story is
  "shape chosen by measured ceiling"; C says the ceiling was API Gateway's,
  not "public HTTP's", and that is a better finding than a cheaper bill — but
  it should be recorded as one, with the measurement, not slipped in as a cost
  cut.

When any of these becomes a decision: ADR in `plan/00-decisions.md`, then
plan/09 L1/L6 and "Why not, in one place" in the same change, per CLAUDE.md.

## Related

- `plan/09-deployment-map.md` — the inventory this reads, L1 (shapes) and L6 (code → deployment)
- `plan/04-runbooks.md` §8 — Aurora provisioning; `plan/10-operations.md` — code-vs-config deploys
- `plan/00-decisions.md` — D21 (the account), D23, D26, D28, D39, D41, D43, D46, D51, D52, D66–D72
- `build-notes/claude/09-secrets-and-environment-identity.md` — why the account boundary is a *file* boundary too
