# Workstreams — the multi-platform buildout (2026-07-19)

The lab's next phase: extend the Agentforce↔Claude↔OpenAI experiments to
the remaining primary agent platforms, keep every addition producing
publishable insights (config/insights.yaml → console Insights section →
plan/08-insights.md → Claude Design presentation), and keep the honesty
rules (D15 real-platform entry, honest matrix statuses, raw wire payloads).

Ordering decided 2026-07-19: **WS1 → WS2 (GCP) → WS3 (Azure) → WS4
(LangGraph) → WS5 (Strands, deferred)**. CrewAI and Pydantic AI are
flagged as candidates only — user decision pending.

**Revised 2026-07-25 after the architecture review** (`tmp-docs/07.25.2026-*`):
WS1–WS3 and WS6 U1–U2 are done; WS4/WS5 remain deferred. The approved next
order is **WS8 (fan-out orchestration) → WS9 (build telemetry) → WS7 (hosted
completion) → WS10 (Agent Fabric comparison)**, with WS9 step 1 pulled forward
immediately because coding-agent telemetry cannot be backfilled. WS8 and WS9
were verified not to depend on WS7 — see the analysis inside WS7.

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
  OpenAI's, recorded in the coverage panel. Registering the source in
  `scripts/obs_harvest.py` is HALF the job: the hosted Lambda
  (`observability/lambda_handlers.py`) and its bundle
  (`deploy/obs/build_zips.sh`) need the same entry and the client library,
  or the platform reads "blocked" hosted forever while local looks fine —
  which is exactly how ADK and Foundry sat missing from Aurora (2026-07-25).
- **Credential rule (2026-07-25):** **AWS auth is the only human login in the
  stack.** Every other platform credential is a SERVICE identity living in a
  Secrets Manager secret and fetched with that AWS auth — never an
  interactive `az login` / `gcloud auth`, never a value that only exists in
  someone's `.env`. Concretely, for a new platform:
  1. create a dedicated service identity with the narrowest read role the
     source needs (`a2alab-obs-harvest` on GCP: logging.viewer +
     monitoring.viewer; the Entra SP on Azure: `Log Analytics Reader` scoped
     to the one workspace);
  2. put its secret in the harvest secret via `deploy/obs/deploy_harvest.sh`,
     never by hand — config no script owns is config nobody updates (D37);
  3. build the client credential **explicitly**. Never
     `DefaultAzureCredential`, `google.auth.default()`-with-ambient-ADC, or
     any other chain that can silently resolve to a developer. Use
     `observability/credentials.py`, which refuses when unconfigured rather
     than falling back;
  4. make failure name the principal — an access error that does not say
     WHICH identity was refused costs an hour.
  The rule is written from a real week-long miss: Foundry's harvest passed
  locally and failed hosted because `DefaultAzureCredential` found Ryan's
  Azure CLI login on the laptop and the service principal in Lambda. The
  green local run was proving a human had access. See the
  `credential-locality` insight.
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

