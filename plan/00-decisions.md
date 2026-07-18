# Decision log (ADRs)

Running log — newest at the bottom. Each entry: date, decision, why, status.

## 2026-07-09 — D1: Salesforce environment = user's production org
Lab metadata is strictly namespaced `A2ALab*`; a dedicated least-privilege
integration run-as user holds the Agentforce permission set. Consequences:
Apex deploys require test runs with ≥75% coverage (the invocable ships with
`A2ALabInvokeRemoteAgentTest`), and Einstein requests draw on real licensing
(matrix runs stay small; sessions are reused). Fallback: free Agentforce DE org.

## 2026-07-09 — D2: Language = Python everywhere outside Salesforce
Apex/Flow inside the org; `uv`-managed Python 3.11 project for everything else.

## 2026-07-09 — D3: Phase 1 covers both Agentforce↔Claude directions
Path A (Agentforce→Claude) and Path B (Claude→Agentforce) both land before
the OpenAI pair (Path C).

## 2026-07-09 — D4: OpenAI runtime = Bedrock AgentCore Runtime
Framework-agnostic containers, one protocol mode per deployment (HTTP :8080,
MCP :8000, A2A :9000). Accepted cost: 3–4 deployments per agent.

## 2026-07-09 — D5: Local exposure = cloudflared named tunnel
Enterprise subdomain zone `lab.agenticthings.com`, NS-delegated from GoDaddy
(GoDaddy stays primary DNS for the apex). Ingress in deploy/tunnel/config.yml.

## 2026-07-09 — D6: Demo scenario = research assistant
Agentforce fields the end-user question and delegates open-ended research/
summarization; transcripts make each agent's contribution obvious.

## 2026-07-09 — D7: Wire visibility is a core requirement
Every hop records a TraceEvent with the raw wire payloads; the lab console
(:8200) shows per-hop protocol badges and side-by-side raw request/response
with SSE live tail.

## 2026-07-09 — D8: Bridge for Path A, shims for Path B
Agentforce's GA outbound is REST-only → thin FastAPI bridge keeps the
MCP/A2A comparison alive (cells recorded **via-bridge**). Agentforce has no
GA MCP/A2A inbound → thin proxy shims over the Agent API (cells recorded
**via-shim**). Native SF MCP actions stay **blocked-beta** until access lands.

