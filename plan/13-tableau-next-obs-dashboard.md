# Data 360 Zero Copy → Tableau Next observability dashboard (WS19 / M10)

The build record for WS19 (`plan/07-workstreams.md` §WS19; ADRs D69/D70;
architecture level plan/09 L5.8). Consolidates the three superseded scratch
notes (`WS19-checkpoint-2026-08-08.md`, `WS19-items6-7-groundwork.md`,
`WS19-items6-7-steps.md`).

**What this is:** land the cross-platform agent telemetry the harvest already
collects into **Salesforce Data 360 with no ETL** (Zero Copy federation over the
hosted Aurora obs store), then surface it to a **Tableau Next** dashboard as a
Salesforce-side, business-analytics view of agent traffic. The console's
Observability section stays the **wire-level** view (raw request/response per
hop); Tableau Next is the **aggregate** view of the *same rows*, reached with
zero copy. **Having both over one Aurora table is the M10 finding**, not a
duplication — the lab's viewer is the per-hop wire truth, Data 360 + Tableau is
the rollup, and Zero Copy means they never diverge.

## Status — where the build is

**The entire data layer is BUILT and live-verified headless (2026-08-09).** The
only work remaining is the Tableau Next dashboard itself — the semantic model,
the individual visualizations and tiles, and the L5.8 render measurement — which
is genuinely UI-only (ruled out across four headless surfaces, below) and is the
operator's manual task.

| # | Item | State |
|---|---|---|
| 1 | Survey the store; identify jsonb needing flattening | ✅ done — only `*_payload_raw` jsonb (not mapped); everything Tableau groups on is already a scalar (D13) |
| 2 | Scoped, TLS-only 5432 ingress for the Data Cloud tenant | ✅ done — `deploy/obs/deploy_datacloud_ingress.sh`, eu-central-1 `/32`s (D70) |
| 3 | `lab_reader` hardening for federation | ✅ done — `ROLE_GRANTS` in `pg.py`, applied by `pg_migrate.py` |
| 4 | Posture docs (`pg.py`, plan/09, runbook §8) | ✅ done |
| 5 | Data 360 `AwsRdsAuroraPostgres` connection | ✅ done — `A2A_Lab_Obs_Aurora` live, "Connection was established" (2026-08-08) |
| 6a/6b | Zero-Copy data layer: views, streams, DLOs, DMOs, mappings (both grains) | ✅ **done headless** via SSOT REST (2026-08-09) |
| 6c | **Build the Tableau Next semantic model + dashboard tiles; measure L5.8** | ⬜ **operator — the remaining manual work** |
| 7a | Matrix finding | drafted, ready to paste once 6c gives the number |
| 7b | Console entry point | scoped, build after 7a |
| — | Inline Tableau Next embed (owner-only, server-side auth) | ✅ built headless; needs the console full-rebuild redeploy + a dedicated integration user |

---

## 1. The data layer — BUILT HEADLESS (2026-08-09)

Everything from the federation view through the DMO was built via the Data Cloud
**SSOT REST API** (`sf api request rest`, Core token) — the same surface the
`build-data360-demo` skill uses. The Metadata API has no DMO-create type, which
earlier read as "UI-only"; that was the wrong surface.

### 1a. Prerequisite — a single-column surrogate PK (§0, load-bearing)

`lab.trace_events` has a **composite** PK `(trace_id, hop_seq, ts)` — one row per
hop. A Data Cloud DLO wants a **single** PK field, and the mapping API refuses a
composite one (`INVALID_DMO: We do not support multiple Primary`). Data Cloud
also **reflects the source table's real DB primary key** and forces the DLO to
key on it, ignoring a single PK declared in the stream body *and* any unique
index — so any stream over the base table always lands a composite DLO PK the
mapping then rejects.

The fix is a stable surrogate added as a **generated column** (repo-owned schema,
in `observability.pg.DDL`, applied by `pg_migrate.py` as owner):

```sql
ALTER TABLE lab.trace_events
  ADD COLUMN IF NOT EXISTS event_key text
  GENERATED ALWAYS AS (trace_id || '#' || hop_seq::text || '#' || ts::text) STORED;
```

`STORED` so a federated `SELECT` returns it without recomputation. `event_key`
is the DLO primary key and the stable per-hop row identity Tableau needs for
detail rows. (Apply: `uv run python scripts/pg_migrate.py`, verify
`SELECT event_key FROM lab.trace_events LIMIT 1`.)

