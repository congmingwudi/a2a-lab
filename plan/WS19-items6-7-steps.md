# WS19 items 6–7 — data layer BUILT (headless), Tableau dashboard steps, item-7 groundwork

Companion to `plan/WS19-checkpoint-2026-08-08.md` and
`plan/WS19-items6-7-groundwork.md` (the content spec — dims, measures, tiles,
the trace-latency decision). Item 5 (the connection `A2A_Lab_Obs_Aurora`) is
done and live. **Item 6's entire data layer — federation view, Zero-Copy stream,
DLO, DMO, and DLO→DMO mapping — is now BUILT and VERIFIED, all headless via the
Data Cloud SSOT REST API (2026-08-09).** What remains for you is the Tableau Next
dashboard (§6c) and the L5.8 measurement. Delete with the checkpoint when WS19
lands. Plan-local scratch — not surfaced anywhere.

---

## Item 6 — data layer: DONE HEADLESS (2026-08-09)

The original plan below assumed 6a/6b were operator UI work. They were built
headlessly instead, via `sf api request rest` against the Data Cloud SSOT REST
API (Core token) — the same surface the `build-data360-demo` skill uses. The
Metadata API has no DMO-create type, which earlier read as "UI-only"; that was
the wrong surface. Full detail + exact payloads: `WS19-items6-7-groundwork.md`
"What is headless vs UI".

### What was built (all live-verified on `a2alab-prod`)

| Artifact | Built by | Result |
|----------|----------|--------|
| Federation VIEW `lab.trace_events_zc` | `observability.pg.DDL` + `pg_migrate.py` | scalar cols + `event_key`, **no jsonb** — residency made structural |
| Zero-Copy stream `A2A_Lab_Trace_Events_K` | `POST /ssot/data-streams` (EXTERNAL / Direct_Access) over the view | DIRECT_ACCESS, acceleration OFF, single PK `event_key` |
| DLO `A2A_Lab_Trace_Events_K__dll` | (auto-created by the stream) | single PK `event_key__c`, federates live rows |
| DMO `A2A_Lab_Trace_Event__dlm` | `POST /ssot/data-model-objects` | 11 fields, PK `event_key__c`, category Other |
| DLO→DMO field map | `POST /ssot/data-model-object-mappings` | 12 pairs, 1:1, no transform |
| **Trace-grain (§4c):** rollup VIEW `lab.trace_rollup_zc` | `observability.pg.DDL` + `pg_migrate.py` | one row per `trace_id`, agg pushed into Aurora, single-col PK, no jsonb |
| Zero-Copy stream `A2A_Lab_Trace_Rollup` | `POST /ssot/data-streams` (EXTERNAL / Direct_Access) over the rollup view | DIRECT_ACCESS, acceleration OFF, single PK `trace_id` |
| DLO `A2A_Lab_Trace_Rollup__dll` | (auto-created by the stream) | single PK `trace_id__c`, federates 960 traces |
| DMO `A2A_Lab_Trace_Rollup__dlm` | `POST /ssot/data-model-objects` | 9 fields, PK `trace_id__c`, category Other |
| DLO→DMO field map (rollup) | `POST /ssot/data-model-object-mappings` | 9 pairs, 1:1, no transform |

**Verified end-to-end:** `POST /ssot/queryv2` on the hop-grain DMO returns live
Aurora rows — the headline `hops/traces by protocol` aggregate and the two-level
trace-latency query (`AVG` over per-trace `SUM(latency)`) both federate correctly.
The trace-grain DMO `A2A_Lab_Trace_Rollup__dlm` federates **960 traces** with every
figure matching the pre-build numbers (avg 32,287 ms, success 0.8979, avg hops
3.15, avg depth 1.43, avg wall 249,877 ms, max 448,241 ms) — the `GROUP BY` runs
in Aurora, nothing materializes to eu-central-1.

### The one blocker and its fix — a VIEW, not the base table
The DLO→DMO mapping API refuses a composite primary key
(`INVALID_DMO: We do not support multiple Primary`), and Data Cloud reflects the
SOURCE table's real DB primary key regardless of what the stream declares — so a
stream over `lab.trace_events` (composite PK `trace_id,hop_seq,ts`) always lands
a composite DLO PK the mapping then rejects. A **view has no PK constraint**, so
the DLO keys on the declared single PK `event_key`. The view also **omits the two
jsonb payload columns**, so "raw wire bytes never leave us-east-1" is structural,
not merely unmapped. So §0's `event_key` surrogate is load-bearing after all.

