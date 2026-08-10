# WS19 (M10) build checkpoint — 2026-08-08

Scratch handoff note for resuming WS19 in a fresh Claude session (needed so the
**Trailhead MCP server** — added this session but not live until a restart —
is available). Delete once WS19 items 5–7 are done. Not surfaced anywhere; a
plan-local note, not part of the deployment map or delivery record.

## Where the build is

WS19 = the zero-copy connector for the lab's Aurora obs store → Salesforce Data
360 (Data 360) → Tableau Next dashboard. Work items live in
`plan/07-workstreams.md` under `## WS19`.

**Done and verified live (items 1–4):**
- **Item 1** — surveyed the store; `lab.trace_events` columns Tableau groups on
  are already top-level scalars, only raw-payload jsonb needs no flattening.
- **Item 2** — `deploy/obs/deploy_datacloud_ingress.sh` opens 5432 on
  `a2alab-aurora-sg` to the Data Cloud tenant's **eu-central-1** egress `/32`s
  only (12 of them, pinned in `config/salesforce_ip_ranges.yaml` from the **"IP
  Addresses Used by Data 360 Services"** article — NOT `ip-ranges.salesforce.com`;
  see the item-5 root cause below and D70). **APPLIED LIVE this build:** 5432 open
  to those `/32`s + TLS enforced (`rds.force_ssl=1` via custom cluster param group
  `a2alab-obs-force-ssl`, reboot done; Data API reads unaffected). `--verify`
  passes.
- **Item 3** — `lab_reader` hardened: `observability.pg.ROLE_GRANTS` (read-only,
  15s statement_timeout, CONNECTION LIMIT 15, SELECT on `lab.*`), applied live
  by `scripts/pg_migrate.py` as table owner. Fixes the D46 "posture only by
  hand" gap.
- **Item 4** — `pg.py` posture note + `plan/09` + runbook §8 updated.

**Done this build (docs, beyond item 4):**
- `plan/09-deployment-map.md` now has **L5.8** — "The cross-region Zero Copy
  path" — a dedicated architecture level with its own mermaid diagram (EU org +
  EU tenant co-located → US store) that the console Architecture page renders for
  free (parsed by `src/console/architecture.py`; verified it picks up L5.8).
  Covers: org and tenant are in-region (the normal customer shape), the tenant↔store
  hop is the customer-representative cross-region leg, the data-residency story
  (rows never leave us-east-1 under Zero Copy), and that the Tableau render time is
  a **real** EU→US round-trip latency measurement to record in
  `plan/03-results.md` once the dashboard exists.
- Level index + intro sentence in `plan/09` updated to list L5.8.
- Memory `datacloud-region-topology-is-demo-artifact` captures the framing.

## Item 5 — DONE: connection live in prod (2026-08-08)

**`A2A_Lab_Obs_Aurora` is created and Test Connection returns "Connection was
established."** Created in the Setup UI (not a script — see below). The fields:
Connection Name `A2A Lab Obs Aurora`, API Name `A2A_Lab_Obs_Aurora`, URL
`a2alab-obs.cluster-c1sik0ik66lk.us-east-1.rds.amazonaws.com:5432` (bare
host:port), Database `a2alab`, Schema `lab`, Username `lab_reader`, password from
Secrets Manager `a2alab/obs/reader`.

**Root cause of the long failure, and the fix (full write-up in D70).** Test
Connection failed for hours with "Could not connect to url provided" while every
layer checked out — public IP, IGW route, force_ssl, URL format, and the
`lab_reader` credential (proven via the RDS Data API). The cause was in TWO parts:
1. **Wrong IP source.** The probe egresses from AWS-native Hyperforce NAT `/32`s
   published in the **"IP Addresses Used by Data 360 Services"** article — NOT
   from `ip-ranges.salesforce.com` (the app-fabric `/23` D69 item 2 pinned), which
   the connector does not use. First proven by a temporary VPC flow log against a
   **different org** (mistake — that org's tenant is in ca-central-1; captured
   `3.98.79.254` / `15.223.107.129`, both REJECTed, both absent from
   `ip-ranges.json`, both present in the article). That proved the METHOD.
2. **Wrong region.** The lab's ACTUAL tenant is `CDP2-AWS-PROD3-EUCENTRAL1` =
   **eu-central-1**, not ca-central-1. After repinning to the article's 12
   eu-central-1 `/32`s and applying them, Test Connection succeeded — and a fresh
   flow log confirmed the real probe egressing from `3.64.2.81` / `18.198.9.100`,
   both in the pinned set, both **ACCEPTed**. (The "same errors" seen immediately
   after applying the eu-central-1 set were SG-propagation lag / a cached failed
   test; the next click sailed through.)