### 1b. The blocker's real fix — federate a VIEW, not the base table

Point the stream at a **view**, which has no PK constraint, so Data Cloud accepts
the declared single PK. Bonus: the view **omits the two jsonb payload columns**,
making the "raw wire bytes never leave us-east-1" residency claim **structural** —
they are not in the object Data Cloud can see, not merely unmapped.

```sql
-- lab.trace_events_zc — hop grain (in observability.pg.DDL, applied by pg_migrate.py)
-- SELECT of the scalar columns + event_key only; NO *_payload_raw jsonb.
```

### 1c. The two federated grains

**Hop grain** — one row per hop, over `lab.trace_events_zc`.

**Trace grain (§4c)** — end-to-end trace latency is *sum the hops within a trace,
then average across traces*: a two-level aggregation a flat measure computes
wrong. Tableau Semantics has **no `FIXED`/LOD** (confirmed 2026-08-09 — the calc
reference lists only a flat set: `AVG CORR COVAR COVARP COUNT COUNTD MEDIAN
PERCENTILE STDEV STDEVP SUM VAR VARP` + single-field `MIN`/`MAX`), so the
aggregation cannot be expressed in the model. A materialized calculated insight
was **ruled out on residency** (it copies the aggregate into eu-central-1). The
chosen fix pushes the `GROUP BY` **down into Aurora** as a view and federates it
as a second Zero-Copy DMO — same live federation, nothing materialized:

```sql
CREATE OR REPLACE VIEW lab.trace_rollup_zc AS
SELECT
  trace_id,                                          -- single-col PK for the DLO
  COUNT(*)                                  AS hop_count,
  MAX(hop_seq)                              AS max_depth,
  SUM(latency_ms)                           AS total_latency_ms,   -- summed work
  (MAX(ts) - MIN(ts)) * 1000                AS wall_ms,            -- end-to-end wall clock
  (BOOL_AND(status = 'ok'))::int            AS all_ok,             -- 1 iff every hop ok
  MIN(ts_at)                                AS started_at,
  MAX(CASE WHEN hop_seq = 0 THEN source END) AS edge_source,
  MAX(CASE WHEN hop_seq = 0 THEN target END) AS edge_target
FROM lab.trace_events
GROUP BY trace_id
```

**Acceleration is OFF** on both streams (`refreshConfig.isAccelerationEnabled:
false`) — the residency crux. Queries federate live to Aurora; rows stay in
us-east-1. If dashboard latency is ever judged unacceptable, turning acceleration
on is a *deliberate, documented* residency trade — record it in
`plan/03-results.md`, don't flip it silently.

### 1d. What was built (all live-verified on `a2alab-prod`, 2026-08-09)

| Artifact | Built by | Result |
|----------|----------|--------|
| Federation view `lab.trace_events_zc` | `pg.DDL` + `pg_migrate.py` | scalars + `event_key`, **no jsonb** |
| Stream `A2A_Lab_Trace_Events_K` | `POST /ssot/data-streams` (EXTERNAL / Direct_Access) over the view | DIRECT_ACCESS, acceleration OFF, single PK `event_key` |
| DLO `A2A_Lab_Trace_Events_K__dll` | auto-created by the stream | single PK `event_key__c`, federates live rows |
| DMO `A2A_Lab_Trace_Event__dlm` | `POST /ssot/data-model-objects` | 11 fields, PK `event_key__c`, category Other |
| DLO→DMO map | `POST /ssot/data-model-object-mappings` | 12 pairs, 1:1, no transform |
| Rollup view `lab.trace_rollup_zc` | `pg.DDL` + `pg_migrate.py` (26/26 stmts) | one row per `trace_id`, agg in Aurora, single-col PK, no jsonb |
| Stream `A2A_Lab_Trace_Rollup` | `POST /ssot/data-streams` over the rollup view | DIRECT_ACCESS, acceleration OFF, single PK `trace_id` |
| DLO `A2A_Lab_Trace_Rollup__dll` | auto-created by the stream | single PK `trace_id__c`, federates 960 traces |
| DMO `A2A_Lab_Trace_Rollup__dlm` | `POST /ssot/data-model-objects` | 9 fields, PK `trace_id__c`, category Other |
| DLO→DMO map (rollup) | `POST /ssot/data-model-object-mappings` | 9 pairs, 1:1 |

