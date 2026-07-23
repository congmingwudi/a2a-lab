# Workstreams — the multi-platform buildout (2026-07-19)

The lab's next phase: extend the Agentforce↔Claude↔OpenAI experiments to
the remaining primary agent platforms, keep every addition producing
publishable insights (config/insights.yaml → console Insights section →
plan/08-insights.md → Claude Design presentation), and keep the honesty
rules (D15 real-platform entry, honest matrix statuses, raw wire payloads).

Ordering decided 2026-07-19: **WS1 → WS2 (GCP) → WS3 (Azure) → WS4
(LangGraph) → WS5 (Strands, deferred)**. CrewAI and Pydantic AI are
flagged as candidates only — user decision pending.

Rules that apply to every workstream:
- **Twin rule (D25):** each platform gets its own Agentforce twin agent
  (Agent Script bundle cloned from the Claude pair, action pinned to that
  platform's target) so experiments stay closed two-platform systems.
- **Seam rule:** a platform = `src/platforms/<name>/` (adapter and/or
  client) + entries in `config/targets.yaml`. Nothing in `interop/` or
  other platforms changes.
- **Obs rule (D18/D22):** every platform lands an
  `src/observability/<name>_source.py` + `SOURCES` entry in
  `scripts/obs_harvest.py` — or an honest "nothing pullable" entry like
  OpenAI's, recorded in the coverage panel.
- **Insight rule:** a workstream isn't done until its findings are added
  to `config/insights.yaml` (and `plan/08-insights.md` regenerated via
  `scripts/export_insights.py`).

---

## WS1 — Finish the AgentCore pair (in flight)

**Goal:** OpenAI *and* Claude agents live on Bedrock AgentCore via a
scripted, repeatable deploy; one-flip switching between local servers and
hosted runtimes.

**Why:** the apples-to-apples cross-vendor cell (two self-hosted SDK
agents on identical runtime) plus the managed-vs-self-hosted comparison
(same Claude adapter on CMA and AgentCore). Fills the empty
managed-vs-sdk latency table in plan/03-results.md.

Done in this session (D26): generic `interop/clients/agentcore.py`,
`claude-agentcore` target, `--extra aws` in the Claude image,
`deploy/agentcore/deploy.sh`, `A2ALAB_MODE=local|hosted` remap, insights
store + console section.

Status 2026-07-19 (deployed live this session):
1. ✅ `a2alab_claude` runtime deployed via the script
   (`runtime/a2alab_claude-gbcFGKHCdF`, ARN in .env). Deploy-script fixes
   along the way: role lookup via get-agent-runtime (list API omits
   roleArn), ECR pull policy widened to `a2alab-*`, and the container gets
   the **writer** PG secret — local .env carries the reader (console
   queries), and a runtime inserting hops through the reader fails
   read-only, silently dropping every hop. The hand-deployed openai
   runtime already used the writer; the script now maps
   `A2ALAB_PG_WRITER_SECRET_ARN` over `A2ALAB_PG_SECRET_ARN` in container
   env so scripted deploys match.
2. ✅ M9 openai runtime verified READY and adopted (config matches script).
3. ✅ Matrix cells recorded → 03-results: openai-agentcore PASS warm p50
   10.3s / p95 11.9s (cold ~31s); claude-agentcore PASS warm p50 8.4s /
   p95 15.4s (cold ~56s — can exceed the 65s client timeout right after a
   runtime update; warm the runtime before recorded runs).
4. ✅ Hosted-mode remap verified end-to-end at the client layer
   (`A2ALAB_MODE=hosted` resolved claude-rest→claude-agentcore, live
   answer, hops in Aurora — including container→Agentforce agent-api hops:
   the AWS runtime calling Salesforce works).
5. ⏳ Remaining: full Agentforce→bridge hosted-mode pass (needs a stack
   restart with A2ALAB_MODE=hosted and Zscaler OFF — deploys needed it ON,
   the local app needs it off); managed vs sdk-local vs sdk-agentcore
   latency table; M6 timeout probes.

Known flake (recorded honestly): the sdk agent occasionally delegates the
matrix question to Agentforce and burns its 3-turn cap
(`CLAUDE_MAX_TURNS=3`) → intermittent 500 "max turns". Options if it
annoys: raise CLAUDE_MAX_TURNS in the runtime env, or pin the research
prompt harder against delegation for factual questions.

**Credentials:** nothing new — embark AWS account (SSO), existing
Anthropic/OpenAI/SF keys already in .env.

**Cost note:** AgentCore bills per-invocation compute; two mostly-idle
runtimes are cents/day. ECR storage negligible.

---

## WS2 — Google ADK on Agent Engine (next up)

**Goal:** a Gemini-brained ADK research agent hosted on Vertex AI Agent
Engine (Gemini Enterprise Agent Platform), reachable natively over A2A —
the lab's **first native×native A2A cell** — plus the reverse direction
via its Agentforce twin.

**Why:** ADK 1.0 is GA and Agent Engine exposes deployed agents as native
A2A endpoints; Salesforce is an A2A council member. This is the cell the
lab is named for: both ends speak the protocol with no bridge/shim. Also
the best queryable observability so far (Cloud Trace/Logging), which
extends the observability-fragmentation insight favorably.

Work items:
- `src/platforms/adk/` — outbound: the existing generic `A2AClient`
  against the Agent Engine A2A endpoint may be ALL we need (target
  `adk-a2a`, status native — first one where the *remote platform* is
  native). Inbound direction: Agentforce twin's action → bridge →
  `adk-a2a` (Path A stays via-bridge; honest statuses).
- Agent interior: ADK agent, Gemini model, `ask_agentforce` tool calling
  the Agent API with a new `SF_ADK_AGENT_ID` twin (Apex unchanged — D15
  invocable already takes `target`).
- Auth: Agent Engine A2A is IAM-gated (google-auth id tokens) — extend
  the A2A client's auth handling or a thin `adk` auth wrapper; document
  what A2A-over-enterprise-auth actually takes (insight material: agent
  cards don't carry cloud IAM).
- Obs: `adk_source.py` — Cloud Trace/Logging harvest into obs store.
- Console: scenario entries, `_PLATFORM_TAGS`, components row, screenshots.

**Credentials / setup (user + Claude):**
1. Create a dedicated project in the existing GCP account (user):
   Console → project picker → New Project → name `a2a-lab` (note the
   project id), attach billing.
2. Install gcloud locally (missing today): `brew install google-cloud-sdk`,
   then `gcloud auth login` and
   `gcloud config set project <project-id>` — plus
   `gcloud auth application-default login` so lab code can use ADC.
3. Enable APIs (Claude can run once authed):
   `gcloud services enable aiplatform.googleapis.com cloudtrace.googleapis.com`.
4. .env additions: `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`
   (us-central1), `SF_ADK_AGENT_ID` (after twin deploy).
5. Python deps: `google-adk` + `google-cloud-aiplatform[agent_engines]`
   as a `gcp` extra.

**Exit criteria:** native×native A2A matrix cell green with wire payloads;
both sync directions live in the console; ADK obs source harvesting;
insights updated (native-A2A reality, A2A auth story, Cloud Trace column).

Status 2026-07-19 (first leg live):
1. ✅ GCP project `a2a-lab-d441` (billing linked, APIs enabled, ADC);
   user's duplicate a2a-lab-503000 deleted (recoverable ~30d).
2. ✅ `src/platforms/adk/` — Gemini agent core + D27-guarded
   ask_agentforce (SF_ADK_AGENT_ID twin, SF_AGENT_ID fallback until the
   twin exists) + the Agent Engine A2aAgent app (a2a-sdk executor
   mirroring the lab's AdapterExecutor lifecycle; InMemory sessions v1).
3. ✅ Deployed via deploy/adk/deploy_adk.py to Agent Engine
   (`a2alab-adk-researcher`, engine 1360159105477509120, us-central1) —
   min_instances=0 + 1cpu/2Gi ON PURPOSE (a warm default-size instance
   ≈ $250/mo on the personal account; scale-to-zero idles at $0).
   Deploy learnings: extra_packages must be RELATIVE paths (absolute →
   ModuleNotFoundError at unpickle); cloudpickle+pydantic belong in
   requirements; one create() failed transiently (code 13, healthy
   container) — retry succeeded.
4. ✅ First native×native A2A cell RECORDED: adk-a2a PASS, warm 2.6s,
   p50 9.3s / p95 34.5s (p95 = scale-to-zero cold start — third column
   in the cold-start comparison: AgentCore claude ~56s / openai ~31s /
   Agent Engine ~34s). Preview roughness recorded honestly: the public
   card route 404s, so the lab client pins transport=http_json and
   builds a minimal card locally (targets.yaml options; A2AClient
   gained card_path/transport/google-adc auth support).
5. ✅ SF ADK twin published+activated (`A2ALab_Research_Assistant_ADK`,
   agent 0XxKB000000xdn80AA, v1 — Agent Script clone, action pinned
   three ways to adk-a2a); `agentforce-adk-rest` target; engine env
   updated with SF_ADK_AGENT_ID (one more transient code-13 on update,
   retry succeeded — pattern confirmed).
6. ✅ ADK→Agentforce live: 18.8s, real CRM data (Omega: 3 opps $212K,
   11 cases) attributed to the twin, cross-cloud GCP→Salesforce tool
   call from inside the container.
7. ✅ Scenarios live + nav group flipped; Google-green chips;
   Agent Engine component row; adk obs source harvesting (500 log
   entries first pull — request-level telemetry, no session/turn API on
   the preview surface; the honest fourth observability column).
8. ✅ Insights: native-a2a-young added; Agent Engine cold/warm folded
   into managed-vs-self-hosted (16 total, export regenerated).
9. ✅ Agentforce→ADK live after stack restart: 18.4s, two labeled
   sections, the external leg over the platform-native A2A endpoint via
   the bridge; agentforce-adk-rest cell recorded (PASS p50 8.4s).
   Finding: Gemini flash-lite once content-refused under the D27 rider
   ("my capabilities are limited to Salesforce data" — small-model
   identity confusion); next run answered perfectly. If it flakes demos,
   bump ADK_MODEL a tier.
10. Card-404 verdict (investigated): genuine preview gap — the current
    A2aAgent template registers NO public-card method (classMethods has
    message/tasks/extended-card only) and the SDK exposes no card getter;
    docs describing GET /v1/card are ahead of the shipped template. The
    extended-card 501 is partly ours (no extended_agent_card passed).
    Minimal-card + pinned-transport in the lab client is the right
    workaround for any external caller.

11. ✅ D27 prompt-layer guard now honored ON the platform side: the ADK
    twin's Agent Script v2 checks for the rider block and skips its
    STEP 2 delegation when the request was itself delegated —
    live-verified (no bridge leg on the wire; answers ~10.5s vs ~18s
    with the nested loop). Claude/OpenAI twins can get the same
    instruction on request. Flash-lite flake log: one content-refusal,
    one hallucinated `run_code` tool across ~8 runs — bump ADK_MODEL if
    demo-critical.

WS2 COMPLETE 2026-07-19. Future polish: VertexAiSessionService for
durable sessions; Cloud Trace spans in the obs source; extended card.

Post-WS2 additions (2026-07-20): the agent contributes its own value via a
market-signals tool — deterministic synthetic by default, live Google
Search grounding behind `ADK_REAL_SEARCH=1` (GoogleSearchTool with
bypass_multi_tools_limit=True; engine redeploy required — see
.env.example for the trade-offs); the D30 direct route (Apex → Agent
Engine A2A, operator route radio) with its Salesforce-side JWT-bearer
credential chain.

---

## WS3 — Microsoft Foundry Agent Service (Azure)

**Goal:** a Foundry-hosted agent as the fourth platform: outbound via
Foundry's A2A tool (Foundry agent calls our agents), inbound via
Foundry's incoming-A2A endpoint (public preview) — the second
native-A2A-speaking *vendor platform*, and the other consolidation-pitch
counterweight to Agentforce.

Work items:
- `src/platforms/foundry/` — outbound to Foundry: A2A client against the
  agent's A2A endpoint (native), fallback REST via Foundry SDK if the
  preview gates us (record honestly). Inbound: Foundry agent configured
  with the A2A tool pointing at our tunnel-exposed A2A servers.
- Agent interior: Foundry prompt agent (or Microsoft Agent Framework
  hosted agent) with an `ask_agentforce`-equivalent tool; `SF_FOUNDRY_AGENT_ID` twin.
- Obs: Foundry threads/runs + Application Insights (queryable — KQL) →
  `foundry_source.py`.
- Insight material: A2A version negotiation (Foundry speaks 1.0/0.3),
  preview-gating reality, Azure auth (Entra ID tokens) vs agent cards.

**Credentials / setup:**
1. Azure subscription (user has). Create a resource group `a2a-lab` and a
   Foundry project (portal: ai.azure.com → New project). Note endpoint.
2. az CLI already installed; `az login`.
3. Model deployment in the project (e.g. gpt-4.1-mini or a Phi/small
   model — decide at build time; Foundry model catalog).
4. .env: `AZURE_FOUNDRY_PROJECT_ENDPOINT`, `AZURE_SUBSCRIPTION_ID`,
   `SF_FOUNDRY_AGENT_ID`; deps `azure-ai-projects azure-identity` as an
   `azure` extra.

**Exit criteria:** Foundry↔Agentforce both directions live; a
Foundry↔ADK native A2A cross-hyperscaler demo cell; App Insights obs
source; insights updated.

Status 2026-07-22 (environment + first answer):
1. ✅ Foundry project `a2a-lab` (RG `a2a-lab`, eastus) created minimal —
   the "recommended resources" bundle deliberately declined (AI Search
   idle-billing trap); one AI Services resource + project, nothing else.
2. ✅ `gpt-5-mini` deployed GlobalStandard 50K-TPM — the SAME model the
   lab's OpenAI Agents SDK researcher runs: a same-model×two-platforms
   cell that isolates the platform variable (WS5 isolates framework).
3. ✅ .env: AZURE_* block; deps `azure-ai-projects azure-identity` as the
   `azure` extra; `az login` ADC verified against the project.
4. ✅ API surface mapped — azure-ai-projects 2.3.0 speaks the NEW Foundry
   agent model: agent VERSIONS + PromptAgentDefinition + sessions (not
   the older assistants-style threads/runs — generation-gap finding).
   A2A/MCP are first-class in the SDK: `A2APreviewTool` (outbound — a
   Foundry agent calling external A2A agents), `A2AProtocolConfiguration`
   / `McpProtocolConfiguration` on agent endpoints (inbound), and
   `ProtocolVersionRecord` (the predicted version-negotiation surface).
5. ✅ `a2alab-foundry-researcher` v1 created (prompt agent); first live
   answer via the project's Responses surface with an `agent_reference`
   (13.7s, response id captured). API quirk logged: the `agent` property
   is already deprecated in favor of typed `agent_reference` — preview
   surfaces move fast.
6. ✅ OUTBOUND LEG LIVE (same session): Foundry agent → hosted A2A shim
   → Agent API twin, real Omega CRM data attributed, 40s total. The twin
   consult happens PLATFORM-SIDE (A2APreviewTool) — no client tool loop.
   What it took (each one insight material):
   - **RemoteA2A connection**: the docs' exact ARM payload (category
     RemoteA2A + authType CustomKeys, api-version 2025-04-01-preview,
     tool references the FULL connection id). A hand-guessed
     CustomKeys-category connection resolves but the tool fails with an
     undiagnosable generic 424 — preview error surfaces are poor.
     The connection DOES send custom keys as headers (x-lab-token
     verified on the wire).
   - **0.3-era card compatibility**: Foundry's .NET A2A client rejects a
     pure 1.x card ("missing required properties url/protocolVersion/
     preferredTransport") — lab A2A servers now serve both generations'
     fields on one card (servers/a2a.py).
   - **0.3 JSON-RPC dialect**: Foundry sends message/send +
     kind-discriminated parts; a2a-sdk 1.x servers answer -32601. New
     `servers/a2a_compat.py` middleware makes every lab A2A server
     bilingual (translates request in, Task out; stamps a2a-version 1.0
     inward). The full version spectrum: Google requires 1.0, Microsoft
     speaks 0.3 — the lab now bridges both.
   - **29s API Gateway ceiling bites Foundry**: no client-side retry on
     their side; a cold twin account turn 500s (surfaced as
     tool_user_error with the target URL — good detail when the call
     actually fires). Warmed shim sessions fit. Demo rule: warm first.
   - **Fabrication under tool failure** (v2, before the anti-fabrication
     instruction): when the tool errored, gpt-5-mini INVENTED a CRM
     answer with full "From the CRM (via Agentforce)" attribution —
     wrong opps, wrong owner, marked "At Risk". v3's instructions forbid
     inventing CRM facts; the honest run then listed what the CRM didn't
     return instead. Trust-boundary insight material, measured.
   - Shim hardening found live: the Lambda handler had never applied
     TokenAuthMiddleware (build_app does it; the handler mounts
     create_a2a_app directly) — the public URL served JSON-RPC
     unauthenticated until 2026-07-22. Fixed + env-gated header debug
     added. Foundry twin routing entry added to the shim proxy
     (rider-text channel; SF_FOUNDRY_AGENT_ID once the twin exists).
7. Next: `src/platforms/foundry/` outbound client + `foundry-a2a`/
   `foundry-rest` targets (inbound: "Enable incoming A2A on a Foundry
   agent" — the second platform-native A2A endpoint); Foundry-paired
   twin (SF_FOUNDRY_AGENT_ID); console scenarios; `foundry_source.py`
   (App Insights/KQL); matrix cells; insights (version spectrum,
   fabrication, connection-category trap).

---

## WS4 — LangGraph on LangGraph Platform

**Goal:** the open-source-framework column: a LangGraph research agent
deployed on LangGraph Platform, whose Agent Server exposes A2A
(`/a2a/{assistant_id}`) and MCP natively; LangSmith as the first
*fully queryable SaaS* observability backend.

**Why:** demonstrates framework-vs-platform (the distinction customers
conflate); LangSmith's read API is the perfect foil to OpenAI's
write-only traces; cheap and fast to stand up.

Work items:
- `src/platforms/langgraph/` — agent interior (small graph: research node
  + `ask_agentforce` tool node), deployed via `langgraph deploy` (cloud
  SaaS tier first; self-host later only if the comparison needs it).
- Outbound: generic `A2AClient` at the deployment's A2A endpoint
  (LangSmith API-key auth header) — target `langgraph-a2a`, native. MCP
  cell too (`langgraph-mcp`) — first remote platform serving both.
- Twin: `SF_LANGGRAPH_AGENT_ID`.
- Obs: `langgraph_source.py` over the LangSmith runs/traces API —
  expected to be the richest programmatic column; say so in insights.

**Credentials / setup (LangSmith is new to you):**
1. Sign up at smith.langchain.com (free/dev tier is enough to start;
   Plus tier if we hit deployment limits). Create an org + workspace.
2. Settings → API Keys → create a Personal Access Token →
   .env `LANGSMITH_API_KEY`.
3. `uv add langgraph langgraph-cli langchain` (as a `langgraph` extra);
   deployments happen via the LangSmith UI from a GitHub repo or
   `langgraph-cli` — decide at build time (the lab repo is private; a
   small public deploy repo or CLI path both work).
4. Model key for the agent brain: reuse ANTHROPIC_API_KEY or
   OPENAI_API_KEY (decide at build; a Haiku-tier brain keeps sync budgets
   comfortable).

**Exit criteria:** A2A + MCP native cells green; both directions with the
twin; LangSmith obs source harvesting; insights updated
(framework-vs-platform, observability column).

---

## WS5 — AWS Strands Agents (deferred; on-deck after WS2–WS4)

**Goal:** third framework on the *identical* AgentCore runtime (OpenAI
Agents SDK / Claude Agent SDK / Strands) — isolates the framework
variable at constant runtime; native A2A + MCP serving from a framework
Amazon runs in production (Q, Glue, Kiro).

Reuses WS1's entire deploy path (`deploy/agentcore/deploy.sh strands`
after a third Dockerfile + `src/platforms/strands/`). No new accounts —
embark AWS + an existing model key (Strands is model-agnostic; Bedrock or
Anthropic direct). Decision on scheduling after WS4.

---

## Flagged candidates (user decision pending — do not build)

- **CrewAI (AMP)** — most-adopted OSS multi-agent framework + its new
  platform; would test crew-style delegation against the lab's
  single-agent twins.
- **Pydantic AI** — the typed-Python contrast; A2A support via FastA2A;
  lightest possible "framework" column.

---

## Lab Guide — embedded Q&A agent for the console (idea, 2026-07-22)

**Scheduling (user decision 2026-07-22): after WS3** — the Azure Foundry
interop builds first; the guide then has the fifth platform's story to
tell.

A "Lab Guide" chat in the console, mirroring the mega-demo's Solution
Guide pattern (~/projects/tdx26/mega-demo: `AskClaude.tsx` drawer +
`server.js` streaming proxy + curated-context system prompt + suggested
questions): visitors ask probing questions about how the lab was built —
call paths and protocol seams, the bridge/shim/direct routes, how each
platform's observability API works, the hosted analyst agent, the
insights and how they were measured, how each agent is written and
hosted.

Design sketch (adapting the pattern to this stack):
- **Grounding**: the lab documents itself — README, the ADR log (already
  parsed per-decision by `/api/decisions`), plan/01-architecture,
  02-matrix, 05-observability, 07-workstreams, 08-insights,
  config/targets.yaml + scenarios.yaml. Server-side prompt assembly from
  a curated subset; no separate knowledge base to maintain — the corpus
  IS the repo's plan/ discipline paying off.
- **Endpoint**: console `POST /api/guide` streaming (SSE, same shape as
  the run tail) → `anthropic.messages.stream` with ANTHROPIC_API_KEY
  already in .env. Haiku-tier by default; no session infra needed
  (stateless turns with client-held history, like the mega-demo).
- **Context-aware**: include the operator's current view (open scenario /
  cell / insight) in the system prompt the way the mega-demo injects the
  current slide — "explain THIS call path" works without the user naming
  it.
- **Suggested questions** seeded per section (Insights → "how was the
  interop tax measured?", a cell → "why is this via-shim?").
- **Read tools** (a small server-side tool-use loop, not just stuffed
  context — three read-only tools executed in the console process against
  data access it already has):
  - `list_briefs` / `read_brief` — the hosted analyst's findings briefs
    from Aurora (`PgObsStore.list_briefs`, the same source as
    `/api/obs/briefs`), so "what did the analyst conclude about cold
    starts?" is answerable with citations.
  - `get_trace(trace_id)` — a run's full hop list from the merged
    local+Aurora view (`_merged_events`), payloads clipped to budget, so
    "why did this run take 35s?" or "which twin answered?" reads the
    actual wire record. The UI passes the currently-selected trace id
    with the view context, so "explain this trace" needs no id typed.
  - `list_recent_runs(experiment?)` — recent trace ids grouped per
    scenario/cell, so questions about "the last Agentforce→ADK run"
    resolve to a concrete trace before reading it.
  All tools read-only; no SQL surface (that stays the analyst's, D23) —
  the guide gets curated accessors, not the store.
- **Not** the obs analyst (D22/D23): the analyst interprets harvested
  run data through SQL and writes briefs; the guide explains the lab from
  its docs and can now READ those briefs and individual traces — it
  consumes the analyst's output, never replaces it.
- **MCP wrapper — the meta exhibit**: implement the guide's interior as
  an `AgentAdapter` (`handle(AgentRequest) -> AgentResponse`) and the
  lab's own inbound seam serves it over REST, MCP, AND A2A for free
  (`serve(guide_adapter, protocol, port)` — say :8031/:8032/:8033) —
  the Lab Guide becomes just another lab agent, demonstrable from
  Claude Desktop (or any MCP client) as a source of insights about the
  very experiments that built it. Two tool shapes to demo, deliberately:
  - `ask_lab_guide(question)` — agent-as-a-tool: the lab-side model runs
    the whole guide loop (docs + briefs + traces) and returns a grounded
    answer. One call, works in any MCP client.
  - The raw read tools (`read_brief`, `get_trace`, `list_recent_runs`,
    `get_decision`, `get_insights`) exposed directly on the same MCP
    server — the CLIENT's model does the reasoning over lab data. The
    side-by-side (whose model reasons: the lab's or the caller's?) is
    itself insight material — same question, two integration shapes.
  Local demo: Claude Desktop → streamable-http on localhost. Public
  demo: the cloudflared tunnel pattern (D20) publishes it like the
  other lab servers; x-lab-token app auth as everywhere.
- Demo-facing polish item for the ~Aug 1 public cutover: the guide turns
  the console from an exhibit into a docent.

## Cross-cutting experiment backlog (platform-independent)

- ✅ **Delegation guard (D27, 2026-07-19):** standard caller/depth rider +
  `A2ALAB_MAX_DELEGATION_DEPTH` enforcement at all four delegation seams;
  circular chains now stop with a wire-visible refusal instead of timeout
  starvation. New platforms must route outbound delegation through
  `interop/delegation.py`. Optional follow-up: rider-honoring instruction
  in the Agent Script twins (Salesforce-side prompt stop).
- **Trust-boundary security cell:** malicious payload embedded in a
  delegated agent's answer; per-protocol behavior; do Einstein Trust
  Layer scores (already harvested) flag it? → Security & trust insights.
- **Interop tax lanes (M11.4):** per-hop token/cost accounting across
  platforms; turns the `interop-tax` insight from hypothesis → measured.
- **Async parity:** an OpenAI async brief cell mirroring the CMA
  scheduled pipeline (their SDK has no scheduled hosting — that asymmetry
  is itself the finding).
- **M6 probes:** the empty timeout table (10/30/60/90s) in 03-results.

## Insights pipeline (how findings reach the deck)

`config/insights.yaml` (source of truth, statuses honest) → console
**Insights** section (`/api/insights`) → `scripts/export_insights.py` →
`plan/08-insights.md` → downloadable at `/api/insights.md` → import into
Claude Design for the presentation. Every workstream ends by updating the
yaml and regenerating.