Fix, as applied: repin `config/salesforce_ip_ranges.yaml` to the 12 eu-central-1
`/32`s, revoke the stale `155.226.152.0/23` (and the interim ca-central-1 set),
apply via `deploy/obs/deploy_datacloud_ingress.sh` (its `--verify` reworked — no
JSON manifest to diff, so it checks pins-are-applied). force_ssl left at 1; all
temporary flow-log infra torn down. Two operator screenshots (fail vs success,
identical fields) are in `tmp-docs/` — local posterity only (gitignored, never
surfaced).

### the original item-5 blocker notes (superseded — kept for the trail)

**The REST POST is a dead end and item 5 is NOT a script.** Decision this
session (browser MCP route): the `AwsRdsAuroraPostgres` connector's *documented,
supported* create path is **UI-only** — Data Cloud Setup → External Integrations
→ **Other Connectors** → New → source *AWS Aurora PostgreSQL*. There is no
documented `POST /ssot/connections` body for it; the only external evidence
(third-party `dc-cli`) uses `credentials[]`/`parameters[]` of `{paramName,value}`,
which **contradicts** the org's own `GET /ssot/connectors` metadata term
(`connectionAttributes`) — unverified against prod, so a blind POST risks an
orphaned connection. `scripts/create_datacloud_connection.py` was NOT written.

**All four "still unknown" items are now answered (from developer.salesforce.com
"Set Up an AWS Aurora PostgreSQL Connection" + the data-stream page):**
1. Name/label: the UI has **two** fields — **Connection Name** (label) and
   **Connection API Name** (dev name, auto-derived). This is why a lone
   `connectionName` was rejected in the body.
2. n/a for the UI path (attributes are discrete labelled fields, below).
3. n/a — UI path, no body.
4. **Zero Copy vs Batch is chosen LATER, at the data-stream step**, per-object
   (Zero Copy path adds a "configure acceleration" step). NOT at connection
   create. Confirms the item-6 plan — no rework.

**Existing connections check:** the "1 Connections" in the grid is the built-in
**`UploadedFiles`** (File Upload) from provisioning — NOT an Aurora connection,
so no duplicate. (Explains why the REST list read empty: `ssot/connections`
omits the built-in File Upload connector.)

**Wizard reached, fields staged, NOT saved.** Got to the "New AWS Aurora
PostgreSQL Source" details form via an `sf org open … --url-only` frontdoor into
`a2alab-prod`. Entered and verified: Connection Name `A2A Lab Obs Aurora`,
Connection API Name `A2A_Lab_Obs_Aurora` (auto), Username `lab_reader`,
Connection URL `a2alab-obs.cluster-c1sik0ik66lk.us-east-1.rds.amazonaws.com:5432`
(host form + `:port`, NOT a `jdbc:` prefix), Database `a2alab`. **Save was never
clicked → nothing persisted in prod.**

**Two fields the browser MCP could NOT complete (operator to finish):**
- **Schema** must be `lab`. The field pre-fills `public` and the MCP `type` tool
  *appends* (no clear/backspace/select-all primitive), so it got corrupted to
  `publiclablab`. Clear it, set `lab`.
- **Password** = `lab_reader`'s secret from Secrets Manager `a2alab/obs/reader`.
  Not entered: the MCP `type` text is a literal tool arg, so typing it would
  print the secret to the transcript (forbidden). No paste/JS-eval browser tool
  exists to inject it silently.

**Operator finish (in your own authenticated browser session):** reopen the
wizard fresh (the MCP draft is unsaved/parked), fill all fields as above, set
**Schema = `lab`**, paste the password, click **Test Connection**, then **Save**.
Values reference: writer endpoint from `A2ALAB_PG_CLUSTER_ARN` describe-clusters;
`database=a2alab`, `SCHEMA=lab`; creds `lab_reader` from `a2alab/obs/reader`.

**Items 6–7** (after item 5, operator/UI-only):
- Item 6: select Zero Copy (BYOL) on the DLO→DMO mapping, build the Tableau Next
  dashboard, **measure render time → `plan/03-results.md`** (the L5.8 latency
  number).
- Item 7: console entry point + `plan/02-matrix.md` finding.

## Deliberate publishes NOT yet done (leave for operator unless asked)
- `uv run python scripts/jira_sync.py --apply` (WS19 item states → Jira).
  Item summaries kept byte-identical so no story forks except where the state
  itself changed; items 1–5 are now done (2 and 5 flipped this build), 6–7 open.
- Console redeploy (**full rebuild** — `plan/` is baked into the image by COPY)
  for L5.8 + WS19 story states to appear on the hosted console.

## Trailhead MCP server — setup done this build
`claude mcp add trailhead --transport http https://mcp.trailhead.salesforce.com/mcp --scope user`
(user scope, in `~/.claude/.claude.json`, NOT the repo's `.mcp.json`). Connects;
no auth. Tools `content_search` + `fetch_content` become available on next
session start.