**Verified end-to-end** via `POST /ssot/queryv2`: the hop-grain DMO returns live
Aurora rows (headline `hops/traces by protocol` aggregate federates); the
trace-grain DMO federates **960 traces** with every figure matching the pre-build
numbers exactly (avg 32,287 ms, success 0.8979, avg hops 3.15, avg depth 1.43,
avg wall 249,877 ms, max 448,241 ms) — the `GROUP BY` runs in Aurora, nothing
materializes to eu-central-1. Grain is preserved: a `DIRECT_ACCESS` DLO returns
every underlying row (`COUNT(*)=hops` ≫ `COUNT(DISTINCT trace_id)=traces`).

The original hand-built composite-PK stream `A2A_Lab_Trace_Events` + its DLO
(superseded by the `_K` stream over the view) were **deleted 2026-08-09** once the
DMO was confirmed to federate solely from `A2A_Lab_Trace_Events_K__dll`.

### 1e. The working SSOT REST calls (for the "later tables" extensions)

- **Create DMO** — `POST /ssot/data-model-objects` with `{name (dev name, NO
  __dlm suffix), label, category, dataSpaceName, fields:[{name,label,dataType,
  isPrimaryKey}]}`. Platform appends `__dlm` and the `KQ_*`/system fields.
- **Map DLO→DMO** — `POST /ssot/data-model-object-mappings?dataspace=default`
  with `{sourceEntityDeveloperName, targetEntityDeveloperName, fieldMapping:
  [{sourceFieldDeveloperName, targetFieldDeveloperName}]}`. DLO fields carry `__c`.
- **Create Zero-Copy stream** — `POST /ssot/data-streams`, `datastreamType:
  EXTERNAL`, `dataAccessMode: Direct_Access`, `connectorInfo.connectorType:
  DataConnector`, `advancedAttributes:{database,schema,object}`.
- **DELETE** needs the `sf api request rest --file` wrapper (`{"method":
  "DELETE","url":...,"body":{"mode":"raw","raw":""}}`); a stream delete also needs
  `?shouldDeleteDataLakeObject=true`.

---

## 2. The connection (item 5) and the D70 root cause

`A2A_Lab_Obs_Aurora` is live; Test Connection returns "Connection was
established." Created in the **Setup UI** (Data Cloud Setup → External
Integrations → Other Connectors → New → AWS Aurora PostgreSQL Source): the
connector has **no documented `POST /ssot/connections` body**, and blind
body-probing a prod org risks orphaning a real connection (the D69 item-4
"API-creatable" claim was wrong — corrected in D70). Fields: Connection Name
`A2A Lab Obs Aurora`, API Name `A2A_Lab_Obs_Aurora`, URL
`a2alab-obs.cluster-c1sik0ik66lk.us-east-1.rds.amazonaws.com:5432` (bare
host:port), Database `a2alab`, Schema `lab`, Username `lab_reader`, password from
Secrets Manager `a2alab/obs/reader`. Zero Copy vs Batch is chosen **later**, per
object at the data-stream step — not at connection create.

**The long failure was IP source + region (D70), not the connection.** Test
Connection failed for hours with "Could not connect to url provided" while every
layer checked out (public IP, IGW route, force_ssl, URL format, and the
`lab_reader` credential proven via the Data API). Two compounded causes:

1. **Wrong IP source.** The probe egresses from AWS-native Hyperforce NAT `/32`s
   in the **"IP Addresses Used by Data 360 Services"** help article — NOT from
   `ip-ranges.salesforce.com` (the app-fabric `/23` that D69 item 2 wrongly
   pinned), which the connector does not use. First proven by a temporary VPC flow
   log against a **different org** (whose tenant is ca-central-1) — that proved
   the *method*.
2. **Wrong region.** The lab's actual tenant is `CDP2-AWS-PROD3-EUCENTRAL1` =
   **eu-central-1**. After repinning to the article's 12 eu-central-1 `/32`s and
   applying them, Test Connection succeeded — a fresh flow log confirmed the real
   probe egressing from `3.64.2.81` / `18.198.9.100`, both pinned, both ACCEPTed.