**Acceleration is OFF** (the residency crux — `refreshConfig.isAccelerationEnabled:
false`): the query federates live to Aurora, rows stay in us-east-1. If dashboard
latency is ever judged unacceptable, turning acceleration on is a *deliberate,
documented* residency trade — record it in `plan/03-results.md`, don't flip it
silently. (The original hand-built composite-PK stream `A2A_Lab_Trace_Events` +
its DLO — superseded by the `_K` stream over the view — was **deleted 2026-08-09**
once the DMO was confirmed to federate solely from `A2A_Lab_Trace_Events_K__dll`.)

**Prereq permission for the Tableau work: Data Cloud Architect permission set.**

---

## 6c. Build the Tableau Next dashboard (UI, operator) + MEASURE THE NUMBER

Everything upstream is done. Build the dashboard over DMO
**`A2A_Lab_Trace_Event__dlm`**. The full content spec is
`WS19-items6-7-groundwork.md` §3 (dims/measures/tiles) and §4 (trace latency) —
this is the click-path.

> **Why this is the one UI step — headless ruled out across FOUR surfaces
> (2026-08-09):** (1) **Metadata API** — `AnalyticsWorkspace`/`AnalyticsVisualization`/
> `AnalyticsDashboard` ARE deployable types (the demo-builder made two workspaces
> headlessly, via Automated Process), but there is **no `SemanticModel` metadata
> type** — a workspace only *references* the SDM by name (`<assetType>SemanticModel`),
> it does not carry it. (2) **SSOT REST** — `/ssot/semantic-models`,
> `/semantic-data-models`, `/metrics` all 404. (3) **Tableau Next hosted MCP
> server** (`api.salesforce.com/platform/mcp/v1/analytics/tableau-next`) is
> **read-only** — `list_/get_semantic_model*`, `analyze_data`, no create/update of
> models, metrics, calc fields, dims, measures, dashboards, or viz. (4) **Headless
> 360 MCP** (Beta) `Dispatch` is a generic API dispatcher (GET/POST/PUT/PATCH over
> operations it can `Discover`/`Describe`) — it adds no capability, only invokes
> operations that EXIST, and none create an SDM; the doc says it "doesn't [cover]
> Tableau semantic models … or Data 360 objects." So the SDM is built in the UI;
> **the Tableau Next MCP server is the headless VERIFY + MEASURE path** once it
> exists (Step 3). If a semantic-model create API is ever published, Headless 360
> `Dispatch` could invoke it headlessly — nothing to invoke today.

### Step 1 — semantic model `Agent Interop Traffic` (UI — operator)
1. **Data Cloud / Tableau Next → Semantic Models → New** (or Tableau Cloud →
   **Data → New Semantic Model**), source = the DMO `A2A_Lab_Trace_Event__dlm`
   in the **default** data space.
2. **Direct dimensions** (groundwork §3a): `Trace Id`, `Hop Seq`, `Source`,
   `Target`, `Protocol`, `Status`, `Transport Detail`, `Platform Ref`, and
   `Event Time` (`ts_at__c` — Tableau derives day/hour/weekday parts natively).
   `Event Key` is the row identity; keep it available for detail rows.
   **Field reference conventions for every formula below** (verified against the
   live DMO 2026-08-09): in the Tableau Semantics calc editor fields are
   referenced by their **object-qualified label** —
   `[A2A Lab Trace Event].[Field]` (e.g. `[A2A Lab Trace Event].[Latency Ms]`).
   There is **no bare `COUNT()`** — count rows via the non-null PK,
   `COUNT([A2A Lab Trace Event].[Event Key])`. Create `Is Error` and `Is Ok Int`
   **first**; later formulas reference them by their own name (calculated fields
   are referenced unqualified, `[Is Ok Int]`). All DMO fields exist incl.
   `[A2A Lab Trace Event].[Event Ts Epoch]` (needed by `Avg Wall Ms`).
   **Dimension vs Measure — pick the type by the formula's OUTERMOST operation:**
   if it's an aggregate (`AVG`/`SUM`/`COUNT`/`COUNTD`/`PERCENTILE`) →
   **Measure** (and declare it UserAgg, item 4); if it returns a per-row value (a
   label, a bucket, a boolean, a 0/1) → **Dimension**. So everything in item 3 is a
   Dimension (`Target Platform`, `Direction`, `Hop Kind`, `Is Error`, `Is Ok Int`,
   `Latency Bucket`) and everything in items 4–5 is a Measure.