## 2026-07-09 — D9 (revised): Claude hosting = Anthropic Managed Agents (beta) first
Originally the self-hosted Claude Agent SDK was primary and Managed Agents an
optional late variant. **Revised at user request**: Managed Agents (beta) is
now the *default* Claude backend (`CLAUDE_BACKEND=managed`) — it's a piece the
lab explicitly wants to exercise. The self-hosted SDK backend remains fully
supported (`CLAUDE_BACKEND=sdk`) as (a) the fallback, (b) the AgentCore
containerization path (M8 — Managed Agents can't be self-deployed), and
(c) the latency comparison cell: managed sessions provision a per-session
container, so first-turn latency vs. the warm SDK server is itself a lab
finding that matters for the Agentforce action-timeout budget (~60s, to be
measured). Both backends sit behind the same `AgentAdapter`; nothing else in
the stack knows which one is running.
Path B symmetry: under `managed`, `ask_agentforce` is a CMA **custom tool**
handled host-side by our orchestrator (Salesforce credentials never enter
the sandbox); under `sdk` it's an in-process SDK MCP tool.

## 2026-07-09 — D10: Salesforce build/deploy tooling = Salesforce MCP servers
**At user request**: the Agentforce agent and all supporting `A2ALab*`
metadata are built and deployed using Salesforce's official MCP servers
rather than hand-run `sf` CLI commands. `.mcp.json` at the repo root
registers the DX MCP server (`@salesforce/mcp`, toolsets orgs/metadata/data/
testing/users) so Claude Code drives org auth checks, `salesforce/` metadata
deploys, and Apex test runs through MCP tools. The raw `sf` CLI remains the
documented fallback in plan/04-runbooks.md. Agent build/publish still happens
in Agent Builder where no MCP/CLI surface exists yet.

## 2026-07-09 — D11: Streaming out of scope for v1
Apex callouts are buffered request/response. One A2A SSE demo ships as a
capability comparison only (M2 verify step).

## 2026-07-10 — D12: Agentforce demo grounding = instruction-embedded fictional dataset
The agent's three original topics all invoked
`EmployeeCopilot__AnswerQuestionsWithKnowledge`, but the org has no Knowledge
base (`knowledgeActionEnabled=false`), so every question died with a
"missing resource" apology — the Path B cells "worked" at the protocol layer
while the agent itself never answered. **Fix**: topics are now
instruction-only (no actions), and a new `Customer account status` topic
carries a small FICTIONAL book of accounts (Omega, Inc.; Acme Corp;
Northwind Traders) embedded in its instructions. Chosen over real CRM
records + a query flow because the lab teaches protocol interop, not CRM
setup: deterministic answers, zero org-data dependencies, no extra
permission surface in a production org, and the agent is instructed to
disclose the dataset is fictional demo data when asked. The console's
default prompt ("Tell me what you know about account Omega, Inc.") targets
this topic; scripts/matrix.py keeps its protocol-comparison utterance for
the all-cells sweep.

## 2026-07-10 — D12 (revised): Agentforce demo grounding = real CRM records + Apex action
**Revised at user request** ("this is easy enough to not fake"): the
instruction-embedded dataset is gone. The Customer account status topic now
calls a real agent action — GenAiFunction `A2ALab_Get_Account_Summary`
wrapping Apex invocable `A2ALabGetAccountSummary` (SOQL over Account + open
Opportunities + open Cases) — and answers only from what the action returns.
Demo records: Acme Corp and Northwind Traders were seeded; "Omega, Inc."
already existed in the demo org with its own opportunities/cases, which the
agent now reports faithfully. Deployment gotchas recorded for reuse:
(1) GenAiFunction bundles need the org's canonical shape — developerName /
localDeveloperName / isIncludeInProgressIndicator in the meta plus
`lightning__objectType` + `copilotAction:` annotations in input/output
schema.json (retrieve an existing function as the template); (2) topic
changes only take effect after redeploying the GenAiPlannerBundle with the
agent deactivated, then reactivating; (3) actions run as the bot user —
permission set `A2ALab_Agent_Actions` grants the Apex class + object read
(no View All: the Einstein Agent license forbids it), and the class is
`without sharing` because the bot user sits outside the role hierarchy and
would otherwise see zero child records.

## 2026-07-10 — D13: Trace persistence = pluggable TraceSink; DynamoDB for cloud
The JSONL-file trace store only works locally: on AWS the container
filesystem is ephemeral (traces lost on every redeploy) and per-service
(each container would write a private traces/ the console can't read).
`TraceRecorder` now fans events out to pluggable `TraceSink`s selected by
`A2ALAB_TRACE_SINK` (comma list): `jsonl` (default, unchanged local dev) and
`dynamodb` (`A2ALAB_TRACE_TABLE`, default a2alab-traces; PK trace_id, SK
ts#hop_seq, GSI day-index, TTL expires_at via A2ALAB_TRACE_TTL_DAYS=14;
payloads stored as JSON strings — DynamoDB rejects floats/empty strings and
Data 360 maps scalars cleanly). `jsonl,dynamodb` tees to both. Sink failures
are contained to a stderr warning — tracing must never break the hop it
observes. boto3 ships as the `aws` extra (`uv sync --extra aws`).
DynamoDB over CloudWatch/S3 because it matches the console's access patterns
(group-by-trace, list-recent, poll) AND the M10 reporting path below.
Still open (M8): a console read path from DynamoDB — the viewer currently
reads only the JSONL files.

## 2026-07-10 — M10 (later phase): Data 360 zero-copy → TableauNext reporting
The DynamoDB trace table is the integration point for Salesforce-side
analytics: connect Data 360 (in the lab org) to it with the zero-copy
AWS DynamoDB connector
(https://developer.salesforce.com/docs/data/data-cloud-int/guide/c360-a-awsdynamodb-connector.html)
so trace telemetry (hops, protocols, latencies, statuses) lands in Data 360
without ETL, then build TableauNext reports on cross-platform agent traffic.
Prereqs: M8 AWS deploy writing `A2ALAB_TRACE_SINK=dynamodb`; an IAM role for
the connector with read access to the table + day-index GSI. The flat scalar
item shape (D13) was chosen so connector field mapping is trivial.

## 2026-07-10 — D14: Agentforce agent re-implemented in Agent Script
**At user request**: the builder-made legacy agent (Bot + GenAiPlugin +
GenAiPlannerBundle metadata) is replaced by an **Agent Script** authoring
bundle — `salesforce/force-app/main/default/aiAuthoringBundles/
A2ALab_Research_Assistant_Script/` is now the source of truth for the agent.
Workflow: `sf agent generate authoring-bundle` (seeded from
specs/a2alabResearchAssistant.yaml) → edit the .agent script →
`sf agent validate authoring-bundle` → `sf agent publish authoring-bundle`
→ `sf agent activate`. Publishing creates a NEW agent
(A2ALab_Research_Assistant_Script, 0XxKB000000xdmP0AQ) — legacy agents can't
be converted in place; the old agent (0XxKB000000xdlb0AA) is deactivated and
`SF_AGENT_ID` now points at the script agent. Its superseded legacy metadata
still sits under force-app (bots/A2ALab_Research_Assistant, genAiPlugins/,
genAiPlannerBundles/, genAiFunctions/) — safe to delete from the repo.
Grammar notes (server-side compiler, iterate via validate): custom actions
are declared INSIDE a subagent under `actions:` as a mapping —
`name: {label, description, target: "apex://Class", inputs/outputs as
`param: type` with nested label/description/is_required}` — and exposed to
the LLM via `reasoning.actions: name: @actions.name`; `run @actions.x
with p=@variables.y` executes deterministically in before_reasoning hooks.
The script keeps the same grounding behavior (get_account_summary →
A2ALabGetAccountSummary Apex) and reuses the same agent user + permission
set (A2ALab_Agent_Actions). Verified live post-switch: direct Agent API and
the full Claude → Agentforce scenario both answer from CRM records.

## 2026-07-11 — D15: Experiments must enter through the real platform agent
**At user request**: every experiment's call path starts by invoking the
designated agent on its own platform exactly as a true API caller or human
would — it is then that platform's job to initiate the cross-platform hop
(through the bridge where needed). The console may never simulate a
platform's leg. Audit result: Claude → Agentforce already complied;
**Agentforce → Claude did not** (the console POSTed straight to the bridge,
faking the Salesforce entry) and was re-architected as a true collaboration:
the console now drives the GA Agent API (target `agentforce-rest`), the
agent answers the account question from its own CRM via
`get_account_summary`, then delegates outside-in market research to Claude
via a new `ask_external_researcher` action (Apex `A2ALabInvokeRemoteAgent`
→ Named Credential → tunnel → bridge → `claude-rest`), replying in two
attributed sections ("From our CRM" / "External market research"). Wired
live: External/Named Credential + updated `A2ALab_Agent_Actions` permset
(class access + credential principal access) deployed; `BridgeToken` set on
`A2ALabPrincipal` via Connect API; agent republished as **version 2**.
Interim transport: a TryCloudflare quick tunnel (no Cloudflare login
needed) fronts the bridge — its hostname changes per restart and lives in
the Named Credential URL; M6's named tunnel replaces it with
`bridge-lab.agenticthings.com`. Trace note: the Salesforce-internal legs
mint their own trace id (Apex generates one per callout), so a scenario run
produces two correlated-by-time traces — the Agent API turn and the Apex →
bridge → Claude leg. Measured e2e: 35.9s wall for the full collaboration
(Agent API turn incl. both actions), within the ~60s action budget.
Metadata gotchas recorded: NamedCredential Metadata API shape has no
calloutOptions wrapper (allowMergeFieldsInBody/Header +
generateAuthorizationHeader are top-level) and HttpHeader parameters
require sequenceNumber.

## 2026-07-12 — D16: Two delegation patterns — sync (proven) + async (what CMA is for)
**At user request**, the Agentforce → Claude path splits into two experiments:
- **Sync** (`agentforce-to-claude`, unchanged mechanics, retitled): one-turn
  collaboration inside the action-timeout chain (~60s action → Apex 110s →
  bridge 45s). Kept deliberately as the protocol proof + response-time
  measurement; the chain is also why synchronous research can't go deep.
- **Async** (`account-brief-async`, new): the architecture Anthropic
  positions Managed Agents for — long-running, scheduled, stateful work.
  An Anthropic **scheduled deployment** (platform-native cron, daily,
  `scripts/setup_brief_agent.py`, ids in `.a2alab/brief.json`) fires a
  research session on a dedicated managed agent ("A2ALab Account
  Intelligence Researcher", `CLAUDE_BRIEF_MODEL` default sonnet-tier —
  quality over latency since no timeout budget applies). The session does
  multi-source web research (news, competitor moves, government/regulatory,
  geopolitics — each web_search/web_fetch recorded as a trace hop), then
  delivers via a `save_account_brief` custom tool executed HOST-SIDE
  (`src/briefs/`): (1) insert `A2ALab_Account_Brief__c` — long-text
  `Brief__c` linked to the Account, `Brief_Date__c`, `Source__c`,
  `Research_Session_Id__c`; (2) log a completed Task on the Account
  crediting the Claude managed agent; (3) fire the `A2ALab_Brief_Alert`
  in-app custom notification (best-effort). Salesforce credentials stay
  host-side (same boundary as ask_agentforce).
  **Consumption path**: `Brief__c` is the corpus Data 360 vector-indexes
  (M10) so the Agentforce agent grounds account answers and sales plays in
  the freshest research at retrieval time instead of researching
  mid-conversation.
  **Servicing**: cron-fired sessions idle at the custom tool until the lab
  host's `python -m briefs --watch` (in run_local.sh) picks up the
  deployment run — nothing is lost if the host was down; the session waits.
  Console "Run" fires the same job ad-hoc via a background task; /api/run
  acks immediately and the hops stream into the turn's trace.
  Ops notes: daily firings bill real multi-minute sessions — pause with
  `client.beta.deployments.pause(<deployment_id>)`; in-app alert recipients
  default to active System Administrators (`SF_ALERT_USERNAME` overrides).

## 2026-07-12 — D17: Brief consumption surfaces in Salesforce + Apple demo account
Follow-through on D16 — the brief must be *usable* where account teams live:
- **Account record page** (`SDO_Account_Default`, the org default): new
  "Account Briefs" tab with LWC `a2alabAccountBriefs` — latest brief
  rendered from markdown in a scrollable pane (source, date, session id,
  open-record link) with a past-briefs list beneath. The standard
  Related List – Single component renders empty for custom related lists
  not on the page layout, so the list lives in the LWC
  (`lightning/uiRelatedListApi`).
- **Brief record page**: new FlexiPage `A2ALab_Account_Brief_Record_Page`
  (LWC `a2alabBriefViewer` main, details sidebar) activated as org default
  via CustomObject actionOverrides (View → Flexipage, Large+Small) — every
  entry path (past-briefs list, Task link, in-app alert) lands on a page
  that shows the brief. Shared renderer in service module `a2alabMarkdown`.
- **Task link**: the delivery Task's description now carries the Lightning
  URL to the brief (record ids are not human-usable).
- **Cite scrubbing**: web_search `<cite>` markers leaked into briefs —
  scrubbed at delivery (BriefWriter), forbidden in the agent prompt (agent
  v2), and stripped at render for legacy records.
- **Apple Inc. demo account** (`001KB00000BLXHSYA5`, apple.com/AAPL, real
  firmographics + 3 opportunities + 3 open cases): the async scenario and
  the daily deployment now research **Apple Inc.**, so briefs carry real,
  current intel (verified live: earnings catalysts, Apple v. OpenAI suit,
  DMA ruling, tariff/Taiwan risk). Deployments are immutable — the Omega
  deployment was archived and replaced (`depl_01C6Vv2bQJAhQjSK8NfTF8h4`).
  Gotcha logged: values with spaces in `.env` must be quoted — run_local
  workflows `source` it.

## 2026-07-17 — D18: Observability = its own console category, harvest-and-join
New left-nav category (peer of Scenarios/Targets/Traces) showing each
*platform's interior view* of the executions the lab drove, joined to our
wire traces — full plan in plan/05-observability.md (M11). Research-verified
pull surfaces: Salesforce Session Tracing DMOs + Einstein GenAI audit DMOs
via Data Cloud Query API v2 (richest; needs Data Cloud + setup toggles);
Anthropic Managed Agents `GET /v1/sessions/{id}/events` (deepest per-session
detail, but **no list-sessions API** — we must persist every CMA session id
we create); OpenAI traces are ingestion-only/UI-only (no read API), so on
that side our own trace layer stays the system of record and M9 must tee a
`TracingProcessor` + persist response ids from day one. Design consequence:
**harvest-and-cache** into a local store (platform logs lag, cost credits,
or expire — CMA events die with the session, OpenAI responses in 30 days),
and a new `platform_ref` field on `TraceEvent` records each hop's native
execution id at emit time so the join is never reconstructed after the fact.

## 2026-07-17 — D19: Local trace/observability query store = SQLite (DynamoDB unchanged)
The console needs a query backend (timeline bucketing, platform filters,
trace⋈platform-log joins) that JSONL can't serve. Chosen: a `sqlite`
TraceSink (`traces/lab.db`, default `A2ALAB_TRACE_SINK=jsonl,sqlite`) that
also hosts the harvested observability tables, becoming the console's read
path. JSONL stays the append-only raw archive (rebuildable via
`scripts/trace_import.py`); the DynamoDB sink stays the cloud path — M10's
Data 360 zero-copy → TableauNext reporting is built on that table, so it is
not replaced. Rejected: Postgres (infra burden for a single-user lab, no
M10 story), OTel/ClickHouse stacks (overkill at ~500KB of traces, and they
abstract away the raw payloads the lab exists to show).

## 2026-07-17 — D20: Cloudflare = free plan, whole-zone DNS (Enterprise subdomain delegation dropped)
The M6 runbook assumed an Enterprise account for a `lab.agenticthings.com`
subdomain zone (NS-delegated from GoDaddy). No Enterprise account exists;
subdomain zones are Enterprise-only and partial/CNAME setup is Business.
Chosen: a **free** Cloudflare account onboarding the whole `agenticthings.com`
zone — GoDaddy stays registrar, nameservers move to Cloudflare, existing DNS
records are imported at onboarding. Named tunnels and unlimited hostnames are
free (Zero Trust free plan). One knock-on rename: free Universal SSL covers
only one subdomain level (`*.agenticthings.com`), so the planned two-level
`bridge.lab.…` names fail TLS at the edge — lab hostnames are single-level
`<svc>-lab.agenticthings.com` (`bridge-lab`, `console-lab`, `claude-rest-lab`,
`claude-mcp-lab`, `claude-a2a-lab`); keeping `*.lab.…` would need paid
Advanced Certificate Manager. Also: this network blocks QUIC/UDP egress, so
config.yml pins `protocol: http2`. Stable hostnames matter twice: the `A2ALab_Bridge` Named
Credential is set once (no redeploy per tunnel restart, unlike TryCloudflare),
and M11.4's Anthropic webhooks require a stable public HTTPS endpoint.
Runbook §3 rewritten accordingly.

## 2026-07-17 — D21: AWS runtime account = embark (730335577398); a2alab-traces provisioned
Lab AWS runtimes (D13 DynamoDB trace table, later M8 AgentCore) live in the
**embark** SSO account `730335577398` (role AccountUser via
ehc-embark.awsapps.com; SSO home region us-west-2), NOT the personal account
967640827025 that the local `default`/`congmingwudi` profiles point to.
`.env` sets `AWS_PROFILE=embark`, `AWS_REGION=us-east-1`. The `a2alab-traces`
table (runbook §6 schema: PK trace_id, SK sk, GSI day-index, PAY_PER_REQUEST,
TTL on expires_at) is created in us-east-1. Gotcha: SSO tokens expire — rerun
`aws sso login --profile embark` when boto3/CLI report an expired token.

## 2026-07-17 — D22: Observability = deterministic ETL below, agent analysis above
Harvesting platform logs stays pure ETL (scripts/obs_harvest.py, later cron
or M11.4 webhook-triggered) — no LLM in the pull loop: it's deterministic
API paging + upserts where an agent adds only cost and nondeterminism. The
agent-shaped job sits one layer up: **M11.5** (plan/05-observability.md), a
scheduled CMA deployment that reads traces/lab.db through a host-side
custom tool and writes an interpretive nightly brief (failures, token
anomalies, cross-platform comparisons). Deferred until the store holds real
multi-platform data — STDM/GenAI toggles were enabled 2026-07-17, DMOs
materializing; revisit after the first live Salesforce harvest.

## 2026-07-17 — D23: Hosted obs analyst = Aurora Postgres store + MCP front + scheduled deployment (no driver loop)
Direction decided for the hosted phase; the D22/D19 local design stays as
the working prototype until then. Constraint forcing the fork: CMA **custom
tools are pull-serviced** — `agent.custom_tool_use` arrives on an outbound
SSE stream a local driver holds open (`analyst.py:_drive`); with no driver
attached the tool call parks until the session times out. Fine on a laptop
(D16's `--watch` exists for exactly this), disqualifying for a hosted,
cron-fired analyst. Rule extracted: **a scheduled/hosted agent may only use
tools servable without a client attached** — custom tools are the one tool
type that blocks on one.

**Store = Aurora PostgreSQL Serverless v2** (scale-to-zero, embark account
per D21, us-east-1) — one store for all five consumers: trace hops (new
`postgres` TraceSink), harvested obs tables (obs_harvest writes here),
hosted console reads, the analyst's ad-hoc SQL, and M10 reporting.
Considered and rejected: DynamoDB-only (no joins/aggregates — kills the
analyst workload, whose entire value is ad-hoc SQL over
trace_events⋈obs_sessions); DynamoDB+Athena two-tier (was the leading
option while DynamoDB held the only Data 360 zero-copy path, but Data 360's
**AWS Aurora PostgreSQL connector is GA for Zero Copy query federation**,
so Postgres now serves M10 too and the second tier buys nothing);
Timestream (dead-ended), OpenSearch/CloudWatch (hide the raw payloads the
lab exists to show). Payloads land as **jsonb** — strictly better than the
JSON-strings-because-DynamoDB-rejects-floats shape of D13. Retention via
pg_cron/partition drops replaces TTL. **Supersedes**: D13's dynamodb sink
is no longer the cloud path (code stays; a2alab-traces decommissionable)
and M10 rebuilds on the Aurora connector instead of the DynamoDB one.

Connector-driven design constraints (verified against the setup doc):
federation reaches the cluster endpoint (`*.rds.amazonaws.com`) with
username/password, scoped to one database+schema, from Salesforce IP
ranges — so the cluster needs a reachable endpoint with a tight
security-group allowlist (Salesforce IPs + the MCP server) and TLS
required; auth is a dedicated schema-scoped **read-only role** shared in
kind (not in credential) with the analyst path. Roles: `lab_writer` for
sinks/harvest, `lab_reader` for Data 360 and the analyst MCP server, with
statement_timeout and row caps enforced in DB grants/settings — the
server-side successor to `_run_readonly_sql`'s app-level guard.

Analyst wiring, three pieces: (1) **harvest** = obs_harvest.py as hosted
cron writing Aurora (keeps SF/Anthropic creds; stays our code);
(2) **access** = thin remote MCP server (Streamable HTTP) exposing
`query_obs_store` backed by `lab_reader`; declared on the agent via
`mcp_servers` + `mcp_toolset`, token in a vault attached by `vault_ids`
(credentials never enter the sandbox) — data access becomes
server-to-server, no session driving needed; (3) **schedule** = CMA
scheduled deployment (the D16 pattern), but unlike D16 firings complete
with **no watcher process** since every tool is server-servable. Brief
delivery: `/mnt/session/outputs/` fetched via Files API on a
`session.status_idled` webhook (D20's stable hostname prereq), or a
`save_brief` MCP tool writing back into Aurora — preferred, since it keeps
the analyst observable by the thing it analyzes. Env note: if the agent's
environment uses `limited` networking, set `allow_mcp_servers: true` or
list the MCP host, else tool calls fail silently.

## 2026-07-18 — D24: Path C = OpenAI Agents SDK on AgentCore; interior built by Codex; ChatGPT cell is manual-only
Refines D4 with three calls. (1) **Runtime vs model**: AgentCore hosts the
container; the brain is the **OpenAI Agents SDK calling the real OpenAI
API** — not gpt-oss-on-Bedrock — because M9's observability column
(TracingProcessor tee, response-id capture) only exists on OpenAI's
platform. SCP preflight passed 2026-07-18: `bedrock-agentcore-control`
responds in embark (tonight's D23 lesson: preflight org SCPs before
committing to an AWS service). (2) **Build split, at user request**: the
lab side (adapter/backend seam, stub backend, protocol servers, ports
8011/8012/8013, targets.yaml cells, AgentCore Dockerfile, tests) is built
here; the agent interior (`AgentsSdkBackend`) is handed to **OpenAI
Codex** against the written contract in plan/06-openai-codex-handoff.md —
on-brand for a cross-vendor lab (each vendor's coding agent builds its own
platform's integration) with the seam kept convention-safe on our side.
(3) **ChatGPT-native paths**: "Agentforce Sales in ChatGPT" (Salesforce's
app, open beta) is a closed surface — can't host our agent, no trace API,
not API-drivable, so it fails D15; a custom GPT with an Action pointed at
the bridge IS wire-traceable our side and becomes a **manual demo cell**
(interior dark, not automatable — recorded honestly in the matrix), not
the primary Path C.

## 2026-07-18 — D25: Per-platform Agentforce twins keep cross-platform experiments closed systems
**At user request**, after the accept-4 trace showed the OpenAI→Agentforce
experiment silently becoming a THREE-platform chain (the shared Agentforce
agent's external-research action delegated to Claude mid-answer): each
counterpart platform now gets its own Agentforce twin so every experiment
is a closed two-platform system with attributable contributions. New Agent
Script bundle `A2ALab_Research_Assistant_OpenAI` (agent
0XxKB000000xdn30AA, published+activated v1) — behaviorally identical to
the Claude-paired agent except its `ask_external_researcher` action
targets **openai-rest**, pinned three ways: a required `target` action
input ("ALWAYS pass exactly: openai-rest"), the input description, and the
STEP 2 instruction. **No Apex change / no prod class deploy**: the D15
invocable already takes `target`; the twin reuses the same agent user,
permission set, and Named Credential. Lab wiring: `SF_OPENAI_AGENT_ID`
(the OpenAI backend's ask_agentforce targets the twin),
`agentforce-openai-rest` target, both OpenAI scenarios flipped live
(mirroring the Claude pair's flows), openai servers added to run_local.
Live-verified both directions with wire proof: Agentforce→OpenAI 20.9s
(apex→bridge→openai-rest, no Claude hop), OpenAI→Agentforce 20.1s (CRM
attributed, nested research loop bounded). The symmetric self-loop
(openai→AF-twin→openai) is intentional — it mirrors claude→AF→claude, so
"the same two experiments" holds exactly across platforms.