Fix as applied: repin `config/salesforce_ip_ranges.yaml` to the 12 eu-central-1
`/32`s, revoke the stale ranges, apply via
`deploy/obs/deploy_datacloud_ingress.sh` (its `--verify` checks pins-are-applied,
since the article has no JSON manifest to diff); `force_ssl` left at 1; all
temporary flow-log infra torn down. See the auto-memory
`datacloud-region-topology-is-demo-artifact` for the framing (org↔tenant split is
a provisioning quirk; tenant↔store is the real cross-region hop).

---

## 3. The Tableau Next dashboard — THE REMAINING MANUAL WORK (item 6c)

Build the dashboard over DMO `A2A_Lab_Trace_Event__dlm` (hop grain) and
`A2A_Lab_Trace_Rollup__dlm` (trace grain). This is the one UI step.

> **Why headless is ruled out — FOUR surfaces (2026-08-09):** (1) **Metadata
> API** — `AnalyticsWorkspace`/`AnalyticsVisualization`/`AnalyticsDashboard` ARE
> deployable, but there is **no `SemanticModel` type** (a workspace only
> *references* the SDM by name). (2) **SSOT REST** — `/ssot/semantic-models`,
> `/semantic-data-models`, `/metrics` all 404. (3) **Tableau Next hosted MCP
> server** (`api.salesforce.com/platform/mcp/v1/analytics/tableau-next`) is
> **read-only** — `list_/get_semantic_model*`, `analyze_data`, no create/update.
> (4) **Headless 360 MCP** (Beta) `Dispatch` only invokes operations that exist,
> none of which create an SDM. So the SDM is built in the UI; the Tableau Next MCP
> server is the headless **verify + measure** path once it exists (§3d).

**Prereq permission:** Data Cloud Architect permission set.

### 3a. Semantic model `Agent Interop Traffic` (UI)

Data Cloud / Tableau Next → Semantic Models → New, source = DMO
`A2A_Lab_Trace_Event__dlm` in the **default** data space.

**Direct dimensions:** `Trace Id`, `Hop Seq`, `Source`, `Target`, `Protocol`,
`Status`, `Transport Detail`, `Platform Ref`, `Event Time` (`ts_at__c` — Tableau
derives day/hour/weekday parts natively). `Event Key` is row identity; keep it
available for detail rows.

**Field-reference conventions (verified against the live DMO):** fields are
referenced by **object-qualified label** — `[A2A Lab Trace Event].[Field]`. There
is **no bare `COUNT()`** — count rows via the non-null PK,
`COUNT([A2A Lab Trace Event].[Event Key])`. Create `Is Error` / `Is Ok Int`
**first**; calculated fields are then referenced unqualified (`[Is Ok Int]`).
**Pick Dimension vs Measure by the formula's OUTERMOST operation:** an aggregate
(`AVG`/`SUM`/`COUNT`/`COUNTD`/`PERCENTILE`) → **Measure** (declare UserAgg); a
per-row value (label, bucket, boolean, 0/1) → **Dimension**.

### 3b. Calculated dimensions (type Dimension; single-pass, safe live)

- `Direction` — `[A2A Lab Trace Event].[Source] + " → " + [A2A Lab Trace Event].[Target]`
- `Hop Kind` — `IF [A2A Lab Trace Event].[Hop Seq] = 0 THEN "edge" ELSE "delegated" END`
- `Is Error` (Boolean) — `[A2A Lab Trace Event].[Status] = "error"`
- `Is Ok Int` (helper for success rate; **hide** from dashboards) — `IF [A2A Lab Trace Event].[Status] = "ok" THEN 1 ELSE 0 END`
- `Latency Bucket` (**NULL caught FIRST**, else the 434 no-latency infra hops land in the top bucket):
  ```
  IF ISNULL([A2A Lab Trace Event].[Latency Ms]) THEN "(no latency)"
  ELSEIF [A2A Lab Trace Event].[Latency Ms] < 1000 THEN "0–1s"
  ELSEIF [A2A Lab Trace Event].[Latency Ms] < 5000 THEN "1–5s"
  ELSEIF [A2A Lab Trace Event].[Latency Ms] < 15000 THEN "5–15s"
  ELSEIF [A2A Lab Trace Event].[Latency Ms] < 60000 THEN "15–60s"
  ELSE "60s+"
  END
  ```
