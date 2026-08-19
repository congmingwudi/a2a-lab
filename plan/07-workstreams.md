# Workstreams — the multi-platform buildout (2026-07-19)

The lab's next phase: extend the Agentforce↔Claude↔OpenAI experiments to
the remaining primary agent platforms, keep every addition producing
publishable insights (config/insights.yaml → console Insights section →
plan/08-insights.md → Claude Design presentation), and keep the honesty
rules (D15 real-platform entry, honest matrix statuses, raw wire payloads).

Ordering decided 2026-07-19: **WS1 → WS2 (GCP) → WS3 (Azure) → WS4
(LangGraph) → WS5 (Strands, deferred)**. CrewAI and Pydantic AI are
flagged as candidates only — user decision pending.

**Revised 2026-07-25 after the architecture review** (local working notes under
the gitignored `tmp-docs/`, not in the repo):
WS1–WS3 and WS6 U1–U2 are done; WS4 remains deferred and WS5 went LIVE
2026-08-04 (D66 — backend built by Kiro, plan/12; claude-haiku-4-5 on Bedrock).
The approved next
order is **WS8 (fan-out orchestration) → WS9 (build telemetry) → WS7 (hosted
completion) → WS10 (Agent Fabric comparison)**, with WS9 step 1 pulled forward
immediately because coding-agent telemetry cannot be backfilled. WS8 and WS9
were verified not to depend on WS7 — see the analysis inside WS7.

**Status 2026-07-27.** **WS8 is shipped** (D41 — the fan-out legs are remote MCP
tools and the model schedules them; measured 3/3 units in a single turn) and
**WS9 is shipped end to end** (both exporters live, per-repo and per-model
attribution read back through the console, its own Harvest button). The
**Console and exhibit backlog at the end of this file is cleared** (D42 settled
the chip/mark tier rule). **WS12 is provisioned and running daily** (2026-07-27
paused-weekly, moved to daily and resumed 2026-07-30 so the console shows a fresh
brief each morning) — and provisioning it produced **D46**, after finding four
ways an artifact can be built without ever being deployed. **WS11's client half
is measured** (D47): the API Gateway ceiling is dissolved, and the per-platform
table of who implements A2A's async half is in plan/03-results.md.

