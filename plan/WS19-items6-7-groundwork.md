# WS19 item 6 groundwork — DLO→DMO mapping, semantic model, calculated insight

Companion to `plan/WS19-items6-7-steps.md` (§6b/6c). The steps doc has the UI
click-path; this doc is the **content** the operator fills into those UI steps
and the record of what got built. Headless-authored, grounded in the live schema
(`src/observability/pg.py` `DDL[trace_events]`). Plan-local scratch — delete with
the checkpoint when WS19 lands.

**Scope now:** `lab.trace_events` only. The store has five more tables
(`obs_sessions`, `obs_events`, `obs_briefs`, `usage_events`, `obs_harvest`) that
are deliberate **later** streams/DLOs — §"Later tables" sketches how each extends
this dashboard so the semantic model grows without rework, not so we build them
now.

---

## 0. Prerequisite code change — give the DLO a single-column primary key ✅ DONE (in code)

`lab.trace_events` has a **composite** PK `(trace_id, hop_seq, ts)` — one row is
one hop. A Data Cloud DLO wants a **single** field as primary key. Rather than a
DLO-side formula/composite key (opaque, lives only in Data Cloud), a stable
surrogate is added as a **generated column** on the table — the repo owns its
schema (`pg_migrate.py` applies DDL as owner; `ROLE_GRANTS` already `SELECT`s
every column for `lab_reader`), so the key is a repo artifact, not UI state.

**Added to `DDL` in `src/observability/pg.py`** (additive, idempotent, never
written by `PostgresSink` — Postgres computes it; dry-run verified):

```sql
ALTER TABLE lab.trace_events
  ADD COLUMN IF NOT EXISTS event_key text
  GENERATED ALWAYS AS (trace_id || '#' || hop_seq::text || '#' || ts::text) STORED;
```

`STORED` (not virtual) so a federated `SELECT` returns it without recomputation.
`event_key` becomes the DLO **Primary Key** in step 6 of the steps doc; it is
also the stable per-hop row identifier Tableau needs for detail rows.

**Operator apply path (touches the live cluster — see UI runbook step 1):**
`uv run python scripts/pg_migrate.py`, then verify with
`SELECT event_key FROM lab.trace_events LIMIT 1`.

---

## 1. DLO field spec (`A2A Lab Trace Events`)

Category **Other** (engagement traces, not profile/party data). 1:1 with the
scalar columns; the two `*_payload_raw` **jsonb** columns are **not mapped** —
they are the raw wire evidence the console renders, they never need to be in Data
Cloud, and keeping them out is what preserves the "raw rows never leave
us-east-1" claim even if acceleration is ever reconsidered.

| Source column        | DLO field         | Data type        | Role in model            | Notes |
|----------------------|-------------------|------------------|--------------------------|-------|
| `event_key`          | Event Key         | Text             | **Primary key**          | generated (§0) |
| `trace_id`           | Trace Id          | Text             | dimension / rollup key   | groups hops into one call |
| `hop_seq`            | Hop Seq           | Number           | dimension / measure      | 0 = edge hop |
| `ts_at`              | Event Time        | Date/Time        | **event time field**     | timestamptz; the time index |
| `ts`                 | Event Ts Epoch    | Number           | (skip in model)          | redundant with `ts_at`; keep only if needed for `wall_ms` math in the CI |
| `source`             | Source            | Text             | dimension                | who called |
| `target`             | Target            | Text             | dimension                | who was called |
| `protocol`           | Protocol          | Text             | dimension                | rest / mcp / a2a / agentforce-api / internal |
| `transport_detail`   | Transport Detail  | Text             | dimension (detail)       | e.g. "tools/call ask" |
| `status`             | Status            | Text             | dimension                | ok / error / pending |
| `latency_ms`         | Latency Ms        | Number           | **measure**              | per-hop latency |
| `platform_ref`       | Platform Ref      | Text             | dimension (detail)       | native execution id (M11 join key) |
| `inserted_at`        | Inserted At       | Date/Time        | (skip in model)          | ingest bookkeeping, not analytical |

**No transform** on any field — Zero Copy = no ETL (steps doc §6b). Mapping is
straight passthrough; all derivation happens in the semantic model (§3) or the CI
(§4), never in the DLO.

---

## 2. DLO → DMO mapping