- `Target Platform` — normalize `Target` to its platform token. `STARTSWITH`
  absorbs the `-hosted`/`-a2a`/`-shim` suffixes; **`google-adk` before `adk`**
  matters; the two leading exact-match arms keep the Claude backends delineated:
  ```
  IF [A2A Lab Trace Event].[Target] = "anthropic-managed-agents" THEN "claude-managed-agents"
  ELSEIF [A2A Lab Trace Event].[Target] = "anthropic-api" THEN "claude-api"
  ELSEIF STARTSWITH([A2A Lab Trace Event].[Target], "claude") THEN "claude"
  ELSEIF STARTSWITH([A2A Lab Trace Event].[Target], "openai") THEN "openai"
  ELSEIF STARTSWITH([A2A Lab Trace Event].[Target], "agentforce") THEN "agentforce"
  ELSEIF STARTSWITH([A2A Lab Trace Event].[Target], "foundry") THEN "foundry"
  ELSEIF STARTSWITH([A2A Lab Trace Event].[Target], "strands") THEN "strands"
  ELSEIF STARTSWITH([A2A Lab Trace Event].[Target], "guide") THEN "guide"
  ELSEIF STARTSWITH([A2A Lab Trace Event].[Target], "google-adk") THEN "google-adk"
  ELSEIF STARTSWITH([A2A Lab Trace Event].[Target], "adk") THEN "google-adk"
  ELSE [A2A Lab Trace Event].[Target]
  END
  ```
  Infra hops (`web`, `bridge`, `obs-store`, `salesforce-org`) and internal
  orchestrators fall to the `ELSE` and keep their identity **by design** — they
  are real lab components traced as hops, not platforms to fold away. (Don't use
  `SPLIT([Target],"-",1)` — it splits `google-adk-a2a`→"google".) **The rename is
  dashboard-only** — renaming at source is WS21 (code + console badges + diagrams
  + the existing Aurora rows the live formula reads).

### 3c. Measures — declare EVERY aggregated measure as a UserAgg

