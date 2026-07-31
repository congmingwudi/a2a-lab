# Observability (M11) — cross-platform agent execution logs

Goal: a dedicated **Observability** category in the console's left nav (its own
accordion section, peer of Scenarios/Targets/Traces) showing what each
*platform* recorded about the agent executions this lab drove — Salesforce
Session Tracing + Einstein GenAI gateway logs, Anthropic Managed Agents
session events, OpenAI (what little it exposes) — on one timeline, with
per-platform drill-down. The lab's own `TraceEvent` layer shows the wire;
this section shows each platform's *interior* view of the same runs, and the
two are joined by ids we already carry.

Research verified 2026-07-17 (API names/endpoints below are from current
platform docs; re-verify betas before building).

## What each platform lets us pull (honest matrix)

All five platforms the lab harvests. The columns are deliberately not ranked —
each is strong somewhere and absent somewhere else, and **which axis a platform
is strong on is the finding**, not a scoreboard.

| Capability | Salesforce Agentforce | Anthropic Managed Agents | OpenAI | Google Agent Engine | Microsoft Foundry |
|---|---|---|---|---|---|
| List executions org-wide | ✅ SQL over STDM DMOs | ✅ `GET /v1/sessions` (paginated; no time-range filter documented) | ❌ none (usage metrics only) | ⚠️ no session/turn read API on the preview A2A surface — Cloud Logging entries per ReasoningEngine instead | ✅ KQL over App Insights `AppDependencies` |
| Per-execution step detail | ✅ interaction steps DMO; OTel export (beta) | ✅ `GET /v1/sessions/{id}/events` | ⚠️ `GET /v1/responses/{id}` by known id only | ⚠️ container app logs and request lines — not agent-semantic, and A2A `contextId` does not appear in the default logs | ✅ gen_ai spans: `invoke_agent` (turn), `chat <model>`, `execute_tool` (its own record of calling the lab's shim) |
| LLM request/response logs | ✅ Einstein GenAI audit DMOs | ⚠️ inside session events (thinking/tool events, token spans) | ⚠️ stored responses, 30-day TTL, fetch by id | ❌ not exposed | ✅ full input/output messages on the `chat` spans |
| Real-time stream | ❌ (poll DMOs) | ✅ `GET /v1/sessions/{id}/events/stream` (SSE) | ❌ | ❌ (poll Cloud Logging) | ❌ (poll KQL) |
| Aggregate usage/cost API | ⚠️ via DMO SQL aggregation | ❌ none | ✅ `GET /v1/organization/usage/*`, `/costs` (admin key) | ✅ Cloud Monitoring — `cpu/allocation_time` and `memory/allocation_time`, the **literal billing meters**, plus publisher token counts | ⚠️ `gen_ai.usage.*` tokens on each span, aggregate by KQL; no cost API |

**Read the table by column, and the shape of each platform falls out.**
Salesforce is queryable session/step data. Anthropic is deep per-session events
and the only real-time stream. OpenAI is effectively **write-only** for
execution detail and the only one with a first-class cost API. Google lands
*between*: real, queryable, billing-grade telemetry — but request-level rather
than agent-semantic, because Agent Engine bills **allocated compute, not
tokens**, and its meters say so. Foundry is the column WS3 hoped for:
agent-semantic **and** queryable, with the platform's own record of calling us.

Two consequences the lab actually ran into. Because Agent Engine exposes no
session read, its honest shape in the store is **one obs session per deployed
engine**, with log entries as events and a daily metrics rollup — not one per
turn. And because Foundry keys its spans on `gen_ai.response.id`, which is the
same id the lab's `FoundryClient` records as `platform_ref`, the
`trace_events ↔ obs_sessions` join works with no extra correlation machinery —
the only platform where that is true by construction rather than by the D27
rider.

### Salesforce — the richest pull surface
- **Session Tracing Data Model (STDM)** — Data Cloud DMOs, queryable with
  ANSI SQL via **Data Cloud Query API v2** (`POST /api/v2/query`, paginate by
  `nextBatchId`; OAuth scope `cdp_query_api`) or SOQL from the core platform:
  `ssot__AiAgentSession__dlm` → `ssot__AiAgentInteraction__dlm` (turns:
  operation, start/end, duration ms, status, `ssot__TelemetryTraceId__c`) →
  `ssot__AiAgentInteractionMessage__dlm` (utterances) and
  `ssot__AiAgentInteractionStep__dlm` (planner/action/LLM steps with
  input/error text). Field names vary between docs — verify in-org with Data
  Explorer before hardcoding.
  **Which path the lab took, and what it costs:** the SOQL route from the
  core platform (`observability/salesforce_source.py` →
  `/services/data/vXX/query`), not Query API v2 — so the harvest needs the
  broad `api` scope rather than `cdp_query_api`. That single dependency is
  why F3 kept `api` instead of trimming it, and why F6 gave the harvest its
  own `a2a_lab_obs` app scoped to `api` ALONE, keeping the broad grant off
  the three agent callers (D37). If the harvest is ever moved to Query API
  v2, re-run that decision: `cdp_query_api` would let even the obs app drop
  `api`.
- **Einstein GenAI audit & feedback DMOs** — the org's LLM gateway log:
  `GenAIGatewayRequest__dlm` (prompt, params) ⋈ `GenAIGatewayResponse__dlm`
  ⋈ `GenAIGeneration__dlm` (response text; joins to STDM steps via
  `ssot__GenerationId__c`), plus trust-layer scores
  (`GenAIContentQuality__dlm`) and feedback DMOs.
- **Session Trace OTel export (beta)** — `GET
  /services/data/v66.0/einstein/audit/otel/{session-id}`: whole session
  pre-joined as OTLP ResourceSpans JSON. One session per call, **72-hour
  lookback** → good for drill-down enrichment, unusable as the polling
  source (DMOs are the polling source).
- Prereqs (org setup, one-time): Data Cloud provisioned; Einstein Trust
  Layer audit & feedback collection ON; Session Tracing enabled in Einstein
  Audit/Analytics/Monitoring Setup; connected app with `cdp_query_api`.
  Note: DMO queries consume Data Cloud credits — poll on demand / coarse
  intervals, not a tight loop.
- UI-only (recorded as gaps): Agentforce Observability / Agent Analytics
  dashboards (Tableau-Next package) have no REST API; the raw STDM under
  them is what we query.

### Anthropic Managed Agents — deep per-session, no discovery
- `GET /v1/sessions/{session_id}/events` (beta header
  `managed-agents-2026-04-01`): full persisted history — `agent.message`,
  `agent.thinking`, `agent.tool_use`/`tool_result`, MCP tool events, status
  events, span events with token counts + timing. Filter with `types[]`.
  Also `GET .../events/stream` (SSE, replayable) and
  `GET /v1/deployment_runs?deployment_id=` for scheduled deployments (links
  each run to its `session_id`; `has_error=true` filter).
- **Listing exists** (correction 2026-07-17): `GET /v1/sessions` /
  `client.beta.sessions.list()` is paginated (`limit`/`page`, and it is the
  one endpoint with backward `prev_page` support), so workspace-wide session
  discovery IS possible — but no `created_after` filter is documented, so
  incremental harvest walks pages newest-first until it hits known ids.
  M11.1's id-persistence is still required for *correlation* (the lab
  session ↔ CMA session map lives only in `ManagedBackend._sessions`, an
  in-memory dict), just no longer for discovery. The session object also
  carries a `usage` field — per-session aggregate tokens without reading
  every event. Events persist only while the session exists (delete removes
  event history); harvest before deleting sessions.