Map the DLO to a DMO **`Agent Trace Event`** (the modeled object Tableau Next and
segments consume). 1:1, same field set, same primary key (`Event Key`), event
time field = `Event Time`. Keep the DMO a single object for the starter — the
other tables (§"Later tables") become their own DMOs with relationships to this
one on `Trace Id` / `Platform Ref` when they come online.

---

## 3. Tableau Next semantic model — `Agent Interop Traffic`

Over the `Agent Trace Event` DMO. Everything here is computed **live at query
time** (federated to Aurora) — no materialization, which is what keeps the L5.8
render-latency measurement real.

### 3a. Dimensions (direct)
`Trace Id`, `Hop Seq`, `Source`, `Target`, `Protocol`, `Status`,
`Transport Detail`, `Platform Ref`, and `Event Time` (Tableau derives
day/hour/weekday parts natively — no calc field needed for date bucketing).

### 3b. Calculated dimensions (query-time, single-pass — safe live)
| Name             | Definition (semantic-model calc)                                   | Why |
|------------------|--------------------------------------------------------------------|-----|
| `Target Platform`| strip the protocol suffix off `Target` (`claude-rest`→`claude`, `agentforce-a2a`→`agentforce`); `CASE` on the known set, default = `Target` | group cells by platform, not by platform×protocol |
| `Direction`      | `Source + " → " + Target`                                          | the edge, for a flow/matrix viz |
| `Hop Kind`       | `IF [Hop Seq] = 0 THEN "edge" ELSE "delegated" END`                | surfaces the D27 delegation story |
| `Is Error`       | `[Status] = "error"`                                               | boolean for error-rate measure |

`Target Platform` is a small `CASE` for now; if it sprawls, promote it to a
**mapping DMO** generated from `config/targets.yaml` + `plan/09` (platform,
protocol, cloud) and relate on `Target` — the Data Cloud-idiomatic form, and it
would add a `Cloud` dimension for free. Noted as an extension, not built now.

### 3c. Measures (query-time aggregates — single-pass, safe live)
| Name              | Aggregate                                  |
|-------------------|--------------------------------------------|
| `Hops`            | `COUNT()`                                  |
| `Traces`          | `COUNTD([Trace Id])`                       |
| `Errors`          | `SUM(IF [Is Error] THEN 1 ELSE 0 END)`     |
| `Error Rate`      | `[Errors] / [Hops]`                        |
| `Avg Hop Latency` | `AVG([Latency Ms])`                        |
| `P50 Hop Latency` | `PERCENTILE([Latency Ms], 0.5)`            |
| `P95 Hop Latency` | `PERCENTILE([Latency Ms], 0.95)`           |

### 3d. Starter dashboard tiles (all live)
- **Hops by Protocol × Target Platform** (heat/bar) — the headline traffic tile;
  keep this one **live** so its render is the L5.8 EU→US measurement (item 6c).
- **Status breakdown** by Target Platform (stacked bar; `ok`/`error`/`pending`).
- **Hop-latency distribution** (histogram of `Latency Ms`) + P50/P95 tiles.
- **Traffic over time** (`Event Time` by day, split by Protocol).
- **Direction matrix** (`Source` × `Target`, colored by `Hops`) — the call graph.

---

## 4. Trace-grain latency — a SECOND federated DMO over a rollup VIEW (§4c)

End-to-end **trace latency** is the one metric a single-pass measure computes
**wrong**: it is *sum the hops within each trace, then average across traces* — a
two-level aggregation. A flat `AVG([Latency Ms])` averages **hops**, not traces.