In Tableau Semantics a measure whose formula already contains an aggregate must
be declared a **user-defined aggregation** ("AggregativeFunction-level calculated
fields require UserAgg"), not left on a default aggregation. This applies to all
of them.

**Hop grain (over `A2A_Lab_Trace_Event__dlm`):**

| Measure | Formula |
|---|---|
| `Hops` | `COUNT([A2A Lab Trace Event].[Event Key])` |
| `Traces` | `COUNTD([A2A Lab Trace Event].[Trace Id])` |
| `Errors` | `SUM(IF [A2A Lab Trace Event].[Status] = "error" THEN 1 ELSE 0 END)` |
| `Error Rate` (`* 1.0` forces float) | `SUM(IF [A2A Lab Trace Event].[Status] = "error" THEN 1 ELSE 0 END) * 1.0 / COUNT([A2A Lab Trace Event].[Event Key])` |
| `Avg Hop Latency` | `AVG([A2A Lab Trace Event].[Latency Ms])` |
| `P50 Hop Latency` | `PERCENTILE([A2A Lab Trace Event].[Latency Ms], 0.5)` |
| `P95 Hop Latency` | `PERCENTILE([A2A Lab Trace Event].[Latency Ms], 0.95)` |

**Trace grain (over the §1c second DMO `A2A_Lab_Trace_Rollup__dlm`)** — point a
new semantic model (or a second source in the same model) at the rollup DMO;
`edge_target` maps to `Target Platform` the same way:

| Measure | Formula |
|---|---|
| `Avg Trace Latency` | `AVG([A2A Lab Trace Rollup].[Total Latency Ms])` |
| `P95 Trace Latency` | `PERCENTILE([A2A Lab Trace Rollup].[Total Latency Ms], 0.95)` |
| `Avg Wall Ms` | `AVG([A2A Lab Trace Rollup].[Wall Ms])` |
| `Avg Hops Per Trace` | `AVG([A2A Lab Trace Rollup].[Hop Count])` |
| `Max Depth Per Trace` | `AVG([A2A Lab Trace Rollup].[Max Depth])` |
| `Trace Success Rate` | `AVG([A2A Lab Trace Rollup].[All Ok Int])` |

### 3d. Dashboard tiles — in Tableau Next a Visualization and a Dashboard are SEPARATE assets

Build each chart in the **Visualization Builder** and **Save** it as its own
asset, then build a **Dashboard** asset and **drag** the saved visualizations
onto its canvas. So each tile = one Visualization you author + save first; the
dashboard is assembled last. (A bare metric/KPI can go straight on a dashboard,
but every chart tile is a saved Visualization.) A "KPI" is just a Visualization
with one measure and no dimension.

| Save as | Mark type | Columns / Rows | Color / other | Verified check |
|---------|-----------|----------------|---------------|----------------|
| **Hops by Protocol × Platform** *(headline — the L5.8 tile, keep live)* | Heatmap (or bar) | Cols = `Target Platform`, Rows = `Protocol` | Color = `Hops` | grand total = 3021 |
| **Status by Platform** | Bar (stacked) | Cols = `Target Platform`, Rows = `Hops` | Color = `Status` | only `ok`(2850)/`error`(171) — no `pending` |
| **Hop-Latency Distribution** | Bar over `Latency Bucket` (no Histogram mark exists) | Cols = `Latency Bucket`, Rows = `Hops` | add P50/P95 as KPI text | 0–1s 510 · 1–5s 544 · 5–15s 781 · 15–60s 712 · 60s+ **40** · (no latency) **434** |
| **Traffic Over Time** | Line | Cols = `Event Time` (Day), Rows = `Hops` | Color = `Protocol` | — |
| **Direction Matrix** *(call graph)* | Heatmap | Cols = `Target`, Rows = `Source` | Color = `Hops` | dense grid |
| **Avg Trace Latency** *(KPI)* | Text / big-number | Rows = `Avg Trace Latency`, no dim | — | ≈ 32,287 ms |
| **Trace Success Rate** *(KPI)* | Text / big-number | Rows = `Trace Success Rate`, no dim (format %) | — | ≈ 0.898 |
| **Avg Hops per Trace by Platform** | Bar | Cols = `Target Platform`, Rows = `Avg Hops Per Trace` | — | ≈ 3.15 overall |
| **Max Depth per Trace by Platform** | Bar | Cols = `Target Platform`, Rows = `Max Depth Per Trace` | — | ≈ 1.43 overall |

**Color** is the visual encoding on the Marks card — put axis fields on
Columns/Rows first, then drag the named field onto the **Color** well (or the
field pill's **Use as → Color**): `Color = Hops` shades the heatmap; `Color =
Status` makes the bar stacked; `Color = Protocol` gives one line per protocol.

**The four trace-grain tiles need the §1c rollup DMO** — they cannot be built
over the hop-grain DMO (no `FIXED`/LOD). The rollup DMO is built and federating
960 traces; build its six flat measures (each UserAgg), then the two KPIs
(measure on Rows, no dim) and two bars (`Target Platform` on Columns).

**Hop-Latency Distribution — there is no Histogram mark.** A histogram is a bar
over a binned dimension. Preferred: bar over the `Latency Bucket` calc dim (fixed,
human buckets, NULLs in their own bar). Alternative: native `Create Bin` on
`Latency Ms` (needs Tableau Next Platform Analyst perm set; axis is raw **ms**, so
label it seconds). Or skip it and show P50/P95 KPIs.

**Why the first attempt "seemed off" (it wasn't):** (a) `Latency Ms` is
**milliseconds** — agent calls genuinely take tens of seconds, so "33k" on a raw
axis = 33 s, not an implausible number. (b) 434 of 3021 hops have **no latency**
(`web` 299, `obs-store` 135 — infra hops); a bucket formula ending in a bare
`ELSE "60s+"` dumps every NULL into the top bar (inflating 40 → 474), which is why
`Latency Bucket` catches `ISNULL` first.

**Assemble the Dashboard** (Workspace → New → Dashboard): drag each saved
Visualization onto the canvas, headline **Hops by Protocol × Platform** top-left;
optional dashboard filter on `Event Time` / `Target Platform`; Save.

> **Verified numbers (live DMO, 2026-08-09) — your tiles should reproduce these:**
> Hops 3021 · Traces 960 · Errors 171 · Error Rate 5.66% · Avg Hop Latency
> 11,807 ms · P50/P95 Hop 7,095/38,921 ms · Avg Trace Latency 32,287 ms · P95
> Trace 101,983 ms · Avg Hops/Trace 3.15 · Max Depth 1.43 · Trace Success 89.8% ·
> Avg Wall 249,877 ms. (`PERCENTILE` may be exact vs the approx used to check —
> magnitudes match, exact percentiles may differ slightly.)

### 3e. MEASURE the L5.8 number

Measure the **cold end-to-end federation round trip** — the real EU→US leg (org +
tenant co-located in eu-central-1 → federated query to Aurora in us-east-1 and
back). Prefer the headless path:

- **Headless (preferred) — Tableau Next hosted MCP server.** Enable it in Setup,
  then `analyze_data` fires a natural-language question at the semantic model
  through the Analytics Agent, executing the same live federation the headline
  tile would; time it cold. Scriptable and repeatable; read-only, which is all a
  measurement needs.
- **UI fallback.** Open the dashboard cold (or force-refresh the headline live
  tile), note wall-clock to rendered.

Record in `plan/03-results.md` with the date and topology note — a genuine
cross-region federation latency, not a synthetic benchmark. Then fill the §4a
`[N]` bracket.

---

## 4. Item 7 groundwork (finalize after 6c)

### 4a. Matrix finding — READY TO PASTE into `plan/02-matrix.md` "Findings ledger"

Fill the one bracket with the 6c number, then append (the ledger is "grow as
measured", so it is deliberately NOT appended yet):

> - Data 360 Zero Copy over the obs store (M10/WS19, measured 2026-08-__): the
>   same `lab.trace_events` rows the console renders as raw wire payloads are
>   federated **with no ETL and no second copy** into Salesforce Data 360 and
>   surfaced to a Salesforce analyst in Tableau Next as aggregate cross-platform
>   traffic. Having both views over one table *is* the finding: the lab's own
>   viewer is the wire-level, per-hop truth; Data 360 + Tableau is the
>   business-analytics rollup, and Zero Copy means they never diverge. The
>   dashboard's first render is a real **cross-region** round trip — org + tenant
>   co-located in eu-central-1 → Aurora (us-east-1) — measured at **[N]s**. Org and
>   tenant are in-region (the normal customer shape); the tenant↔store hop is the
>   honest cross-continent leg (see plan/09 L5.8). Rows stay resident in us-east-1
>   because acceleration is off. The connector reaches the store from the Data 360
>   Services egress IPs, NOT `ip-ranges.salesforce.com` (D70).

### 4b. Console entry point — SCOPE (build after 4a)

The console is one-canvas-per-section off the Control Panel (D57). Data 360 /
Tableau is a **reporting** surface over the obs store — it belongs in the
**Infrastructure** category alongside Observability and Architecture, NOT
Experiments (not a protocol cell) and NOT DevOps.

**Recommendation: a dedicated "Data 360" nav item under Infrastructure** with its
own `view.type` (e.g. `datacloud`), per D57: the THING is the federation (a small
diagram — console viewer + Tableau both reading `lab.trace_events`, reusing the
L5.8 mermaid); the **Details** sub-tab narrates the connection, the federation
view `lab.trace_events_zc` (scalars + `event_key`, no jsonb), the Zero-Copy stream
with acceleration OFF, the DMO, and the DLO→DMO map — how it reads (`lab_reader`,
DIRECT_ACCESS federation, the 5432/TLS path, 15s statement_timeout), **why a view
not the base table**, citing **D69/D70/plan/09 L5.8** so the chips linkify. Nesting
it under Observability would falsely imply it's a facet of the lab's own viewer,
when the finding is precisely that they are two *independent* views over one table.
The alternative (a tab on Observability) is cheaper but less honest.

Reminders when building (the CLAUDE.md parts that bite):
- Anything the new handler imports/reads/opens must be a `COPY` target in
  `deploy/console/Dockerfile` — or it 500s only on that route, hosted.
- Cite real `D<n>`/`plan/*.md` in the Details markdown or no chips render.
- Update the diagram in all three places if it asserts the federation shape:
  `plan/09` L5.8, `config/diagrams.yaml`, and the `*_DIAGRAM` consts in
  `index.html`.
- Console redeploy is a **full rebuild** (`plan/`, `config/`, `src/` baked in by
  `COPY`).

---

## 5. Inline Tableau Next embed — built 2026-08-09 (owner-only, server-side auth)

The console renders the dashboard **inline** for the owner, no login step, via the
Tableau Next Embedding SDK. The backend mints the session server-side: a
**JWT-bearer** token (as the ECA run-as user, carrying the `web` scope) →
`/services/oauth2/singleaccess` → short-lived frontdoor URL → SDK `authCredential`.

**Built, headless, in the repo:** `/api/tableau/frontdoor` (owner-gated) +
`tableau_next.embed` in `/api/config`; the SDK loader + `<analytics-dashboard>`
mount in `index.html`; `CorsWhitelistOrigin` metadata; the deep link points at the
in-org tab `/lightning/n/A2A_Lab_Traffic`; the `A2A_Lab_Home` App Builder page;
diagrams + plan/09 + README + Details pane updated; tests green. The
`a2a_lab_tab_embed` ECA was created **fully headlessly** (four metadata files inc.
the JWT public cert on global-OAuth settings).

**Auth is JWT-bearer, not client-credentials (RESOLVED 2026-08-09).**
Client-credentials is a dead end for the frontdoor: SF's CC flow issues the `api`
scope only, never `web` (and rejects a `scope=web` param with `invalid_request`),
while `/singleaccess` **requires** `web` — so a CC token gets `403 Invalid_Scope`.
The JWT-bearer flow runs in user context (the `sub` user), so its token carries the
ECA's assigned scopes including `web`. Fully headless — the signing cert attaches
via the `certificate` field on `ExtlClntAppGlobalOauthSettings` (PEM, Metadata API
v60+), no Setup upload. Live-proven: JWT (`iss`=consumer key, `sub`=run-as user,
`aud`=`https://login.salesforce.com`, `exp`=+180s, RS256) →
`/services/oauth2/token` returned `scope: web api` → `/singleaccess` returned
HTTP 200 with `frontdoor_uri`.

**The `auraCmpDef` 504 was permissions + asset sharing, not a platform bug
(RESOLVED 2026-08-09).** Every user who views the embed (including an admin
testing) needs BOTH a **Tableau Next** perm set (`Tableau Unmetered Admin`, or
`Tableau Next Consumer (Unmetered)` for view-only) AND a **Data 360** perm set
(`Data Cloud User`/`Architect`) — without the latter the backend data engine can't
run the dashboard's queries and throws generic runtime errors. THEN share the asset
in Tableau Next → **Share / Manage Access** on the dashboard. Same gate for the
inline SDK embed.

---

## 6. Deliberate publishes still pending (operator runs these)

- `uv run python scripts/jira_sync.py` (dry run, read the diff) then `--apply` —
  mirror the WS19 item states to Jira.
- Retrieve/track the `a2a_lab_tab_embed` `.ecaOauth`/`.ecaOauthPlcy` like the five
  siblings; record its key in the encrypted `f6-eca-wiring.md`; `chezmoi
  add --encrypt` the JWT private key.
- **Create a dedicated minimal-privilege INTEGRATION USER** as the JWT `sub` /
  ECA run-as, instead of the System Administrator. Today `clientCredentialsFlowUser`
  / JWT `sub` is the sysadmin (a minted token can hit the REST API as admin, not
  just mint frontdoor URLs). Tighter: an integration user with only the Tableau Next
  perm set + `Data Cloud User` + the dashboard asset shared to it; set it as the ECA
  run-as and JWT `sub`; verify the chain still returns `web`-scoped + 200. Sysadmin
  run-as is the working stopgap.
- Deploy the CORS origin metadata for `console-lab.agenticthings.com`.
- Console **full-rebuild** redeploy so D70 / item-5-done / L5.8 / the inline embed /
  any §4b work appears on the hosted console.

---

## 7. Later tables (deliberate extensions, NOT this build)

The other obs tables extend this dashboard later; sketched so the model grows by
adding relationships, not rewrites:
- **`obs_sessions` / `obs_events`** → DMOs relating to `Agent Trace Event` on
  `Platform Ref` (= `native_id`) — the M11 join: lab-side hop traffic ⋈
  platform-interior execution logs (tokens, event types). Adds cost/usage measures.
- **`usage_events`** (WS18) → its own DMO; console-visitor analytics, a sibling
  dashboard rather than a tile here.
- **`obs_briefs`** → the analyst/sentinel narrative feed; a text panel, not a
  metric source.
- **`obs_harvest`** → freshness/status only; a "last harvested" caption.

Each is a new stream + DLO + DMO via the §1e calls; the semantic model gains
relationships, which is the point of keeping the starter DMO clean and 1:1.
