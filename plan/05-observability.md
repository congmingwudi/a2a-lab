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

| Capability | Salesforce Agentforce | Anthropic Managed Agents | OpenAI |
|---|---|---|---|
| List executions org-wide | ✅ SQL over STDM DMOs | ✅ `GET /v1/sessions` (paginated; no time-range filter documented) | ❌ none (usage metrics only) |
| Per-execution step detail | ✅ interaction steps DMO; OTel export (beta) | ✅ `GET /v1/sessions/{id}/events` | ⚠️ `GET /v1/responses/{id}` by known id only |
| LLM request/response logs | ✅ Einstein GenAI audit DMOs | ⚠️ inside session events (thinking/tool events, token spans) | ⚠️ stored responses, 30-day TTL, fetch by id |
| Real-time stream | ❌ (poll DMOs) | ✅ `GET /v1/sessions/{id}/events/stream` (SSE) | ❌ |
| Aggregate usage/cost API | ⚠️ via DMO SQL aggregation | ❌ none | ✅ `GET /v1/organization/usage/*`, `/costs` (admin key) |

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
platform APIs ──pull──►  harvester (src/observability/)  ──upsert──►  obs store (SQLite)
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
- **Store = SQLite** (see D19): observability tables live in the same
  `traces/lab.db` as the sqlite TraceSink so timeline/drill-down joins are
  plain SQL. JSONL stays the raw archive; DynamoDB stays the cloud/M10 path.
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
  when built.

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