3. **Calculated dimensions** (§3b — New Calculated Field, type **Dimension**; all
   single-pass, safe live). `Target Platform` has the long formula — it is written
   out in full just below this list.
   - `Target Platform` — normalize `Target` to its platform token (full formula below).
   - `Direction` — `[A2A Lab Trace Event].[Source] + " → " + [A2A Lab Trace Event].[Target]`
   - `Hop Kind` — `IF [A2A Lab Trace Event].[Hop Seq] = 0 THEN "edge" ELSE "delegated" END`
   - `Is Error` (Boolean) — `[A2A Lab Trace Event].[Status] = "error"`
   - `Is Ok Int` (helper for Trace Success Rate; **hide** from dashboards) — `IF [A2A Lab Trace Event].[Status] = "ok" THEN 1 ELSE 0 END`
   - `Latency Bucket` (for the distribution tile — see Step 2 note; **NULL caught
     FIRST**, else the 434 no-latency infra hops all land in the top bucket) —
     ```
     IF ISNULL([A2A Lab Trace Event].[Latency Ms]) THEN "(no latency)"
     ELSEIF [A2A Lab Trace Event].[Latency Ms] < 1000 THEN "0–1s"
     ELSEIF [A2A Lab Trace Event].[Latency Ms] < 5000 THEN "1–5s"
     ELSEIF [A2A Lab Trace Event].[Latency Ms] < 15000 THEN "5–15s"
     ELSEIF [A2A Lab Trace Event].[Latency Ms] < 60000 THEN "15–60s"
     ELSE "60s+"
     END
     ```

**`Target Platform` formula.** Normalize `Target` to its platform token. Tokens
(from `config/targets.yaml`): claude / openai / agentforce / foundry / strands /
guide / google-adk. `STARTSWITH` absorbs the `-hosted`/`-a2a`/`-shim`/
`agentforce-openai-rest` suffixes; **`google-adk` before `adk`** matters. The two
leading exact-match arms **keep the Claude backends delineated** — a Managed
Agent, the Claude API invoking a model, and the AWS-hosted agent are genuinely
distinct things — and rename the `anthropic-*` targets to the `claude-*` labels:

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
orchestrators (`brief-researcher`, `a2alab-supply-orchestrator`) fall to the
`ELSE` and keep their own identity **by design** — they are real lab components we
trace as hops, not agent platforms to fold away. (Don't use `SPLIT([Target],"-",1)`
— it splits `google-adk-a2a`→"google" and `adk-logistics-a2a`→"adk", two labels
for one platform.)