**Credentials:** nothing new — lab-account AWS account (SSO), existing
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
7. ✅ INBOUND LEG + PLATFORM PACKAGE (2026-07-22 second session):
   - Incoming A2A enabled on the agent (PATCH card + protocol config —
     portal/SDK can't set the card yet): the lab's SECOND platform-native
     A2A endpoint. Foundry serves BOTH protocol versions with
     version-specific cards (agentCard/v1.0 + v0.3, one authored card
     projected into both shapes — the same dual-generation answer the lab
     built for its own servers), defaults to 0.3 without a version
     header, Entra-only auth (no key option — cloud IAM above the
     protocol AGAIN, Microsoft edition).
   - `foundry-a2a` cell GREEN via the lab's generic A2AClient + new
     azure-ad auth scheme (17.4s). `foundry-rest` native entry via new
     `src/platforms/foundry/client.py` (Responses surface,
     agent_reference, previous_response_id sessions, response id as
     platform_ref).
   - `deploy/foundry/provision_foundry.py` codifies the whole Azure side
     (connection + agent version from core.FOUNDRY_INSTRUCTIONS + card/
     endpoint PATCH) — validated by provisioning v4 live.
   - Console: foundry-to-agentforce scenario (group live), foundry-api
     protocol badge + Azure-blue chips, cell blurbs/flows.
   - Reliability learnings: gpt-5-mini SKIPS the A2A tool call for CRM
     questions ~half the time under default tool_choice (roleplayed
     lookups, honest refusals, or fabrication — see the updated
     fabricated-attribution insight: hard rules alone stayed ~50%
     fabrication under tool failure). Fixed operationally: scenario
     prompt_suffix mandating the tool + FoundryClient one-retry on
     platform-side tool failure (absorbs the cold shim leg under the
     API GW 29s ceiling). Verified: repeated runs return REAL Omega CRM
     data in 36-44s.
8. ✅ REVERSE DIRECTION LIVE (same session): Foundry-paired twin
   published+activated (`A2ALab_Research_Assistant_Foundry`, agent
   0XxKB000000xdnm0AA, v1 — ADK clone with the direct-route branch
   stripped, action pinned to bridge target foundry-a2a);
   `agentforce-foundry-rest` target; SF_FOUNDRY_AGENT_ID in .env + shim
   Lambda env (twin routing live for rider-text platform=foundry).
   Live pass 30.8s: real CRM sections + Foundry external research over
   the bridge → platform-native A2A leg, rider guard honored (no tool
   callback). Console: agentforce-to-foundry scenario, obs coverage
   panel gains an honest Foundry column (nothing harvested yet — App
   Insights deliberately not attached; response-id retrieval and admin
   APIs noted as the pullable surface). Bridge restarted to pick up the
   foundry-a2a target + azure-ad scheme (full stack restart still
   pending for console UI).
9. ✅ Shim envelope capture (2026-07-22 evening): the WireTap was
   rewritten buffer-and-replay (its passive receive() tee hung under
   Mangum's single-shot bodies — post-replay receives now delegate to the
   real channel under uvicorn for SSE-disconnect semantics and fabricate
   the disconnect only under Lambda) and enabled on the hosted shim: the
   raw inbound A2A envelope — including Foundry's actual 0.3 message/send
   bytes WITH the model-composed D27 rider — now lands in the Aurora
   store next to the proxy's Agent API hops. The foundry→shim leg is dark
   only on Microsoft's side of the wire. Also: foundry-rest now forces
   tool_choice=required (target option) — the deterministic fix for the
   ~50% skipped-delegation flake; verified 3/3 runs firing the tool with
   envelopes on the wire.
10. ✅ OBS COLUMN LIVE (2026-07-23): App Insights (a2a-lab-appinsights →
   workspace a2a-lab-logs, workspace-based, free-tier volume) attached to
   the project via an AppInsights-category connection; agent runs emit
   AGENT-SEMANTIC OTel gen_ai spans — invoke_agent, chat (token usage +
   full messages), execute_tool (the platform's own timed record of
   calling the lab's shim). `foundry_source.py` harvests over KQL
   (azure-monitor-query; AZURE_LOGS_WORKSPACE_ID); sessions keyed by
   gen_ai.response.id = the lab's platform_ref, so the store's
   trace↔session join works out of the box. Coverage panel updated;
   observability-fragmentation insight extended (best column yet;
   "five platforms, five answers").
11. ✅ CROSS-HYPERSCALER CELL LIVE (2026-07-23): google-adk-to-foundry —
   GCP Gemini (Agent Engine) consulting Azure gpt-5-mini (Foundry) over
   BOTH platforms' native A2A endpoints, 16.9s, both sections labeled,
   D27 rider honored. Auth: Entra service principal
   (a2alab-adk-caller, Foundry User role on the account) in the engine
   env — DefaultAzureCredential's EnvironmentCredential path; the lab
   A2AClient's azure-ad scheme works unchanged from inside GCP. The
   REVERSE direction (Foundry→ADK) is auth-blocked and recorded as such:
   Foundry connections cannot mint Google IAM tokens. New ask_foundry_agent
   tool (D27-guarded) + cross-cloud nav group + scenario; insight
   capstone added to native-a2a-young ("cloud identity decides who may
   call whom; the card says nothing about it").
12. Remaining for WS3 close-out: component/screenshot rows; final
   03-results sweep; insights pass over the full workstream.

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
lab-account AWS + an existing model key (Strands is model-agnostic; Bedrock or
Anthropic direct). Decision on scheduling after WS4.

---

## WS6 — User identity layer: per-user experiments + cross-platform user-context propagation (planned 2026-07-24)

**Goal.** Two layers, one experiment. (1) A **lab user layer**: named
users run experiments; every run, trace, and observability row carries
who ran it; data reads (the guide's `get_trace`, the console's traces
and obs tables) are authorized per user. (2) The **research question**
this lab exists to ask: can USER context propagate across platform
boundaries over REST/MCP/A2A — verifiably, not just as words — and can a
remote platform act *on behalf of* that user? This picks up the
anti-pattern audit directly (D37): its measured harm of one shared
integration user (every remote audit trail attributing a generic
identity), F6 (per-caller identities), E3 (identity & scope measurement,
in the experiment backlog below), and the capstone observation that
*agent identity lives outside every agent protocol today* — WS6 tests
whether USER identity does too.

**Current baseline (honest, revised 2026-07-24 after D36/D37).** One
shared app token (`A2ALAB_TOKEN`, `x-lab-token` or bearer header — the
`?token=` query form is gone, D36) authenticates *service* callers to lab
seams; browsers now sign in as a persona and hit a server-side role gate,
so the browser surface is no longer one identity for everyone. Every
PLATFORM credential is still a service identity: AWS SSO/IAM, GCP ADC +
Entra SP, Anthropic/OpenAI API keys, and Salesforce client-credentials
ECAs → a single run-as integration user (`bypassUser: true`). What F6
changed is the *app*, not the user: each hosted seam now presents its own
External Client App (`a2a_lab_claude` / `_openai` / `_shim` / `_obs`), so
Salesforce login history attributes per CALLER even though every one of
them still resolves to the same run-as user. That gap — per-caller app,
shared end user — is exactly the seam WS6 exists to close. The guide's
Postgres reads never touch the browser: tools run in the console process
against the RDS Data API using host AWS credentials + secret ARNs; the
lab token only gates the HTTP surface in front of them. There is still no
end-user concept in the PLATFORM legs — by design, until now.

**Design principle (the D27/D34 lesson, applied to identity).** Ship user
context on TWO channels at once and measure the difference:
- a **verifiable channel** — a lab-issued JWT (RS256; any seam or hosted
  runtime verifies with the public key, no shared secret) riding the
  protocol's native slot;
- the **text channel** — an `on-behalf-of: <user>` line in the D27 rider,
  which survives every hop the way `caller-agent`/`lab-trace` do but
  *proves nothing* (any caller can type it).
The asymmetry IS the finding: text survives everywhere and verifies
nowhere; signatures verify but drop at hops that strip metadata. Cells
report `verified` / `asserted-only` / `dropped` per platform × protocol.

**Status:** U1 ✅ + U2 ✅ built and live-verified 2026-07-24 (hygiene
first: F2 credential scrub + F7 rider versioning, both verified against
tracing/obs with a live run and a retroactive Aurora/sqlite scrub of 2
historical rows). Working now: console sign-in (users.yaml, RS256 lab
JWTs, JWT-only auth), both channels on the wire over all three protocols
(loopback-proven: REST body+Authorization, MCP tool arguments — the
protocol's only carriage, A2A message metadata), on-behalf-of in the
rider at every delegation seam, and the F2 interplay working as designed:
the JWT rides the wire but lands in traces as [REDACTED-JWT] while
user_context stays visible. Deploy scripts ship A2ALAB_JWT_PUBLIC_KEY to
the runtimes (verification only — the signing key never leaves the
laptop). Next: U3 enforcement.

**Milestones:**

1. **U1 — lab identity provider.** `config/users.yaml` (demo users +
   roles: operator / viewer), RS256 keypair under `.a2alab/`, lab-issued
   JWTs, console login (user picker for demos; the shared token keeps
   working as a legacy/service credential so nothing breaks).
   `TokenAuthMiddleware` learns to accept either.
2. **U2 — user context on the wire.** `metadata["user_context"]`
   ({sub, name, roles}) + the JWT in each protocol's native slot —
   `Authorization: Bearer` (REST), tool argument (MCP has no session or
   auth semantics for this — that asymmetry again), message metadata
   (A2A) — mirroring exactly how trace_id rides today. Rider gains
   `on-behalf-of:`. Every delegation seam forwards both channels.
3. **U3 — enforcement + data scoping.** Seams verify the JWT when
   present (invalid → refuse; text-only → tag `asserted-only`).
   `TraceEvent` gains a `user` field; the guide's `get_trace`/
   `list_recent_runs`, console traces, and obs tables filter by
   role (viewer: own runs; operator: all). `A2ALAB_REQUIRE_USER=1`
   strict mode for the demo of enforcement actually refusing.
4. **U4 — platform on-behalf-of cells (the measured comparison).**
   Which platforms can act AS the lab user, not just be told about them:
   - **Salesforce**: per-user JWT bearer flow (subject = the lab user)
     so the twin session runs as that user vs the O1 baseline — F6's
     per-caller ECAs, measured via session-log attribution (extends the
     rider-provenance harvest from caller-agent to user).
   - **Foundry**: Entra On-Behalf-Of — user assertion exchanged for a
     downstream token (the one true OBO primitive in the lab's estate).
   - **Google Agent Engine**: per-user impersonation is expected
     BLOCKED for external identities — recorded honestly, like the
     Foundry→ADK auth block.
   - **Anthropic / OpenAI**: no end-user identity primitive on the API —
     metadata-only cells, status `asserted-only` by construction.
5. **U5 — matrix + insights.** New matrix section "user-context
   propagation" (platform × protocol × verified/asserted/dropped); new
   insights category **Identity & authorization**. Expected shape of the
   findings (to be measured, not assumed): agent protocols carry
   *conversation* identity (A2A contextId) but no *user* identity slot;
   A2A card securitySchemes authenticate the CALLER to the ENDPOINT,
   not the user to the chain; OBO exists only inside each cloud's own
   IdP boundary — cross-cloud user delegation today is a trust
   convention, not a protocol feature.
6. **U6 (stretch) — standards alignment.** MCP's OAuth 2.1
   resource-server auth on the guide's MCP server (real spec auth
   replacing x-lab-token on one exhibit), RFC 8693 token exchange at the
   bridge (the standards-shaped version of what U2 hand-rolls), A2A
   cards advertising securitySchemes. Each one becomes a
   convention-vs-standard comparison cell.

**Hygiene folded in (from the antipattern analysis) — all landed ahead of
WS6, 2026-07-24:** browser auth moved from `?token=` query strings to the
JWT in headers (D36); the trace redaction pass shipped before
user-attributed traces became multi-user-visible (F2); and the Salesforce
scope diet arrived as per-caller ECAs rather than as an edit to U4's
future ones (F3/F6, D37) — so U4 inherits a per-caller app per seam and
only has to add the USER dimension on top.

**Sequencing.** U1–U3 are lab-only (no platform work, ~same shape as the
D34 trace threading — every seam already routes through
`delegation.delegate()`). U4 is where platform reality bites and the
deck material lives. Schedule: user decision — candidate for the next
build day; U1–U3 in one sitting, U4 one platform at a time (Salesforce
first: it has both the baseline harm and the richest attribution logs).

---

## Sequencing decided 2026-07-25 (post-architecture review)

Approved order: **WS8 (fan-out orchestration) → WS9 (build telemetry) → WS7
(hosted completion) → WS10 (Agent Fabric comparison)**, with WS9's step 1
pulled forward to *today* because coding-agent telemetry cannot be backfilled.

**WS8 and WS9 do not depend on WS7** — see "Can AD1/AD2 run before the hosted
workstream?" inside WS7. That was the deciding question and the answer is yes,
with one narrow caveat (the CMA orchestrator's tool execution) that has a
one-day workaround rather than needing the whole workstream.

---

## WS7 — Hosted completion: retire the laptop from the runtime path

**Goal.** Every component the lab demonstrates runs in a cloud someone else
operates. Today "hosted mode" is far narrower than it sounds and the gap is
worth stating precisely:

- **21 targets total; 11 point at `localhost`.**
- **`A2ALAB_MODE=hosted` remaps exactly 2 of those 11** (`claude-rest`,
  `openai-rest` → the AgentCore runtimes).

So nine local targets have no hosted counterpart at all: the Lab Guide's three
(`guide-rest/mcp/a2a`), the Claude and OpenAI MCP and A2A servers (four), and
the two Agentforce shim ports whose hosted twin exists only for A2A (D28). They
run on a laptop and reach the world through the cloudflared tunnel.

**Why it has to happen regardless** (both reasons are yours, and both hold):
1. It is the target end state for the deployed solution.
2. **WS10 needs it.** MuleSoft Omni Gateway has to reach the lab's A2A agents;
   today that means a tunnel to a laptop, which is fine for an experiment and
   wrong for a customer-facing comparison.

A third reason worth adding: the honest matrix currently cannot say the lab is
hosted. The one-liner it *can* say today is *"hosted mode covers the two
runtimes that have hosted counterparts; the rest is local-only, published
through a tunnel."*

### Can AD1/AD2 run before this workstream? — yes, with one caveat

| Work | Hosted-independent? | Why |
|---|---|---|
| WS9 — build telemetry | ✅ **Fully** | Claude Code/Codex → CloudWatch managed OTLP; `coding_source.py` runs in the harvest Lambda. No lab server involved |
| WS8 — Claude (AWS) ↔ ADK | ✅ **Fully** | Both ends already hosted (AgentCore, Agent Engine) |
| WS8 — **ADK orchestrator** | ✅ **Fully** | Runs inside the Agent Engine container calling hosted endpoints |
| WS8 — **CMA orchestrator** | ⚠️ **One caveat** | See below |

**The caveat, precisely.** Managed Agents custom tools execute **host-side**:
`managed_backend.py` receives `agent.custom_tool_use` and answers with
`user.custom_tool_result`, and for the async pattern `briefs --watch` is the
process that services those calls while the session idles. That watcher runs on
the laptop today (started by `run_local.sh`). A CMA orchestrator whose three
fan-out legs are custom tools therefore has a laptop in its path.

Three ways out, in increasing cost:

- **(a) Accept the watcher for the build phase.** Exactly how the existing D16
  async brief already works, and it proves the experiment. **Recommended for
  building WS8.**
- **(b) Host the watcher** — an EventBridge-scheduled Lambda polling for
  pending deployment runs and servicing tool calls. ~1 day, and it
  independently un-tethers the *existing* async brief, which is a demo risk
  today. **Recommended before any public demo.**
- **(c) Remote MCP fan-out server** — expose the three legs as MCP tools on a
  Lambda Function URL, reusing `src/obs_mcp/` (a hand-rolled MCP Streamable
  HTTP transport already running as a Lambda Function URL for the obs analyst,
  D23). Then the CMA agent needs no host process at all — this is exactly the
  "hosted, the deployment's firings need no watcher" note in
  plan/05-observability.md. ~1–2 days. **This is the right end state and it is
  a WS7 item, not a WS8 blocker.**

**So: build WS8 and WS9 now under (a); do (b) before the demo; fold (c) into
WS7.**

### Work items

1. **Hosted Lab Guide** (3 targets) and
2. **Hosted Claude/OpenAI MCP + A2A faces** (4 targets)

   **These two collapsed into one mechanism once item 7 landed (2026-07-26), and
   the original framing of item 2 was asking the wrong question.** It posed the
   choice as "additional AgentCore runtimes per protocol, or one runtime
   multiplexing protocols." The answer is neither, for the same reason the
   bridge is not on API Gateway:

   - An **A2A face delegates onward**. That is agent work on an open-ended
     budget, the shape that does not fit a request/response product — measured
     the day of cutover, when a cold Agent Engine leg took 39.8s against a 30s
     gateway ceiling.
   - **WS11's fire-then-poll needs a server that can hold a task across
     requests.** Both AgentCore's invoke model and an API Gateway integration
     fight that; a long-lived container does not.
   - **WS10 needs external callers to reach these faces.** AgentCore's data
     plane is SigV4-only, which MuleSoft's Omni Gateway cannot easily present.

   **So: the same ALB, host-based listener rules, one ECS service per face.**
   The marginal cost of each additional face is a target group, a listener rule
   and a Fargate task — **not another load balancer**, which is what made this
   look expensive before item 7 existed.

   Three things already done make this mostly repetition rather than design:

   - **The Origin certificate is a wildcard** (`*.agenticthings.com`), so every
     one of these hostnames is already covered. No new certificates, no new ACM
     imports, no validation records.
   - **The hostnames already exist and already point at the tunnel** —
     `claude-rest-lab`, `claude-mcp-lab`, `claude-a2a-lab`, `console-lab`. Each
     cutover is the identical DNS edit performed for `bridge-lab`, one at a
     time, each independently reversible.
   - **The servers already take `--protocol` and `--port`**, and
     `deploy/agentcore/Dockerfile` already builds them. Either one task per
     face, or one task running several containers to save cost.

   Suggested order, cheapest risk first: Lab Guide (nothing depends on it) →
   Claude MCP → Claude A2A → OpenAI pair. Keep `cloudflared` running throughout;
   DNS alone decides which origin serves each hostname, so a bad cutover is a
   one-field rollback.

   **Cost note:** each face is roughly a $10–12/month Fargate task on lab-account
   with no new ALB charge. Consolidating several faces into one task cuts that
   further and is worth doing if the count grows past three or four.
3. **Hosted watcher** — item (b) above.
4. ✅ **Remote MCP fan-out server** — item (c) above, **done 2026-07-26 (D41)**.
   `src/fanout_mcp/` exposes one tool per business unit on a Lambda behind API
   Gateway; the transport moved to `src/mcp_http/` now that two servers share
   it. The CMA orchestrator selects the topology per run through
   `agent_with_overrides` — same agent, two tool inventories — so the host-side
   variant survives as a control rather than being replaced.

   **Measured:** the model issued all three units in a single turn on both runs
   (traces `ede9e3bc…` 3/3 50.5s, `161d7a46…` 3/3 42.6s) and reported its own
   coverage correctly without code computing it. The cost is a request budget
   the host-side path does not have — API Gateway's 29s integration timeout,
   not raisable for HTTP APIs. Full numbers in plan/03-results.md.

   Also lands the AWS→GCP half of cross-cloud identity (`interop/cloud_auth.py`):
   the Lambda federates its IAM role into a Google service account and holds no
   Google key. **This does not close item (b)** — the hosted watcher is still
   needed for the D16 async brief, which remains laptop-bound.
5. **Widen the `modes:` map** in `config/targets.yaml` as each hosted
   counterpart lands, so one flip really does move the whole stack.
6. **Retire or re-scope the tunnel.** Once 1–2 land, cloudflared should be a
   convenience for local development, not the lab's public front door.
7. **Host the bridge — on Fargate behind an ALB, NOT behind API Gateway.**
   See the section below; this item was missing from the plan until the
   45s/30s conflict was noticed on 2026-07-26.

### Item 7: hosting the bridge, and why it does not get the shim's treatment

**The bridge is the actual laptop dependency in Path A.** The Apex callout
resolves `A2ALab_Bridge` to `https://bridge-lab.agenticthings.com`, the D20
named Cloudflare tunnel, which terminates at `uv run python -m bridge` on
`:8100`. Nothing in that path is AWS today.

**The conflict, stated precisely.** The obvious move is to copy the shim: a
Lambda behind an API Gateway HTTP API (D28). That works for the shim because
its work fits — 10.1s measured on a simple question, 18–19s when the Agentforce
twin delegates onward, comfortably inside the gateway's ceiling with the
`targets.yaml` warm-up ping. **The bridge does not fit.** Its client timeout is
45s (plan/01-architecture.md, Path A budget chain: action ~85–90s → Apex 110s →
bridge 45s), and an HTTP API's integration timeout maxes at **30s, hard and not
adjustable** — the adjustable 29s account quota governs REST APIs only. Hosting
the bridge that way silently cuts Path A's sync research depth by 15s.

Two components, one ceiling, opposite verdicts. That is the whole decision.

**Options considered:**

| Option | Ceiling | Verdict |
|---|---|---|
| **Fargate behind an ALB** | ALB `idle_timeout.timeout_seconds` defaults to 60s and is a configurable load-balancer attribute | **Chosen.** The bridge is already a FastAPI app; it runs unchanged. Costs a standing ALB charge. |
| Lambda + REST API (v1) | The filed 29s quota, if granted | Migrating gateway *products* for one component, on a pending request that may cost account throttle headroom |
| Lambda Function URL | 15 min, no gateway | **Two unverified blockers**: whether the org SCP permits it (the D23 finding was specifically about *auth-NONE* needing `AddPermission`; IAM-auth same-account may differ) and whether Salesforce can SigV4-sign a callout — the docs could not confirm it. A question, not a plan. |
| Don't route through the bridge | n/a | Partly available already: **D30** proved Apex can call Agent Engine's A2A endpoint directly via a JWT-bearer cert chain. Narrows the bridge's job but does not remove it — the bridge exists for protocol fan-out Apex cannot speak. |

**The reasoning worth keeping.** The lab has just published a finding (D41) that
moving agent work behind a managed request/response gateway imposes a budget it
did not have. Hosting the bridge on a request/response product would be adopting
that constraint deliberately, one workstream after documenting it. Fargate is
the option where the bridge keeps its measured budget without a rewrite, a
pending quota, or an unverified vendor capability.

**Applies to item 2 as well.** The hosted Claude/OpenAI **A2A** faces have the
same shape as the bridge, not the shim: an A2A call that delegates onward is
agent work on an open-ended budget. Decide their ingress with this ceiling in
hand rather than defaulting to the D23/D28 pattern because it is familiar.

**Exit criteria:** `A2ALAB_MODE=hosted` with the laptop powered off, a matrix
run green, and the console reachable — that is the actual test, and it is worth
recording as a measured result the first time it passes. For item 7
specifically: Path A green end-to-end with the tunnel down and `bridge-lab`
resolving to the ALB, and a recorded latency showing the 45s budget intact.

**Effort:** ~1 week, dominated by item 2; item 7 is ~1 day.

---

## WS8 — Fan-out orchestration: the lab's missing shape (AD1, approved 2026-07-25)

**Status 2026-07-26 (overnight build).** Dispatch layer built, tested, and
**proven live**: two legs in parallel across GCP and Azure, 36.7s wall against a
50.7s serial equivalent, and the partial-failure contract verified against a
real dead leg rather than an injected one (plan/03-results.md).

| Item | State |
|---|---|
| `src/orchestration/` — legs, parallel dispatch, partial-failure contract | ✅ built, 10 unit tests |
| `scripts/run_fanout.py` — CLI runner, exits non-zero on a partial run | ✅ built, run live |
| Scenario + business case + mermaid diagram + `fan-out` nav group | ✅ |
| Insight `orchestration-topology` | ✅ `observed`, `review: required` |
| **Five dedicated per-leg agents** | ⬜ **not deployed** — see below |
| Claude (AWS) ↔ ADK pair | ⬜ blocked on AWS auth |
| Four-platform join-rate measurement | ⬜ needs all legs + a harvest |

**Read this before continuing: the live run used the lab's EXISTING
general-purpose research agents, not the dedicated per-leg agents this
workstream specifies.** That was deliberate — it proved the dispatch layer
without waiting on provisioning — but it means the measured numbers describe
the plumbing, not the scenario. The answers came from research agents being
asked a logistics question, and it showed: see the instruction-adherence
finding in plan/03-results.md.

Swapping to dedicated agents is a **config change, not a code change** —
`orchestration/legs.py` reads `A2ALAB_LEG_EXPOSURE_TARGET`,
`A2ALAB_LEG_COMMERCIAL_TARGET` and `A2ALAB_LEG_COMMS_TARGET`, so each leg
repoints via `.env` once its agent exists.

**Goal.** One long-running orchestrator farms a single business task out to
three agents on three different platforms *in parallel*, then synthesises one
answer — built twice, once with an Anthropic Managed Agent and once with Google
ADK on Agent Engine, so the two async models can be compared directly.

**Why.** Every one of the lab's 12 scenarios is 1:1. Fan-out is a different
shape with different failure modes, and it is the shape real enterprises
actually have. It stresses three things nothing in the lab currently touches:

1. **The delegation guard under fan-out.** D27's depth limit was designed for
   chains. A supervisor calling three platforms is depth-1 three times — does
   the rider survive fan-out, and do the delegates correctly refuse to call each
   other sideways?
2. **Observability across a genuinely distributed task.** Today a lab trace
   spans at most two platforms' logs; this spans four, each with different
   retention, lag, and join key. Only platforms that log the utterance text can
   be joined by the D34 rider convention — Foundry cannot, and joins by
   `platform_ref` instead. **How many of four legs can be joined back to the run
   is the deliverable number.**
3. **Partial failure.** At fan-out, partial failure is the normal case, not the
   edge case. This is the multi-agent form of the finding measured on
   2026-07-25: Agentforce returning 200 with its section heading present and the
   delegated content silently absent.

### Scenario A — Supplier disruption response (the business case)

**Document this in the scenario's own `description` in `config/scenarios.yaml`,
not only here** — the console renders it, and the business case is the point.

> **The situation.** A port strike halts container traffic through a major
> hub. A multinational manufacturer needs to know, within the hour: which
> orders are exposed, what the contracts say about delay penalties and force
> majeure, and what to tell affected customers — then act on all three.
>
> **Why it needs multiple agents.** These three questions are owned by three
> different functions, grounded in three different systems, and — in a company
> of any size — served by three different AI platforms chosen by three
> different business units at three different times. Logistics runs on Google
> Cloud. Commercial/legal runs on Microsoft. Customer operations standardised
> on OpenAI. Nobody is going to re-platform because a strike started at 6am.
>
> **Why it needs orchestration rather than a chain.** The three questions are
> genuinely independent — exposure does not depend on the contract position,
> and neither depends on the customer message. Run sequentially they take the
> sum of three latencies; run in parallel they take the slowest. In a
> disruption that difference is the whole value.
>
> **Why async.** Nobody sits watching a spinner during a port strike. The work
> is dispatched, runs for minutes, and lands in the systems people already
> use — which is what the lab's D16 async pattern already proved by delivering
> briefs into CRM records rather than into an HTTP response.
>
> **The measured claim this scenario supports:** an enterprise does not need
> one agent platform. It needs one *protocol* and an honest account of what
> crossing platform boundaries costs — in latency, in identity plumbing, and in
> the observability you lose at each seam.

**Platform assignment — the business-unit story:**

| Role | Business unit | Platform | New agent needed |
|---|---|---|---|
| Orchestrator (variant 1) | Corporate operations | Anthropic Managed Agent | ✅ `a2alab-supply-orchestrator` |
| Orchestrator (variant 2) | Corporate operations | Google ADK / Agent Engine | ✅ `a2alab-supply-orchestrator-adk` |
| Exposure assessment | Logistics | Google ADK / Gemini | ✅ `a2alab-logistics-agent` |
| Contract & penalty position | Commercial / Legal | Microsoft Foundry (gpt-5-mini) | ✅ `a2alab-commercial-agent` |
| Customer communications | Customer operations | OpenAI on AgentCore | ✅ `a2alab-customer-comms-agent` |
| Delivery / system of record | Sales & service | Agentforce | ♻️ reuse the D16/D17 brief delivery path |

**Dedicated scenario agents, per your instruction — and it follows D25's twin
rule.** Each leg gets its own agent on its own platform, scoped to this
scenario only, rather than reusing the general-purpose research agents. Two
reasons beyond tidiness: the general agents carry research prompts that would
drift the answers, and separate agents mean the observability harvest can
attribute a session to *this experiment* rather than to "the ADK agent." Note
that ADK hosts **two** agents in variant 2 — orchestrator and logistics leg are
different agents on the same platform, which keeps the comparison clean and
incidentally tests intra-platform vs cross-platform delegation in one run.

**Determinism shaping** (same technique as the existing `[A2A-LAB DELEGATION]`
and `[A2A-LAB ROUTING]` blocks, so call paths are comparable run to run):
- Each leg receives a rider pinning it to **one turn, no external tool calls**,
  and a fixed output shape (3 bullets, ≤60 words).
- The orchestrator gets a **fixed leg list** — no dynamic routing — so the call
  path is identical every run.
- Delegation depth stays 1; the legs must refuse onward delegation.

**Architecture diagram — required deliverable.** Add a mermaid diagram to
`config/diagrams.yaml` naming this scenario's id, showing: the orchestrator, the
three parallel legs with their platform and cloud, the delivery leg, and where
each platform's execution logs are harvested from. Set `readme: true` so it is
embedded in README.md as well (`tests/unit/test_diagrams.py` asserts the two
copies stay identical). The chip on the insight tile is the readout affordance.

### The CMA vs ADK comparison (why build it twice)

These are not two implementations of one pattern — they are two different
patterns sharing the word "async," and that is the insight:

| | Anthropic CMA | Vertex AI Agent Engine |
|---|---|---|
| Async model | Cron-scheduled; each firing is a **new session** | **Duration-based**; one agent runs up to 7 days holding state |
| State between runs | Memory / explicit persistence | Session state + Snapshot API |
| Natural fit | Recurring work ("brief me every morning") | A single mission ("watch this until it resolves") |
| Lab status | Proven (D16/D17: 69–127s briefs delivering into CRM) | Not yet exercised async |

**Expected finding:** picking the wrong one is an architecture mistake that no
amount of protocol compatibility fixes. A disruption response is a *mission*
(duration-shaped); a daily account brief is *recurring* (cron-shaped). Same
fan-out, opposite hosting choice.

**Foundry and OpenAI have no comparable long-running/scheduled hosting** —
record that honestly rather than working around it, same as OpenAI's write-only
traces.

### Other scenarios — documenting *why* this shape recurs

Not to be built; documented so the pattern reads as general rather than as one
contrived demo. Each is a real domain where the same three properties hold —
independent sub-questions, different owning functions, different platforms.

**B — Third-party / vendor risk due diligence.** Clear a new vendor: parse
security evidence (SOC 2 / ISO 27001 / PCI DSS), check commercial standing,
screen sanctions and adverse media, produce a go/no-go. Security, Finance and
Research own the three questions and none depends on the others. Reported
outcomes in this space run 40–70% labour reduction and 30–50% faster cycles, so
the ROI framing is easy. *Why it recurs:* every regulated enterprise runs this
process continuously and hates it.

**C — Security incident triage.** An alert fires; specialised agents reason
concurrently over endpoint, identity, and network evidence rather than one
agent working sequentially. *Why it recurs:* time-to-triage is the metric, and
sequential analysis is the bottleneck. **This is also where partial failure
matters most** — a triage that silently drops a leg is worse than one that
fails loudly, which is exactly the failure mode measured on 2026-07-25.

**D — M&A / deal-team diligence.** Financial model, legal exposure, and market
position assessed in parallel against a target company, synthesised into an
investment memo. *Why it recurs:* deal timelines are fixed and the three
workstreams are owned by different advisors — often literally different firms
on different tech stacks, which is the cross-organisation version of the
cross-business-unit story.

**The common shape, stated once:** *when sub-questions are independent, owned
by different functions, and answered by different platforms, orchestration is
not an optimisation — it is the only structure that matches the org chart.*

### Build items

1. Five new platform agents (table above) + their deploy/provision scripts,
   following each platform's existing pattern.
2. Orchestrator delegation tools through **`interop/delegation.py`** — D27 is
   non-negotiable for new delegation paths, and this experiment exists partly to
   stress it.
3. Fan-out with `asyncio.gather`; each leg its own `Hop` under one `trace_id`.
4. **Console call-path renderer** — verify it can draw parallel legs rather than
   assuming a chain. Likely the one real UI change.
5. **Partial-failure policy, decided before the first run:** deliver with an
   explicit `[leg unavailable: <platform>]` marker. Never a silently short
   brief.
6. Scenario entries + business-case description + `config/diagrams.yaml` entry.
7. Post-run harvest across all five platforms; record how many legs join back.
8. New insight category **Orchestration topology**; mark `review: required`.

**Exit criteria:** both orchestrator variants green; the join-rate number
measured and recorded; partial failure demonstrated deliberately; insight
published for sign-off.

**Effort:** ~3–4 days for variant 1, ~1 day for variant 2, plus the
Claude(AWS)↔ADK pair (~0.5 day) which lands first as a warm-up.

---

## WS9 — Build telemetry: what this lab cost to make (AD2, approved 2026-07-25)

**Status 2026-07-26 (overnight build).** Everything that does not need AWS is
done; the one step that does is the one that matters most.

| Item | State |
|---|---|
| `src/observability/coding_source.py` + 6 tests | ✅ built; namespaces discovered, not hardcoded |
| Registered in local harvest, Lambda map, and bundle (the Obs rule) | ✅ |
| `/api/build-telemetry` + Build Telemetry console section | ✅ built, 3 tests |
| Coding telemetry excluded from the Observability coverage panel | ✅ test-locked |
| **Exporters switched on** | ✅ 2026-07-26 — key issued, helper wired, ingestion verified |
| `observability/promql.py` | ✅ **the harvest was reading the wrong API** — see below |
| First real coding metrics | ✅ 2026-07-26 — `1 tool-day, $2.82 modelled`, after a second query fix |

### The correction that mattered

**OTLP metrics ingested through CloudWatch's native endpoint do not appear in
`ListMetrics` or `GetMetricStatistics` at all.** They land in a
Prometheus-compatible store queried over SigV4 at
`monitoring.<region>.amazonaws.com/api/v1/*`.

The first `coding_source` used ListMetrics. Ingestion returned HTTP 200 and
discovery returned nothing, so **both halves looked healthy** and the harvest
would have reported "no coding metrics yet, switch the exporters on" forever
while the exporters worked perfectly. It was only found by sending a real
metric and failing to read it back — a reminder that a source which degrades
politely to "nothing here yet" can hide a total failure indefinitely.

Verified rather than assumed, and worth keeping: a PromQL selector must name a
metric; `/api/v1/label/__name__/values` is unsupported, so **there is no metric
enumeration** and the name list must be fixed and extendable.

### The second correction — same failure mode, one layer down

Reading the right API was not enough. With the exporters confirmed working, the
harvest still reported "no coding metrics — switch the exporters on" while this
very session's `session.id` was queryable in CloudWatch. Two independent bugs,
both measured on identical live data 2026-07-26:

- **Step alignment.** CloudWatch aligns a range query's evaluation points to
  epoch multiples of the step. Stepping daily put the last evaluation point at
  last midnight, so everything recorded since was invisible — 0 series at step
  86400 / 3600 / 900 / 600, and 4 series at step 300. A build-cost view that is
  permanently a day behind is indistinguishable from an exporter that is off.
  The step is now 300s; the daily rollup was always in Python
  (`summarize_series`) and stays there.
- **Wrong aggregation.** These are delta-temporality Sums, so each datapoint is
  already a delta and rate-style functions are actively harmful on them.
  `increase(...[5m])` read 2,531,607 tokens and silently dropped 4 of 8 series
  (it needs two samples); a bare selector read 3,160,892 by repeating the last
  sample through its 5-minute lookback; `sum_over_time` read 2,870,521 —
  **identically at step 60 and step 300**, and that resolution-independence is
  what identifies it as the correct one.

**The pattern worth naming, because it has now cost three separate debugging
sessions on this one source:** the harvest's failure message is a helpful
instruction ("switch the exporters on"), and a helpful instruction is a claim
about the cause. Each time, the cause was on the reading side and the message
sent the reader to the writing side. The `except: continue` around each metric
query made it worse by discarding the evidence. Query failures now appear in
the harvest detail, so "nothing was recorded" and "nothing could be asked for"
no longer read the same.

### Credential handling followed D39, not the vendor quickstart

The AWS guide has you paste the bearer token into a shell profile. Instead: an
IAM user with `CloudWatchAPIKeyAccess`, a 90-day service-specific credential
(expires 2026-10-24), the key written **straight into Secrets Manager without
passing through a terminal**, and `scripts/otel_headers.sh` fetching it at
runtime via `otelHeadersHelper`. A token pasted into a config file is exactly
the long-lived laptop credential D39 exists to remove.

**Also corrected against the AWS docs, three counts the first draft got wrong:**
the `/v1/metrics` PATH is required; the protocol is `http/protobuf`, not
`http/json`; and a metrics bearer token **cannot carry logs or traces**, so
`OTEL_LOGS_EXPORTER` against the same endpoint silently yields nothing.

**Goal.** Capture Claude Code and Codex OpenTelemetry into CloudWatch, then
surface it in the console as its own section — the lab measuring its own
construction, across two coding tools, as if two team members used different
ones.

### ⚠️ Step 1 is time-critical

**Telemetry is not retroactive.** The ~16 days already spent building the lab
are gone. Turn the exporters on before the WS8 work starts, so the fan-out
build is measured. Everything else here can wait; this cannot.

```bash
CLAUDE_CODE_ENABLE_TELEMETRY=1
OTEL_METRICS_EXPORTER=otlp
OTEL_LOGS_EXPORTER=otlp
OTEL_EXPORTER_OTLP_PROTOCOL=http/json
OTEL_EXPORTER_OTLP_ENDPOINT=https://monitoring.us-east-1.amazonaws.com
OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer ${CW_BEARER_TOKEN}"
OTEL_RESOURCE_ATTRIBUTES="project=a2a-lab,tool=claude-code,developer=ryan"
```

CloudWatch exposes a **managed OTLP endpoint** — no collector, no sidecar; auth
is a CloudWatch API bearer token on an IAM user with `CloudWatchAPIKeyAccess`.
Codex CLI ships its own OTel exporter; point it at the same endpoint with
`tool=codex`. **CloudWatch Coding Agent Insights** then renders both (it
supports Claude Code, Codex and Copilot) with no further setup.

### Console placement — a separate section, not a sixth platform column

**Recommendation: its own console section, sharing the harvest plumbing.**

I originally sketched this as a sixth column in Observability and I now think
that is wrong. The Observability coverage panel answers *"what did the agents
do, per platform"* and its honesty depends on all five columns being the same
kind of thing. Claude Code is not a partner agent platform — it is the tool
that built the lab. Putting it in that panel would quietly corrupt the
five-platform story that is the section's whole point.

- **New section: `Build Telemetry`** — setup documentation + a dashboard
  (cost and tokens by tool and model, sessions, active time, lines of code,
  commits/PRs, tool-decision accept/reject).
- **Shared plumbing:** `src/observability/coding_source.py` implementing
  `PlatformLogSource`, registered in `scripts/obs_harvest.py` **and** in
  `observability/lambda_handlers.py` + `deploy/obs/build_zips.sh` (the Obs rule
  — half-registration is what left ADK and Foundry missing hosted).
- **No new credentials.** It reads CloudWatch in the lab-account account with the AWS
  auth D39 made the single root of trust. **The first platform to land under
  D39 needing zero new secrets** — worth saying out loud, since it demonstrates
  the rule is not just overhead.

### ⚠️ Honesty constraint for any published number

`claude_code.cost.usage` is a **client-side USD estimate computed from token
counts at list prices** — not an invoice, and for subscription/credit plans not
money that changed hands. Any published claim must say **"modelled build cost
at list price."** Mark the resulting insight `review: required`; this is exactly
what D38 exists to catch.

Report the *measured* phase (WS8 onward) rather than guessing at the past.
Reconstructing history from local session data is a bonus, clearly labelled,
only if the data turns out to be there.

### Follow-up: a static credential in `~/.codex/config.toml` (raised 2026-07-26)

Found while fixing the Codex OTel exporter. `~/.codex/config.toml` carries a
plaintext `LOGGING_API_KEY` (plus `LOGGING_API_URL`) in
`shell_environment_policy.set`, so the hooks→Slack bridge key sits on disk as a
long-lived literal.

It does not contradict `build-notes/claude/05` — that note says these live in
user-level settings outside the repo, and they do — but it *is* the one
credential in the build-tooling path that D39 hasn't reached: every other
secret is now a service identity fetched from Secrets Manager with the
developer's AWS session. The Codex OTel token was just moved to exactly that
model (fetched by `scripts/codex_otel.sh`, injected with `codex -c`, never
written to a file), which makes this one the odd remaining case.

Options, cheapest first: (a) rotate it and accept a file-scoped secret, mode
0600, documented as a known exception; (b) have the hook script fetch it from
Secrets Manager the way `otel_headers.sh` does, so nothing durable is on the
laptop; (c) drop the shared key for a per-developer identity. (b) matches D39
and reuses code that already exists.

**Effort:** ~1 hour for (b). Not scheduled — logged so the exception is
deliberate rather than forgotten.

### Related finding worth chasing separately

Four of five agent platforms emit OTel in some form: Foundry natively
(`gen_ai` spans, already harvested), Salesforce via the **Session Trace OTel
API** (beta — full session as OTLP spans, one session per call, no bulk),
ADK/Agent Engine via Cloud Trace, OpenAI via a `TracingProcessor` tee.
Anthropic CMA is the exception. That opens a possible **M11.6: map the lab's
obs model onto the GenAI semantic conventions**, turning five bespoke adapters
into one standard schema plus an honest account of where each platform falls
short of it. Bigger than WS9 and likely a better insight — logged here, not
scheduled.

**Effort:** step 1 ~1 hour; `coding_source.py` + section ~1–2 days.

---

## WS10 — MuleSoft Agent Fabric comparison (AD3, approved 2026-07-25 — last)

**Goal.** Stand up Agent Fabric against the lab's own agents and produce a
customer-facing **build-vs-buy comparison matrix**.

**Scheduled last, and gated on access.** Phase 0 is an entitlement check
(Agent Fabric + CloudHub 2.0 in a US/EU host) that must pass before any build.
**Do the access check early even though the build is last** — it may need
someone else to act.

**Why after WS8:** Agent Fabric's pitch is orchestration and governance across
many agents. Comparing it against a lab whose every experiment is 1:1 is an
unfair fight in both directions; with the fan-out built there is something real
to compare Agent Broker against.

Full research, the four productised-convergence findings, the phase plan, the
deployment constraints, and the draft comparison matrix are in
`tmp-docs/07.25.2026-AD3-mulesoft-agent-fabric.md`. Headline for planning
purposes: **Agent Fabric has independently productised four mechanisms the lab
built by hand** — the prompt rider (A2A Prompt Decorator), the 0.3↔1.0
translation layer (A2A Transcoder), per-caller access control (MCP ABAC), and
deterministic call ordering (Agent Script node graphs). That convergence, plus
the lab's measured version-wall evidence, is the comparison's most valuable
output.

**Effort:** ~1 week after Phase 0 clears.

---

## Flagged candidates (user decision pending — do not build)

- **CrewAI (AMP)** — most-adopted OSS multi-agent framework + its new
  platform; would test crew-style delegation against the lab's
  single-agent twins.
- **Pydantic AI** — the typed-Python contrast; A2A support via FastA2A;
  lightest possible "framework" column.

---

## Lab Guide — embedded Q&A agent for the console (idea 2026-07-22 → ✅ BUILT 2026-07-24, D35)

**Scheduling (user decision 2026-07-22): after WS3** — the Azure Foundry
interop builds first; the guide then has the fifth platform's story to
tell.

**Status (2026-07-24):** built as designed below — `src/platforms/guide/`
(corpus + read tools + adapter), console `POST /api/guide` SSE + header
drawer (🧭) with per-section suggested questions, and the meta exhibit
live: guide-rest/mcp/a2a targets on :8031–:8033 (run_local.sh), the MCP
server carrying both `ask` and the raw read tools. ADR D35; Claude
Desktop hookup in plan/04-runbooks.md §10. Remaining polish: publish
through the tunnel for the ~Aug 1 public cutover (D20 pattern).

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

### From the anti-pattern audit (D37) — measure the claims, don't just assert them

The self-audit that produced the F1–F8 remediation pass (D37) also left six
experiments, each designed to generate raw data for or against a specific
anti-pattern claim rather than to settle it by argument:

- **E1 — Trust Layer wire test** (tests: "the platform masks PII for you").
  Seed known-shape synthetic PII in CRM, run every delegation path, diff the
  raw wire payloads the wiretap already captures against the masking claim.
  Output: a measured per-path answer to what is actually masked on the wire.
- **E2 — `input-required` handoff cell** (tests: A2A's task-state model is
  usable in practice). Emit `TASK_STATE_INPUT_REQUIRED` on a delegation
  failure or guard refusal and survey which platform A2A clients handle it.
  Expected finding: none do — which extends the maturity spectrum in
  `native-a2a-young` with a second concrete axis.
- **E3 — Identity & scope diet measurement** (tests: least-privilege is
  reachable). Deploy the minimal-scope ECA (F3) and per-twin ECAs (F6),
  record what breaks and how Salesforce session logs attribute each caller.
  Output: measured deltas + a matrix ledger entry. **Known before starting:**
  the harvest's Data Cloud queries go through `/services/data/vXX/query`, so
  dropping the `Api` scope trades the Salesforce observability column for the
  tighter grant — that trade IS the experiment's first result.
- **E4 — Per-user session isolation cost** (tests: multi-tenant remediation
  is free). User-keyed vs platform-keyed warm sessions, cold-start multiplier
  under N users. Prices the remediation in seconds against D32's 31–56s.
- **E5 — Output-schema enforcement survey** (tests: declaring a schema means
  callers honor it). Now that MCP `ask` publishes an output schema and
  contract version (F4), test which calling platforms validate or consume it.
  Expected finding: declaration outpaces enforcement.
- **E6 — Interop tax lanes** — the M11.4 item above, listed here because it
  is the same question asked with money instead of latency.

## Insights pipeline (how findings reach the deck)

`config/insights.yaml` (source of truth, statuses honest) → console
**Insights** section (`/api/insights`) → `scripts/export_insights.py` →
`plan/08-insights.md` → downloadable at `/api/insights.md` → import into
Claude Design for the presentation. Every workstream ends by updating the
yaml and regenerating.

**Diagrams for the readout** ride alongside: `config/diagrams.yaml` holds
mermaid sources, each naming the insight ids whose tiles should carry its
chip (the mapping lives on the diagram, so one picture can serve several
insights without being duplicated). In the console a chip on the insight
tile opens the diagram full-size — that is the readout affordance: talk to
the insight, click the chip, the picture is on screen. The same diagrams are
embedded into `plan/08-insights.md` as ```mermaid fences, so GitHub and
Claude Design render them too.

Mechanics worth knowing before editing one: mermaid is **vendored** at
`src/console/static/vendor/mermaid.min.js` (3.4MB, MIT, lazily loaded on the
first chip click) rather than pulled from a CDN — a readout must not depend
on the network — and rendered in the browser rather than pre-baked to SVG, so
the mermaid text stays the single source of truth with no regeneration step
to forget. Diagrams that README.md also embeds carry `readme: true`, and
`tests/unit/test_diagrams.py` asserts the two copies stay identical, so
editing one and not the other fails the suite instead of the demo.

---

## WS11 — A2A fire-then-poll: the protocol's own answer to the gateway ceiling (raised 2026-07-26)

**Why this exists.** D41 measured the cost of putting agent work behind a
managed gateway: the fan-out MCP server's legs inherit API Gateway's 30s
ceiling, against 120s host-side. The workaround so far is to keep legs fast and
report the ones that do not fit as unavailable. **A2A already specifies the
right fix**, and the lab has been using only half the protocol.

### What the spec provides (verified against a2a-protocol.org)

A2A is asynchronous at heart. `SendMessage` **"MUST return immediately with
either task information or response message"**, and **"task processing MAY
continue asynchronously after the response."** The client then polls `GetTask`
(JSON-RPC `tasks/get`) with the task id, or subscribes with `SubscribeToTask`,
or registers a webhook via the push-notification config.

The seven task states are the state machine to drive off:

| State | Meaning |
|---|---|
| `TASK_STATE_SUBMITTED` | acknowledged, not started |
| `TASK_STATE_WORKING` | actively processing |
| `TASK_STATE_INPUT_REQUIRED` | interrupted, needs input |
| `TASK_STATE_AUTH_REQUIRED` | interrupted, needs auth |
| `TASK_STATE_COMPLETED` / `FAILED` / `CANCELED` | terminal |

**This dissolves the ceiling rather than working around it.** The MCP tool call
becomes: submit the task, return the task id — a sub-second call, nowhere near
30s. The model then calls a second tool to poll. The gateway never holds a
connection open across the agent's actual work.

It also makes the orchestrator *more* agentic, not less: the model manages
in-flight work and decides when to check back, which is a strictly larger
decision surface than "call three tools and wait".

### What the lab has today, and the one line that blocks it

`interop/servers/a2a.py` is already most of the way there. It runs
`InMemoryTaskStore`, so `tasks/get` is served by the framework, and
`AdapterExecutor` already walks SUBMITTED → `start_work()` → `add_artifact()` →
`complete()`. **The blocker is that `execute()` awaits `self.adapter.handle(req)`
inline**, so the HTTP request for `message/send` does not return until the work
is done. The lab implements the async lifecycle and then drives it
synchronously — exactly the "async-capable protocol everyone drives
synchronously" insight already published, now with a concrete cost attached.

### Build sketch

1. **Server:** `AdapterExecutor` dispatches the adapter call as a background
   task and returns once WORKING is enqueued. Needs care with the a2a-sdk's
   request/event-queue lifetime — verify the queue and task store survive the
   originating request, and that a completion arriving after the response is
   still recorded.
2. **Client:** `A2AClient` grows `submit()` (returns task id, does not wait) and
   `poll(task_id)` on top of `tasks/get`, alongside today's blocking `ask()`.
3. **Fan-out MCP server:** each unit gets a submit tool and a check tool, or one
   tool with a `mode` argument. The run id already threads the units together;
   the task id threads a single unit across calls.
4. **Orchestrator prompt:** submit all three, then poll — the model chooses the
   cadence. Measure whether it polls sensibly or busy-waits; that is a finding
   either way.
5. **Partial failure contract is unchanged in spirit** but gains a state: a leg
   can now be *still working* rather than only answered/unavailable, and a brief
   must say so rather than treating pending as absent.

### The honest limitation, and the measurement worth taking

**Not every leg is A2A.** `openai-agentcore` is a SigV4 AgentCore call and
Agentforce is the Agent API — neither has A2A's task lifecycle, so those would
need the lab's own submit/poll store rather than the protocol's. Fire-then-poll
via A2A covers the ADK and Foundry legs natively and nothing else.

**And vendor support for the async half is unverified.** The spec says an
implementation MAY continue processing after responding; it does not say every
implementation does. Whether Agent Engine's and Foundry's A2A endpoints
actually hold a task and serve `tasks/get` — rather than blocking like ours —
is a per-platform question. **That measurement is itself the most valuable
output here:** the matrix currently records who speaks A2A, not who implements
the asynchronous half of it. "Everyone ships the sync subset" would be a
first-class finding, and so would the opposite.

**Exit criteria:** one fan-out leg submitted and polled to completion through
the MCP server with the gateway never holding a connection over ~2s; a recorded
per-platform table of who honours the async lifecycle; the orchestrator's poll
behaviour observed and written up.

---

## Console and exhibit backlog (raised 2026-07-26, after the hosted bridge)

Not a workstream — UI and presentation debt to clear before the demo.

1. **"Not yet available" components.** Diagnosed: `components_for()` renders a
   component as unavailable when its console URL env var is unset, and four are
   missing from `.env` — `OPENAI_CONSOLE_URL` (this is the M9 one),
   `SF_LIGHTNING_DOMAIN`, `ADK_CONSOLE_URL`, `FOUNDRY_CONSOLE_URL`. Setting
   them is the whole fix; no code change.
2. **Chips are too prominent.** Shrink the platform/product/feature chips
   relative to surrounding text.
3. **Chips at group level.** Platform chips currently sit on individual
   experiments; add them to the Control Panel → Experiments *subsection*
   headers too (Claude – Agentforce, OpenAI – Agentforce, …).
4. **Chip iconography cleanup**, with one decision to make deliberately:
   - **Vendor chips should name the vendor, not the model.** Every other chip
     is a company — AWS, Microsoft, Google, OpenAI — so **Anthropic** is the
     consistent choice for that tier, and Claude belongs on the product/model
     tier where the lab already distinguishes Managed Agents from the
     self-hosted SDK. The rule to apply everywhere: tier 1 names who operates
     the cloud, tier 2 names which product or model runs on it. Picking Claude
     for tier 1 means Google's chip should say Gemini, which nobody wants.
   - Use the **Google Cloud** logo (not the Gemini spark) for Google ADK and
     everywhere else Google appears as the platform.
   - Warm-up panel: the `aws-shim` product chip should carry an **AWS** logo
     rather than a generic one.