### 4a. RULED OUT — level-of-detail (LOD) calc in the semantic model
The original plan was a `FIXED` LOD in the semantic model (inner aggregation
pinned to trace grain, outer rolls across traces — live, nothing materialized).
**This is impossible in Tableau Semantics** (confirmed 2026-08-09): the
[Functions for Calculated Field Formula](https://help.salesforce.com/s/articleView?id=analytics.c360_a_sl_calc_fields_formula_functions.htm&type=5)
reference has a **flat, single-level** aggregation set only — `AVG CORR COVAR
COVARP COUNT COUNTD MEDIAN PERCENTILE STDEV STDEVP SUM VAR VARP` (+ `MIN`/`MAX`
on a single field). There is **no `FIXED`, no `{ }` LOD, no `INCLUDE`/`EXCLUDE`**.
So `AVG({FIXED [Trace Id] : SUM([Latency Ms])})` never validates — the operator
hit exactly this. (Separately, every aggregated measure must be declared a
**user-defined aggregation** / "UserAgg" — real, but orthogonal; no UserAgg
setting brings back `FIXED`.)

### 4b. RULED OUT — calculated insight (materializes into eu-central-1)
A CI on `trace_id` grain would produce the right numbers, but it **materializes**
its aggregate into Data Cloud (eu-central-1) on a schedule — a real in-region copy
of derived data, sacrificing the residency property. Superseded by §4c, which
gets the same numbers while staying live. (Template kept only if a materialized
rollup is ever wanted for another reason.)

### 4c. CHOSEN — federate a rollup VIEW as a second Zero-Copy DMO ✅ BUILT (2026-08-09)
Push the two-level aggregation **down into Aurora** as a view, and federate it
the same way the hop grain is (composite-PK→view trick, §"the one blocker"): one
row per `trace_id`, single-col PK, Direct_Access, acceleration OFF. The grouping
runs in Postgres at query time — **nothing materializes to eu-central-1**, and the
L5.8 render stays a real cross-region measurement. The view definition IS the
rollup SQL:

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
Add to `observability.pg.DDL` (applied by `pg_migrate.py` — the one gated,
live-cluster step); then stream → DLO → DMO `A2A_Lab_Trace_Rollup__dlm` via the
same headless SSOT REST calls as the hop grain. The six trace-grain measures
become **flat** aggregates over that DMO (steps doc §4c table): `AVG([Total
Latency Ms])`, `PERCENTILE([Total Latency Ms], 0.95)`, `AVG([All Ok Int])` (=
Trace Success Rate), `AVG([Hop Count])`, `AVG([Max Depth])`, `AVG([Wall Ms])` —
each declared UserAgg. `edge_target` maps to `Target Platform` for per-platform
trace tiles.

**BUILT and live-verified 2026-08-09** — the whole chain is headless, exactly the
hop-grain pattern:

| Artifact | How built | Status |
|----------|-----------|--------|
| Rollup view `lab.trace_rollup_zc` | `observability.pg.DDL` + `pg_migrate.py` (26/26 statements) | ✅ DONE — one row per `trace_id`, single-col PK, no jsonb |
| Zero-Copy stream `A2A_Lab_Trace_Rollup` | `POST /ssot/data-streams` (EXTERNAL / Direct_Access) over the view | ✅ ACTIVE — DIRECT_ACCESS, acceleration OFF, single PK `trace_id` |
| DLO `A2A_Lab_Trace_Rollup__dll` | (auto-created by the stream) | ✅ federates 960 traces (`queryv2` count) |
| DMO `A2A_Lab_Trace_Rollup__dlm` | `POST /ssot/data-model-objects` | ✅ 9 fields, PK `trace_id__c`, category Other |
| DLO→DMO field map | `POST /ssot/data-model-object-mappings` | ✅ 9 pairs, 1:1 |
| **DMO federates from Aurora** | `POST /ssot/queryv2` on `__dlm` | ✅ VERIFIED — 960 traces, avg 32,287 ms, success 0.8979, avg hops 3.15, avg depth 1.43, avg wall 249,877 ms, max 448,241 ms — all match the pre-build figures exactly |

Nothing materialized to eu-central-1 — the `GROUP BY` runs in Aurora at query
time (the DMO issues a nested aggregate over the view), so L5.8 stays a real
cross-region measurement.

**Numbers already proven** (the nested SQL federates today — avg ≈ 32.3 s, max ≈
448 s over 960 traces, verified 2026-08-09; re-verified through the DMO after the
build); the view just relocates that
aggregation into a queryable object. **Statement-timeout note:** a trace-grain
scan aggregates the whole table, so it is the query most likely to approach
`lab_reader`'s 15 s `statement_timeout` as the table grows — if it bites, bump the
timeout deliberately in `observability.pg.ROLE_GRANTS` (via `pg_migrate.py`),
never silently.

**Keep the hero traffic tile (§3d) live** — both DMOs stay federated (no
materialization), so the item-6c latency number measures a real EU→US round trip.

---

## What is headless vs UI — BUILT against the org (2026-08-09)

**Everything through the DMO is headless and DONE.** The whole Zero-Copy chain —
federation view → keyed DLO → DMO → DLO-DMO mapping — was built via the Data
Cloud **SSOT REST API** (`sf api request rest`, Core token), not the Metadata
API. Only the Tableau Next semantic model + dashboard remain — and the
**semantic-model CREATE step is genuinely UI-only**, confirmed across all three
headless surfaces (2026-08-09): Metadata API has `AnalyticsWorkspace`/
`AnalyticsVisualization`/`AnalyticsDashboard` but **no `SemanticModel` type** (a
workspace only references the SDM by name); SSOT REST `/ssot/semantic-models`
et al. 404; and the **Tableau Next hosted MCP server is read-only** (discovery +
`analyze_data`, no authoring). The MCP server IS, however, the headless way to
**verify** the built model and **measure** the L5.8 round trip — see the steps
doc §6c Step 3.

### Correction: DMO creation IS headless (the Metadata API was the wrong surface)

The earlier note "DMO creation is UI-only, no metadata type" was **wrong**. There
is no DMO-create *Metadata API* type, but that is one surface out of several. The
`build-data360-demo` skill creates DMOs (and maps them, and builds CIs/segments)
via the SSOT REST API — which is how this very org was originally provisioned.
The working calls (all live-verified on `a2alab-prod`, 2026-08-09):

- **Create DMO** — `POST /ssot/data-model-objects` with `{name (dev name, NO
  __dlm suffix), label, category, dataSpaceName, fields:[{name,label,dataType,
  isPrimaryKey}]}`. Platform appends `__dlm` and the `KQ_*`/system fields.
- **Map DLO→DMO** — `POST /ssot/data-model-object-mappings?dataspace=default`
  with `{sourceEntityDeveloperName, targetEntityDeveloperName, fieldMapping:
  [{sourceFieldDeveloperName, targetFieldDeveloperName}]}`. DLO fields carry
  `__c`.
- **Create Zero-Copy stream** — `POST /ssot/data-streams`, `datastreamType:
  EXTERNAL`, `dataAccessMode: Direct_Access`, `connectorInfo.connectorType:
  DataConnector`, `advancedAttributes:{database,schema,object}`.
- **DELETE** needs the `sf api request rest --file` wrapper (`{"method":
  "DELETE","url":...,"body":{"mode":"raw","raw":""}}`); a stream delete also
  needs `?shouldDeleteDataLakeObject=true`.

### The one real blocker and its fix — a federation VIEW, not the base table

The mapping API **refuses a composite DLO primary key** (`INVALID_DMO: We do not
support multiple Primary`). And Data Cloud **reflects the source table's actual
DB PRIMARY KEY** and forces the DLO to key on it — so any stream over
`lab.trace_events` (composite PK `trace_id,hop_seq,ts`) ALWAYS lands a composite
DLO PK the mapping then rejects. Declaring a single PK in the create body is
ignored; adding a unique index on `event_key` is also ignored (Data Cloud keys on
the declared PK constraint, not any unique index).

**Fix: point the stream at a VIEW.** `lab.trace_events_zc` (added to
`observability.pg.DDL`, applied by `pg_migrate.py`) is `SELECT`-only over the
scalar columns + `event_key`, and a view has **no PK constraint** — so Data Cloud
accepts the declared single PK `event_key`. Bonus: the view **omits the two jsonb
payload columns**, making the "raw wire bytes never leave us-east-1" residency
claim *structural* — they are not in the object Data Cloud can see, not merely
unmapped. The original hand-built stream `A2A_Lab_Trace_Events` (composite PK) +
its DLO were **deleted 2026-08-09** — verified orphaned first: the DMO federates
(3021 rows) and its mapping sources `A2A_Lab_Trace_Events_K__dll`, never the old
composite-PK DLO (whose mapping the API had rejected — the whole reason `_K`
exists). The live chain is the `_K` stream over the view.

| Artifact | How built | Status |
|----------|-----------|--------|
| Federation view `lab.trace_events_zc` | `observability.pg.DDL` + `pg_migrate.py` | ✅ DONE — scalars + event_key, no jsonb |
| Zero-Copy stream `A2A_Lab_Trace_Events_K` | `POST /ssot/data-streams` over the view | ✅ DONE — DIRECT_ACCESS, single PK event_key |
| DLO `A2A_Lab_Trace_Events_K__dll` | (created by the stream) | ✅ single PK `event_key__c`, federates rows |
| DMO `A2A_Lab_Trace_Event__dlm` | `POST /ssot/data-model-objects` | ✅ DONE — 11 fields, PK `event_key__c` |
| DLO→DMO field map | `POST /ssot/data-model-object-mappings` | ✅ DONE — 12 pairs, 1:1 |
| **DMO federates from Aurora** | `POST /ssot/queryv2` on `__dlm` | ✅ VERIFIED — live rows, protocol aggregate, 2-level trace latency all work |
| Semantic model + dashboard | Tableau Next | ⬜ UI — no create API on any of the 3 surfaces (MCP server is read-only; verify/measure only) |

**Grain is preserved.** The single-PK DLO does NOT collapse hops — a
`DIRECT_ACCESS` DLO returns every underlying row (verified: multiple hops per
`trace_id`; `COUNT(*)=hops` ≫ `COUNT(DISTINCT trace_id)=traces`).

### §4 trace-latency decision — settled toward LOD, and the CI is unnecessary

The two-level trace-latency metric (`AVG` over per-trace `SUM(latency)`)
**federates correctly as a nested SQL query through the DMO** (verified live:
avg ≈ 32.3s, max ≈ 448s over 960 traces). §4a (LOD in the semantic model) is
**ruled out** — Tableau Semantics has no `FIXED`/LOD (2026-08-09). §4b (a
calculated insight) is ruled out on residency. The chosen route is **§4c: a
second Zero-Copy DMO over a `lab.trace_rollup_zc` VIEW** — same live-federation,
nothing materialized, the aggregation pushed into Aurora.

---

## Operator UI runbook — only Tableau remains

The entire data layer is built and verified headless. Only the Tableau Next work
needs you:

**Step 1 — build the Tableau Next semantic model + dashboard (UI).** Point it at
DMO `A2A_Lab_Trace_Event__dlm`. Use §3 (dims, measures, tiles). Declare every
aggregated measure as a **user-defined aggregation** ("UserAgg" — required for any
formula containing `AVG`/`SUM`/`COUNT`/`PERCENTILE`). The five **hop-grain** tiles
build over `A2A_Lab_Trace_Event__dlm`. The four **trace-grain** tiles build over
the **§4c second DMO `A2A_Lab_Trace_Rollup__dlm`** — ✅ **BUILT and federating
960 traces 2026-08-09** (LOD is impossible in Tableau Semantics — no `FIXED` — so
the trace-grain aggregation was pushed into Aurora as `lab.trace_rollup_zc` and
federated as a second Zero-Copy DMO; its six flat measures are in the steps doc
§4c table). Keep the **Hops by Protocol × Target Platform** tile
live (it is the L5.8 measurement surface).

**Step 2 — MEASURE and record (item 6c → 7a).** Open the dashboard cold, note
wall-clock to first render (the real EU→US round trip) → `plan/03-results.md`
with date + topology note, then fill the `[N]` bracket in steps doc §7a.

(§0's `event_key` column, once described as cosmetic, turned out to be **load-
bearing after all** — it is the single-column surrogate PK the whole mapping
chain depends on. The earlier "PK not a blocker" correction was itself wrong; the
truth is the base-table composite PK is the blocker and `event_key` via the view
is the fix.)

---

## Later tables (deliberate extensions, NOT this build)
The user's note: the other obs tables extend this dashboard later. Sketched so
the model grows cleanly:
- **`obs_sessions` / `obs_events`** → DMOs relating to `Agent Trace Event` on
  `Platform Ref` (= `native_id`) — the M11 join: lab-side hop traffic ⋈
  platform-interior execution logs (tokens, event types). Adds cost/usage
  measures (`usage_json`) alongside protocol traffic.
- **`usage_events`** (WS18) → its own DMO; console-visitor analytics, a different
  subject area, likely a sibling dashboard rather than a tile here.
- **`obs_briefs`** → the analyst/sentinel narrative feed; a text panel, not a
  metric source.
- **`obs_harvest`** → freshness/status only; a "last harvested" caption, not a
  DMO.

Each is a new data stream + DLO + DMO; the semantic model gains relationships,
not rewrites — which is the point of keeping the starter DMO clean and 1:1.
```