**The rename is dashboard-only for now** (see WS21 in `plan/07-workstreams.md`).
These two labels live in the trace-event `target` string, stamped by lab code
(`src/platforms/claude/managed_backend.py`, `src/platforms/guide/core.py`,
analyst/briefs) and matched in the console (`index.html`, `console/app.py`).
Renaming at source is a separate cross-cutting change (code + console badges +
diagrams + the 3021 existing Aurora rows the live formula reads) — tracked as a
potential workstream, not done here. Until then the remap arms give the dashboard
the right names.
4. **Measures** (§3c — New Calculated Field, type **Measure**; single-pass
   aggregates, safe live). **Each formula already contains an aggregate, so
   declare it as a user-defined aggregation** ("AggregativeFunction-level
   calculated fields require UserAgg" — see §4/§5 note) rather than leaving it on a
   default aggregation:
   - `Hops` — `COUNT([A2A Lab Trace Event].[Event Key])`
   - `Traces` — `COUNTD([A2A Lab Trace Event].[Trace Id])`
   - `Errors` — `SUM(IF [A2A Lab Trace Event].[Status] = "error" THEN 1 ELSE 0 END)`
   - `Error Rate` (inline + `* 1.0` to force float, not integer division) —
     `SUM(IF [A2A Lab Trace Event].[Status] = "error" THEN 1 ELSE 0 END) * 1.0 / COUNT([A2A Lab Trace Event].[Event Key])`
   - `Avg Hop Latency` — `AVG([A2A Lab Trace Event].[Latency Ms])`
   - `P50 Hop Latency` — `PERCENTILE([A2A Lab Trace Event].[Latency Ms], 0.5)`
   - `P95 Hop Latency` — `PERCENTILE([A2A Lab Trace Event].[Latency Ms], 0.95)`
5. **Trace-grain latency — §4a IS OUT; a second federated DMO is the fix
   (§4c).** The two aggregation caveats surfaced on 2026-08-09 when the operator
   tried to build these:
   - **"AggregativeFunction-level calculated fields require UserAgg."** In
     Tableau Semantics a measure whose formula *already contains* an aggregate
     (`AVG`/`SUM`/`COUNT`/`PERCENTILE`, etc.) must be declared as a **user-defined
     aggregation** — it isn't left on a default/auto aggregation. This applies to
     **every** aggregated measure, incl. the §3c ones (`Hops`, `Errors`,
     `Avg Hop Latency`). Real, and fixable in the calc dialog.
   - **`FIXED` / LOD does not exist in Tableau Semantics — decisive.** The
     [Functions for Calculated Field Formula](https://help.salesforce.com/s/articleView?id=analytics.c360_a_sl_calc_fields_formula_functions.htm&type=5)
     reference lists a **flat, single-level** aggregation set (`AVG CORR COVAR
     COVARP COUNT COUNTD MEDIAN PERCENTILE STDEV STDEVP SUM VAR VARP`, plus
     `MIN`/`MAX` on a single field). There is **no `FIXED`, no `{ }` LOD, no
     `INCLUDE`/`EXCLUDE`** (verified 2026-08-09 against the full page). So
     `AVG({FIXED [Trace Id] : SUM([Latency Ms])})` can never validate — the
     construct isn't in the language. No UserAgg setting rescues it; the two-level
     "sum hops within a trace, then average across traces" aggregation cannot be
     expressed as a semantic-model calc over the hop-grain DMO.

   **The fix — federate a SECOND grain (§4c), the same view trick as the hop
   grain.** Rather than materialize a CI into eu-central-1 (the old §4b, which
   sacrifices the residency property §4a existed to protect), add a **trace-grain
   view** `lab.trace_rollup_zc` in Aurora — one row per `trace_id`, pre-aggregated
   *in Postgres* (`hop_count`, `max_depth`, `total_latency_ms`, `wall_ms`,
   `all_ok`, `edge_source`/`edge_target`, single-col PK `trace_id`) — and federate
   it as a **second Zero-Copy DMO** `A2A_Lab_Trace_Rollup__dlm` (Direct_Access,
   acceleration OFF). The six trace-grain measures then become **flat** aggregates
   over that DMO, each declared UserAgg, and stay **live-federated** — the
   grouping happens in Aurora at query time, **nothing materializes to
   eu-central-1**, and L5.8 stays a real cross-region measurement. This is the
   exact composite-PK→view pattern already proven for the hop grain.

   | Trace-grain measure (over the `Trace Rollup` DMO) | Formula (flat, UserAgg) |
   |---|---|
   | `Avg Trace Latency`   | `AVG([A2A Lab Trace Rollup].[Total Latency Ms])` |
   | `P95 Trace Latency`   | `PERCENTILE([A2A Lab Trace Rollup].[Total Latency Ms], 0.95)` |
   | `Avg Wall Ms`         | `AVG([A2A Lab Trace Rollup].[Wall Ms])` |
   | `Avg Hops Per Trace`  | `AVG([A2A Lab Trace Rollup].[Hop Count])` |
   | `Max Depth Per Trace` | `AVG([A2A Lab Trace Rollup].[Max Depth])` |
   | `Trace Success Rate`  | `AVG([A2A Lab Trace Rollup].[All Ok Int])` (view emits `all_ok::int`) |

   **Status: ✅ BUILT and live-verified 2026-08-09.** The view DDL was the one
   gated step (`pg_migrate.py`, 26/26 statements); the stream `A2A_Lab_Trace_Rollup`
   → DLO `A2A_Lab_Trace_Rollup__dll` → DMO `A2A_Lab_Trace_Rollup__dlm` → 9-pair
   mapping were built headless via the proven SSOT REST calls. The DMO federates
   **960 traces** through `queryv2` with every number matching the pre-build
   figures exactly (avg 32,287 ms, success 0.8979, avg hops 3.15, avg depth 1.43,
   avg wall 249,877 ms, max 448,241 ms). Nothing materialized to eu-central-1 — the
   `GROUP BY` runs in Aurora at query time. The old §4b calculated-insight
   template (`tmp-docs/ws19-md/TEMPLATE_A2A_Lab_Trace_Rollup.mktCalcInsightObjectDef`)
   is superseded — keep only if a future need for a *materialized* rollup appears.

### Step 2 — dashboard tiles (§3d), all LIVE

**How the pieces fit (the part that isn't obvious): in Tableau Next a
Visualization and a Dashboard are SEPARATE assets.** Unlike classic Tableau
(worksheets living inside one workbook), here you build each chart in the
**Visualization Builder** and **Save** it as its own asset in the workspace, then
build a **Dashboard** asset and **drag the saved visualizations onto its canvas**.
So each tile below = one Visualization you author + save first; the dashboard is
the last thing you assemble. (You *can* also drop a bare metric/KPI straight on a
dashboard, but every chart tile is a saved Visualization.)

**2a. Build each Visualization** (Workspace → **New → Visualization**, on the
`Agent Interop Traffic` semantic model). For each: pick the mark type, drop fields
on the shelves as noted, **Save** with the name given. Expected values to sanity-
check against are in the "Verified numbers" box after this step.

| Save as | Mark type | Columns / Rows shelf | Color / other | Verified check |
|---------|-----------|----------------------|---------------|----------------|
| **Hops by Protocol × Platform** *(headline — the L5.8 tile)* | Heatmap (or bar) | Cols = `Target Platform`, Rows = `Protocol` | Color = `Hops` | grand total = 3021 |
| **Status by Platform** | Bar (stacked) | Cols = `Target Platform`, Rows = `Hops` | Color = `Status` | only `ok`(2850)/`error`(171) — no `pending` |
| **Hop-Latency Distribution** | Bar (over `Latency Bucket`) — see note; Tableau Next has **no Histogram mark** | Cols = `Latency Bucket`, Rows = `Hops` | — ; add `P50 Hop Latency`/`P95 Hop Latency` as KPI text | 0–1s 510 · 1–5s 544 · 5–15s 781 · 15–60s 712 · 60s+ **40** · (no latency) **434** |
| **Traffic Over Time** | Line | Cols = `Event Time` (by Day), Rows = `Hops` | Color = `Protocol` | — |
| **Direction Matrix** *(call graph)* | Heatmap | Cols = `Target`, Rows = `Source` | Color = `Hops` | dense grid; edge hops dominate |
| **Avg Trace Latency** *(KPI)* | Text / big-number | Rows = `Avg Trace Latency`, **no dimension** | — | ≈ 32,287 ms |
| **Trace Success Rate** *(KPI)* | Text / big-number | Rows = `Trace Success Rate`, **no dimension** (format as %) | — | ≈ 0.898 |
| **Avg Hops per Trace by Platform** | Bar | Cols = `Target Platform`, Rows = `Avg Hops Per Trace` | Color = `Target Platform` (optional) | ≈ 3.15 overall |
| **Max Depth per Trace by Platform** | Bar | Cols = `Target Platform`, Rows = `Max Depth Per Trace` | Color = `Target Platform` (optional) | ≈ 1.43 overall |

**Setting the "Color / other" column.** That column is the **visual encoding** — a
field attached not to an axis (Columns/Rows) but to how the marks *look*. Put the
axis fields on **Columns**/**Rows** first, then find the **Color** well on the
**Marks card** (in the Cards pane — "customize the look and feel") and **drag the
named field onto it**: `Color = Hops` shades the heatmap cells by hop count (what
makes it a heatmap, not a blank grid); `Color = Status` splits each bar into
`ok`/`error` segments (what makes it *stacked*); `Color = Protocol` gives one line
per protocol. If the builder shows no labelled Color well, the equivalent is a
dropdown on the field pill (**Use as → Color**) or the right-hand encoding panel —
same outcome, field drives color. The "other" entries are non-color extras: for
**Hop-Latency Distribution**, add `P50/P95 Hop Latency` as reference lines or KPI
text rather than an encoding.

**The four trace-latency tiles are four separate Visualizations, and they need
the §4c trace-grain DMO first.** A "KPI" in Tableau Next is just a Visualization
with **one measure and no dimension** — drop the measure alone and it collapses to
the grand-total big number. But the four measures (`Avg Trace Latency`, `Trace
Success Rate`, `Avg Hops Per Trace`, `Max Depth Per Trace`) **cannot be built over
the hop-grain DMO** — Tableau Semantics has no `FIXED`/LOD, so trace-grain
aggregation can't be expressed there (§4c, confirmed 2026-08-09). They come from
the **second DMO** `A2A_Lab_Trace_Rollup__dlm` as flat `AVG`/`PERCENTILE` measures
(§4c table). That DMO is now **✅ BUILT and federating 960 traces (2026-08-09)** —
the rollup view + stream + DLO + DMO + mapping are all live and verified. To build
the four tiles: point a new semantic model (or a second data source in the same
model) at `A2A_Lab_Trace_Rollup__dlm`, define the six flat measures from the §4c
table (each declared UserAgg), then build the two KPIs with the measure on Rows
and no dimension; build the two bars with `Target Platform` on Columns (the rollup
view carries `edge_target` → map it to `Target Platform` the same way). Save each
with its own name, then drag all four onto the dashboard in Step 2b. **All nine
tiles are now buildable** — the five hop-grain over `A2A_Lab_Trace_Event__dlm`, the
four trace-grain over `A2A_Lab_Trace_Rollup__dlm`.

**Building the Hop-Latency Distribution (there is no Histogram mark type).**
Tableau Next's Visualization Builder has no "Histogram" mark — a histogram is just
a bar chart over a binned dimension, so build it as one. Three ways, preferred
first:

1. **Bar over the `Latency Bucket` calc dim (recommended, and what the table
   above assumes).** Add the `Latency Bucket` dimension (§3 formula) to the model,
   then: Mark type **Bar**, Columns = `Latency Bucket`, Rows = `Hops`. Fixed,
   labelled, human buckets (`0–1s … 60s+` + `(no latency)`) — and it puts the
   NULL-latency hops in their own bar instead of hiding the units problem.
2. **Native binning on `Latency Ms` (`Create Bin`).** Tableau Next's built-in
   binning, per the [Create Bins from Measure Values](https://help.salesforce.com/s/articleView?id=analytics.tua_viz_create_bins_how_to.htm&type=5)
   doc — needs the **Tableau Unmetered Platform Analyst** or **Tableau Next
   Platform Analyst** permission set. Steps: in the **Data pane**, open the
   `Latency Ms` field's context menu → **Create Bin** → in the **Create Numeric
   Bin** window enter a name and (optional) description → enter a **bin size**
   (e.g. `5000` = 5 s) → **preview** the ranges and adjust the size → **Save** →
   drop the bin field on **Columns** with `Hops` on **Rows**. Auto-bins are
   equal-width, and the axis is raw **milliseconds** — this is exactly what
   produced the confusing "buckets up to 33k" read (33k = 33,000 **ms** = 33 s).
   Two caveats vs Option 1: (a) label or format the bin so the axis reads in
   seconds, or reviewers misread ms as a small unit; (b) equal-width bins can't
   give the human `0–1s / 1–5s / 5–15s …` breakpoints — the `Latency Bucket`
   calc dim can, which is why Option 1 is preferred. NULLs land in their own bin
   here too, so the units problem stays visible either way.
3. **Skip the distribution, show percentiles.** The shape is well summarized by
   three KPI tiles — `Avg Hop Latency`, `P50 Hop Latency`, `P95 Hop Latency` —
   which is often the clearer executive read anyway.

**Why the first attempt "seemed off" (it wasn't — two things compounded).**
(a) **Units.** `Latency Ms` is **milliseconds**. Agent calls genuinely take tens
of seconds (Agentforce actions measure ~85–90 s, CLAUDE.md), so latencies really
do range 0 → ~105,000 ms. A raw-ms histogram axis showing "33k" means **33
seconds**, not an implausibly huge number — the distribution was correct, the
axis was just unlabelled ms. (b) **NULLs.** 434 of 3021 hops have **no latency**
(`web` 299, `obs-store` 135 — infra hops that don't record it). A bucket formula
ending in a bare `ELSE "60s+"` dumps every NULL into the top bar, inflating "60s+"
from its true **40** to 474. The §3 `Latency Bucket` formula catches `ISNULL`
**first** for exactly this reason. (If you'd rather drop them entirely, add a
dashboard filter `Latency Ms IS NOT NULL` — but keeping the `(no latency)` bar is
more honest about which hops are un-timed.)

Notes: the **headline heatmap uses `Target Platform` (calc dim) not raw `Target`**
so cells group by platform, not platform×protocol. Every tile stays **live**
(federated, no materialization — hop grain over `__dlm`, trace grain over the §4c
rollup DMO) so the L5.8 render is real. The Status tile has **two** categories
only — the spec's "ok/error/pending" was aspirational; there is no `pending` in
the data (verified 2026-08-09).

**2b. Build the Dashboard** (Workspace → **New → Dashboard**): drag each saved
Visualization from the asset panel onto the canvas; arrange with the headline
**Hops by Protocol × Platform** top-left. Add a dashboard-level **filter** on
`Event Time` and/or `Target Platform` if you want interactivity (optional). Save.

> **Verified numbers (live DMO, 2026-08-09) — your tiles should reproduce these:**
> Hops 3021 · Traces 960 · Errors 171 · Error Rate 5.66% · Avg Hop Latency
> 11,807 ms · P50/P95 Hop 7,095/38,921 ms · Avg Trace Latency 32,287 ms · P95
> Trace 101,983 ms · Avg Hops/Trace 3.15 · Max Depth 1.43 · Trace Success 89.8% ·
> Avg Wall 249,877 ms. (Tableau `PERCENTILE` may be exact vs the approx used to
> check — magnitudes will match, exact percentiles may differ slightly.)

### Step 3 — MEASURE the L5.8 number (headless via the Tableau Next MCP server)
**Measure the cold end-to-end federation round trip — the real EU→US leg** (org +
Data Cloud tenant co-located in eu-central-1 → federated query to Aurora in
us-east-1 and back). Two ways, prefer the first:

- **Headless (preferred) — the Tableau Next hosted MCP server.** Enable it in
  Setup, then `analyze_data` fires a natural-language question at the semantic
  model through the Analytics Agent, executing the same live federation the
  headline tile would; time it cold. This is a scriptable, repeatable measurement
  and doesn't depend on the dashboard UI. (`api.salesforce.com/platform/mcp/v1/
  analytics/tableau-next`, OAuth, read-only — query only, which is all a
  measurement needs.) I can run this once you've built the SDM and enabled the
  server.
- **UI fallback.** Open the dashboard cold (or force-refresh the headline live
  tile), note wall-clock to rendered.

Record in `plan/03-results.md` with the date and topology note — a genuine
cross-region federation latency, not a synthetic benchmark (org and tenant are
in-region; the tenant↔store hop is the honest cross-continent leg). Then fill the
§7a `[N]` bracket below.

---

## Item 7 groundwork (drafted — finalize after 6c)

### 7a. Matrix finding — READY TO PASTE into `plan/02-matrix.md` "Findings ledger"
Fill the one bracket with the 6c number, then append. Drafted so the shape is
right and only the measurement is pending (the ledger is "grow as measured", so
it is deliberately NOT appended yet):

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
>   because acceleration is off (item 6 step 8). The connector reaches the store
>   from the Data 360 Services egress IPs, NOT `ip-ranges.salesforce.com` (D70).

### 7b. Console entry point — SCOPE (build after 7a)
The console is one-canvas-per-section off the Control Panel (D57). Data 360 /
Tableau is a **reporting** surface over the obs store — it belongs in the
**Infrastructure** category alongside Observability and Architecture (it answers
"how is the lab's own data surfaced back to Salesforce"), NOT in Experiments
(it's not a protocol cell) and NOT in DevOps.

Two viable shapes — pick when building:
- **A new "Data 360" nav item** under Infrastructure with its own `view.type`
  (e.g. `datacloud`), following the D57 canvas template: the THING is the
  federation (a small diagram: console viewer + Tableau both reading
  `lab.trace_events`, reusing the L5.8 mermaid), the **Details** sub-tab narrates
  what's deployed — the connection, the **federation view `lab.trace_events_zc`**
  (scalars + `event_key`, no jsonb), the **Zero-Copy stream over the view** with
  acceleration OFF, the DMO `A2A_Lab_Trace_Event__dlm`, and the DLO→DMO map —
  how it reads (`lab_reader`, Zero Copy DIRECT_ACCESS federation, the 5432/TLS
  path, 15s statement_timeout), **why a view not the base table** (composite-PK /
  single-PK mapping constraint), and cites **D69/D70/plan/09 L5.8** so the chips
  linkify. A link out to the Tableau dashboard if it can be shared.
- **A tab on the existing Observability section** ("Dashboard | Observability
  Analysis | Cost Analysis" → add "Data 360 View") — cheaper, but D57 says top
  tabs name THINGS as peers, and the Data 360 view is arguably a peer thing, so
  a dedicated nav item is the more honest shape.

Recommendation: **dedicated nav item under Infrastructure** — it's a distinct
deployed thing with its own Details story, and nesting it under Observability
would imply it's a facet of the lab's own viewer when the finding is precisely
that they are two *independent* views over one table.

Reminders when building 7b (from CLAUDE.md, the parts that bite):
- Anything the new handler imports/reads/opens must be a `COPY` target in
  `deploy/console/Dockerfile` — or it 500s only on that route, hosted.
- Cite real `D<n>`/`plan/*.md` in the Details markdown or no chips render.
- Update the diagram in all three places if it asserts the federation shape:
  `plan/09` L5.8, `config/diagrams.yaml`, and the `*_DIAGRAM` consts in
  `index.html`.
- Console redeploy for any of this to appear hosted is a **full rebuild**
  (`plan/`, `config/`, `src/` are baked in by `COPY`).

---

## Inline Tableau Next embed — built 2026-08-09 (owner-only, server-side auth)

The console now renders the dashboard **inline** for the owner, no login step,
via the Tableau Next Embedding SDK. The backend mints the session server-side:
client-credentials token (as the ECA run-as user) → `/services/oauth2/singleaccess`
→ short-lived frontdoor URL → SDK `authCredential`. Proven by a live spike: the
token minted AS the admin and `/singleaccess` accepted it — it failed only on
`403 Invalid_Scope` because no existing ECA carries the **`web`** scope.

**What's built (headless, in the repo — updated 2026-08-09):**
`/api/tableau/frontdoor` (owner-gated) + `tableau_next.embed` in `/api/config`; the
SDK loader + `<analytics-dashboard>` mount in `index.html`; the frontdoor is minted
via **JWT-bearer** (see the RESOLVED section below — client-credentials can't reach
`/singleaccess`); `CorsWhitelistOrigin` metadata for the console origin (DEPLOYED);
the deep link now points at the in-org tab `/lightning/n/A2A_Lab_Traffic` (carries
the licence + asset-share context); the `A2A_Lab_Home` App Builder page (DEPLOYED);
diagrams + plan/09 + README + Details pane updated; tests green (77 console +
env-contract). The `a2a_lab_tab_embed` ECA was created **fully headlessly** (four
metadata files inc. the JWT public cert on global-OAuth settings). Remaining
operator step: the console AWS full-rebuild redeploy (blocked on `aws sso login`).

**The `auraCmpDef` 504 was NOT a platform bug — it was permissions + asset
sharing (RESOLVED 2026-08-09).** The Tableau Next runtime returned a generic
504/runtime error because the viewing user lacked the licenses/perm sets and the
dashboard asset wasn't shared. Every user who views the embed (including an admin
testing the live app) needs BOTH:
- a **Tableau Next** perm set — `Tableau Unmetered Admin` (assigned to the owner),
  or `Tableau Next Consumer (Unmetered)` / `Tableau Next Included App Business
  User` for view-only business users; AND
- a **Data 360** perm set — `Data Cloud User` (or `Data Cloud Architect`).
  Without it the backend data engine can't run the dashboard's underlying queries
  and throws generic runtime errors — this was the actual failure.
Assign at Setup → Users → Users → *user* → Permission Set Assignments. THEN, in
Tableau Next (App Launcher), open the workspace holding the dashboard → **Share /
Manage Access** on the asset → grant the target users/roles/groups (owner shared
view access to all users 2026-08-09); without asset read access the LWC can't
fetch metadata. This applies identically to the inline SDK embed — same runtime,
same license/sharing gate — so a new console viewer needs the perm sets + asset
share, not just the a2a_lab_embed OAuth wiring.

## Inline embed auth = JWT-BEARER, not client-credentials (RESOLVED 2026-08-09)

The `a2a_lab_tab_embed` ECA was created headlessly (four metadata files,
`sf project deploy` — see [[check-headless-build-paths-first]]; the earlier
"ECA is UI-only" claim was a mistake). But **client-credentials is a dead end for
the frontdoor**: Salesforce's client-credentials flow issues the `api` scope
ONLY — it never grants `web` (and rejects a `scope=web` parameter with
`invalid_request: scope parameter not supported`), while
`POST /services/oauth2/singleaccess` REQUIRES `web`. So a CC token gets
`403 Invalid_Scope`. The prior "CC just works, JWT unnecessary" note was wrong —
that spike got this same 403 and misread it.

**Fix, proven live 2026-08-09: the JWT Bearer flow.** It runs in user context
(the `sub` user) so its token carries the ECA's assigned scopes including `web`.
Fully headless — the signing cert attaches to the ECA via the `certificate` field
on `ExtlClntAppGlobalOauthSettings` (PEM, Metadata API v60+), NO Setup upload.
Built + deployed: RSA-2048 keypair (`.a2alab/tab_embed_jwt_{private,cert}.pem`,
gitignored), cert deployed to the ECA. Live test: JWT (`iss`=consumer key,
`sub`=run-as user, `aud`=`https://login.salesforce.com`, `exp`=+180s, RS256) →
`/services/oauth2/token` returned `scope: web api` → `/singleaccess` returned
**HTTP 200 with `frontdoor_uri`**. Chain works end to end.

**TODO (operator — Ryan does later, per SF security policy): create a dedicated
minimal-privilege INTEGRATION USER as the JWT `sub` / run-as, instead of the
System Administrator.** Right now the ECA's `clientCredentialsFlowUser` /
JWT `sub` is `admin@a2a-lab.07092026.demo` (full sysadmin), so a minted token can
hit the REST API as the admin, not just mint frontdoor URLs. Tighter posture: an
integration user with ONLY the Tableau Next perm set (`Tableau Next Consumer
(Unmetered)` or similar) + `Data Cloud User` + share the dashboard asset to it,
then set it as the ECA run-as (`clientCredentialsFlowUser` in the `.ecaOauthPlcy`)
and as the JWT `sub`. Verify the chain still returns `web`-scoped + 200 after the
swap. Until then the sysadmin run-as is the working stopgap.

## Deliberate publishes still pending (operator runs these)
- `uv run python scripts/jira_sync.py` (dry run, read the diff) then `--apply`.
- Retrieve/track the `a2a_lab_tab_embed` `.ecaOauth`/`.ecaOauthPlcy` like the 5
  siblings; record its key in the (encrypted) `f6-eca-wiring.md`; `chezmoi
  add --encrypt` the JWT private key.
- Create the dedicated integration user (above) and repoint the ECA run-as/`sub`.
- Deploy the CORS origin metadata for `console-lab.agenticthings.com`.
- Console **full-rebuild** redeploy so D70 / item-5-done / L5.8 / the inline
  embed / any 7b work appears on the hosted console.