What is open: **WS13** (full hosting — this supersedes WS7, which is closed
except for its completed bridge item), the remainder of **WS11** (the fan-out
server's submit/check tools), then **WS10**.

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

## WS1 — Finish the AgentCore pair (complete)

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
5. ✅ Managed vs sdk-local vs sdk-agentcore latency table recorded
   (plan/03-results.md, `scripts/probe_backend_latency.py` — managed 5.2s
   cold / sdk 11.7s / sdk-agentcore 7.3s p50 / 25.1s p95).
6. ✅ M6 timeout probes recorded (plan/03-results.md,
   `scripts/probe_action_timeout.py`).
7. ✅ Full Agentforce→bridge hosted-mode pass — the stack is fully hosted
   now (WS13), so the old "restart with A2ALAB_MODE=hosted, Zscaler OFF"
   premise is gone: Path A verified end-to-end hosted with no laptop in the
   path, 27.5s (trace `dfb600f6`, plan/03-results.md). CAVEAT (D68): under
   `A2ALAB_MODE=hosted` the bridge now resolves `claude-rest` →
   `claude-rest-hosted` (the ECS faces task, managed backend), NOT the
   `claude-agentcore` runtime this WS originally targeted — D55 removed that
   remap. The `*-agentcore` cells are exercised directly by IAM invoke.

Known flake (recorded honestly): the sdk agent occasionally delegates the
matrix question to Agentforce and burns its 3-turn cap
(`CLAUDE_MAX_TURNS=3`) → intermittent 500 "max turns". Options if it
annoys: raise CLAUDE_MAX_TURNS in the runtime env, or pin the research
prompt harder against delegation for factual questions.

**Credentials:** nothing new — the lab's AWS account (SSO), existing
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

Status 2026-07-19 (COMPLETE — see the "WS2 COMPLETE" note after item 11; two
dated post-completion additions followed: the 2026-07-20 market-signals tool and
the D30 direct route):
1. ✅ GCP project created (billing linked, APIs enabled, ADC); its id lives
   in `.env` as `GOOGLE_CLOUD_PROJECT`, not in this repo;
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

## WS4 — LangGraph on Heroku

**Goal:** the open-source-framework column: a LangGraph research agent
(small ReAct graph — an agent node + an `ask_agentforce` tool node) that
delegates CRM knowledge to Agentforce, exercised over REST/MCP/A2A; LangSmith
as the *fully queryable SaaS* observability backend.

**Why:** adds the open-source-framework variable (LangGraph) to the platform
column; LangSmith's read API is the perfect foil to OpenAI's write-only
traces.

**Revised 2026-08-16 (D77 — the Heroku pivot).** WS4 originally targeted
**LangGraph Platform** (its managed Agent Server exposes A2A/MCP natively).
The operator chose to host on **Heroku** instead — a hosting-shape change, not
a framework one. On Heroku (a generic PaaS with no agent-protocol surface) the
agent is served through the lab's OWN `serve()` adapters, exactly like
`platforms/strands` and `platforms/openai`. This is the lab's **first
non-AWS-hosted platform**. WS4 loses the framework-vs-managed-PLATFORM contrast
but KEEPS the queryable-SaaS observability column (LangSmith is host-agnostic).
See D77 for the full rationale, the one-dyno/three-protocol multiplexer, and the
cross-cloud trace path (Data API, no VPC).

Work items:
1. ✅ `src/platforms/langgraph/` — agent interior on the lab's two-seam shape:
   `core.py` adapter + system prompt, deterministic `stub_backend.py`, and the
   real `langgraph_backend.py` (`create_react_agent`, Haiku-tier
   `langchain-anthropic` brain, delegation-guarded `ask_agentforce` per D27).
   Backend selected by `LANGGRAPH_BACKEND=stub|langgraph`. done 2026-08-16
2. ✅ Serve entry `__main__.py` (REST 8051 / MCP 8052 / A2A 8053) plus a
   `--protocol all` multiplexer that reuses the faces app
   (`build_faces_app(faces=LANGGRAPH_FACES)`) so one Heroku web dyno serves all
   three protocols behind one `$PORT`. Wired into `run_local.sh`. done 2026-08-16
3. ✅ Targets: `langgraph-rest`/`-mcp`/`-a2a` (native, local) + the paired
   Agentforce twin `agentforce-langgraph-rest` (`SF_LANGGRAPH_AGENT_ID`, D25);
   the `langgraph-*-hosted` Heroku twins + hosted-mode remap staged COMMENTED
   until first deploy (no phantom live cell in the matrix). done 2026-08-16
4. ✅ `deploy/heroku/` — Dockerfile (`langgraph` + `aws` extras) and a HEADLESS
   Platform-API deploy script (app create, config vars, container push, release
   over docker+curl; no `heroku login`). Traces reach the shared Aurora store
   off-VPC via the rds-data Data API. done 2026-08-16
5. ✅ Unit tests (`tests/unit/test_langgraph_platform.py`) + `langgraph` extra
   in pyproject; D77 ADR; plan/09 estate/L6; plan/01 dev-stack diagram. done 2026-08-16
6. ✅ Deployed to Heroku team `sfdc-ta` as app `a2a-lab-langgraph` (cedar
   generation → `https://a2a-lab-langgraph-08c59c66097f.herokuapp.com`, one
   Basic web dyno, `LANGGRAPH_BACKEND=langgraph`). `A2ALAB_LANGGRAPH_BASE` set,
   the `langgraph-*-hosted` twins + hosted-mode remap uncommented. Live smoke
   green: `/healthz`, faces index, token-gated A2A card advertising the real
   origin, and a REST `/invoke` running the real ReAct agent (haiku-4-5, ~4.5s).
   The Heroku Container Registry needs a Docker **schema2** manifest, so the
   build pushes via buildx `oci-mediatypes=false,push=true` (Docker 29's
   containerd store is OCI, which the registry rejects). done 2026-08-17
7. ✅ Obs: `src/observability/langgraph_source.py` over the LangSmith runs API
   — LangSmith is LangGraph's framework-native trace store, so this is the
   platform-obs column (NOT a bespoke Heroku Postgres log — WS4 stays true to
   "observe each platform through its own telemetry"). Maps one root run → one
   obs session (rolled-up tokens/latency/model/tool-count, status) and each
   `llm`/`tool` child → one event (the run tree; LangChain scaffolding chains
   dropped). The wire-trace join rides `extra.metadata.lab_trace_id`, which the
   `langgraph_backend.answer()` `ainvoke(config=…)` now stamps. Registered in
   the CLI sweep (`scripts/obs_harvest.py`), the console Harvest endpoint, AND
   the 6h harvest Lambda (`lambda_handlers.py`) — the seventh agent-platform
   column; console coverage card + plan/05 matrix column updated. **Live-
   validated 2026-08-17** against project `a2a-lab`: `ok`, 1 turn, haiku-4-5,
   829 tokens, 1361ms (the LangSmith page cap is 100 rows — a 500 400s, fixed).
   Two follow-ups travel with this: (a) the `lab_trace_id` join is null on turns
   run BEFORE the backend stamp ships — it populates after the next Heroku full
   rebuild (`src/` change, not `--skip-build`); (b) the hosted Lambda needs
   `LANGSMITH_API_KEY` in the harvest secret (Secrets Manager) or the source
   degrades to `blocked` — it is NOT an AWS-role read like the others. done
   2026-08-17 (code + live read; the two follow-ups are deploy/operator steps).
8. ⏳ Live validation. FORWARD (langgraph-to-agentforce) is LIVE and validated
   end to end 2026-08-19: an account question drove the ReAct graph to its
   `ask_agentforce` node → the D25 LangGraph-paired twin over the Agent API,
   which returned live CRM data, and the whole chain recorded FIVE hops in the
   shared Aurora store (inbound REST + internal graph + three agentforce-api
   session/message/delete hops). Scenario flipped to `status: live`. Two fixes
   this took: the dyno was missing `SF_AGENT_ID` (which `AgentforceClient.
   from_env()` hard-reads before the paired override — added to the deploy VARS,
   mirroring deploy/agentcore/deploy.sh), and the hosted bridge was rebuilt so
   its baked `targets.yaml` knows `langgraph-rest`. REVERSE (agentforce-to-
   langgraph) stays `coming-soon`: the twin routes + returns CRM + fires
   `ask_external_researcher`, and the hosted bridge resolves langgraph-rest, but
   the production `A2ALab_Bridge` Named Credential points at the Cloudflare
   tunnel (`bridge-lab.agenticthings.com`, currently unresolvable), so the Apex
   callout can't reach the working bridge. Blocked on the shared Path A →
   ALB cutover (cert + DNS on a Salesforce-visible hostname, unscripted, plan/09)
   — not on anything LangGraph-specific. matrix + insights: open.
9. ✅ Cross-cloud trace sink — DONE 2026-08-19. The operator authorized minting a
   **static, scoped IAM access key** (the laptop only has SSO creds, which do not
   exist inside a dyno). It rides `.env` under DEDICATED names
   (`A2ALAB_HEROKU_AWS_ACCESS_KEY_ID`/`_SECRET_ACCESS_KEY`) so `source .env`
   cannot shadow the operator's SSO locally; `deploy/heroku/deploy_langgraph.sh`
   maps them to `AWS_ACCESS_KEY_ID`/`_SECRET_ACCESS_KEY` in the pushed config
   ONLY, swaps `A2ALAB_PG_SECRET_ARN` → the writer secret (mirroring the other
   trace-writers), and defaults `A2ALAB_TRACE_SINK=postgres`. Re-released
   `--skip-build` (config-only). PROVEN: a REST call to the dyno wrote two hops
   to Aurora over the rds-data Data API (off-VPC), and the forward-delegation run
   wrote five. Keys synced via `env_sync push` (both `.env` secret and the
   harvest secret allowlist).

**Credentials / setup (what the operator provides — see D77):**
1. A **Heroku API token** scoped to team `sfdc-ta`. In practice the operator's
   Enterprise **SSO** login was enough: the deploy script falls back to `heroku
   auth:token` (a short-lived session token) when `HEROKU_API_KEY` is unset, and
   the 2026-08-17 deploy used exactly that — no long-lived key minted (SSO
   usually disables them). Everything else is the headless Platform API. For an
   unattended/cron deploy, mint `heroku authorizations:create` → `.env`
   `HEROKU_API_KEY` (secret; synced via env_sync).
2. Confirm **app-create rights** in `sfdc-ta` (Enterprise teams may lock this),
   and whether it is a **Private Space** (then `HEROKU_SPACE` is needed).
3. `HEROKU_APP` / `HEROKU_TEAM` → `.env` (no hardcoded identifiers).
4. Model key for the brain: reuse `ANTHROPIC_API_KEY` (Haiku-tier by default,
   `LANGGRAPH_MODEL_ID` to override) — keeps sync budgets comfortable.
5. For LangSmith obs (item 7): `LANGSMITH_API_KEY` + `LANGCHAIN_TRACING_V2=true`
   + `LANGCHAIN_PROJECT=a2a-lab` as Heroku config vars (the emit side —
   host-agnostic, works the same on Heroku). For the *hosted harvest* (the 6h
   Lambda) to read those runs, the SAME `LANGSMITH_API_KEY` must also be in the
   harvest secret (Secrets Manager, `env_sync push`) — it is the lab's own
   personal-account key (the GCP/Azure pattern for non-Salesforce platforms).
   Without it the langgraph source degrades to `blocked`, honestly.

**Exit criteria:** A2A + MCP native cells green; both directions with the
twin; LangSmith obs source harvesting; insights updated
(open-source-framework, observability column).

---

## WS5 — AWS Strands Agents (LIVE 2026-08-04; backend built by Kiro)

**Goal:** third framework on the *identical* AgentCore runtime (OpenAI
Agents SDK / Claude Agent SDK / Strands) — isolates the framework
variable at constant runtime and constant model cloud; native A2A + MCP
serving from a framework Amazon runs in production (Q, Glue, Kiro).

**Status 2026-08-04 (D66).** LIVE. Built the OpenAI/Codex way (D24): the lab
scaffolded **everything except the model backend**; **Amazon Kiro** delivered
the backend behind the standing contract `plan/12-strands-kiro-handoff.md`. The
runtime is deployed and `strands-to-agentforce` runs against it end-to-end —
verified with a real Agentforce consult (top open opportunities for Apple,
CRM-attributed). Model: **claude-haiku-4-5 on Bedrock** via the runtime IAM role
(no API key), matched to the Claude AgentCore twin so the ONLY difference across
that pair is the SDK (Strands vs Claude Agent SDK) — the tightest
framework-isolation of the three. Chosen over Kiro's Sonnet-4 default to fit the
Path A action-timeout budget; Sonnet 4 needs a Bedrock model-access agreement
that this account has not accepted.

One honest caveat travels with the live cell (in the scenario copy):
- **platform_ref null:** the Strands `AgentResult` did not expose
  `metrics.request_id` at SDK 1.50.2, so the hop records no Bedrock request-id
  join key rather than inventing one.

The D25 twin is now provisioned (2026-08-04): `A2ALab_Research_Assistant_Strands`
(`SF_STRANDS_AGENT_ID = 0XxKB000000xdwt0AA`, published + activated v1), its
`ask_external_researcher` action pinned to bridge target `strands-agentcore` (the
Bedrock AgentCore runtime — repinned from `strands-rest` on 2026-08-05 so the
reverse cell hits the runtime, not the faces task; D68). The runtime was
redeployed config-only so `ask_agentforce` consults the twin directly — the clean
per-experiment attribution D25 wants, both directions live-verified.

**Reverse cell — `agentforce-to-strands` (2026-08-05, D68).** The mirror of the
live forward cell: you talk to the Strands-paired twin, which answers from CRM
(Apex) then delegates outside-in research through the bridge to the Strands agent
on its AgentCore runtime. It reuses the whole forward stack — no new agent, no
Kiro handoff. Two things made it real rather than a copy of `agentforce-to-claude-aws`
(whose text D68 found stale): the twin's action posts `strands-agentcore`
directly (a mode remap cannot reach an `agentcore-http` runtime without changing
the protocol, which D55 forbids), so the cell needs no `requires_mode`; and the
bridge task role's `invoke-agentcore` policy now includes `STRANDS_AGENTCORE_ARN`
(it listed only the Claude/OpenAI runtimes — the reverse leg would have hit
AccessDenied). Same stacked-timeout caveat as the other AgentCore reverse cells:
warm the runtime first (a cold start inside the bridge's 45s budget fails the
sync turn).

Work items (stories):
1. ✅ `src/platforms/strands/` scaffold — adapter, faces `__main__.py`, `stub_backend.py`, `STRANDS_BACKEND` switch; deployed to AgentCore via `deploy/agentcore/deploy.sh strands` with the `bedrock:InvokeModel` IAM grant (D66).
2. ✅ Strands SDK backend delivered by Kiro behind the `plan/12` contract — `src/platforms/strands/strands_backend.py` + tests + the `strands` extra; runtime live on `claude-haiku-4-5` via the runtime IAM role, no API key (D66).
3. ✅ Forward cell `strands-to-agentforce` live end-to-end on the AgentCore runtime — real Agentforce consult, CRM-attributed; tightest framework-isolation of the three (SDK is the only variable vs the Claude twin) (D66).
4. ✅ D25 Strands-paired Agentforce twin `A2ALab_Research_Assistant_Strands` published + activated; `ask_agentforce` consults it directly after a config-only runtime redeploy (D66).
5. ✅ Reverse cell `agentforce-to-strands` built and live-verified both directions — twin answers from CRM (Apex) then delegates outside-in research through the bridge to the runtime; confirmed the `bridge → strands-agentcore` hop on `agentcore-http` with the D27 rider on the wire (D68).
6. ✅ Twin repinned `strands-rest → strands-agentcore` in the authoring bundle and republished as v2 (validate → publish → activate), so the reverse cell hits the AgentCore runtime, not the faces task — a mode remap cannot reach an `agentcore-http` runtime (D68).
7. ✅ Bridge task role `invoke-agentcore` policy extended with `STRANDS_AGENTCORE_ARN` and redeployed — the reverse leg is the first `bridge → Strands-runtime` path and would otherwise hit AccessDenied (D68).
8. ✅ Strands observability source `src/observability/strands_source.py` — `AWS/Bedrock` model meters (tokens/cost/latency) + AgentCore runtime access log (invocation/error counts); registered in the 6h harvest Lambda and the CLI sweep as the eighth harvest source (D67).
9. ✅ Console: AWS Strands added to the title-bar platform list, the obs coverage card + logo, and both observability diagrams (harvest + analyst); Experiments nav orders the live Strands pair ahead of the upcoming LangGraph group.
10. ✅ Deployment map + architecture diagrams updated — Strands runtime in the AgentCore estate/L1, the scheduled harvest reaching it, and the L6 code→deploy rows (`plan/09`, `config/diagrams.yaml`, console `*_DIAGRAM` constants).
11. ✅ Cross-hyperscaler cell `AWS Strands → Google ADK` (2026-08-11) — the second Cross-hyperscalers experiment, the AWS→GCP mirror of `Google ADK → Foundry`. Two sibling scenarios so the TRUST MODELS read side by side: `strands-to-google-adk` (native-direct — the runtime role federates into GCP keyless, no lab server in the path) and `strands-to-google-adk-bridge` (via the already-federated bridge, both cross-cloud wire payloads captured).
12. ✅ Backend tools `ask_google_adk` + `ask_google_adk_bridge` in `strands_backend.py` (D27-guarded), system-prompt awareness in `core.py`, and `google-auth` added to `strands.Dockerfile` (not pulled by the `strands`/`aws` extras — the direct route's federation would `ModuleNotFoundError` without it).
13. ✅ Deploy wiring — `deploy/agentcore/gcp_federation.sh strands` binds the runtime execution role into the existing `a2alab-aws` pool (one more `principalSet` member, no new pool); `deploy.sh strands` ships `ADK_A2A_ENDPOINT`/`A2ALAB_BRIDGE_URL`/`BRIDGE_TOKEN` and renames the `A2ALAB_STRANDS_GCP_*` pair to the generic federation vars in the runtime env.

**Cross-hyperscaler cell (2026-08-11).** WS5 was a framework-isolation pair (Strands vs Claude/OpenAI on one runtime); this extends it outward to the cell the lab is named for — an AWS agent calling a GCP agent, native A2A, no shim. It deliberately ships BOTH routes because the interesting variable is the trust boundary: the direct route is least-privilege end-to-end but records nothing lab-side, while the bridge route trades a shared broker identity and one extra hop for full raw-payload capture. Same destination agent (`google-adk-a2a`), same protocol; what changes is who is trusted and what is observable.

One honest caveat rides the live cells: `platform_ref` (Bedrock request-id) comes back null at this SDK version, so obs sessions correlate to the runtime, not to individual lab traces.

Done, lab-side (also runs locally on a deterministic stub):
- `src/platforms/strands/` — adapter (`core.py`), `__main__.py` (faces on
  :8041/:8042/:8043), `stub_backend.py`. Backend selected by `STRANDS_BACKEND`
  (`stub` default, `strands-sdk` = Kiro's).
- `deploy/agentcore/strands.Dockerfile` + the `strands` case in
  `deploy/agentcore/deploy.sh` — including the `bedrock:InvokeModel` grant on
  the runtime role (Bedrock model access via IAM, no API key) and the
  Salesforce-only runtime secret.
- `config/`: three local faces + `strands-agentcore` (now `native`) + the
  Strands-paired Agentforce twin + hosted twins + `modes` remap; the
  `strands-to-agentforce` scenario (now `live`, target `strands-agentcore`);
  the agent-registry entry; the faces registry + `_adapter` branch.
- `tests/unit/test_strands_platform.py`; `.env.example`/`.env` `STRANDS_*`
  keys; console platform chip/logo (AWS steel + `aws` mark).

Delivered by Kiro (merged 2026-08-04):
- `src/platforms/strands/strands_backend.py` + `tests/unit/test_strands_backend.py`
  + the `strands` dependency extra (contract: plan/12) — plus the Kiro OTel
  build-telemetry path (`.kiro/hooks/`, `scripts/kiro_otel.sh`, WS9 metrics in
  the Coding Agents Telemetry dashboard).

Remaining follow-ups (do not block the live cell):
- Optionally record a matrix run now that the attribution is clean.

Reuses WS1's entire deploy path. No new accounts — the lab's AWS account, model
on Bedrock via the runtime IAM role (D66; Strands is model-agnostic, Bedrock
chosen for a clean framework-only comparison against the Claude twin).

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
   roles: master of the universe / operator / viewer — the owner role is
   operator-equivalent with its own password so the operator login can be
   shared without the owner's, D63), RS256 keypair under `.a2alab/`, lab-issued
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

### Work items

The milestones above are the design; this table is the delivery record (the
shape `jira_sync.py` imports). U1/U2 shipped 2026-07-24; U3–U6 are the
roadmap the status paragraph names as still ahead.

| # | Item | State |
|---|---|---|
| 1 | U1 — lab identity provider: `config/users.yaml` (demo users + roles, owner role D63), RS256 keypair under `.a2alab/`, lab-issued JWTs, console login, `TokenAuthMiddleware` accepts JWT or the legacy shared token | done 2026-07-24 (commit da99a1d), loopback-tested in `tests/unit/test_identity.py` |
| 2 | U2 — user context on the wire: `metadata["user_context"]` + the JWT in each protocol's native slot (REST `Authorization: Bearer`, MCP tool argument, A2A message metadata), rider gains `on-behalf-of:`, every delegation seam forwards both channels; deploy scripts ship `A2ALAB_JWT_PUBLIC_KEY` to the runtimes (verify-only) | done 2026-07-24 (commit 66c33d8), proven over all three protocols in `tests/e2e/test_loopback.py` |
| 3 | U3 — enforcement + data scoping: seams verify the JWT when present (invalid → refuse; text-only → `asserted-only`), `TraceEvent` gains a `user` field, guide/console/obs reads filter by role, `A2ALAB_REQUIRE_USER=1` strict mode | not started |
| 4 | U4 — platform on-behalf-of cells (the measured comparison): Salesforce per-user JWT bearer flow, Foundry Entra OBO, Google Agent Engine per-user impersonation (expected blocked, recorded honestly), Anthropic/OpenAI metadata-only `asserted-only` | not started |
| 5 | U5 — matrix + insights: new "user-context propagation" matrix section (platform × protocol × verified/asserted/dropped) + new **Identity & authorization** insights category | not started |
| 6 | U6 (stretch) — standards alignment: MCP OAuth 2.1 resource-server auth on the guide's MCP server, RFC 8693 token exchange at the bridge, A2A cards advertising `securitySchemes` | not started |

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
| WS9 — build telemetry | ✅ **Fully** | Claude Code/Codex/Cursor → CloudWatch managed OTLP (Cursor via cursorscope hooks, D64); `coding_source.py` runs in the harvest Lambda. No lab server involved |
| WS8 — Claude (AWS) ↔ ADK | ✅ **Fully** | Both ends already hosted (AgentCore, Agent Engine) |
| WS8 — **ADK orchestrator** | ✅ **Fully** | Runs inside the Agent Engine container calling hosted endpoints |
| WS8 — **CMA orchestrator** | ⚠️ **One caveat** | See below |
| WS8 — **Agentforce orchestrator** | ✅ **Fully** | Agent Script bundle in the prod org; both topologies reuse `A2ALabInvokeRemoteAgent` → bridge (delegated = bridge `fanout:` route runs the legs; serial = three stacked callouts). No host process, no new Apex (D61) |

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

   **Cost note:** each face is roughly a $10–12/month Fargate task
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
7. ✅ **Host the bridge — on Fargate behind an ALB, NOT behind API Gateway.**
   Done 2026-07-26 (commit `ccc9934`); `deploy/bridge/deploy_bridge.sh`. Path A
   verified hosted end-to-end, no laptop in the path — 27.5s (trace
   `dfb600f6`), the 45s budget defended live by a 39.8s cold ADK leg
   (plan/03-results.md). See the section below for why it does not get the
   shim's API-Gateway treatment. (Items 1, 2, 3, 5 and 6 were completed under
   WS13 — D51/D52 — so they are recorded as stories there, not duplicated
   here.)

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

| # | Item | State |
|---|---|---|
| 1 | `src/orchestration/` parallel dispatch layer with an honest partial-failure contract (`runner.py`: `asyncio.gather`, per-leg hop under one trace_id, `[leg unavailable: …]` marker) | done — 13 unit tests |
| 2 | Three business-unit legs with lazy env-resolved targets (`legs.py`: exposure/commercial/customer-comms; `A2ALAB_LEG_*_TARGET` read at call time) | done |
| 3 | `scripts/run_fanout.py` CLI runner, exits non-zero on a partial run | done — live 36.7s vs 50.7s serial, real dead leg (plan/03-results.md) |
| 4 | Dedicated per-leg agents: `a2alab-logistics-agent` (ADK/Agent Engine) and `a2alab-commercial-agent` (Foundry); customer-comms deliberately reuses shared `openai-agentcore` (OpenAI traces are write-only) | done — `src/orchestration/agents.py` |
| 5 | CMA orchestrator (variant 1) — real Managed Agent, 3/3 legs, reports its own coverage | done — 37.4s wall (plan/03-results.md) |
| 6 | ADK orchestrator (variant 2) — declared `ParallelAgent` graph, deployed via `deploy_adk.py --role orchestrator` | done — 3/3 legs, 16.8s wall |
| 7 | Agentforce orchestrator (variant 3, D61) in Agent Script — delegated topology via the bridge `fanout:` route + a serial-constraint toggle, no new Apex | done — bundle live, `config/scenarios.yaml` |
| 8 | Fan-out legs exposed as a remote MCP server (D41) — `src/fanout_mcp/` Lambda behind API Gateway; the model schedules the three tools itself | done — 17+6 tests |
| 9 | GCP workload-identity federation so the AWS Lambda holds a Google identity keylessly (`deploy/fanout/provision_gcp_federation.py`) | done |
| 10 | Orchestrator delegation through `interop/delegation.py` under the D27 guard — the guard survives fan-out (depth-1 ×3) | done |
| 11 | Fan-out join rate measured and recorded (`scripts/fanout_join_rate.py`) | done — 1 of 4 join cleanly; the others documented (ADK structurally unjoinable, OpenAI/Foundry bookkeeping) |
| 12 | Scenario entries + business-case descriptions + `config/diagrams.yaml` fan-out diagram + `fan-out` nav group | done — `supplier-disruption-{cma,adk,agentforce}` all `status: live` |
| 13 | Insight `orchestration-topology` published | done — `observed`, `review: required` |
| 14 | Console call-path renderer draws parallel legs (not just chains), incl. SERIAL-topology fold | done — `src/console/static/index.html` |
| 15 | Agentforce orchestrator recorded run | not done — the bundle + bridge route exist, but no measured run for it is recorded in plan/03-results.md yet (the CMA and ADK runs are) |

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
answer — built **three** times, once with an Anthropic Managed Agent, once with
Google ADK on Agent Engine, and once with an **Agentforce agent in Agent Script**
(D61), so the three concurrency-ownership models can be compared directly. The
axis is **who owns the concurrency**: a host tool (Managed Agents), a declared
graph (ADK `ParallelAgent`), and a serial Apex callout budget that constrains it
(Agentforce). The Agentforce variant reuses the same three legs; only the
orchestrator is new. See "The three-orchestrator comparison" below.

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

### The three-orchestrator comparison (who owns the concurrency) — D61

The same scenario, the same three legs, orchestrated three ways. The axis is not
"async model" but **where the fan-out concurrency lives**:

| | Anthropic Managed Agents | Google ADK | Agentforce (Agent Script) |
|---|---|---|---|
| Fan-out owner | A `custom` tool the **host** executes | **Declared** in the agent graph (`ParallelAgent`) | The **lab bridge**, off-platform, via a delegated callout |
| Concurrency lives | In the lab's host code | In the framework's scheduler | Not on-platform — Agentforce's only GA outbound is a **serial** Apex callout |
| Topology | Parallel | Parallel | **DELEGATED** (default: one callout → bridge `fanout:` runs three legs in parallel) or **SERIAL** (three stacked ~110s callouts) |
| Failure mode measured | Per-leg timeout in host code | Framework leg failure | Serial overruns the 120s Apex callout budget by design — a later leg abandoned (HTTP 200, empty section, the 2026-07-25 failure) |
| New agent | Managed Agents host tool | ADK graph | `A2ALab_Supply_Orchestrator` Agent Script bundle; **no new Apex, no new Named Credential** |

**Expected finding:** a platform whose native outbound cannot fan out *at all*
can still participate in a 1:many topology — by **delegating the parallelism to
a seam that has it** (the bridge's `fanout:` route runs the same
`orchestration.dispatch()` the Managed Agents host tool runs). The serial
topology is kept as a selectable **constraint demo**: it shows exactly what the
on-platform path costs when it tries to do the fan-out itself. The delegated
caller id `agentforce-orchestrator-via-bridge` and the D27 rider separate this
orchestrator's legs in the trace layer, so the three legs are **reused**, not
twinned.

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

| # | Item | State |
|---|---|---|
| 1 | OTLP exporters switched on into CloudWatch's managed endpoint — Claude Code telemetry live, ingestion verified at destination | done 2026-07-26 |
| 2 | `src/observability/coding_source.py` implementing `PlatformLogSource` — namespaces discovered, not hardcoded | done — 21 tests |
| 3 | Registered per the Obs rule in all three places (`obs_harvest.py`, `lambda_handlers.py`, `build_zips.sh` bundle) | done |
| 4 | Corrected the read path to the Prometheus-compatible API (`observability/promql.py`) — `ListMetrics` does not surface OTLP metrics | done |
| 5 | Fixed step alignment + aggregation — 300s period, daily rollup, `sum_over_time` for delta Sums, `increase()` for cumulative counters | done |
| 6 | First real coding metrics recorded — `1 tool-day, $2.82 modelled`, labelled "modelled at list price, not an invoice" | done 2026-07-26 |
| 7 | Per-repository attribution read end to end — two repos, both tools, one view; `normalize_repos()` folds placeholder-owner labels | done 2026-07-27 |
| 8 | By-model breakdown fixed for Codex — session counts carry model, so a tool with no cost metric still reports a model breakdown | done |
| 9 | `/api/build-telemetry` endpoint + Build Telemetry console section | done — 3 tests |
| 10 | Coding telemetry excluded from the Observability coverage panel, test-locked both directions | done |
| 11 | Harvest button in the console's telemetry section (posts `coding`/`coding-logs`) | done |
| 12 | Credentials followed D39, not the vendor quickstart — IAM user + 90-day service-specific credential in Secrets Manager, fetched at runtime | done |
| 13 | Codex OTel exporter wired to the same endpoint (`scripts/codex_otel.sh`); a `metrics_exporter` mismatch found and fixed | done |
| 14 | Static-key follow-up resolved via macOS Keychain — hooks read a Keychain service, env fallback warns on stderr | done |
| 15 | Insight published — "a telemetry config that parses is not evidence of telemetry" | done — `measured`, `review: required` |
| 16 | IAM-auth for the `/log` route (the true keyless D39 shape) | in-repo half ✅ — the console forwarder now signs SigV4 for `execute-api` under `A2ALAB_LOGGING_AUTH=iam` (`_logger_request_headers`, default stays `apikey` so nothing flips unasked); region from `A2ALAB_LOGGING_REGION`/host/`AWS_REGION`, absent creds skip the forward (same fail-quiet contract as a missing key). BLOCKED on the operator's out-of-repo half: the external `aws-logging-service` `/log` route must move to `AuthorizationType: AWS_IAM` with a cross-account resource policy trusting the lab principal — both halves must land together or telemetry breaks, which is why the flag defaults off |
| 17 | Usage-plan scoping / rate-limit on the `/log` route | not started |
| 18 | Rotate the logging key | not started — assuming it stayed contained is not free |

### The section can now refresh itself

The Coding Agents Telemetry section had no way to pull: its data appeared only
when someone remembered to run `scripts/obs_harvest.py coding` in a terminal,
and the empty per-repo view said so in prose. It now has its own **Harvest**
button, exactly like Observability's. Two details that are deliberate rather
than incidental:

- **`coding` is reachable by name but excluded from the unqualified sweep.**
  Observability's button reports "harvested from all platforms", and the whole
  premise of this section (and of `obs_summary` popping the key) is that the
  tool which BUILT the lab is not one of those platforms. Test-locked in both
  directions.
- **No dollar figures in the Control Panel.** The sidebar shows day counts; the
  money lives on the dashboard, next to the "modelled at list price, not an
  invoice" caveat. A `$162.76` chip in a nav rail travels without its caveat,
  and that caveat is the honest half of the number.

### What the first cross-repo read showed

Three tool-days, both tools, **two** codebases — the attribution works across
checkouts, which is the claim `@resource.repo` was set to support. It also
surfaced an attribution-hygiene finding worth keeping: the same project
appeared as **two** repos, `congmingwudi/aws-logging-service` and
`<owner>/aws-logging-service`, because that checkout's
`OTEL_RESOURCE_ATTRIBUTES` carried the example placeholder for its first
sessions. Resource attributes are free-text and nothing validates them, so a
typo does not error — it silently splits one codebase's cost into two bars that
each look plausible.

**Fixed on the read side, because there is no other side.** CloudWatch cannot
delete metric datapoints at all — "metrics cannot be deleted, but they
automatically expire after 15 months" is the whole of the vendor's answer, and
the OTLP store's retention is the same 15 months. So `normalize_repos()` folds
a `<placeholder>/name` label into the real repo of that name before bucketing:
the money stays attributed and the totals do not move, where dropping the rows
would have quietly shrunk the measured cost — the exact failure this section
exists to avoid. A placeholder with **no** real counterpart is left alone; it is
the only record of that work, and the odd name is the signal that a checkout
needs configuring. `A2ALAB_CODING_REPO_ALIASES=wrong=right,…` handles mislabels
the rule cannot infer.

The `unattributed` bucket keeps its place in the totals but loses its row in
the table when it carries no cost and no tokens — a $0.00 bar satisfies nobody,
and what it actually holds (session counts from a Codex run launched without
the wrapper) is stated in a footnote instead.

### Model attribution was free on both tools — nobody had to configure it

Asked live 2026-07-27, and the answer inverts the project/repo story: **`model`
is a datapoint label on both tools' metrics already.** `claude_code.cost.usage`
and `claude_code.token.usage` carry `model=claude-opus-5[1m]` /
`claude-haiku-4-5-20251001`; `codex.thread.started`,
`codex.conversation.turn.count` and `codex.turn.token_usage` all carry
`model=gpt-5.6-sol`. Nothing in `OTEL_RESOURCE_ATTRIBUTES` produces this —
where the work happened needs configuring, what ran it does not.

The lab was throwing half of it away: `by_model` was keyed off the cost and
token suffixes only, so **Codex — which publishes no cost metric — reported an
empty model breakdown while naming its model on every datapoint.** Session
counts now carry the model too, and the console has a By model table. The units
stay honest and different: dollars and tokens for Claude Code, sessions and
turns for Codex.

Worth noting what else is on the wire for later use, all unconfigured: Claude
Code labels datapoints with `effort`, `query_source` (main vs auxiliary),
`terminal.type`, `session.id`, and — on tool-call metrics — `skill.name`,
`mcp_server.name` and `mcp_tool.name`. Codex labels its own with `auth_mode`,
`session_source`, `originator` and `app.version`.

A separate limit, stated because it looks like a bug and is not: a repo whose
checkout never set the exporter env vars emits **nothing at all** and cannot
appear here after the fact, harvest or no harvest. Attribution is per-checkout
configuration (`.claude/settings.local.json`), so a new project is invisible
until it is configured — telemetry is not retroactive at repo granularity
either.

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
- **No new credentials.** It reads CloudWatch in the lab's account with the AWS
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

### Follow-up: a static credential in the agents' shell env (raised + largely resolved 2026-07-26)

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

**Resolved 2026-07-26 — but not the way this entry first proposed.** The
original recommendation here was "have the hook fetch it from Secrets Manager
the way `otel_headers.sh` does". That was written before checking which account
hosts the service, and it is wrong twice over.

**First, the exposure is worse than "plaintext on disk".**
`shell_environment_policy.set` injects its values into the environment of every
shell command Codex runs, and Claude Code's settings `env` block does the same
— confirmed by finding `LOGGING_API_KEY` in a Bash tool's own environment
mid-session. (The `OTEL_*` vars do *not* propagate; Claude Code consumes those
internally. This is specific to using the generic env channel.) So the key was
being handed to every subprocess an agent chose to spawn: one stray `env` in a
log, one diagnostic upload, one crash reporter, and it is out. A broadcast
channel was being used to reach exactly one consumer — the hook script.

**Second, Secrets Manager does not transplant here.** D39's pattern is "fetch
it with the AWS session you already have", but that session is the lab's **runtime account**
(Salesforce) and `aws-logging-service` runs in the **personal** AWS account. A
Secrets Manager fetch would need personal-account credentials, which is the
same problem one layer up. Storing a personal service's key in a corporate
account's secret store would satisfy the letter of D39 and be wrong on
governance.

**What was done instead:** both hook scripts
(`~/.claude/hooks/claude-notify.sh`, `~/.codex/hooks/codex-notify.sh`) now read
the key from the **macOS Keychain** (service `a2alab-logging`), and
`LOGGING_API_KEY` is removed from both config files. Encrypted at rest, no
network dependency, no cross-account entanglement, and the key exists only
inside the hook process for the life of one `curl` — never in an agent
subprocess again. `LOGGING_API_URL` and `LOGGING_CHANNEL` stay in config; they
are not secrets. The env var remains a documented fallback so a machine without
the Keychain item keeps working, with a stderr warning when that path is taken.
Verified: 200 with the Keychain key, 401 with a bogus one.

**Still open, and the reason this is not fully closed:**

1. **Rotate the key.** It was plaintext on disk and exported into every agent
   subprocess for weeks. Rotation is cheap; assuming it stayed contained is not.
2. **The real fix is to have no key at all.** Switch the API Gateway `/log`
   route from API-key auth to IAM, with a resource policy trusting the lab's
   principal, and let the hook SigV4-sign with the session already in hand.
   Nothing stored, nothing to rotate, nothing to leak — the true D39 shape.
   Needs a cross-account resource policy, so it is real work; worth it if this
   ever moves beyond one laptop.
3. Scope the usage plan to `/log` and rate-limit it, so any leak is bounded.

**Effort:** Keychain migration was ~20 minutes and is done. Item 2 is ~half a
day.

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

**Status: NOT STARTED.** Research and planning only — no Agent Fabric build,
no code, config, deploy script or ADR yet. Gated on the Phase 0 entitlement
check below, and scheduled last. (This line is explicit so the delivery record
does not inherit the status of the `## Lab Guide` section that follows: both are
un-numbered sub-sections inside WS10's span, and `jira_sync.py` would otherwise
read the Lab Guide's "built" status as WS10's.)

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
`tmp-docs/07.25.2026-AD3-mulesoft-agent-fabric.md` — a **local working note,
not in the repo** (`tmp-docs/` is gitignored), so it is cited here for the
author's provenance rather than as something a reader can open. Headline for planning
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

**Status 2026-07-27: the client half is BUILT and the per-platform measurement
is TAKEN (D47).** `A2AClient.submit()` / `.poll()` ship alongside the blocking
`ask()`, `tests/e2e/test_a2a_async.py` proves the shape against a deterministic
slow adapter, and `scripts/a2a_async_probe.py` produced the per-platform table
in plan/03-results.md. Three results, in order of importance:

1. **The gateway ceiling is dissolved, measured on the component that hit it.**
   The hosted Agentforce shim ran **31.1s of work behind API Gateway's 29s
   integration timeout**, longest single request **1.18s**. The quota request
   and the apigatewayv2→v1 migration D41 costed out are both unnecessary for
   this path.
2. **The workstream's stated blocker did not exist.** No change to
   `AdapterExecutor` was needed — the a2a-sdk already honours
   `configuration.return_immediately` and keeps consuming in a background task.
   The lab had the async half switched on and had simply never asked for it.
3. **Support is uneven, and Lambda is not free.** Foundry implements the async
   lifecycle properly; Agent Engine *appeared* submit-only until item 12 traced
   it to our missing `A2A-Version: 1.0` header — with the header it serves
   fire-then-poll fully; and on Lambda the background work only advances while
   the client polls, because the runtime freezes between invocations.

**Build item 3 — the durable half, written 2026-07-28.** `src/fanout_mcp/tasks.py`
plus `lab.fanout_tasks` (migrated, live). The obvious submit/check would have
been broken, and finding 3 above is why: starting a leg in the background of the
invocation that returns the task id gives work that does not progress, in state
no other instance can read. So the state goes in Aurora, which every instance
shares, and the work runs in a **separate invocation** (`InvocationType='Event'`)
that owns its own execution window. Six tests pin the properties that follow
from the measurement — submit does not do the work, a second instance can read a
task it did not create, a worker that raises leaves FAILED rather than a task
stuck WORKING for ever, and a redelivered task (async invoke is at-least-once)
does not re-run the leg and bill a second agent call.

**Items 6–7 — the MCP submit/check tools, built 2026-08-12.** The dispatcher and
worker (item 4) are now wired into the served registry: the fan-out MCP server
exposes `submit_<unit>` per business unit and one shared `check_task` alongside
the blocking `consult_*`, so **both topologies live on the one deployed server**
and switching between them needs no redeploy — only a different SYSTEM prompt.
`submit_<unit>` creates the durable task, self-invokes the worker in its own
execution window, and returns a task id in ~1s without running the leg; the model
then polls `check_task` (by task id, or by run id for all three at once) until
every unit is terminal. This is deliberately the A2A submit/poll lifecycle
re-expressed as MCP tools — the same shape item 3 measured over real A2A
endpoints, now something the *model* schedules rather than our client. Async is
selected per run by a fire-then-poll system prompt recorded as `system_async` on
the same agent (`agent_with_overrides`); `CmaOrchestrator` picks it by
`dispatch_mode` and the console offers the sync/async radio for the mcp variant
too. **Still held for the operator:** the live poll-vs-busy-wait *measurement* —
on Lambda, polling is what advances the work, so a model that backs off politely
makes its own run slower — because it needs the fan-out bundle redeployed with
the new tools and the orchestrator re-provisioned to write `system_async` against
the live Anthropic API. The code and its unit tests are in; the live run is a
deploy step.

**Build items 8–10 — host-side async dispatch as a per-experiment choice, built
2026-08-11.** The fire-then-poll pattern is now a run-time control on the
host-side fan-out, not only a probe: `orchestration.dispatch()` takes a
`dispatch_mode` of `"sync"` (today's blocking `ask()`) or `"async"` (A2A submit
+ poll to a terminal state), the supplier-disruption CMA experiment carries a
`dispatch_mode_toggle`, and the console renders a synchronous / async radio.
(Originally this radio was gated to the host-side "tool" variant, because async
was then a no-op under "mcp" whose legs run in the Lambda; items 6–7, built
2026-08-12, added the MCP submit/check tools so the radio now applies to both
topologies.) Capability is
detected per leg: the two A2A legs (ADK, Foundry) run the real lifecycle, while
the AgentCore comms leg has no `submit`/`poll` and is recorded **async→sync** —
the WS11 per-platform finding, now surfaced live in the fan-out coverage line,
the operator-facing run summary, and a turn badge, rather than only in a probe
table.

**Item 11 (2026-08-11) — the "submit-only" verdict was wrong, and we found out
by implementing the pattern the spec claims.** The first cut of item 11 caught
the ADK Logistics leg's poll failure and degraded it to `async→sync`, framed as
"Agent Engine accepts the submit and then will not return the task". That is the
kind of finding the lab exists to produce — *except it was our bug, not the
platform's.* Before capturing it as an insight we researched the A2A spec and the
ADK/Agent Engine docs (as the lab's own method demands), then probed the live
endpoint. Agent Engine's managed A2A handler is pinned to protocol **1.0** and
reads a **missing** `A2A-Version` header as "0.3" (a2a-python's
`@validate_version` + `constants`), so the header-less `tasks/get` our client
sent came back 400 "A2A version '0.3' is not supported by this handler. Expected
version '1.0'." The a2a-sdk never sends that header on its own. Pinning
`options.protocol_version: "1.0"` on the ADK targets (`config/targets.yaml`) — a
per-target `A2A-Version: 1.0` on every request, scoped so our own 0.3-speaking
Agentforce shim is untouched — **makes fire-then-poll work end-to-end against the
managed Agent Engine**, verified live: the exposure (ADK) leg now runs `async, 1
poll` alongside Foundry, and only the OpenAI AgentCore leg falls back (it has no
A2A task lifecycle at all — a real platform difference).

One live wrinkle the header did not cover: right after submit, the first
`tasks/get` can 404 while Agent Engine's task store catches up (eventually
consistent), and a later poll succeeds. `_run_leg_async` now rides through a
not-yet-visible task for a bounded grace window (`POLL_NOT_FOUND_GRACE_S`, 45s —
wide enough for a ~34s cold start) before treating the poll as the genuine
"took the submit, won't serve the task" degradation. The distinction is
*position*, not error code: the same 404 (`MethodNotFoundError`, the SDK's map of
a bare 404) means "not visible yet" right after submit and "never will be" once
the task has been read once. The `_POLL_UNRETRIEVABLE` tuple stays narrow and the
`AsyncLifecycleUnsupported` fallback stays — a genuine `TASK_STATE_FAILED`, a
500, or an auth error still fails the leg, and any *other* remote that is truly
submit-only degrades honestly to `async→sync`. But the Agent Engine legs no
longer take that path. **The finding flips from "the A2A-flagship platform can't
serve its own async half" to "the SDK client must know to send `A2A-Version:
1.0` — once it does, two of three platforms serve fire-then-poll and the third
has no task lifecycle to serve."** Folding the poll count and per-leg mode into
the obs store, and extending the toggle to the other experiments, follows.

### Work items

| # | Item | State |
|---|---|---|
| 1 | A2A async client — `submit()` (sets `configuration.return_immediately`) and `poll()` on `tasks/get`, alongside the blocking `ask()` (`src/interop/clients/a2a.py`) | done |
| 2 | E2E shape proof against a deterministic slow adapter — submit-returns-early, poll-walks-to-completion, polling-is-traced, blocking-ask-still-waits (`tests/e2e/test_a2a_async.py`) | done |
| 3 | Per-platform async-lifecycle measurement (D47) via `scripts/a2a_async_probe.py` — Foundry honours the full lifecycle, Agent Engine *appeared* submit-only (later found to be our missing `A2A-Version` header, item 12), Lambda advances only while polled; the 31.1s-of-work / 1.18s-longest-request gateway-ceiling result recorded | done — plan/03-results.md |
| 4 | Durable task store — Aurora-backed `lab.fanout_tasks` + separate-invocation worker (`src/fanout_mcp/tasks.py`, `InvocationType='Event'`; DDL run by `pg_migrate.py`) | done |
| 5 | Six property tests pinning the durable-store guarantees (submit-does-no-work, state-in-store-not-process, cross-instance read, worker-failure-is-terminal, run-id-joins-units, redelivered-task-is-a-no-op) | done |
| 6 | Register the submit/check MCP tools on the fan-out server (2026-08-12) — `submit_<unit>` per business unit + one shared `check_task` poll tool, alongside the existing blocking `consult_*` on the ONE deployed server; submit creates the task, self-invokes the worker (`InvocationType='Event'`) and returns a task id in ~1s without running the leg; the Lambda entry routes `{"a2alab_fanout_task": id}` to the worker, which resolves the run id from the store and runs the leg to a terminal state in its own window; the model drives the poll loop (`src/fanout_mcp/tools.py` `AsyncFanOutTools`/`worker_runner`, `lambda_entry.py`, tests in `test_fanout_mcp.py`) | done — code + tests; server redeploy is the operator step |
| 7 | Async orchestrator prompt + console wiring (2026-08-12) — a fire-then-poll SYSTEM prompt (`mcp_orchestrator_prompt_async`) recorded as `system_async` on the same agent/server and selected per run by `dispatch_mode` (`agent_with_overrides`, no redeploy to switch); `CmaOrchestrator(variant="mcp", dispatch_mode="async")` picks it, guarding a pre-WS11 agent with a catchable `OrchestratorNotProvisioned`; the console renders the sync/async radio for the mcp variant too and sends `dispatch_mode` for both topologies; `run_fanout.py --dispatch-mode async` for CLI (`src/orchestration/agents.py`, `cma.py`, `scripts/setup_fanout_orchestrator.py`, `run_fanout.py`, `index.html`). The poll-vs-busy-wait measurement over a live model run is held for the operator (re-provision + server redeploy) | done — code + tests; live measurement pending re-provision |
| 8 | Host-side async dispatch in the fan-out — `dispatch_mode` of sync/async on `orchestration.dispatch()`/`run_one()`/`CmaOrchestrator`, submit+poll per leg with capability detection and async→sync fallback recorded per leg (`src/orchestration/runner.py`, `cma.py`) | done |
| 9 | Per-experiment sync/async toggle — `dispatch_mode_toggle` on `supplier-disruption-cma`, console radio (originally gated on the host-side "tool" variant; extended to "mcp" in item 7), coverage line + run summary + turn badge surfacing the async dimension (`config/scenarios.yaml`, `src/console/app.py`, `index.html`) | done |
| 10 | Unit tests for host-side async dispatch — submit+poll on A2A legs, async→sync fallback for a leg with no task lifecycle, sync path unchanged (`tests/unit/test_orchestration.py`) | done |
| 11 | Runtime submit-only fallback (2026-08-11) — an A2A leg whose remote genuinely accepts the submit but will not return the task through the poll is caught across all three measured shapes (`MethodNotFoundError` / `VersionNotSupportedError` / `TaskNotFoundError` → `AsyncLifecycleUnsupported`) and degraded to blocking `ask()` recorded async→sync, instead of failing the leg — while a genuine `TASK_STATE_FAILED`/500/auth error still fails it; console `describeTrace` now narrates a fan-out as N concurrent legs rather than mislabelling it a single "Direct cell", and the "remote MCP tools" hint states the API Gateway ceiling that times out cold legs (`src/orchestration/runner.py`, `index.html`) | done |
| 12 | **Agent Engine's "submit-only" was our missing `A2A-Version` header** (2026-08-11) — researched the A2A spec + ADK/Agent Engine docs and probed the live endpoint: the managed handler pins 1.0 and reads a missing header as 0.3, 400ing every header-less poll. Added `A2AClient.protocol_version` → per-request `A2A-Version` header, wired `options.protocol_version` through the registry, pinned `"1.0"` on `google-adk-a2a`/`adk-logistics-a2a` (scoped, so the 0.3 Agentforce shim is untouched). Added a not-yet-visible grace window (`POLL_NOT_FOUND_GRACE_S`) for the eventually-consistent task store. Verified live: exposure (ADK) leg now runs async end-to-end. Header/registry/transient-404 tests added (`src/interop/clients/a2a.py`, `registry.py`, `config/targets.yaml`, `runner.py`, tests) | done |
| 13 | **Fire-then-poll for the OTHER two primary controllers — ADK and Agentforce (2026-08-12, D76)** — the CMA orchestrator's async dispatch (items 6–9) extended to the two remaining supplier-disruption controllers, honestly surfacing that *where the poll loop runs* differs per controller. **ADK**: each declared `ParallelAgent` leg tool now routes through the shared `orchestration.runner.run_one` (not a bespoke A2A call), so `dispatch_mode`/`trace_id` thread from the inbound A2A task metadata into the submit+poll loop that runs *inside the Agent Engine container*, off any gateway, on the full async budget (`_leg_tool`/`build_fanout_orchestrator`/`_OrchestratorExecutor._build_agent`/`execute`, `src/platforms/adk/agent.py`). **Agentforce**: it cannot poll (one serial Apex callout), so the async loop runs at the **bridge**, on the orchestrator's behalf, during the single held-open callout (bounded by that ~110s callout, not a gateway — the bridge is long-lived Fargate). The mode rides the situation as a `fanout-dispatch:` `[A2A-LAB ROUTING]` block the bridge reads and strips before the legs see it; absent → sync, never an error (`af_channel.dispatch_block`/`read_dispatch_mode`/`strip_routing_blocks`, `bridge/app.py` `_fanout`). Console: `dispatch_mode_toggle: true` on both scenarios, controller-aware Details prose (`fanoutControlsDetailHtml`), per-run metadata/block wiring (`src/console/app.py`). Tests: `test_af_channel.py`, bridge fan-out dispatch, ADK leg-tool async wiring. Full narration of the three controllers and where each poll loop runs — plus the "can a Salesforce Flow poll natively" answer — in `plan/14-supplier-disruption-orchestrators.md` | done — code + tests; ADK+bridge+console redeploys and the live measurement are the operator step |


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

## WS12 — Cost sentinel: a scheduled agent over the build-telemetry store (raised 2026-07-27)

**Status: PROVISIONED 2026-07-27 (D44); moved to DAILY and resumed 2026-07-30
(D44 addendum).** The agent, its own vault on the obs MCP server and the
deployment exist. The schedule was `0 7 * * 1` and paused as designed; it is now
`0 7 * * *` (America/New_York) and **active**, changed in place via
`deployments.update` and repointed to agent v2 whose prompt leads with
day-over-day. This was done because the paused-weekly shape meant no scheduled
brief ever fired, so the console's newest cost brief was the 2026-07-28 manual
one and a demo viewer read the panel as "not provisioned". Pause/Resume/Run are
now console buttons. The daily brief and its day-over-day are live. On the exit
criterion below (a week-over-week read): as of 2026-08-11 the store has 16 days
of history (2026-07-26→08-11), so the data-accumulation gate is now met — but the
kickoff prompt was deliberately redirected to lead with day-over-day + a single
trailing-7d trend line (D44 addendum), so no brief has yet attempted the
trailing-7d-sum vs prior-trailing-7d-sum comparison the criterion names. The
remaining gap is therefore *design*, not data: either revise the exit criterion
to the day-over-day shape that shipped, or extend the kickoff prompt to also run
the original week-sum-vs-week-sum read now that the history exists.

**What provisioning found — three gaps between built and running, now D46.**
Getting from code-complete to a working firing was not one setup script. The
`kind` column had never reached Aurora (the DDL's only caller runs as
`lab_writer`, which cannot ALTER a master-owned table, and caught the failure as
"assuming provisioned"); nothing in the repo had ever pushed the obs MCP zip, so
the deployed function predated the column by three days; `pg_backfill.py` had
been unable to write rows at all since the reader/writer secret split; and the
hosted harvest both predated `coding_source.py` and lacked the CloudWatch PromQL
grant, so **Aurora held zero coding rows** while the local console looked
healthy. All four are fixed, and `scripts/pg_migrate.py` now owns DDL.

**Goal.** A daily briefing that answers the question nobody remembers to ask
until the invoice arrives: *what did this lab cost yesterday, how does that
compare to trend, and **why** did it move?* (Originally scoped weekly; moved to
daily 2026-07-30 for a fresh console read each morning — D44 addendum.)

**Why an agent, and why a scheduled one.** This passes the test the credential
analyst failed. That analyst was built as a Managed Agent and demoted to a plain
API call, because it had no tools, no schedule and no state — see the postscript
in `build-notes/claude/09-secrets-and-environment-identity.md`, and D22 for the
underlying split. The cost sentinel is the opposite case on all three counts:

- **Tools** — the numbers live in the obs store, which already has a hosted MCP
  server (D23). "Which repo drove the delta" is a query, not a paragraph that
  fits in a prompt.
- **Schedule** — collection is already hosted. `coding_source.py` runs in the
  6-hourly harvest Lambda against CloudWatch, so unlike credential expiry there
  is **no dependency on the operator's laptop**. A cron can genuinely run this.
- **State** — week-over-week comparison needs history, and the store has it.

That combination is the whole argument. If any one of the three were missing,
this should be a script.

**What it produces.** A short brief per week: total modelled cost, the delta
against the previous week and the 4-week trend, the split by repo and by model,
and — the part only a model can write — *the attribution*: "the fan-out
experiments ran 40 times on Tuesday, which is the delta", or "opus replaced
haiku on the research agent and cost per turn tripled". Plus an honest floor:
Codex publishes no cost metric, so any cross-tool total is modelled from tokens
and a price table and must say so.

**Where it lives in the console.** Under the existing **Coding Agents
Telemetry** section, presented the way the Credentials Expiry Analysis page is:
a `tabbar` with **Run** and **Details**, Run showing the measured table beside
the brief, Details carrying the architecture diagram, the why-an-agent
reasoning, and links to the agent in the Claude console. That pattern is now
established — reuse it rather than inventing a third layout.

**Sketch of the build.**

1. `scripts/cost_sentinel.py setup` — create the Managed Agent wired to the obs
   MCP server with a `query_cost` tool (or extend the existing obs MCP tools;
   prefer extending, since a second server is a second thing to deploy).
2. A weekly scheduled deployment, **created paused** like the obs analyst
   (D23) — real sessions bill per firing.
3. Briefs land in `lab.obs_briefs` with a `kind` discriminator so the cost
   briefs and the observability briefs share one table and one reader.
4. Console: `/api/cost-brief`, operator-gated, and the two-tab view.

**Open questions — all three settled 2026-07-27.**

- **Does the brief go in the same table as the obs analyst's?** ✅ **Yes.**
  `lab.obs_briefs` gained a `kind text NOT NULL DEFAULT 'observability'` column
  (an idempotent `ADD COLUMN IF NOT EXISTS` — the table predates it everywhere
  deployed, and the default correctly backfills the analyst's existing rows).
  `save_brief` takes an optional `kind`; omitting it still means observability,
  so the deployed nightly agent, whose prompt never mentions the field, keeps
  working unchanged.
- **What is the honest cost number?** ✅ Settled as a constant with two
  consumers: `BUILD_TELEMETRY_COST_NOTE` in `src/console/app.py` rides on both
  `/api/build-telemetry` and `/api/cost-brief`, and the sentinel's system prompt
  makes leading with it rule 2. Related and more damaging, found while doing
  this: the telemetry was reporting **two** of the four billed token buckets, so
  `input_tokens` (the *uncached remainder*) was rendering as "input" — a 36x
  understatement on one real day. Fixed in the same change; see D44 and
  `build-notes/claude/10-consumption-and-list-price.md`.
- **How does it avoid crying wolf?** ✅ Rule 5 of the system prompt makes
  "spend was flat, nothing needs attention" an explicitly correct brief, and
  rule 1 forbids any figure not traceable to a query it ran. Same discipline as
  the credential analyst.

**What was built (2026-07-27).** `scripts/setup_cost_sentinel.py` (agent +
weekly deployment, created **paused**, cron `0 7 * * 1` — Monday morning, when
the week it reports on is closed), `scripts/cost_sentinel.py`
(run/status/latest/pause/resume, sibling of `obs_analysis.py`), the `kind`
column and its plumbing through `pg.py` and the obs MCP `save_brief` tool,
`/api/cost-brief` + operator-gated `/api/cost-brief/run`, and the Run / Details
tabbar on the console's Coding Agents Telemetry section — Run carries the brief
above the measured tables, Details carries the why-an-agent table, the pipeline
diagram and the provisioning facts. It reuses the obs MCP server rather than
deploying a second one, and takes its own vault so revocation stays per-agent.

**Moved to daily, resumed, and given console controls (2026-07-30).** The
schedule was changed in place to `0 7 * * *` (America/New_York) and the
deployment resumed — via `deployments.update`, so the deployment id, vault and
run history are preserved rather than recreated — and repointed to a revised
agent (v2) whose prompt and kickoff lead with day-over-day and treat the current
in-progress day as not comparable. `scripts/cost_sentinel.py` grows no new
verbs, but the console gains `/api/cost-brief/schedule` (operator-gated
pause/resume, reading the deployment's live status back so the button reflects
reality) alongside the existing `/api/cost-brief/run`, surfaced as **Pause /
Resume** and **Brief now** buttons on the Run tab. The first daily-prompt firing
(brief id 100) led with 07-29 vs 07-28 (‑32%, attributed to a token-mix
collapse), correctly discounted the partial current day, and noted the trend was
only five points — the day-over-day shape working as intended.

### Work items

| # | Item | State |
|---|---|---|
| 1 | Setup script — Managed Agent + scheduled deployment, created **paused**, with its own vault so revocation stays per-agent (`scripts/setup_cost_sentinel.py`) | done |
| 2 | Operator CLI — run / status / latest / reconcile / pause / resume; `latest` reads the store directly without Anthropic creds (`scripts/cost_sentinel.py`) | done |
| 3 | `kind` discriminator so cost and observability briefs share one table + reader (`ADD COLUMN IF NOT EXISTS kind`, `save_brief`/`list_briefs` filter) | done |
| 4 | Console API + two-tab UI — `/api/cost-brief`, operator-gated `/api/cost-brief/run`, `/api/cost-brief/schedule` pause/resume; the list-price caveat shared across both cost surfaces | done |
| 5 | Moved to daily + resumed + console controls (D44 addendum) — in-place `deployments.update` to `0 7 * * *` America/New_York, repointed to agent v2; Pause/Resume/Brief-now buttons | done |
| 6 | `reconcile` links briefs → billed sessions after the fact (the runtime never tells the agent its own session id); recovered the first firing's session at +73s | done |
| 7 | First firings produced correct briefs — the 2026-07-28 manual brief refused a too-thin week-over-week and attributed the move to session count; daily brief id 100 led with day-over-day and discounted the partial day | done — author-attested in the plan, not a checked-in results file |
| 8 | Exit criterion — a real week-over-week read verifiable from the by-day table | in progress — the data-accumulation gate is now MET (16 days of history as of 2026-08-11); what remains is a design call, since the kickoff prompt was redirected to day-over-day (D44 addendum) and never attempts the trailing-7d-sum comparison the criterion names |

**Exit criteria.** One scheduled firing produces a brief that correctly explains
a cost movement the operator can independently verify from the by-day table —
and one day where nothing notable happened produces a brief that says so in two
lines.

**First firing, 2026-07-28 (manual, while paused).** The brief is in
`lab.obs_briefs` with `kind='cost'`, and it did the thing it was built to do:
it **refused the week-over-week comparison** ("too thin for a week-over-week
read"), reported the two days it had, and attributed the movement correctly —
$16.22 → $171.31 driven by 1 → 7 sessions and 9.5x active time on the *same*
repo and model, with cost per active-second flat at +6%, explicitly ruling out a
mix shift. Every figure was checked against `lab.obs_sessions` by hand and
matches to the cent. It kept Codex's sessions out of the dollar total, led with
the list-price caveat once, and — unprompted — **flagged the CloudWatch 403 as
an operational risk to next week's brief**, which is exactly the missing PromQL
grant D46 describes. An agent reporting the gap in its own input is the best
available evidence that rule 1 is holding.

**`obs_briefs.session_id` — closed by reconciliation 2026-07-27.** The column
had been null for every brief either analyst ever wrote, and it is structurally
unfillable at the write site: `save_brief` takes it as a tool argument, and the
Managed Agents runtime never tells the agent its own session id. Stamping it
from `cost_sentinel.py run` would have covered manual firings only — the
scheduled cron has no local runner, which is the entire point of scheduling it. The id
lives on the deployment RUN, so the join happens after the fact:
`cost_sentinel.py reconcile` matches each unlinked brief to the newest run
starting at or before it within an hour, claiming each session at most once
(two briefs matching one session means the guess is wrong, so both are left
null rather than duplicated). It runs opportunistically from `status` — the
command a person runs after a scheduled firing — and explicitly on demand.
Verified against the first firing: it recovered `sesn_01ExJPuJvqKGSBuJQ8M1mpnp`
at +73s, the same id `run` had printed, and a second pass is a no-op.

**The analyst's brief is still unlinked** (`kind='observability'`, 2026-07-18).
Same defect, different deployment id — the fix ports to `obs_analysis.py`
unchanged, and has not been done.

**What the data supports today (2026-07-27).** Aurora holds **three** coding
day-rows: `claude-code:2026-07-26`, `claude-code:2026-07-27`, `codex:2026-07-27`.
Telemetry is not retroactive and collection started on the 26th, so a
week-over-week comparison is arithmetically impossible until 2026-08-03 — which
is, conveniently, the first scheduled firing. The kickoff prompt already handles
this ("if the store holds less than two weeks of data, say so and report what is
there rather than inventing a comparison"), so the first brief is a test of rule
1 — *never state a number you did not get from a query* — rather than of the
attribution the workstream exists for. **A first brief that refuses to compare
is a pass, not a failure**; the exit criteria stay open until a firing has two
real weeks behind it.

**Cursor added as a third coding tool (2026-07-31, D64).** The same
`coding_source.py`, but Cursor is the odd one out *three* ways, all handled on
the read side so nothing downstream changes. First, it ships **no native OTel
exporter** — the checked-in `.cursor/hooks.json` forwards lifecycle events to the
cursorscope ingestor, which exports to the same managed metrics endpoint
(`scripts/cursor_otel.sh`, build-notes/cursor/01). Second, its counters are
**cumulative**, not the delta Sums the two native exporters emit, so the harvest
queries `cursor_*` with `increase()` (gated by `CUMULATIVE_METRICS`) where the
others use `sum_over_time()`; summing a cumulative counter over-counts by the
running total at every step. Third — and this is what made the first attempt
show nothing — cursorscope carries **none of the `@resource.tool`/`repo`/
`project` labels** the two native exporters do; it labels by
`@resource.service.name` / `service.namespace` / `deployment.environment`, so
`_metric_rows` falls back to those for Cursor (tool from service.name, repo from
deployment.environment, project from service.namespace) or every row lands
`unattributed`. The other half of the initial blank was the metric list: the one
series that had landed was `cursor_hook_events_total`, which the first
`CURSOR_METRICS` omitted. Like Codex, Cursor publishes no cost metric and its
token figures are `gen_ai.*` histograms this surface cannot scalarise, so it is
read for **sessions only** and contributes nothing to the cross-tool dollar
total. Status: **harvest path proven** — the writer Lambda landed a
`cursor:2026-07-31` row in Aurora that shows as a `cursor` line item in the
console. It reads zero sessions honestly: the only datapoints so far are the
setup script's flush probe, so a real session's counters landing with the
intended labels is the last mile still open, exactly the bar the
`telemetry-config-is-not-evidence` insight sets. Behavioural **logs** for Cursor
are out of scope; `coding-logs` stays Claude Code-only.

---

## WS15 — Jira as the delivery record (raised 2026-07-29)

**The requirement, in the operator's words:** manage tasks and workstreams in
Jira going forward, and *retroactively* represent the build so far so the project
can be shown as delivered work rather than only as a repository.

Two jobs, and they want different treatment. **Forward** is ordinary backlog
management. **Backward** is a reconstruction, and a reconstruction of a
three-week build has one failure mode worth naming up front: inventing process
that never happened.

### The recommendation that differs from the ask

The ask floated *"sprints for each workstream"*. That is a category error worth
declining: a **sprint is a time box**, a **workstream is a scope box**. Bucketing
WS1–WS14 into fourteen "sprints" would produce a board that looks like Scrum and
describes nothing — WS2 and WS8 overlapped, WS12 spanned two weeks, and several
workstreams were a single afternoon.

Two honest options instead, and the choice depends on what the board is *for*:

| | Kanban, epics = workstreams | Scrum with the REAL calendar |
|---|---|---|
| Shape | one board, 14 epics, no sprints | 3 sprints from actual week boundaries |
| Shows | breadth and structure | breadth, structure **and pace** |
| Cost to build | lower | one extra decision per issue (which week) |
| Honest? | yes | yes — the dates are in git |

Scrum with real week boundaries was recommended here first — the repository knows
them (186 commits across 17 working days, 2026-07-09 to 2026-07-29), so the
sprints would have been measured rather than invented, and pace is the most
interesting thing about the build.

**Decided 2026-07-29: Kanban, no sprints** (operator's call — a solo project has
no sprint ceremony to hold, and nobody is planning capacity against it). The pace
story does not need Jira to tell it: git has the dates and WS9's build telemetry
has the cost. Recorded as D58. **Do not** create fourteen one-workstream sprints
under any option.

### The mapping

| Jira | This repo | Notes |
|---|---|---|
| **Epic** | one per workstream, `WS1`–`WS15` | the workstream's own title becomes the epic summary |
| **Story / Task** | one per numbered work item — a work-items table row (WS13–15) or a `✅`/`⏳` numbered status line (WS1–WS12) | these already read as deliverables; the narrative sections are left alone, because prose turned into stories invents a granularity the work never had |
| **Sub-task** | only where an item genuinely split | do not manufacture depth |
| **Label** | `adr-D<n>` | an ADR is a DECISION, not a task — link it, never convert it |
| **Link → repo** | commit or ADR permalink in the issue | the lab's rule that every claim travels with a source, applied to the board |
| **Done reason** | the ADR or the measured result | "done" with no evidence is the thing this project does not do |

**ADRs stay documentation.** 58 of them; turning each into a ticket would triple
the board and lose the distinction the lab cares about — a decision is not a unit
of work. They become labels and links.

### What the operator does in Jira (before hookup)

1. **Create the project.** Team-managed is enough; nothing here needs
   company-managed schemes. Template: **Scrum** if taking the recommendation,
   **Kanban** otherwise. Note the **project key** (e.g. `A2A`) — every issue id
   derives from it.
2. **Enable the Epic issue type** if the template did not (team-managed Scrum
   includes it).
3. **Create an API token** — id.atlassian.com → Security → API tokens. It is a
   credential: it goes in `.env` and Secrets Manager like every other one (D39),
   never in the repo.

   **Take the classic, unscoped token.** Atlassian now offers scoped tokens too,
   but they force every call to `https://api.atlassian.com/ex/jira/{cloudId}`
   instead of the site domain, and the scope list is the **granular** set
   (`write:issue:jira`, `read:issue-type:jira`, `read:field:jira`, …) rather
   than the classic `read:jira-work` / `write:jira-work` pair that Atlassian's
   OAuth and Forge docs describe — a real trap, since searching for scope names
   lands on the wrong list. Least privilege argues for scoping, and it is
   normally the right call (D39/F6); here the token's owner is the only user of
   the one project it touches, so the containment is nominal and the extra
   indirection is not.
4. **Decide the sprint question** above, and say which.
5. Hand over **two** values: the **account email** and the **token**.

   Not the site URL or the project key — those are discoverable and guessing
   them is how a config file acquires a wrong value that looks right. The
   `home.atlassian.com/o/<org>/s/<uuid>/…` URL the console shows is the new
   unified home, not an API host; the `s/` segment is the cloudId.
   `GET https://api.atlassian.com/oauth/token/accessible-resources` returns the
   real site URL and cloudId, and `GET /rest/api/3/project/search` returns the
   project key. Both get confirmed back before anything is created.

### What happens once it is connected

| # | Item | State |
|---|---|---|
| 1 | Confirm Jira reachability and the space's issue types before creating anything | **done 2026-07-29** — space `A2A` created (team-managed; the API still calls it a `project`); Epic/Story both expose `parent`, so epic linking needs no custom field |
| 2 | Create 15 epics from `plan/07-workstreams.md` | **done 2026-07-29** — `scripts/jira_sync.py`, idempotent, dry-run by default |
| 3 | Create stories from the work-item tables, with their real state (`done` / open) | **done 2026-07-29** — 46 stories, 40 closed |
| 4 | Attach `adr-D<n>` labels and repo links so each closed item carries its evidence | **done 2026-07-29** — 30 distinct ADR labels; repo links existence-checked before they are written |
| 5 | Import from the plan's TWO item shapes — work-item tables (WS13–15) and statused numbered lines (WS1–WS12) — and import nothing from the narrative sections rather than inventing items for them | **done 2026-07-29** — 8 workstreams import as childless epics, on purpose |
| 6 | Record the two open operator actions (WS14 items 4 and 5) as real open tickets | **done 2026-07-29** — open on the board, not buried in a plan table |
| 7 | Add a `plan/11-delivery.md` mapping epic → workstream, so the board and the plan cannot drift | **done 2026-07-29** (D58) |

**Do NOT** create issues before item 1 confirms the schema. A team-managed
project with the wrong template silently lacks Epics, and the repair is
per-issue.

### Exit criteria

Every workstream is an epic, every work item is an issue in the state the repo
says it is in, and each closed issue names its evidence. New work starts as a
Jira issue rather than a line in `plan/07-workstreams.md` — with the plan
remaining the place decisions and reasoning live, because a ticket is a unit of
work and an ADR is a unit of thinking, and this project has been better for
keeping those apart.

**The honesty constraint applies to the board too.** The retroactive import
should not show a tidier process than happened: several workstreams were raised,
partly built, and revised days later (WS7 folded into WS13; the analyst's
`always_allow` fix sat un-deployed for days). Where the repo records that, the
issue should say so rather than reading as a clean single pass.

---

## WS14 — Zero laptop dependency: host the credential collector (raised 2026-07-29)

**The standing requirement, in the operator's words:** *"zero laptop dependency
is the goal"* — the lab and everything the console exposes should depend on the
laptop only when pushing builds into the hosted environments.

WS13 got the **runtime** there. One thing still runs on the operator's machine
and feeds the console: `scripts/expiry_report.py`, whose snapshot the
Credentials Expiry panel renders. It has to be run by hand, so the panel is only
as current as the last time somebody remembered. That is now visible — the panel
dates its snapshot and flags it past 24h (D56-era work) — but a date on a stale
number is a mitigation, not a fix.

### Why it is still local, precisely

Every collector shells out to a **CLI**, and those CLIs read the operator's own
logins:

| Reads | Today | Hosted equivalent |
|---|---|---|
| AWS IAM service credential age | `aws iam list-service-specific-credentials` | boto3 + `iam:ListServiceSpecificCredentials` on the task role |
| AWS ACM certificate expiry | `aws acm list-certificates` / `describe-certificate` | boto3 + `acm:ListCertificates`, `acm:DescribeCertificate` |
| GCP service-account key age | `gcloud iam service-accounts keys list` | google-auth + IAM REST, using the SA key already in the harvest secret |
| Entra app secret expiry | `az ad app credential list` | Microsoft Graph `/applications` — **blocked, see below** |
| Declared rotations | `config/credentials.yaml` | already a file in the image |

### The one that does not move without you

**Microsoft Entra requires an admin consent the service principal does not
have.** Measured 2026-07-29: a client-credentials token for the lab's SP calling
Graph `/applications?$filter=appId eq '<id>'` returns

```
403 Authorization_RequestDenied — Insufficient privileges to complete the operation.
```

Reading application objects needs the **`Application.Read.All`** Graph
*application* permission, granted and admin-consented on the lab's app
registration. That is a portal action only the directory's admin can take, and
it is the one manual step this workstream cannot remove.

Until it is granted, the hosted collector reports that row as *"cannot tell you"*
with the reason — which is the honest state and the same convention every other
collector already follows for an unreachable provider.

### Work items

| # | Item | State |
|---|---|---|
| 1 | Rewrite the AWS, GCP and Entra collectors against SDKs/REST so one code path runs locally and hosted | **done 2026-07-29** |
| 2 | Add an `expiry` step to the harvest Lambda — it already runs every 6h with the service identities, so no new schedule and no new function | **done** — verified publishing at 17:55 UTC |
| 3 | Grant `iam:ListServiceSpecificCredentials`, `acm:ListCertificates`, `acm:DescribeCertificate` to `a2alab-obs-lambda` | **done** |
| 4 | Entra: grant `Application.Read.All` + admin consent | **operator action — still open** |
| 5 | GCP: the harvest service account cannot list service accounts | **operator action — found by doing it** |
| 6 | Keep `expiry_report.py --write` working from a laptop — the `az` CLI path survives as a local fallback | **done** |

**Measured after the first hosted run: 11 of 13 credentials.** The two that did
not resolve are permission grants, not code, and each reports its own reason in
the panel rather than going quiet:

- **Entra** — `Graph 403 Authorization_RequestDenied`. Needs
  **`Application.Read.All`** (Graph *application* permission) with admin consent
  on the lab's app registration. Locally this row still resolves through the
  `az` CLI fallback, so the laptop shows 13 and the hosted snapshot shows 11 —
  the difference is exactly this grant.
- **GCP** — `could not list service accounts (HTTPError)`. The harvest service
  account authenticates fine (it reads Cloud Logging and Monitoring for the ADK
  platform) but has no IAM read: listing service accounts and their keys needs
  **`roles/iam.serviceAccountViewer`** on the project, or a custom role with
  `iam.serviceAccounts.list` + `iam.serviceAccountKeys.list`.

Neither blocks the workstream's point — the snapshot now refreshes every 6 hours
with nobody running anything, which was the dependency to remove.

### Exit criteria

The Credentials Expiry panel shows a snapshot **no older than 6 hours** without
anyone having run anything, and its staleness flag stays off on its own. The
AWS SSO row does not return: it is a deploy-time credential, and this workstream
is the argument for why that distinction matters.

---

## Operations backlog (raised 2026-07-28, after full hosting)

Not a workstream — operational debt found while running the hosted lab. Each
item has a written-down workaround in `plan/10-operations.md`, so nothing here
blocks use; they are the "this should be one command" list.

1. **A rotate script for the per-seam secrets — NOT STARTED.** Rotating a
   console password today is: edit `.env`, `env_sync.py push`, then re-run
   `deploy/console/deploy_console.sh --skip-build`. The redeploy is not about
   where the value is *read* — it is because the per-seam secret
   (`a2alab/runtime/console`) is **built by the deploy script**, from a
   `keys = [...]` list that lives inside that script. `env_sync push` updates
   `a2alab/env/dev`, which no container reads.

   The fix is a script that rewrites the per-seam secrets from `.env` — no
   image, no task definition, no service update. The obstacle is that those key
   lists live in four separate shell scripts (bridge, console, faces, briefs)
   and would move to one place both they and the rotate script read.

   Pairs with a **TTL re-read in `interop/secret_env.py`**: today
   `load_secret_env()` runs once per process (`_loaded`, `setdefault`), so even
   a refreshed secret needs a restart. A short cache on values checked per
   request would let a rotation take effect on its own. With the rotate script
   alone, rotating is push + rotate + restart; with both, push + rotate.

   **Do NOT solve this by pointing the console at `a2alab/env/dev`.** That
   secret holds every credential in the lab — the GCP service-account key, the
   Aurora master secret. The per-seam secrets exist to scope what a compromised
   container can read (D39/F1). Related and worth doing in the same change: the
   console task role currently grants `secretsmanager:GetSecretValue` on
   `a2alab/*`, which already includes `env/dev` — tighten it to its own secret.

2. **Run All steals the view — FIXED 2026-07-28.** Every turn wrote the global
   `selected` and re-rendered the main pane, so a background run dragged the
   screen back to itself and the console read as locked while an experiment
   ran. Nothing was locked. Runs now update their own `chat.selected` and the
   sidebar chip, and only touch the view when their experiment is on screen.

---

## Canvas template rollout (raised 2026-07-29, D57)

The console canvas template is settled (D57) and two of the seven canvases
follow it. The rest predate it and are **not broken** — they are simply missing
the Details pane that says where their content comes from, which is the half a
visitor needs to read a claim rather than take it on trust.

| Canvas | State |
|---|---|
| `experiment` | **conforms** — `Run \| Details` since WS6 |
| `obs` | **conforms** — `Dashboard \| Observability Analysis`, each with Details (D57). The cost sentinel's brief used to be a third `Cost Analysis` tab here; it now lives ONLY in `build` → Cost, beside the tokens and modelled cost it explains, so one brief is not rendered in two sections (D65) |
| `insights` | Details pane not started — should explain: `config/insights.yaml` is the source, `review: required` gates sign-off, sign-offs live in `lab.lab_state` (D50), export regenerates `plan/08-insights.md` |
| `build` | **conforms** — `Cost \| Behaviour`, each with Details (D57). Cost shows segmented per-tool tiles (Claude Code: cost·tokens·sessions; Codex/Cursor: activity only, D64) and the cost sentinel's rolling-week briefs; Details explains WS9: CloudWatch PromQL, the four billing buckets (D44), and that the cost is a modelled client-side estimate at list price. Operator controls (Brief now / Schedule) are in the Control Panel, not the canvas (D65) |
| `creds` | Details pane not started — should explain: the collector runs on the operator's own sessions, publishes to `lab.lab_state`, and the console only READS it (WS13) |
| `arch` | Details pane not started — arguably exempt: the canvas IS the explanation. Decide rather than default |
| `trace` | Details pane not started — should explain the wire capture: raw bytes, the ASGI wiretap for MCP/A2A, the D27 rider as the correlation channel that survived every hop |

Not urgent, and deliberately not done in one sweep — each pane is worth writing
properly, with real refs, when that area is next touched.

---

## Console and exhibit backlog (raised 2026-07-26, after the hosted bridge)

Not a workstream — UI and presentation debt to clear before the demo.
**Cleared 2026-07-26**; each item below records what was actually found.

1. **"Not yet available" components — DONE, and the diagnosis was half wrong.**
   The mechanism was right (`components_for()` renders a row as unavailable
   when its url resolves to `None`) but the count was not. Only **one**
   rendered row was ever missing a link: the M9 OpenAI one, the single
   component whose url had no code default. `SF_LIGHTNING_DOMAIN` and
   `ADK_CONSOLE_URL` do not exist as variables — Salesforce rows derive from
   `SF_MY_DOMAIN` (set, working) and the Agent Engine row has a default built
   from `GOOGLE_CLOUD_PROJECT`. The real second gap was invisible rather than
   badged: **Foundry had no component row at all**, so WS3's Details tab
   listed nothing. Both fixed in code with working defaults, not in `.env`:
   OpenAI points at `platform.openai.com/traces` (the Agents SDK exports every
   run there, and the agent itself is our container, so the runs *are* the
   platform-side asset), Foundry at the portal root — Microsoft documents
   `ai.azure.com` and no per-project deep-link format, so `FOUNDRY_CONSOLE_URL`
   is the override for a URL pasted from the browser. A test now fails if any
   component ships without a link, which is what made the count wrong the first
   time.
2. **Chips are too prominent — DONE.** 11px→9.5px, padding 10px→7px, rim
   softened 38%→30%. Metadata now sits under the text it annotates.
3. **Vendor marks at group level — DONE.** The Experiments subsection headers
   carry the same inline vendor marks the experiment cards use, at 12px to
   match the heading. Chips were the first attempt and the wrong instrument:
   the group title already names the pair, so a row of chips restating it was
   furniture. `Google`, `Microsoft` and `AWS Strands` joined the mark patterns
   so every group title resolves; bare `AWS` deliberately does not, or every
   "Claude (AWS) → Agentforce" title would carry two competing marks.
4. **Chip iconography cleanup — DONE, and the rule is now D42.** Tier 1 names
   who operates the cloud, tier 2 names which product or model runs on it: the
   vendor chip reads **anthropic**, and Google ADK / Agent Engine wear the
   **Google Cloud** mark while the Gemini spark stays on the model. Display
   only — `claude` is still the tag id, CSS class and hue.
   - The `aws-shim` warm-up row needed no change: it already carried the AWS
     mark and the solid AWS pill (shipped 2026-07-25, the day before this was
     raised). Left alone rather than "fixed" twice.

---

## WS13 — Full hosting: take the laptop off the runtime path (raised 2026-07-28)

**Why this exists, in the operator's words:** *"can't the whole lab env stack be
completely deployed on AWS and not rely on my AWS login from my host machine?"*
Yes — and the question exposed that two different dependencies had been running
together in the record:

1. **Runtime.** The console, nine protocol faces and the Managed Agents watcher
   run on a laptop and reach the world through `cloudflared`. This is the one
   that hurts, and it is what this workstream removes.
2. **The AWS login.** A *deploy-time* credential. Hosting does not remove it and
   should not: you authenticate to push changes. Once deployed, nothing needs a
   live session for the lab to keep working.

**What this supersedes.** WS7 framed the same work as "hosted completion" and
picked up a front-door problem along the way — the operator's corporate proxy
blocks the lab's whole domain at DNS (measured in plan/03-results.md: a hostname
that never existed still hangs 30s). A CDN front door was built, measured
working, and reverted the same day, because it removed a *toggle* and left the
*dependency*. With nothing running locally there is no cost to dropping the
proxy to look at the console, so the front door stops being a problem worth
solving. WS7's items 1, 2, 3 and 5 move here; its bridge item (7) is done.

**The tunnel stays.** Explicitly kept for local development — it is a
convenience for iterating on a laptop, not the lab's front door. WS7 item 6
("retire or re-scope the tunnel") is settled as *re-scope*.

### The shape, and why it is cheap

The bridge already proved every piece of this on 2026-07-26: ECS Fargate, an
ALB, and a one-field DNS cutover that is reversible. Two facts make the rest
repetition rather than design, both **verified 2026-07-28**:

- The bridge's ALB already terminates TLS on **:443** with the imported
  Cloudflare Origin certificate for `*.agenticthings.com` — so every lab
  hostname is already covered and **no new certificate is needed**.
- Additional faces are a **listener rule**, a target group and a task. Not
  another load balancer, which is what made this look expensive.

The bridge stays the listener's **default action** and carries no rule, so a
malformed host condition can only make a new face unreachable — it cannot break
Path A. That property is why the work is safe to do incrementally.

### Work items

| # | Item | State |
|---|---|---|
| 1 | **Console on Fargate** behind the bridge ALB | **deployed and serving 2026-07-28**; `console-lab` DNS cut over to the ALB the same day (plan/09 L5.5), first run hardened per D48 |
| 2 | Nine local protocol faces (Lab Guide ×3, Claude MCP/A2A, OpenAI MCP/A2A, Agentforce shim ×2) | **done 2026-07-28** (D51) — one Fargate service, addressed by path |
| 3 | **Hosted watcher** — servicing Managed Agents custom tool calls | **done 2026-07-28** (D52) — an ECS service reusing the faces image, not the assumed EventBridge Lambda |
| 4 | Widen `modes:` in `config/targets.yaml` as each face lands | **done 2026-07-28** — nine `*-hosted` twins, nine mode mappings |
| 5 | Re-scope `cloudflared` to local development | **done 2026-07-28** — decided, no work needed: the tunnel stays as the local dev path |
| 6 | **`PgObsStore` read side** — the Observability section is empty when hosted | **done 2026-07-28** (D49) |

### What the first run of item 1 actually found (2026-07-28)

The script ran clean end to end on its first execution — image, secret, roles,
target group, listener rule, stable service, `/healthz` 200 through the ALB. It
was also, at that moment, **serving every `/api` surface unauthenticated**. The
full account is D48; the short version is that three gaps composed, and a
healthy container shows none of them:

- The console never loaded its runtime secret, so `A2ALAB_TOKEN` was unset and
  `TokenAuthMiddleware` fell open. Fixed by loading the secret and **failing
  closed** when a hosted container has no token.
- `A2ALAB_PG_SECRET_ARN` was never shipped, because the env derivation only
  matches string literals and `pg.py` reads it through a module constant. Four
  Aurora-backed surfaces returned empty on a healthy console.
- `SF_CLIENT_ID_OBS`, `SF_CLIENT_SECRET_OBS` and `A2ALAB_FANOUT_MCP_TOKEN` sat
  in cleartext on the task definition. **The same exposure is still live on the
  bridge** — `deploy_bridge.sh` has the same enumerated exclusion list and has
  not been fixed.

Verified after the fix: unauthenticated and wrong-token requests both 401,
valid token 200, `/healthz` still open, doc chips 200 (the image carries the
prose), briefs reading from Aurora, and Path A unaffected throughout.

`/api/traces` returning `[]` is **not** a defect — the console's remote window
is 6h (`_REMOTE_WINDOW_S`) and the newest hop was 9.9h old.

### Item 6 — Postgres is now the only observability store (done, D49)

The section was empty hosted because `_obs_store()` returned the **SQLite-only**
`ObsStore()` unconditionally, while `A2ALAB_OBS_STORE=postgres` was honoured
only by `scripts/obs_harvest.py` — and was commented out in `.env`. So the
*local* harvest filled `traces/lab.db` (382 sessions), the *hosted* harvest
filled Aurora (479), and the console read the first. It was never empty on a
laptop, so nothing looked wrong; hosting it removed the local file and made the
divergence visible.

Closed by making Postgres the source of truth for storage, dashboard and the
Managed Agent's analysis briefs, with **one** selector
(`observability.make_obs_store()`) that both the console and the harvest call.
`PgObsStore` gained the six read methods it never had. Two of them had to be
written around the RDS Data API's **1 MB result cap**: rider extraction moved
into SQL (the matching payloads total 3.6 MB) and `list_events` pages itself
(one session's events measured 2.43 MB).

**Verified hosted 2026-07-28:** 5 platforms, 200 sessions, 35 caller riders, 29
lab-trace riders, build telemetry enabled — all read from Aurora, with
unauthenticated requests still 401.

### Two things the same session found by accident

- **A live console bug, not a test artifact.** `.env` carried
  `AGENTCORE_CONSOLE_URL=` and `AGENT_ENGINE_CONSOLE_URL=` with *empty* values,
  and the code read them as `os.environ.get(var, default)` — where an empty
  string is a present key and beats the default. Both component rows rendered
  "not yet available" in the running console, which is precisely what
  `test_every_component_has_a_console_url` exists to prevent. The test could not
  see it because the suite does not load `.env` and `run_local.sh` does. Now
  routed through `_env_url()`, with a regression test that sets the vars empty.
- **The unit suite could read and write hosted Aurora.** With `A2ALAB_PG_*`
  exported, `/api/expiry` returned the *real* credential report instead of the
  temp file two tests had just written — so the suite failed only for developers
  who had sourced `.env`. `tests/conftest.py` already cleared `A2ALAB_TOKEN`,
  `BRIDGE_TOKEN` and `A2ALAB_MODE` for exactly this reason; the database group
  was simply never added. `A2ALAB_TRACE_SINK` is cleared with it, because
  `postgres` there would have the unit suite writing hop rows into the hosted
  store.

**How item 1's script was built (it has since been run — see "What the first
run of item 1 actually found" above, and D48).** The script is modelled
line-for-line on `deploy/bridge/deploy_bridge.sh`, including its two hard-won
details: env vars derived from *both* `targets.yaml` and a scan of what the code
reads (a client can read `os.environ` for something in no config file —
`SF_AGENT_ID` taught that), and the ambient-shell exclusion that keeps the
laptop's `AWS_DEFAULT_REGION` from misdirecting the container's secret lookup.
The first run needed a person watching, because its one risky step touches the
load balancer Salesforce depends on — that run happened 2026-07-28, surfaced the
fail-open auth gap (D48), and was hardened and re-verified the same day.

### What had to change in the console before it could be hosted

Both found by asking what a container would *not* have, rather than by running
it:

- **`/healthz`** did not exist. The ALB health check carries no credentials, so
  it also had to join the auth-exempt list — a gated health path marks every
  task unhealthy and the service never stabilises.
- **`/api/expiry` read `.a2alab/expiry.json`**, a file no container has. The
  collector cannot move into the container either: it needs the operator's own
  AWS/az/gcloud sessions. Resolved by giving the hosted store a `lab.lab_state`
  key/value table — `scripts/expiry_report.py --write` now publishes there as
  well as to the file, and the console reads the store first and falls back to
  the file. **Verified 2026-07-28:** 14 credentials round-tripped through
  Aurora. A key/value table rather than a column per artifact, because these
  are whole documents produced by a script and rendered verbatim, and the next
  one should not need a migration.
- **The image must carry the repo's prose.** The console *renders* `plan/`,
  `docs/`, `build-notes/` and `README.md` through `/api/docs`, reads
  `config/insights.yaml`, and parses `plan/09-deployment-map.md` for the
  Architecture section. An image with only `src/` starts healthy and 404s every
  doc chip.

**Still to find:** the same question has not been asked of the Run buttons, the
warm-up path or the obs harvest trigger. Each one that shells out, reads
`.a2alab/`, or assumes a local trace directory is another item 1 discovery, and
they will surface one at a time on the first hosted run rather than all at once.

### Exit criteria

`A2ALAB_MODE=hosted` with the laptop closed: the console reachable, an
experiment runnable from it end to end, the async brief firing without a local
watcher, and `config/targets.yaml` showing no `localhost` target that lacks a
hosted counterpart. The tunnel still runs for local development and nothing in
the live path depends on it.

---

## WS16 — Behavioural telemetry: what building the lab looked like, not just cost (raised 2026-07-30, D59)

**Status.** Phases 0–3 done 2026-07-30; Phases 4 (traces, beta) and 5
(behavioural brief) not started and not gating. WS9 already reads Claude Code's
eight aggregate metrics into the Coding Agents Telemetry section — cost, tokens,
sessions, commits (D44 made the four token buckets honest). This workstream adds
the OTLP **logs** signal to the same telemetry CloudWatch account the metrics
already reach (a separate account from the lab's hosting, pinned by `AWS_PROFILE`
in `.env`; never named here, D39) and derives a
second class of insight the metrics cannot express: **edit-acceptance rate** (the
star — how often the human kept the agent's proposed edits), tool mix and MCP
usefulness, per-request latency, reliability/retries, and prompt cadence. It runs
with every content flag **off**, so prompts, file contents and tool arguments are
never emitted — the insights are all computed from metadata that ships regardless
(D59). The console tiles land under the new DevOps category (WS17).

**Phase 0 result — measured against the telemetry account, 2026-07-30.** A real
round-trip answered both gate questions and confirmed the build note's "failure
#2": the metrics bearer token returns **400** (authenticated, empty body) against
`monitoring…/v1/metrics` but **403 "API Key … valid"** against
`logs.us-east-1.amazonaws.com/v1/logs`. So the logs endpoint exists and is
bearer-authenticated, but the metrics credential — scoped to service
`cloudwatch.amazonaws.com` — does **not** cover it; logs needs its own
service-specific credential for `logs.amazonaws.com` (+ IAM `logs:PutLogEvents`,
`logs:CallWithBearerToken`). Two consequences that shape the build: (1) unlike
metrics, logs require a **pre-provisioned log group with bearer auth enabled**
(`aws logs put-bearer-token-authentication`), and read-back is **SigV4 via Logs
Insights / FilterLogEvents**, not the PromQL `query_range` path — so the logs
reader is a different client from `promql.py`; (2) `otelHeadersHelper` returns one
header set for all OTLP signals, so the second, different token is a real
collision resolved by a launch-wrapper injecting `OTEL_EXPORTER_OTLP_LOGS_HEADERS`
at runtime (the D39 posture `scripts/codex_otel.sh` already uses). Traces use
**SigV4** at `xray…/v1/traces` — a different auth model, deferred to Phase 4.

**Round-trip proven end to end, 2026-07-30.** After minting a `logs.amazonaws.com`
service-specific credential (`scripts/setup_cw_logs_otlp.py`), a real OTLP/JSON
log record wrote with **200** and read back over SigV4 `FilterLogEvents` — write
with a bearer token, read with SigV4, the same split the metrics path has. Two
facts the AWS docs omit, both found by doing it: the logs endpoint **requires
`x-aws-log-group` and `x-aws-log-stream` request headers** (absent them it 400s
"headers cannot be null"), and while the provisioner creates the log *group*, the
**stream is not auto-created** (400 "log stream does not exist" until it is).
A third that eases the credential design: a service-specific credential key is
**long-lived**, not a short-STS token — so a launch-time fetch is viable, and the
`otelHeadersHelper` 29-minute re-fetch exists to pick up *rotations*, not because
the token expires. First mint stored an empty token because the bearer secret is
`ServiceCredentialSecret`, not `ServicePassword` (the empty SMTP field) — a
one-shot value, so the fix was delete-and-re-mint; the script now refuses to
store an empty token.

**Why content-off costs nothing.** The five insight families are derived from
`prompt_length`, `tool_name`, `decision`, `duration_ms`, the token counts and
`status_code` — all emitted whether or not the content flags are set. So the
dashboard is complete with nothing sensitive leaving the laptop, and the
publish/store division becomes structural rather than a discipline: there is no
raw content anywhere in the pipeline to leak (D59).

### What each signal gives, verified against the Claude Code monitoring docs

| Insight | Source event / span | Signal |
|---|---|---|
| Edit-acceptance rate | `claude_code.tool_decision` → `decision` (accept/reject), `source` | logs |
| Tool mix, MCP usefulness, per-tool latency | `claude_code.tool_result` → `tool_name`, `mcp_server_scope`, `success`, `duration_ms` | logs |
| Per-request latency + finer cost attribution | `claude_code.api_request` → `model`, `duration_ms`, four token buckets | logs |
| Reliability / retries | `claude_code.api_error`, `claude_code.api_refusal` → `status_code`, `attempt` | logs |
| Prompt cadence (no text) | `claude_code.user_prompt` → `prompt_length` | logs |
| Time-to-first-token per model | `claude_code.llm_request` span → `ttft_ms` (nowhere in logs) | traces (beta) |
| Turn-tree / conversation shape | `claude_code.interaction` span → sequence, duration | traces (beta) |

Traces are gated behind `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` and sequenced
after logs, for the same one-signal-at-a-time reason the metrics path followed.

### Work items

| # | Item | State |
|---|---|---|
| 1 | Phase 0 — prove the CloudWatch OTLP logs endpoint and credential model at the destination | **done 2026-07-30** — round-trip against the telemetry account: logs endpoint is `logs.<region>/v1/logs` (bearer), needs its own `logs.amazonaws.com` credential (metrics token 403s), pre-provisioned log group, SigV4 read-back |
| 2 | Phase 1 — enable the logs exporter via a launch wrapper, content flags off | **done 2026-07-30** — `scripts/claude_otel.sh` (opt-in wrapper, mirrors `codex_otel.sh`); proven at the destination: OTLP/JSON POST returned 200 and a SigV4 `FilterLogEvents` read-back matched a unique marker |
| 3 | Phase 2 — logs harvest ETL sibling to `coding_source.py`, aggregates-only into the store | **done 2026-07-30** — `src/observability/coding_logs_source.py` (platform `coding-logs`, summable histograms, aggregates only); registered in `obs_harvest.py`, `lambda_handlers.py`, and the console Harvest button; 14 unit tests |
| 4 | Phase 3 — console: five insight tiles in the Coding Agents Telemetry section plus a Details sub-tab | **done 2026-07-30** — Cost / Behaviour peer top tabs (D57), each with a Details sub-tab; `/api/build-behaviour` merges the window and computes the five families; Details names the reader, Lambda, tables, identity and bounds and cites D59 |
| 5 | Phase 4 — traces (beta): the TTFT latency profile and the turn tree | **not started** (behind `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA`; sequenced after logs) |
| 6 | Phase 5 — behavioural brief interpreting the new aggregates | **not started** (extend the obs analyst / cost sentinel; optional, tiles do not need an agent) |
| 7 | Docs: build note, insights entry, deployment map and diagrams for the new reader and tables | **done 2026-07-30** — `build-notes/claude/08-coding-agent-telemetry.md` §5, `config/insights.yaml` (`behaviour-survives-content-off`), `plan/09-deployment-map.md` (L5, L6, harvest grant), `config/diagrams.yaml` (`behaviour-signal-content-off`) |

### Exit criteria

The Coding Agents Telemetry section shows edit-acceptance rate, tool mix,
per-request latency and reliability for Claude Code, computed from harvested log
events and refreshed by the same Harvest button as the metrics — with a `Details`
pane that states the content flags are off and why the insights survive it, and
no raw prompt or tool content present in CloudWatch, `lab.db`, or the console.
Traces (TTFT, turn tree) are a follow-on, not a gate on this.

---

## WS17 — A DevOps category in the console: how the lab is built and delivered (raised 2026-07-30, D60)

**Status.** Done 2026-07-30 (items 1–5). The console's Control Panel groups sections by what they
are *about* — platforms, protocols, observability. Two things the lab now
produces are *about the build itself*, not about any agent platform: the
coding-agent telemetry (WS9/WS16 — what building it cost and looked like) and the
delivery process (WS15 — workstreams and ADRs authored locally, epics and stories
generated into Jira one way). This workstream adds a **DevOps** category to the
Control Panel nav and puts both under it, following the one-canvas template (D57)
— each a thing with a `Details` sub-tab.

**Two sections under the category.** *Coding Agents Telemetry* moves here
unchanged (WS16's tiles then land inside it). A new *This A2A Lab Project* section
surfaces the delivery process: the workstream/ADR authoring flow, the generated
board with its epic/story counts, a **launch-out link** to the Jira space, and
selected build-notes on how the lab was made.

**The constraint that defines it (D60, extending D58).** The project section
renders from the **plan and repo** — `plan/07-workstreams.md`,
`plan/00-decisions.md`, `plan/11-delivery.md`, `build-notes/**` through the
existing `/api/docs` path — and **only links out to Jira; it never reads the
board back in.** D58 made the board a one-way delivery *view* generated from the
plan; a console panel that pulled live Jira would reintroduce exactly the drift
D58 removed (a status true in Jira and nowhere the repo can see). So the counts
this section shows are computed the same way `jira_sync.py` computes them — from
the plan — which is why the console and the board cannot disagree. The Jira link
is a launch point, not a data source.

### Work items

| # | Item | State |
|---|---|---|
| 1 | Add a DevOps category to the Control Panel nav and move Coding Agents Telemetry under it | **done 2026-07-30** — a `.cp-cat` labelled divider groups the two sections as peers in the flat accordion list |
| 2 | Build the *This A2A Lab Project* section: workstream/ADR process, board counts from the plan, build-notes, per D57 | **done 2026-07-30** — `/api/project` imports `jira_sync.parse_plan` (same arithmetic as the board), console renders the process, counts and per-workstream list; Delivery / Details tabs |
| 3 | Launch-out link to the Jira space from the section header | **done 2026-07-30** — link only, from `JIRA_SITE_URL`; empty state says the board is generated but not linked when unset; nothing reads the board back |
| 4 | `Details` sub-tab naming the source docs, the one-way D58 rule, and why nothing reads Jira back | **done 2026-07-30** — cites D58, D60 and the plan docs (chips linkify), per D57 |
| 5 | Docs: deployment-map/diagram touch if the nav structure is drawn; note the new category in the console backlog | **done 2026-07-30** — `plan/09-deployment-map.md` L5 exclusion prose names the DevOps category and the Cost/Behaviour tabs |

### Exit criteria

The Control Panel shows a DevOps category; Coding Agents Telemetry lives under it;
and the *This A2A Lab Project* section renders the delivery process and current
board counts from the plan with a working launch-out to Jira and a `Details` pane
that states the render is one-way from the repo. No console request reads the
Jira board.

## WS18 — Console usage analytics: who visits the released lab (raised 2026-07-31, D62)

**Status.** Done 2026-07-31. The console is about to go to the field, so the lab
now needs to know **who reaches it, from where, and what they open** — while
staying anonymous. This workstream adds a same-origin **`/api/track`** proxy, a
new Aurora table **`lab.usage_events`**, and an **A2A Lab Monitoring** section
that reads the aggregates back.

**Three anonymous beacons.** The browser fires fire-and-forget events to the
proxy: a **site_visit** on every load of the main shell (before any sign-in — the
one event that must log while anonymous), a **persona_login** on a successful
sign-in, and a **nav** on each top-level section change (captured once at the
`pushNav` choke point, so a re-render does not re-count). Modelled on the
`tdx26/mega-demo` `useLogger` — never awaited, failures swallowed, so analytics
is never in the path of a render.

**Server-side proxy, not a client-baked key (D62).** The mega-demo inlines its
logger URL + API key into the browser bundle via `VITE_` vars; that is disallowed
here — the no-hardcoded-identifier rule and the fact that this is a released
public surface. So the external logger's URL and key live only in the server's
environment (Secrets Manager), the browser talks to same-origin `/api/track`, and
the proxy stamps the persona from the **verified JWT** rather than trusting the
client. The proxy still **forwards** every event to the operator's existing AWS
logging service (the Claude/Codex-hooks Lambda) for the Slack notify — that
forward is a hard requirement, it is how the operator tracks projects.

**PII-free by construction.** No IP, no name, no payloads. Country is
Cloudflare's `CF-IPCountry` (two-letter code); a visitor is a random
`localStorage` UUID; the event name is a closed set. See D62 for the full list of
what is stored and refused.

### Work items

| # | Item | State |
|---|---|---|
| 1 | `lab.usage_events` table in `observability.pg.DDL` + `record_usage`/`usage_stats`; applied by `scripts/pg_migrate.py` (owner, D46) | **done 2026-07-31** — append-only, indexed on `occurred_at` and `(event, occurred_at)` |
| 2 | `POST /api/track` proxy: store row (writer secret) + forward to the external logger; auth-exempt, 204, closed event set | **done 2026-07-31** — persona from the JWT, country from `CF-IPCountry`, both null-safe |
| 3 | Client `track()` + `visitor_id`, wired at boot (site_visit), `labSignIn` (persona_login) and `pushNav` (nav, top-level only) | **done 2026-07-31** — fire-and-forget, `keepalive`, mirrors the mega-demo's swallow |
| 4 | A2A Lab Monitoring section (D57): Visitors / Sections tabs, day/week/month/year/all window, `GET /api/monitoring` (reader) | **done 2026-07-31** — a sub-section under Infrastructure (peer to Agent Registry and Credentials Expiry — it is stack-monitoring, not an agent platform); unique/returning visitors, country + locale, sections + experiments viewed, Lab Guide usage |
| 5 | `Details` sub-tab naming the proxy, table/columns, reader/writer identities, CF header and anonymity bounds | **done 2026-07-31** — cites D62 and plan/09 (chips linkify) |
| 6 | Docs + env: D62, this WS18, `plan/09` estate edge + L6 row, `.env(.example)` for `A2ALAB_LOGGING_API_*` | **done 2026-07-31** |

### Exit criteria

An unauthenticated visit is recorded before any sign-in; a persona login and each
top-level section change are recorded with the persona stamped server-side; every
event is also forwarded to the external Slack logger; and the A2A Lab Monitoring
section renders unique/returning visitors, country/locale, and sections/
experiments viewed over a selectable timeframe, with a `Details` pane that states
the anonymity bounds. No IP or payload is stored; a logging failure never blocks
the UI.

## WS19 — Data 360 zero-copy over Aurora → Tableau Next observability dashboard (raised 2026-08-07, D69)

**What this is, and why it is not new.** Close the loop the lab was built to
close: land the cross-platform agent telemetry the harvest already collects into
**Data 360** with **no ETL**, then build a **Tableau Next** dashboard on it for a
Salesforce-side, business-analytics view of agent traffic. This is **M10** — the
"Data 360 zero-copy → TableauNext reporting" scoped 2026-07-10 (plan/00 M10
note), then retargeted by D23 from the DynamoDB connector onto Data 360's **AWS
Aurora PostgreSQL connector** (GA for Zero Copy query federation). The store was
built to feed it: `PostgresSink`'s docstring names `lab.trace_events` as "the
table Data 360's Aurora Postgres zero-copy connector federates for M10," payloads
are flat **jsonb scalars** (D13) so connector field-mapping is trivial, and the
`lab_reader` role D23 defined is explicitly "for Data 360 and the analyst MCP
server." So WS19 executes an approved direction — the design work is done in D13,
D23 and D69.

**The two views are the finding, not a duplication.** The console's Observability
section stays the lab's **wire-level** view — raw request/response payloads per
hop, the thing the lab exists to show. Tableau Next is the **aggregate,
Salesforce-side** view of the *same rows* — cross-platform traffic, protocol mix,
latency and status by target — reached with **zero copy** between them. Having
both, over one Aurora table, with no transform in between, is itself the M10
result: the same telemetry serves an engineer reading wire bytes and a Salesforce
analyst reading a dashboard.

**The one genuinely new cost (D69).** Everything hosted reaches Aurora through
the IAM'd **RDS Data API**, so the cluster's **5432 ingress is closed** (`pg.py`
says so). The Zero Copy connector cannot use the Data API — it authenticates
**username/password to the cluster endpoint over 5432 from Salesforce's IP
ranges**. So M10 opens the one network path the hosted design avoided: a scoped,
TLS-only 5432 security-group rule for Salesforce Data Cloud IPs (and the MCP
server), authenticating as `lab_reader` with statement_timeout + row caps. That
is a real security-posture change, which is why it has an ADR.

**What is UI-only — narrower than first assumed (D70).** D69 called the connector
setup, the DLO→DMO mapping *and* Tableau authoring all UI-only. The build proved
only two of those true. The **connection** is UI-only (the `AwsRdsAuroraPostgres`
connector has no documented `POST /ssot/connections` body, and blind-probing a
prod org risks orphaning a live connection — D70). The **Tableau Next semantic
model + dashboard** are UI-only (four headless surfaces ruled out — no
`SemanticModel` Metadata type, SSOT `/semantic-models` 404s, both Tableau/360 MCP
servers are read-only). But the **entire Zero-Copy data layer in between** —
federation views, data streams, DLOs, DMOs and DLO→DMO mappings, at both hop and
trace grain — was built **headless via the Data Cloud SSOT REST API** (the wrong
"no Metadata type ⇒ UI-only" call, corrected on the check-headless rule). WS19
automates the AWS side and the whole data layer; only the connection and the
dashboard tiles are declared operator actions.

**The full build record is `plan/13-tableau-next-obs-dashboard.md`** — the
consolidated plan doc (SSOT REST payloads, the view-not-base-table fix, every
Tableau calc-field formula, verified numbers, the D70 IP/region diagnosis, and the
inline-embed JWT-bearer auth). This section is the delivery-record summary; plan/13
is the how.

### Work items

| # | Item | State |
|---|---|---|
| 1 | Confirm the Aurora tables feed the connector cleanly and document which jsonb fields Tableau needs flattened | **done** (2026-08-07) — survey of the live store: `lab.trace_events` is 2,977 rows over 8 protocols / 53 targets, and every column Tableau groups/filters on is already a top-level scalar (protocol, target, status, latency_ms, ts_at, source, D13). The only jsonb columns are `request/response_payload_raw` — raw wire payloads for drill-down, which the aggregate dashboard does not flatten. No schema change |
| 2 | **Scoped 5432 ingress (D69):** a TLS-only security group opening the cluster endpoint to Salesforce Data Cloud IP ranges + the MCP server only | **done** (scripted 2026-08-07, IP source corrected 2026-08-08 per D70; apply is a deliberate operator action) — `deploy/obs/deploy_datacloud_ingress.sh` opens 5432 on `a2alab-aurora-sg` to the tenant's **eu-central-1** egress `/32`s only, pinned in `config/salesforce_ip_ranges.yaml` from the **"IP Addresses Used by Data 360 Services"** help article — NOT `ip-ranges.salesforce.com`, whose `/23` app-fabric range the connector does not egress from (D70); `--verify` checks every pinned `/32` is authorized on the SG (the article has no JSON manifest to diff), `--tls` enforces `rds.force_ssl=1`, `--revoke` reverses. Sources `aws_preflight.sh`. NOTE: the MCP server uses the Data API, so 5432 opens to the Data Cloud CIDRs alone, not the MCP server |
| 3 | `lab_reader` role hardening for federation: schema-scoped read-only grants over `lab.*`, `statement_timeout` + row caps in DB settings, password in Secrets Manager (D39/D45); applied via `scripts/pg_migrate.py` as owner (D46) | **done** (2026-08-07) — posture codified in `observability.pg.ROLE_GRANTS` and applied live by `pg_migrate.py`: read-only, 15s statement_timeout (Postgres has no per-role ROW cap — the honest control), and a new `CONNECTION LIMIT 15` (was unlimited). Reuses the D23 role, no new credential. This also fixed the D46 gap: the reader's posture existed only by hand, un-reproducible from the repo |
| 4 | Update `src/observability/pg.py` posture note + `plan/09-deployment-map.md` in the SAME change as the SG — the store no longer claims closed ingress | **done** (2026-08-07) — `pg.py` docstring rewritten (the Data API path no longer claims a closed door); `plan/09` updated across L0 estate (a return edge + Data 360/Tableau box), L5 obs (the second reader + its state), the L6 code→deploy table (two rows), and "Why not, in one place" (the 5432 exception). Runbook §8 also updated |
| 5 | Create the Data 360 `AwsRdsAuroraPostgres` connection to the Aurora store as `lab_reader` | **done** (2026-08-08) — connection `A2A_Lab_Obs_Aurora` created and Test Connection returns "Connection was established." Created in the **Setup UI**, not by REST: the connector has no documented `POST /ssot/connections` body (D69 item 4's "API-creatable" claim was wrong — corrected in D70), and blind body-probing a prod org risks orphaning a real connection. The long failure was the IP-source/region diagnosis in item 2 / D70, not the connection itself |
| 6 | Build the Zero-Copy data layer headless: federation views (`lab.trace_events_zc`, `lab.trace_rollup_zc`), data streams, DLOs, DMOs and DLO→DMO mappings at both hop and trace grain | **done** (2026-08-09) — all built via the Data Cloud **SSOT REST API** (not the UI D69 assumed). Federating a **view** not the base table sidesteps the composite-PK block and keeps the raw-payload jsonb out of the object Data Cloud sees (residency is now structural); a surrogate `event_key` generated column is the single hop-grain PK; the trace-grain rollup pushes the two-level `GROUP BY` down into Aurora (Tableau Semantics has no LOD). Acceleration OFF on both — rows stay in us-east-1. Live-verified via `/ssot/queryv2`: hop DMO federates 3,021 hops, rollup DMO 960 traces, every figure matching the pre-build numbers. See plan/13 §1 |
| 7 | **Operator action:** build the Tableau Next semantic model + dashboard (visualizations, tiles) on the DMOs, and measure the L5.8 cold cross-region federation render | **operator action, partially built** — the `A2A_Lab` workspace, `New_Dashboard`, and **5 of 9 hop-grain visualizations** are live in the org (created 2026-08-09, confirmed via `sf org list metadata`: Hops_by_Protocol_Platform, Status_by_Platform, Traffic_Over_Time, Hop_Latency_Distribution, Direction_Matrix). **Remaining:** the 4 rollup-grain KPI tiles (Avg Trace Latency, Trace Success Rate, Avg/Max Hops per Trace, plan/13 §3d) and the **L5.8 cold cross-region federation render measurement** (not yet in plan/03-results.md). Still UI-only for authoring (four headless surfaces ruled out — no `SemanticModel` Metadata type, SSOT `/semantic-models` 404s, both Tableau/360 MCP servers read-only, plan/13 §3); the L5.8 number can go through the read-only Tableau Next MCP `analyze_data` |
| 8 | Inline Tableau Next embed in the console (owner-only, server-side JWT-bearer auth) | **built headless** (2026-08-09) — `/api/tableau/frontdoor` (owner-gated) mints a `web`-scoped session via JWT-bearer → `/singleaccess`; SDK mount + `CorsWhitelistOrigin` + the `a2a_lab_tab_embed` ECA (four metadata files, JWT cert on global OAuth) all created headlessly. Resolved: client-credentials can't get `web` scope (JWT-bearer runs in user context and can), and the auraCmpDef 504 was a perms + asset-sharing gap not a platform bug (plan/13 §5). **Pending operator publish:** console full-rebuild redeploy, a dedicated minimal-privilege integration user as JWT `sub`, and the CORS origin deploy (plan/13 §6) |
| 9 | Console entry point + `plan/02-matrix.md` finding (two views over one table, zero copy) | **console surface shipped (in a different spot than scoped); matrix finding + final nav placement after item 7** — a full working canvas + Details pane already ships as a **Tableau Next top tab inside Observability** (`index.html` `obsTableauNextHtml`/`obsTableauNextDetailsHtml`, citing D69–D72/plan/09), NOT the dedicated **Data 360** nav item under Infrastructure plan/13 §4b recommends. Still to do: paste the drafted matrix finding into `plan/02-matrix.md`'s Findings ledger (one `[N]` bracket = the item-7 L5.8 number) and decide whether to relocate the console entry to the scoped nav location. Closes the delivery-record loop (D58/D60) |

### Exit criteria

A Salesforce Data 360 Zero Copy connection federates `lab.trace_events` from the
hosted Aurora store with **no ETL job** — the connection is live and the whole
data layer (views, streams, DLOs, DMOs, mappings at both grains) is **built
headless via SSOT REST** and verified federating 3,021 hops / 960 traces with
acceleration off; a Tableau Next dashboard renders cross-platform agent traffic
(volume, protocol mix, latency, status by target/platform) over that federated
data; the 5432 ingress is scoped to Salesforce IP ranges + the MCP server over TLS
and authenticates as `lab_reader` with enforced statement/row limits; `pg.py` and
`plan/09` no longer describe a closed-ingress posture the store no longer has; and
the console + `plan/02-matrix` record the finding that the lab's wire-level view
and the Salesforce-side Tableau view read the **same rows** with zero copy between
them. The genuinely UI-only steps — the connection, and the Tableau semantic
model + dashboard — are declared as operator actions, not claimed as automated.
**Remaining:** the Tableau dashboard tiles + the L5.8 render measurement (item 7),
then finalize the matrix finding + console entry point (item 9). Full build record
in `plan/13-tableau-next-obs-dashboard.md`.

## WS20 — Claude Science patterns: provenance + actor-critic over the lab's own insights (raised 2026-08-09)

**What this is.** Overlay the transferable ideas from Anthropic's **Claude
Science** workbench (the AI-for-science beta, June 30 2026) onto the lab's own
insights feed and method. The disqualifying mismatch is why this is an *overlay*
and not an adoption: Claude Science orchestrates a generalist coordinator
spawning sub-agents inside **one session, one machine, one trust boundary**,
every sub-agent a Claude — the exact property this lab exists to *not* have, so
its harness has none of the cross-vendor, cross-trust-boundary seam under test.
What transfers is not the harness or the biology; it is the **epistemics** — how
a claim earns its evidence tier, and how a reviewer agent demotes claims it
cannot back. Working note: `tmp-docs/claude-science.md` (a local note, not a
checked-in doc).

**Why it is worth building.** The lab already has the weaker, hand-rolled
version of Claude Science's provenance contract — `config/insights.yaml`'s
`measured / observed / hypothesis` ladder — but the tier is **author-declared**:
a human typed "measured". Claude Science's model is **structural** — a figure
ships with the code and environment, so "reproducible" is a property you test by
re-running, not a label. Making the lab's ladder derive from whether the backing
artifact re-executes turns the demotion check mechanical, and that is exactly the
failure class behind the four-bucket token finding (120K reported for a day that
processed 4.42M — a 36× understatement that raised no error and looked entirely
plausible). The **cost sentinel already behaves like the critic** (it refused a
week-over-week comparison rather than invent one, WS12/D44); this generalises
that trust-under-pressure move across the whole insights feed.

**The defensible framing (guards the lab's own honesty bar).** These are
**conventions and a vocabulary, not a standard** — nothing in Claude Science is a
published spec with other implementers, and calling it one would trip the lab's
own editorial rule. Climb at most one rung above the measurements and stay
attached to them: the scarce asset is the specific number (join rate 1-of-4, the
36× understatement), not an abstract "reproducible-workbench pattern" any
architect could write in an afternoon. This also surfaces in the console's
What's Next section as the `claude-science-overlays` plan (`config/whats_next.yaml`),
which graduates to this workstream.

### Work items

| # | Item | State |
|---|---|---|
| 1 | ✅ Redefine the insights evidence ladder as artifact-derived: `measured` = names a run id / trace file that still exists and re-executes; `observed` = a trace exists but is not reproducible (endpoint moved, credential rotated); `hypothesis` = no artifact. Document the rule in `config/insights.yaml`'s legend and mirror it in the console Insights legend | done |
| 2 | ✅ Add a reference-integrity check over `config/insights.yaml`: an entry citing a run id / trace / `plan/*.md` anchor that no longer resolves auto-demotes one tier. Folded into `insights-audit` (the existing workflow) — the Audit agent now computes `artifact_tier` + `dead_refs` per entry | done |
| 3 | ✅ Build the actor-critic reviewer: an agent whose only job is to DEMOTE insight claims it cannot back, run over the feed, reporting what it did as the artifact ("demoted N of M, caught K citing a run that no longer exists") — the same shape as the cost sentinel refusing a comparison (D44). MECHANISM built (`insights-audit` Critic phase emits the demotion artifact) AND run over the live feed 2026-08-10: 37 audited, 36 backed, 1 demotion applied — `credential-locality` measured→observed (its one figure is a since-fixed Aurora state, no longer reproducible), 0 dead refs; the demotion survived the adversarial Verify pass | done |
| 4 | ✅ Name session-forking as a repeatable method: one scenario, one baseline, N variants, differences reported against the shared origin — the move already run by hand building the supplier-disruption fan-out three times on three orchestrators (WS8). Written up in `plan/01-architecture.md` as a lab method | done |
| 5 | ✅ Extend the honest-status vocabulary (`native / via-bridge / via-shim / blocked-beta`) to provenance and observability claims, as a *vocabulary* not a standard; recorded the finding in `plan/02-matrix.md` findings ledger | done |

### Exit criteria

An insight's evidence tier is derived from whether its backing artifact
re-executes, not asserted by its author; `insights-audit` auto-demotes any entry
whose cited run/trace/doc no longer resolves; an actor-critic reviewer runs over
`config/insights.yaml` and reports the demotions it made as a checkable number;
session-forking is documented as a named lab method in `plan/01-architecture.md`;
and the honest-status vocabulary is applied to provenance claims and recorded in
`plan/02-matrix.md`. Framed throughout as conventions and a vocabulary borrowed
from Claude Science, never as a standard the lab defines.

## WS21 — Rename the `anthropic-*` trace targets to `claude-*` at source (raised 2026-08-09)

**What this is.** Two trace-event `target` strings read `anthropic-managed-agents`
and `anthropic-api`; they should read `claude-managed-agents` and `claude-api` to
match the lab's own naming (the Claude platform, its Managed Agents backend, and
the Claude API are the vocabulary everywhere else). Today the WS19 Tableau
`Target Platform` calc relabels them **at the dashboard only** — the underlying
data and every other surface still say `anthropic-*`.

**Why it is (maybe) worth building.** Consistency: a visitor reading the console
trace viewer sees `anthropic-managed-agents` while the Tableau dashboard says
`claude-managed-agents` — the same hop, two names. **Why it may not be worth it
now** (the operator's call, 2026-08-09: "not sure it's worth the effort"): it is a
genuine cross-cutting rename with a data-migration tail, and the dashboard remap
already gives the analytics surface the right names. Low value, non-trivial cost —
parked as a potential workstream, not scheduled.

**Scope if built** — the string is stamped and matched in several places (grep
`anthropic-managed-agents` / `anthropic-api` before starting):
- **Source (new rows):** `src/platforms/claude/managed_backend.py`,
  `src/platforms/guide/core.py`, `src/observability/analyst.py`,
  `src/briefs/runner.py`, `src/orchestration/cma.py`, `src/console/app.py`.
- **Console display + badge matching:** `src/console/static/index.html`
  (vendor-badge logic keys on `anthropic-managed-agents`).
- **Existing data:** ~120 rows in Aurora `lab.trace_events` (98 + 22 as of
  2026-08-09) carry the old `target` — a one-shot `UPDATE` (via `pg_migrate.py` as
  owner) OR accept that historical rows keep the old name and only new rows change.
- **Downstream that reads the string:** the WS19 Tableau calc's two remap arms
  become unnecessary and should be removed in the same change; check
  `config/scenarios.yaml` / diagrams for the literal.
- **Delete-and-recreate is wrong** — this is a relabel, so an in-place `UPDATE`
  (or leave-history) is the only safe shape; the generated `event_key` does not
  include `target`, so the PK is unaffected.

### Work items

| # | Item | State |
|---|---|---|
| 1 | Rename `anthropic-managed-agents`→`claude-managed-agents` and `anthropic-api`→`claude-api` at every source site (managed_backend, guide/core, analyst, briefs, cma, console/app) | done 2026-08-16 |
| 2 | Update console badge/vendor matching in `index.html` and any diagram/config literal | done 2026-08-16 (transitional — see note) |
| 3 | Decide + apply the historical-row policy: one-shot `UPDATE` on `lab.trace_events` via `pg_migrate.py`, or leave history and change new rows only | open — operator decision (recommendation below) |
| 4 | Remove the now-redundant two remap arms from the WS19 Tableau `Target Platform` calc | open — blocked on item 3 |

**Build note (2026-08-16, items 1–2).** Renamed all three trace labels at
source: `anthropic-managed-agents`→`claude-managed-agents` (managed_backend,
analyst, console/app), `anthropic-api`→`claude-api` (guide/core, console/app),
and the `source` counterpart `anthropic-scheduler`→`claude-scheduler`
(briefs/runner) so the exit-criteria grep can be clean. `cma.py` had no such
literal. `config/scenarios.yaml` flow labels and the console `FRIENDLY` map /
badge matching updated. Confirmed `anthropic-managed-agents` is a trace LABEL,
not a `targets.yaml` registry key, so no resolution path changed.

**Transitional console state (item 2).** Because the ~120 historical
`lab.trace_events` rows keep the old `target` until item 3 runs, the console
deliberately recognizes BOTH names (`claude-*` for new rows, `anthropic-*` for
historical) in its badge logic and `FRIENDLY` map — the same shape as the WS19
Tableau remap. Those `anthropic-*` arms in `index.html` are the ONLY remaining
`anthropic-` trace-label references in `src/`; they come out in the same change
as item 3.

**Item 3 recommendation (operator's call).** A one-shot in-place `UPDATE` on
`lab.trace_events` (`target` and `source`) via `pg_migrate.py` as owner is the
clean shape — the generated `event_key` does not include `target`/`source`, so
the PK is unaffected and the change is reversible with the inverse UPDATE. Doing
it lets items 2 and 4 drop their `anthropic-*` arms and fully satisfies the exit
criteria. Left as the operator's decision per the 2026-08-09 "not sure it's
worth the effort" note; the SQL is trivial and can run the morning this is
picked up.

### Exit criteria

`grep anthropic- src/ config/` returns nothing that names a trace target; the
console trace viewer and the Tableau dashboard show the same `claude-*` names for
the same hop; the WS19 `Target Platform` remap arms are gone; and the historical-
row policy is decided and recorded. **Status 2026-08-16: items 1–2 met (new
rows and every source surface now say `claude-*`); items 3–4 open, gated on the
historical-row migration decision.**

## WS22 — Track B: cross-cloud infrastructure metrics — harvest, store, surface (raised 2026-08-11)

**What this is.** The forecasting-ready half of the Moirai exploration
(`plan/explore-moirai-timeseries-forecasting.md`), Track B: land the runtime's own
infrastructure metrics — CloudWatch (AWS Fargate/Aurora/Lambda), Cloud Monitoring
(GCP Vertex), Azure Monitor (Foundry) — as a **dense regular grid** in the obs store,
and surface them in the console. This is the M11 *sibling*: M11 harvests platform
*agent* execution logs; WS22 harvests the *infrastructure underneath* them. It fills a
standalone observability gap the lab had regardless of forecasting — AWS runtime
metrics and Azure Monitor were **not harvested at all** — and it is the data feed the
(not-yet-built) Moirai forecast runner needs.

**Why it is worth building.** It is the better TSFM data fit and needs no load harness
(these metrics emit on a fixed cadence whether or not an experiment runs), so it is the
faster path to a real forecasting result — but even before any forecast, dense
cross-cloud runtime series are a genuine SRE coverage improvement. The forecast runner
itself is **held**, gated on the exploration doc's graduation criteria (Q5: a zero-shot
forecast must beat a seasonal-naive baseline on one real harvested series before Moirai
graduates). So this workstream ships the *plumbing and the surface*; the *forecast* is
a deliberately separate, later step.

**Scope boundary (kept honest).** Infrastructure is **not a sixth platform column** —
`infra` (like the coding-agent telemetry) is kept OUT of the unqualified five-platform
coverage sweep; it is opt-in via `uv run python scripts/obs_harvest.py infra` and the
console Harvest button. Reads go through `make_obs_store()` (Aurora hosted, sqlite
fallback, D49); the Aurora read downsamples IN SQL (window-function stride) to stay
under the RDS Data API 1 MB result cap. No environment identifier is hardcoded —
`config/infra_metrics.yaml` is `${VAR}`-expanded through the registry's own expander.

### Work items

| # | Item | State |
|---|---|---|
| 1 | `src/observability/infra_source.py` — three sources (AWS CloudWatch `GetMetricData`, GCP Cloud Monitoring `timeSeries.list` reusing `adk_source.py` auth, Azure Monitor) reading `config/infra_metrics.yaml`, each degrading honestly (unset series skipped, credential-less cloud → `blocked`) | done |
| 2 | `lab.infra_metrics` on both stores (sqlite `store.py` + Aurora `pg.py`, duck-typed parity D49), keyed `(cloud, resource, metric, ts_at)` so an overlapping re-harvest is idempotent; DDL applied via `pg_migrate.py` as owner | done |
| 3 | Wire `infra` into `scripts/obs_harvest.py` and the hosted harvest Lambda as a group alias (infra-aws/gcp/azure), kept out of the five-platform sweep | done |
| 4 | Aurora read path: `PgObsStore.infra_metrics_series()` downsampling in SQL (window-function stride) under the Data API 1 MB cap; shared `_shape_infra_series`/`_downsample` helpers with sqlite | done |
| 5 | `/api/infra-metrics` endpoint (windowed by `hours`) + `infra` group alias on `/api/obs/harvest` | done |
| 6 | Console **Infrastructure Metrics** child under Infrastructure (D57 canvas): Metrics tab (grouped cloud→resource→metric grid with sparklines + harvest-status pills), Details sub-tab (markdown citing sources + D-refs), Forecast tab (honest empty-state — no runner yet), Harvest button | done |
| 7 | Run the infra harvest against the live estate and confirm the finest grid each cloud actually returns at the configured resolution (exploration Q4 tail) | done 2026-08-11 — the hosted harvest Lambda ran clean (~10s) against Fargate/Aurora/Lambda + Vertex + Foundry, landing 2,463 `lab.infra_metrics` rows across aws/gcp/azure (2026-08-10→08-11); spot-checked native cadence (aws 60s / Aurora 120s / ACUUtilization 180s gaps) confirms CloudWatch's own per-metric floor, not the configured 300s Period, sets the finest grid |
| 8 | Moirai forecast runner over `lab.infra_metrics` (Q5 gating experiment → `obs_briefs.kind='forecast'`, reusing the analyst/sentinel brief seam) + Forecast tab wired to real bands | not started — gated on graduation criteria in the exploration doc |
| 9 | ADR for the non-commercial-model (CC-BY-NC) decision and the metric-harvest access model, written when item 8 graduates | not started — gated |

### Exit criteria

Plumbing + surface (items 1–6): **met** — `uv run python scripts/obs_harvest.py infra`
lands regular-grid series in `lab.infra_metrics`, and the console Infrastructure Metrics
section renders them grouped with sparklines, harvest-status pills, a self-explaining
Forecast empty-state, and a Details pane citing its sources. Live-estate confirmation
(item 7): **met** 2026-08-11 — the hosted harvest ran against the real estate and
confirmed each cloud's native grid (CloudWatch's per-metric cadence, not the requested
Period, is the floor). The forecast half (items
8–9) stays open and gated: it graduates only when the exploration doc's Q5 shows a
zero-shot forecast beats a seasonal-naive baseline on one real harvested series, at
which point the runner, its brief kind, and the CC-BY-NC ADR are built and this
workstream's Forecast tab shows real bands.

## WS23 — Agentforce Session Trace OTel API: a standard route to the same obs data (raised 2026-08-12, D73)

**What this is.** Agentforce shipped a Session Trace OpenTelemetry API
([otel-api.html](https://developer.salesforce.com/docs/ai/agentforce/guide/otel-api.html),
beta): `GET /services/data/v66.0/einstein/audit/otel/{session-id}` returns a
session's trace as an OTLP/JSON `resourceSpans` document — turns, messages, LLM
calls, actions, metric scores, feedback, each a span. This workstream builds a
harvest source over it (`salesforce-otel`) as a SECOND route to the data the
live M11 Agentforce harvest already pulls from four STDM DMOs — and
deliberately does NOT switch the live path to it.

**Why it is worth building.** It is the same Data 360 record read through a
pre-joined standard view, so it changes retrieval, not truth. On that same
data it buys real things: the server does the join (no manual
interaction→session FK walk, so the orphan bug class that once stranded 823
events cannot occur), a stable OTLP schema instead of drift-prone `ssot__*`
column heuristics, and one round trip per session instead of paged
`FIELDS(ALL)` across four DMOs. Having it built means the day the API leaves
beta and grows a bulk read, promoting it to the live path is a one-line change,
not a rewrite.

**Scope boundary (kept honest, D73).** Three beta limits rule out switching the
LIVE harvest today — single-session only (no bulk read), 72h lookback, and
beta — all fatal to the bulk coverage sweep the DMO path serves. So the OTel
source ships under its OWN platform name (`salesforce-otel`), reachable by name
in `obs_harvest.py` and NEVER in the unqualified five-platform sweep, writing
its own rows so it never doubles the Agentforce column, redefines "harvested
from all platforms," or clobbers the live `salesforce` rows. The live harvest
stays on the DMO path (`src/observability/salesforce_source.py`).

### Work items

| # | Item | State |
|---|---|---|
| 1 | `src/observability/salesforce_otel_source.py` — `SalesforceOtelSource` (platform `salesforce-otel`): OTLP `resourceSpans` → one obs session + N span events, unwrapping the OTLP KeyValue/AnyValue oneof and unix-nano timestamps, degrading honestly (missing env → `blocked`, beta-not-enabled 403/404 → `blocked`, per-session 404 reported not raised) | done |
| 2 | Session enumeration for a single-session API: explicit `A2ALAB_OTEL_SESSION_IDS` (deterministic path), else a thin id-only query on the session DMO capped at `A2ALAB_OTEL_MAX_SESSIONS`, preferring the runtime `ssot__AiAgentSessionId__c` id over the surrogate PK | done |
| 3 | Reuse the F6 obs caller identity (a2a_lab_obs ECA, client-credentials, D37) — no new credential | done |
| 4 | Register in `scripts/obs_harvest.py` as `OTEL_SOURCES` — reachable by name, out of the default sweep and the coverage claim | done |
| 5 | Unit test with a synthetic OTLP document (`tests/unit/test_salesforce_otel_source.py`): span→event mapping, semantic-attribute-vs-name event typing, own-platform row isolation, and 404-reported-not-raised | done |
| 6 | ADR D73 (build-but-keep-DMO-live), field insight (`agentforce-otel-same-data-standard-route`), and an in-flight What's Next tile with the doc link | done |
| 7 | Live validation against a real Agentforce session (confirm the STDM session id maps to the OTel endpoint's session id, and the OTLP attribute names match the mapping's hints) | done — validated 2026-08-12: 25 sessions enumerate via the session DMO, and a real session's OTel GET returns an OTLP doc (1 resourceSpans, 12 spans). The beta API IS enabled on the org and the runtime session id resolves to the endpoint. |
| 8 | Console **Session Trace** tab — an Observability peer tab (D57): a session picker (recent-sessions dropdown from the DMO + paste-an-id), a Fetch that makes ONE live OTel GET, a span table + a raw OTLP `resourceSpans` pane (the raw-wire-bytes contract), and a Details sub-tab citing D73/WS23/D37 with the live-OTel-vs-DMO diagram (`OTEL_TRACE_DIAGRAM`) | done |
| 9 | Console live-read endpoints: `GET /api/obs/otel-sessions` (picker) and `GET /api/obs/otel-trace?session_id=` (one live GET; honest not-enabled/not-found states, never a 500; no store write), backed by public `list_session_ids()`/`fetch_trace()` on the source; unit-tested (`tests/unit/test_salesforce_otel_source.py`) | done |
| 10 | Experiment deep-link: an Agentforce-PRIMARY run stamps `session_platform="salesforce"` on its `/api/run` response, and that turn renders a "view session trace →" link into the tab. A Claude orchestrator that merely consults Agentforce returns ITS own session, so it gets no link — the link is only offered where the id is OTel-eligible. | done |
| 11 | Promote `salesforce-otel` to the live `PLATFORM_SOURCES` path | not started — gated on the API leaving beta and growing a bulk read |
| 12 | Durable experiment→session map so the picker shows LABELLED sessions: an Agentforce-PRIMARY run best-effort records its runtime session id into `obs_sessions` under platform `salesforce-otel` (title = experiment + variant, e.g. `supplier-disruption-agentforce · delegated · sync`), reusing the console's existing writer secret and the shared `obs_sessions` table — zero schema change, works on both sqlite and Aurora. `GET /api/obs/otel-sessions` then PREFERS lab-recorded sessions (newest-first, with their variant labels) and falls back to the raw DMO enumeration only when none are recorded yet (honest `detail` string either way). The `no_card`/synthesized-card rendering and the ADK fan-out state-seed async fix ship alongside. | done |

### Exit criteria

Build + surface (items 1–6): **met** — `uv run python scripts/obs_harvest.py
salesforce-otel` runs the OTLP source against a live org (or a pinned id list),
lands span-derived sessions/events under the `salesforce-otel` platform without
touching the live `salesforce` rows, and the decision is recorded (D73), tested,
and published as an insight + What's Next tile. Live validation (item 7):
**met** — the beta API is enabled on the org, sessions enumerate, and a real
session returns an OTLP trace. Console exposure (items 8–10): **met** — the
Session Trace tab makes the live read interactive and reachable from the
Agentforce experiments that generate the sessions. Only promotion to the live
sweep (item 11) stays open, gated on the API leaving beta and growing a bulk read.

## WS24 — Agentforce SOMA: native single-org multi-agent orchestration (raised 2026-08-15)

**BLOCKED on Salesforce beta enrollment.** SOMA ("Single Org, Multiple Agents")
is Agentforce's native orchestrator-plus-connected-subagents shape. The org
check on 2026-08-15 confirmed it is not enabled here: absent from Feature
Manager **and** from every relevant Settings metadata type at v67
(`AgentPlatform`/`Bot`/`EinsteinAgent`/`EinsteinCopilot`/`EinsteinGpt`/`AgentforceForDevelopers`
— the Agentforce substrate is all ON, but no multi-agent / connect-agents flag
exists). Enablement is therefore a Salesforce-side **beta request**, not a
headless toggle. This workstream stays blocked until that lands; the design is
recorded now so it is build-ready the moment it does.

**What this is.** One native Agentforce orchestrator (`A2ALab_Supply_Orchestrator_SOMA`,
a NEW bundle beside the WS8 bridge orchestrator — the bridge one is left intact
as the comparison baseline) routes to **three connected specialist subagents —
Logistics, Commercial/Legal, Customer-ops — all Agentforce agents in this ONE
org**, over the **same supplier-disruption scenario as WS8**. No A2A wire, no
trust boundary crossed: this is the field's cleanest control case.

**Why it is worth building.** SOMA is the same "who owns concurrency" question
WS8 asks, answered a fourth way. WS8's Agentforce variant-3 fans out via a
serial Apex callout to the lab bridge (Path A, D61); SOMA fans out via **native
agent-to-agent routing inside one org**. Same orchestrator brand, same three
business questions, two dispatch mechanisms — so the deliverable is *what native
buys over the bridge*. And because there is no wire between the agents, the
native session trace SOMA emits is the **clean baseline** the cross-org (MOMA)
and cross-vendor (A2A) hops get measured against.

**The three deliverable findings.**

1. **Native session trace vs the lab's wire trace.** How Agentforce's unified
   session trace plus per-subagent independent traces line up with the lab's
   per-hop wire trace for the same scenario. Reuses the WS23 Session-Trace OTel
   path (D73) and the M11 harvest — no new obs plumbing — and surfaces in the
   console's existing Session Trace tab.
2. **Whether the D27 delegation guard even applies with no wire.** There is no
   outbound request to stamp a rider on; the trust boundary is internal to
   Agentforce. That absence is itself the finding — the guard is a seam
   convention, and SOMA has no seam.
3. **Latency vs the bridge fan-out, and whether the Apex-callout-budget
   constraint disappears** — the constraint that makes variant-3's serial path
   degrade by design (D61). Native routing has no Apex transaction to overrun.

### Work items

| # | Item | State |
|---|---|---|
| 1 | Salesforce beta enrollment for Agentforce multi-agent ("connect agents") on the lab org — the one non-headless dependency; everything below is gated on it | blocked — beta request with Salesforce |
| 2 | Learn the connected-subagent node schema once: build one orchestrator→subagent link in Agent Builder, `sf project retrieve` the `GenAiPlannerBundle`, and diff the base64 `agentGraph` to identify the cross-agent node field (existing bundles only carry `type:"subagent"` internal-topic nodes) | not started — gated on item 1 |
| 3 | Three new specialist single-org Agentforce agents (`GenAiPlannerBundle`s): Logistics, Commercial/Legal, Customer-ops, each grounded in the supplier-disruption scenario, deployed headless via the Metadata API | not started — gated on item 1 |
| 4 | `A2ALab_Supply_Orchestrator_SOMA` orchestrator bundle whose `agentGraph` wires the three specialists as connected subagents (native routing), leaving `A2ALab_Supply_Orchestrator` (bridge variant-3) untouched as the comparison baseline | not started — gated on items 2–3 |
| 5 | Headless deploy script for the SOMA fleet (orchestrator + 3 subagents), sourcing `deploy/aws_preflight.sh`-equivalent org guards; no hardcoded org identifiers (`.env` only) | not started |
| 6 | `config/scenarios.yaml` entry `supplier-disruption-soma` with its own business-case `description` (console renders it) and a `soma` topology alongside the WS8 `delegated`/`serial` variants | not started |
| 7 | Native trace capture: pull the SOMA run's unified session trace + per-subagent independent traces via the WS23 `salesforce-otel` source (D73) / M11 harvest; record in `obs_sessions`/span events under the existing platform name | not started — gated on items 4–6 |
| 8 | Recorded comparison run in `plan/03-results.md`: SOMA native routing vs WS8 variant-3 bridge fan-out on the identical scenario — wall latency, per-leg coverage, and trace-fidelity delta | not started |
| 9 | Console: SOMA run renders its native session trace in the Session Trace tab, and a Details pane (D57) narrates native-routing-vs-bridge citing D61/D73/WS8/WS24 | not started |
| 10 | Field insight (native multi-agent orchestration as the no-wire control case) + ADR D77 recording the SOMA-as-baseline decision, written when the build is committed post-enablement | not started |
| 11 | Diagram + console-copy pass (`config/diagrams.yaml`, `plan/09-deployment-map.md`, the `*_DIAGRAM` constants) so the SOMA topology is drawn and the estate map shows the new agents | not started |

### Exit criteria

**Blocked** until item 1 (beta enrollment) lands. Once enabled: the build is
expected to be fully headless via `GenAiPlannerBundle` + `agentGraph` (the same
metadata path the lab already deploys), with item 2 the single one-time
schema-learning step. The workstream is **met** when a `supplier-disruption-soma`
run routes natively to three single-org subagents, its unified session trace is
harvested and surfaced, and `plan/03-results.md` records the native-vs-bridge
comparison against WS8 variant-3 on the identical scenario.