- No usage/cost aggregation API, no OTel export — token spans inside session
  events are the only usage signal, aggregated on our side.

### OpenAI — our trace layer stays the system of record
- **Traces dashboard is write-only**: Agents SDK exports to an undocumented
  `POST /v1/traces/ingest`; there is **no read/list API** (open issue
  openai/openai-agents-python#793). Plan: register a custom
  `TracingProcessor` in the M9 OpenAI agent to tee spans straight into our
  observability store — capture at emit time or lose it.
- Responses API: `GET /v1/responses/{id}` (+ `/input_items`) returns full
  output/tool-call/usage detail, but only by known id; stored 30 days
  (`store:true` default); **no list endpoint** → persist response ids as we
  create them. Conversations API same pattern (items exempt from the TTL).
- **Assistants API sunsets 2026-08-26** — do not build anything on
  runs/run-steps.
- Usage/Costs API (`/v1/organization/usage/completions`, `/costs`, admin
  key, 1m/1h/1d buckets, group by project/model) is the only org-wide poll
  surface — metrics only, feeds the timeline's usage lane.

## Design

```
platform APIs ──pull──►  harvester (src/observability/)  ──upsert──►  obs store (Aurora PG)
  SF Query API v2                                                    │
  CMA sessions/events        correlation keys from traces/ ──join────┤
  OpenAI responses/usage                                             ▼
                                            console :8200  "Observability" nav section
                                              timeline · platform drill-down · gaps panel
```

- **Correlation is the spine.** Every lab hop already carries `trace_id` +
  `session_id`; each platform holds its own native id. M11.1 adds a
  `platform_ref` (native execution id) to `TraceEvent` so the join is
  recorded at emit time: CMA session id (managed backend), Agentforce Agent
  API session id (client + shims) — which STDM also sees —
  `ssot__TelemetryTraceId__c` on the SF side, OpenAI response ids (M9).
- **Harvest-and-cache, not live-proxy.** A `PlatformLogSource` per platform
  (`salesforce.py`, `anthropic.py`, `openai.py`) pulls into local tables
  (`obs_sessions`, `obs_events`, keyed by platform + native id + lab ids
  where joinable). Rationale: SF DMO ingestion lags minutes and costs
  credits; CMA events vanish with the session; OpenAI responses expire in
  30 days. The store is the durable superset; the dashboard reads only the
  store. Triggers: on-demand button per platform, `scripts/obs_harvest.py`
  CLI, optional post-scenario hook.
- **Store = Aurora Postgres, source of truth (D23, D49).** Observability tables
  share the hosted `a2alab-obs` cluster with the PostgresSink trace hops, so
  timeline/drill-down joins are plain SQL. `observability.make_obs_store()` is
  the single selector — it defaults to Postgres and falls back to the local
  sqlite `traces/lab.db` (the original D19 design) only when
  `A2ALAB_OBS_STORE=sqlite` or Postgres is unconfigured, i.e. a fresh checkout
  with no AWS session. This split is exactly what D49 exists to enforce: the
  console once rendered the laptop's `lab.db` while Aurora filled unseen. JSONL
  stays the raw archive; the DynamoDB M10 path is superseded by the Aurora
  zero-copy connector.
- **Raw payloads preserved.** Same ethos as D7: every harvested record keeps
  the raw platform payload (DMO row / CMA event JSON / OTLP span) alongside
  the normalized columns, shown in the drill-down like the wire view.

### Console: Observability nav section
- **Timeline view** — all harvested executions as swimlanes per platform,
  lab trace markers overlaid; brush a time range, click through. Usage lane
  (tokens/cost) where a platform provides it.
- **Execution drill-down** — per platform, show what *it* uniquely offers:
  SF: session → interactions → planner steps + gateway prompt/response +
  trust-layer scores (+ OTel trace tree when within 72h); CMA: event stream
  incl. thinking + tool calls + token spans; OpenAI: response output items +
  usage buckets.
- **Side-by-side** — a lab trace opens with its platform-interior views
  beside the wire payloads: what went over the wire vs. what each platform
  logged internally — a headline comparison artifact for the lab.
- **Coverage/gaps panel** — render the honest matrix above live: per
  platform, what was harvested, what is API-inaccessible (UI-only), last
  harvest time. Feeds plan/02-matrix.md findings.

### What this section is NOT: coding-agent telemetry (WS9)

Claude Code, Codex and Cursor telemetry shares this subsystem's plumbing — the
same `PlatformLogSource` seam, the same `ObsStore`, the same harvest Lambda —
and it is deliberately **not** a sixth column in the coverage panel. (Cursor is
a third `@resource.tool` inside the one `coding` source, not a new source — it
reaches CloudWatch via cursorscope hooks and its cumulative counters are read
with `increase()`; D64.) That panel's
honesty rests on every column being an agent platform whose interior the lab
harvests; the tools that BUILT the lab are a different subject, and listing them
beside Agentforce would quietly claim otherwise. So `coding` is popped from
`/api/obs/summary`, gets its own **Coding Agents Telemetry** console section
with its own Harvest button, and is reachable from `/api/obs/harvest` only by
name — the unqualified sweep behind the Observability Harvest button stays the
five agent platforms, because that button reports "harvested from all
platforms". Details and measurements live in WS9 (plan/07-workstreams.md).

## Work items

- **M11.1 — ids + store** ✅ (2026-07-17): `platform_ref` on `TraceEvent`; persist CMA and
  Agent API session ids from `managed_backend.py` / `client.py` / shims;
  `SqliteSink` (D19) + `scripts/trace_import.py` JSONL backfill; obs tables.
- **M11.2 — harvesters** ✅ (2026-07-17; SF source built but blocked on org setup): `PlatformLogSource` interface; Salesforce source
  (Query API v2 over STDM + GenAI audit DMOs; org setup toggles per
  prereqs above → runbook entry in plan/04-runbooks.md); Anthropic source
  (session events + deployment runs); `obs_harvest.py`. Unit tests with
  canned payloads; `-m live` tests against real orgs.
- **M11.3 — console** ✅ (2026-07-17): Observability nav section, timeline, drill-downs,
  side-by-side, gaps panel; `/api/obs/*` endpoints reading SQLite.
- **M11.x — hosted harvest repair** ✅ (2026-07-25). An Aurora survey found the
  hosted store telling a quieter lie than an outage would have: it looked
  healthy while missing two platforms and mis-filing a third.
  - **adk / foundry were never harvested hosted.** The Lambda's source map
    listed neither foundry (simply absent) nor working adk credentials, and
    its bundle carried neither `google-auth` nor `azure-*`. Aurora held zero
    rows for both while local sqlite had them, so the coverage panel could
    not distinguish "nothing to pull" from "we never asked". Fixed: both
    sources registered, libraries bundled, and their credentials moved into
    the harvest secret (F1 shape) — a dedicated GCP service account
    (`a2alab-obs-harvest`, logging.viewer + monitoring.viewer, key
    materialized to /tmp at cold start because google.auth wants a file), and
    `Log Analytics Reader` granted to the existing Entra SP on the
    `a2a-lab-logs` workspace. **The Foundry failure is the instructive one:**
    it worked locally and failed hosted for the same reason every time —
    `DefaultAzureCredential` silently resolved to the developer's own Azure
    CLI login on the laptop and to the service principal in Lambda. A
    credential chain that falls back to a human is a test that passes for
    the wrong reason.
  - **823 orphaned Salesforce events** were not stale data; they were a
    modelling error, and deleting them (the first instinct) would have
    destroyed real telemetry that regenerated within six hours. Two causes:
    `SELECT FIELDS(ALL)` is capped at 200 rows by the platform, so the
    session query truncated while three child DMOs each returned their own
    200; and the child DMOs are **not all children of the session** — steps
    carry only `ssot__AiAgentInteractionId__c` and reach the session through
    their interaction. The heuristic column matcher, asked for a
    session-ish id on a step row, happily returned
    `ssot__SessionOwnerId__c` — whose value is the literal string
    `"NOT_SET"`, which STDM writes where other APIs write null. So every
    step was filed under a session named NOT_SET. Fixed with OFFSET paging,
    an interaction→session map, named columns ahead of the heuristic, and a
    fetch-by-id backfill for anything still referenced. Orphans: 823 → **0**,
    with no rows deleted. Regression test in
    `tests/unit/test_observability.py`.
  - Post-fix Aurora: 388 sessions / 5,209 events across all five platforms
    (adk 1, claude 111, foundry 8, openai 51, salesforce 217), zero orphans,
    zero zero-event sessions.
- **M11.4 — enrichment**: SF OTel single-session export in drill-down;
  Anthropic webhooks (session/deployment-run state changes) as a
  harvest trigger; usage/cost lanes.
- **M9 hook**: OpenAI platform lands with `TracingProcessor` tee + response
  id persistence from day one (there is no after-the-fact pull).
- **M11.5 — observability analyst agent** (deferred until the store holds
  real multi-platform data — at minimum live STDM rows next to the CMA
  harvest): a scheduled CMA deployment (D16 daily-brief pattern) that
  *interprets* the harvested store nightly — run/failure counts, cold-start
  timeout clusters, token-spend anomalies ("scenario X looped on
  web_search"), trust-layer score dips, cross-platform latency comparison —
  and writes a short findings brief. Division of labor is the point and is
  itself a lab finding: the pull stays deterministic ETL
  (obs_harvest.py / cron / M11.4 webhooks — no LLM in that loop); the agent
  only does the analysis layer above it. Access via a host-side custom tool
  that queries traces/lab.db (read-only SQL, results into the session as
  tool results) so no credentials or raw DB enter the sandbox. Its own runs
  land back in the dashboard via the CMA harvester — the analyst is
  observable by the thing it analyzes. Output: append to
  plan/03-results.md-style findings or an in-console "briefs" feed; decide
  when built. **Hosted-phase fork (D23)**: the host-side custom tool is the
  laptop-only design — custom tools block on a connected driver. Hosted, the
  store moves to Aurora Postgres Serverless v2 (also the M10 zero-copy
  source via the Data 360 Aurora connector, superseding DynamoDB), fronted
  by a remote MCP server (`query_obs_store` on a read-only DB role), and
  the deployment's firings then need no watcher process at all.
  **Built 2026-07-17** (runbooks §8): Aurora `a2alab-obs` live with the
  local store backfilled; `a2alab-obs-mcp` + `a2alab-obs-harvest` Lambdas
  deployed (harvest on a 6h EventBridge schedule, verified end-to-end);
  console gained Analyze + briefs feed. Remaining human steps:
  `deploy/obs/expose_mcp.sh` then `setup_obs_analyst.py --recreate --run`.

## References (research sources, verified 2026-07-17)

- SF Session Trace OTel API (beta): https://developer.salesforce.com/docs/ai/agentforce/guide/otel-api.html
- SF trace trees / STDM via SOQL blog (2026-05): https://developer.salesforce.com/blogs/2026/05/agent-platform-tracing-debug-agentforce-with-trace-trees-soql-and-slack
- SF Einstein audit & feedback data model: https://developer.salesforce.com/blogs/2024/07/the-einstein-audit-and-feedback-data-model-in-data-cloud
- SF STDM field reference (canonical, JS-rendered — open in browser): https://help.salesforce.com/s/articleView?id=ai.generative_ai_session_trace_data_model.htm
- Data Cloud Query API v2: https://developer.salesforce.com/docs/data/data-cloud-query-guide/references/data-cloud-query-api-reference/c360a-api-query-v2.html
- OpenAI Agents SDK tracing (custom `TracingProcessor`): https://openai.github.io/openai-agents-python/tracing/
- OpenAI "no read API for traces" issue: https://github.com/openai/openai-agents-python/issues/793
- OpenAI Responses retrieve / no-list confirmation: https://developers.openai.com/api/reference/resources/responses/methods/retrieve , https://community.openai.com/t/api-to-list-all-responses/1359403
- OpenAI deprecations (Assistants sunset 2026-08-26): https://developers.openai.com/api/docs/deprecations
- OpenAI Usage/Costs API: https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/completions
- Anthropic Managed Agents session events / deployment runs: docs.anthropic.com Managed Agents (beta `managed-agents-2026-04-01`); endpoints in the matrix above. Webhooks (session/deployment-run state changes) are subscribe-in-Console, deliver ids only — fetch full objects via GET.

## Findings this section will produce (for plan/02-matrix.md)

The observability *capability comparison* is itself a lab result: SF exposes
the most queryable execution telemetry (full SQL over sessions/steps/LLM
calls) but needs Data Cloud; Anthropic exposes the deepest per-session
detail (thinking + tool events) but no discovery/aggregation; OpenAI expects
you to run your own tracing — its dashboard is not programmatically
readable. Record measured harvest lag, retention limits hit, and any
field-name corrections against the tables above.
