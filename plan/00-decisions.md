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

## 2026-07-17 — D21: AWS runtime account is the work SSO account, not a personal one; a2alab-traces provisioned
Lab AWS runtimes (D13 DynamoDB trace table, later M8 AgentCore) live in the
**work SSO account**, NOT the personal account the machine's `default` profile
points at. `.env` — gitignored — carries which one: `AWS_PROFILE`,
`AWS_REGION=us-east-1`, and `A2ALAB_AWS_ACCOUNT_ID`. Account ids and the SSO
profile name are deliberately absent from this repo; the mapping lives in
`.a2alab/accounts.md` (also gitignored).

The distinction is load-bearing rather than administrative: the two accounts
have different budgets, different org SCPs (D23's Function-URL denial is one),
and only one of them is appropriate for lab spend. **A deploy that silently
lands in the wrong account creates real, billable, wrongly-placed
infrastructure and stays quiet about it** — so since 2026-07-27 every AWS
deploy script sources `deploy/aws_preflight.sh`, which resolves the session's
account with `sts get-caller-identity` and refuses to continue when it does not
match `A2ALAB_AWS_ACCOUNT_ID`.

The `a2alab-traces` table (runbook §6 schema: PK trace_id, SK sk, GSI
day-index, PAY_PER_REQUEST, TTL on expires_at) is created in us-east-1.
Gotcha: SSO tokens expire — rerun `aws sso login` when boto3/CLI report an
expired token. Second gotcha, which has now cost three components: the SSO
home region differs from the deploy region, and an ambient
`AWS_DEFAULT_REGION` beats `AWS_REGION` in boto3 — the preflight pins both.

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

**Store = Aurora PostgreSQL Serverless v2** (scale-to-zero, the lab's account
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
responds in the lab's account (tonight's D23 lesson: preflight org SCPs before
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

## 2026-07-19 — D26: Claude sdk twin on AgentCore, scripted deploys, and the local/hosted mode switch
The Managed-Agents Claude cell and the self-hosted OpenAI Agents SDK cell
are different architectural species (managed platform runtime vs BYO
container), so the cross-vendor comparison gets an apples-to-apples peer:
the Claude **sdk backend** (already the M8 containerization path) deploys
to Bedrock AgentCore Runtime exactly like the OpenAI agent — same image
contract (`POST /invocations` + `GET /ping`), same IAM-only data plane,
same twin rules (its `ask_agentforce` targets `SF_AGENT_ID`, the
Claude-paired Agentforce twin, per D25). The Managed cell **stays** — the
lab now runs the same Claude adapter both ways, making managed-vs-
self-hosted itself a measured comparison (cold start, credential locality,
observability access, ops burden), alongside the cross-vendor pair on
identical runtime. Mechanics: `AgentCoreClient` lifted to
`interop/clients/agentcore.py` (platform-generic; the openai module is
gone), `claude-agentcore` target added, Claude image gains `--extra aws`
(boto3 for the PG TraceSink — without it container hops drop silently),
and the M9 hand-deploy is replaced by `deploy/agentcore/deploy.sh
<claude|openai>` (ECR build/push arm64, create-or-update runtime, role
copied from the existing a2alab_* runtime, env written back to .env).
Dev↔hosted switching: `A2ALAB_MODE=hosted` remaps `claude-rest`/
`openai-rest` to the agentcore targets at client resolution (bridge,
custom tools, console runs) via a `modes:` block in targets.yaml — one
env flip, no Salesforce or scenario changes; `scripts/matrix.py` resolves
exact names so matrix cells always measure the target they name. Roadmap
context in plan/07-workstreams.md (WS1); CrewAI and Pydantic AI are
flagged as candidate future platforms, **user decision pending**.

## 2026-07-19 — D27: Delegation guard — standard rider + depth limit at every delegation seam
The paired experiments intentionally wire both directions of each platform
pair, which makes circular execution possible by construction
(claude→agentforce→claude...). Loops previously terminated only by
starvation (stacked timeouts + per-agent turn caps) — surfacing as
timeouts and max-turns errors, not clean stops. None of REST/MCP/A2A
defines TTL/max-forwards semantics (networking solved this with IP TTL and
SIP Max-Forwards; agent protocols haven't — recorded as an insight), so
the lab adds its own convention in `interop/delegation.py`, enforced at
every delegation seam (the three ask_agentforce tool paths — sdk, managed
host-side, openai — and the bridge): (1) every delegated request carries a
standard parseable **rider** block naming caller, platform, and
delegation-depth, with a do-not-call-back directive — the prompt-level
guard and the only channel into text-only platform APIs (Agentforce Agent
API); (2) the same context rides `AgentRequest.metadata["delegation"]` on
lab protocols; (3) seams forward only while depth <
`A2ALAB_MAX_DELEGATION_DEPTH` (default 1: a delegated-to agent answers
itself, delegates no further) and otherwise return a standard wire-visible
refusal. Known bound: depth cannot survive *through* the Agentforce
platform (its model composes fresh action inputs), so an ignored rider
costs at most one extra leg before the next lab seam re-stamps depth 1 and
the chain stops — claude→AF→claude(refused tool, answers directly).
Optional follow-up (not done): add rider-honoring instructions to the
Agent Script twins so Agentforce also stops at the prompt level.

## 2026-07-19 — D28: Hosted Agentforce A2A shim + the operator-selectable Agentforce channel
Two moves that finish decoupling the experiments from the laptop and turn
protocol choice into a demo control. (1) **The A2A shim now runs in AWS**:
the same `create_a2a_app(AgentforceProxyAdapter())` app, wrapped with
Mangum on Lambda (`a2alab-af-shim`, arm64 py3.12, 11MB vendored bundle via
deploy/shim/build_zip.sh) behind an API Gateway HTTP API (SCP blocks
Function URLs — same D23 pattern; app-layer x-lab-token auth; card
well-known stays exempt). Any cloud container (AgentCore, Agent Engine)
can now reach Agentforce over A2A with no local dependency. Found and
worked around: WireTapMiddleware hangs under Mangum's single-shot ASGI
body semantics — the hosted shim runs `wiretap=False` (adapter-level Hops
still record; uvicorn-hosted servers keep the wiretap). Lambda cap note:
API Gateway's 29s ceiling fits the twin's guarded (CRM-only) answers;
full two-step answers flirt with it. (2) **Agentforce channel toggle**:
every self-hosted backend (claude sdk, openai, adk) now exposes
`ask_agentforce_a2a` (A2A via the hosted shim, `interop/af_channel.py`)
next to `ask_agentforce` (GA Agent API, the default). The console's
per-run radio injects a standard `[A2A-LAB ROUTING]` block after the
prompt suffix — the entry agent honors it, so one conversation can be
re-run across intermediate protocols. Riders (D27) apply on both
channels. Also this session: all three Agentforce twins' Agent Scripts
gained the rider-honoring DELEGATION GUARD (v2 active) — the D27
"optional follow-up" is done platform-wide; delegated-to twins answer
CRM-only with an explicit skip note instead of nesting delegations.

## 2026-07-19 — D29: WS2 platform decisions — ADK on Agent Engine, preview-A2A workarounds, scale-to-zero economics
The Google column runs the ADK 1.x surface the A2A-on-Agent-Engine docs
target (google-adk pinned <2), a Gemini flash-lite brain (ADK_MODEL;
3.x-family needs a location workaround — deferred), and Vertex AI Agent
Engine as the runtime because its native A2A serving is the lab's first
platform-native cell. Deploy shape (deploy/adk/deploy_adk.py):
cloudpickled A2aAgent + extra_packages as RELATIVE paths (absolute paths
break unpickling), min_instances=0 + 1cpu/2Gi on the personal GCP account
(default-size warm instance ≈ $250/mo; scale-to-zero idles at $0, cold
starts ~34s are lab data the warm-up panel manages). Preview-A2A
workarounds, recorded honestly (native-a2a-young insight): the public
card route 404s → the lab client pins transport http+json and builds a
minimal card locally (A2AClient gained card_path/transport options and
refreshing google-adc auth); create/update calls fail transiently with
bare INTERNAL errors → retry. Sessions are InMemory v1 (contextId →
session within a warm instance); VertexAiSessionService is the durable
follow-up. Twin: A2ALab_Research_Assistant_ADK per D25. Observability:
Cloud Logging harvested (request-level; no session/turn API on the
preview surface) — the fourth column of the fragmentation comparison.

## 2026-07-20 — D30: Direct platform-to-platform route (Apex → Agent Engine A2A) + operator route radio

The Agentforce→ADK experiment gains an operator-selectable outbound route,
the reverse-direction sibling of D28's channel radio: **via bridge**
(default — the twin's Apex hits the lab bridge, every hop recorded) or
**direct A2A** (Apex calls Vertex AI Agent Engine's platform-native
endpoint itself — no lab component in the path, and therefore a leg the lab
cannot trace; that visibility gap is the experiment). Mechanics:

- **Auth without exported keys:** a Salesforce self-signed certificate
  (`A2ALab_GCP_JWT`) signs an OAuth 2.0 JWT-bearer assertion; the cert's
  PUBLIC key is uploaded as a key on the GCP service account
  `a2alab-sf-caller@<gcp-project>` (`roles/aiplatform.user`). External
  credential `A2ALab_GCP` exchanges it at Google's token endpoint; named
  credential `A2ALab_AgentEngine` carries the token. No Google private key
  exists outside GCP; no Salesforce key leaves the org. Field learnings:
  the `sub` claim must be OMITTED (Google treats it as a Workspace
  impersonation target → `invalid_grant: account not found`), and claim
  parameterValues are raw strings except URLs, which the validator demands
  JSON-quoted (`aud`) — quoting `iss` breaks the exchange the same way.
- **Apex A2A client** (`A2ALabInvokeAgentEngine`): speaks the HTTP+JSON
  binding `POST {engine}/a2a/message:send` and MUST send `a2a-version:
  1.0` — the handler rejects the implied 0.3 default with
  `VERSION_NOT_SUPPORTED` (live A2A version negotiation, observed on
  Agent Engine; WS3 expects the same on Foundry). As a delegation seam with
  no bridge behind it, the class stamps the D27 rider + metadata itself.
- **Route selection** rides the same `[A2A-LAB ROUTING]` block as D28
  (`agentforce-route: direct|bridge`); the ADK twin's Agent Script v3
  branches between `ask_external_researcher` (bridge invocable) and
  `ask_external_researcher_direct`.
- Live 2026-07-20: direct round trip 35s, CRM + external sections, rider
  honored (no callback), external section built on the ADK agent's new
  synthetic `search_industry_news` signals.

Sibling fixes shipped with it: hosted runtimes carry the shim credential as
`AF_SHIM_TOKEN` (setting `A2ALAB_TOKEN` in a runtime flips on inbound
bearer auth that `invoke_agent_runtime` cannot satisfy — every invoke
401s), the shim Lambda writes its interior hops to the Aurora store which
the console now merges into the live trace view (real shim legs instead of
ghosts, same trace id end-to-end), and the A2A client forwards full request
metadata (the D28 twin-routing regression fix).

## 2026-07-20 — D31: GCP obs column upgraded — Cloud Monitoring rollup (tokens, billing meters, est. cost)

The ADK harvest (`adk_source.py`) now pulls a Cloud Monitoring rollup
alongside Cloud Logging entries, using the same ADC credentials
(cloud-platform scope) and plain REST — no new client libraries:

- `reasoning_engine/request_count` + `request_latencies` (per engine, by
  response code) and the **literal billing meters**:
  `cpu/allocation_time` (vCPU-s) and `memory/allocation_time` (GiB-s) —
  Agent Engine bills allocated compute, not tokens.
- `publisher/online_serving/token_count` — real Gemini token counts per
  model, input/output split. Caveat recorded honestly: project+model
  granularity, not per-engine (the lab project runs only the ADK agent, so
  it is effectively that agent's usage).
- An **estimated daily cost** computed from documented list prices
  (constants in the source, labeled estimates, never billing truth):
  compute + token components.

Where it lands: the engine session's `usage_json` (the coverage tile's
token count works generically), a new optional `est_cost_usd` rollup in
the store summary → an "est. 24h cost" cell on any platform's tile that
reports one, and a daily `metrics-rollup` obs event so the hosted analyst
(D23) can query the cost/compute picture over SQL. First live harvest:
41.9k tokens ≈ $0.05/day.

Also catalogued from the newly-enabled APIs: `observability.googleapis.com`
/ `telemetry.googleapis.com` power Google's unified o11y UI and OTLP
ingest (nothing extra to harvest), Cloud Trace spans remain the "not yet
harvested" future item (needs enable_tracing on the deployment),
`agentregistry.googleapis.com` is future discovery-insight material, and
`modelarmor.googleapis.com` is the GCP counterpart to the Einstein Trust
Layer — prime material for the trust-boundary security cell.

Insight updated: observability-fragmentation now records the punchline
that GCP is the inverse shape — no session/turn API, but the only platform
handing over token counts AND its actual billing meters, making the GCP
agent the only one the lab can price per day.

## 2026-07-23 — D32: Warm-up coverage & the serverless split — what colds, what doesn't

The console's warm-up panel covers exactly the targets backed by
**scale-to-zero containers** — the runtimes that pay a measured cold
start (warm figures from plan/03-results.md):

| runtime | cold | warm p50 |
|---|---|---|
| claude-agentcore (Bedrock AgentCore) | ~56s | 8.4s |
| openai-agentcore (Bedrock AgentCore) | ~31s | 10.3s |
| google-adk-a2a (Vertex AI Agent Engine, min-instances=0) | ~34s | 2.6s |

**Foundry is deliberately absent**: prompt agents are serverless on the
platform's always-on model pool (billed per token) — measured cold ≈ warm
(10–17s either way), nothing of ours to wake. Same class as Anthropic
Managed Agents (whose ~5–10s first-turn cost is session provisioning,
not a runtime cold start). The split matters architecturally:
container-backed serverless (AgentCore, Agent Engine) trades idle cost
for cold starts; token-serverless prompt platforms (Foundry, CMA) trade
runtime control for no-cold-start serving.

The Foundry **direction** still has a cold element — not the agent, the
**hosted shim leg**: Lambda init ~2s + a fresh Salesforce session + the
twin's first account turn (~20–27s) straddles API Gateway's hard 29s
ceiling, and third-party callers (Foundry's A2A tool) don't retry.
Mitigations, layered: the FoundryClient one-retry (rides the warmed
session) and — this decision — **warm-the-shim as a first-class panel
entry**: `agentforce-a2a-shim` is now a real target (cell + warm-up), and
its warm-up ping is composed as a *delegated* request
(`options.warmup_delegated_platform: foundry`) so it pre-creates the
platform-keyed twin session the next real call will ride; the D27 rider
on the ping keeps the twin's answer fast (no external-research step).

## 2026-07-23 — D33: Console navigation & naming overhaul (Control Panel, vendor-qualified chips, models on the path)

With five platform pairs live, the console's demo ergonomics got a pass:

- **Control Panel drawer**: the left nav is a collapsible drawer, closed
  on first visit — the landing canvas is the whole first impression, with
  a labeled edge tab (☰ + vertical "Control Panel") to open it; landing
  navigation (experiment tiles, protocol-call links) auto-opens it.
  First-open defaults: Experiments/Observability/Insights expanded,
  platform groups collapsed, Protocol calls + Traces closed. State
  persists per browser.
- **Vendor-qualified chips** (display labels only — raw ids stay as
  config/CSS keys): adk → google-adk, foundry → ms-foundry, gemini →
  google-gemini; the warm-up shim row reads **aws-shim** (what's being
  warmed is the Lambda + twin session, not Agentforce).
- **Models visible on every call path** (gpt-5-mini, claude-haiku-4-5,
  gemini-2.5-flash-lite) — as ghost node names where no real hop
  constrains naming, as detail text where one does. The Agentforce twins'
  reasoning model is deliberately NOT shown (a reasoning-model
  comparison — OpenAI vs Claude models inside Agentforce — is reserved
  as a future experiment).
- **Sequence-diagram honesty rules**: inbound-to-Agentforce flows no
  longer show the twin's interior Apex/action execution (its business,
  not the experiment's — the lab-built outbound action stays visible in
  Agentforce→X directions where its bridge hop is a real traced event);
  the hosted shim renders as an explicit network node.

## 2026-07-23 — D34: lab-trace rider line — text-level trace propagation into platform logs

**Decision.** Extend the D27 delegation rider with a `lab-trace: <trace_id>`
line, stamped by `delegation.delegate(..., trace_id=...)` at every
delegation seam (the three ask_agentforce tool paths, the bridge, the ADK
agent's outbound tools, the OpenAI backend, and the Apex direct invocable
`A2ALabInvokeAgentEngine.withRider`). The observability harvester
regex-extracts the id back out of each platform's raw logs
(`ObsStore.session_lab_traces()`), and the console's executions table shows
it as an "Experiment" column linking a platform's private session log to
the lab run that caused it.

**Why text, not protocol.** Protocol-level correlation already exists
(X-Trace-Id header on REST, tool argument on MCP, metadata.trace_id on A2A)
but dies at platform boundaries — the D28 incident proved at least one A2A
client silently drops metadata, and no platform propagates a foreign header
into its own execution logs. The message text is the only channel every
platform preserves AND logs, so the trace id travels the same way the
caller identity does: as words in the prompt.

**Honest limits.**
- Foundry's outbound rider is composed into static agent instructions
  (PromptAgentDefinition), so it self-identifies (`caller-platform:
  foundry`) but cannot carry a per-run id — its column can be joined by
  response id (platform_ref) instead.
- The id only appears in the logs of platforms that RECEIVED a delegated
  turn; direct (non-delegated) runs still join via platform_ref only.
- Salesforce session logs lag harvest by minutes-to-hours; the link
  appears when the platform's own pipeline catches up.

**Also in this change-set:** obs platform key renamed `anthropic` →
`claude` (sessions/events/harvest rows migrated in sqlite + Aurora);
executions table gains Experiment / Model / Tokens in-out columns (the
common fields the five platforms' logs actually share: input/output tokens
exist for claude, openai, adk, foundry — never salesforce; a model name is
logged only by openai and foundry); insight file-ref chips now open the
rendered doc via the whitelisted `/api/docs/{name}` endpoint (same popover
as decision chips).

## 2026-07-24 — D35: Lab Guide — the console docent, served as a lab agent

**Decision.** Built the Lab Guide (the plan/07 design, scheduled after
WS3): `src/platforms/guide/` — an `AgentAdapter` whose interior is a
direct Anthropic tool-use loop (Haiku-tier, `GUIDE_MODEL` →
`CLAUDE_AGENT_MODEL` fallback) grounded in the lab's own docs.

- **Grounding split by size:** README + 01-architecture + 02-matrix +
  08-insights (~57KB) are stuffed into the system prompt (Anthropic
  prompt caching makes repeat turns cheap); the long tail — the full ADR
  log, results, runbooks, workstreams, config — sits behind read tools
  (`get_decision`, `read_doc` whitelist) so the model pulls only what a
  question needs. The corpus IS the repo's plan/ discipline; there is no
  separate knowledge base.
- **Curated read tools, not a store surface:** `list_briefs`/`read_brief`
  (the hosted analyst's Aurora output, D23), `list_recent_runs` +
  `get_trace` (merged local jsonl + recent Aurora hops, payloads clipped).
  No SQL — that stays the analyst's. All accessors soft-fail honest.
- **Console chat:** `POST /api/guide` streams SSE (delta/tool/done);
  stateless turns with client-held history; the operator's current view
  rides as a second uncached system block so "explain this" resolves.
  Drawer UI in the header (🧭), suggested questions seeded per section.
- **The meta exhibit:** `serve(adapter, protocol, port)` gives the guide
  REST (:8031), MCP (:8032), A2A (:8033) for free — targets guide-rest/
  mcp/a2a, status native. Two MCP shapes on one server, deliberately:
  `ask` (agent-as-a-tool — the LAB's model runs the loop) and the raw
  read tools (`get_decision`, `get_trace`, … — the CLIENT's model
  reasons over lab data). Generic seam change: `create_mcp_server`
  registers an adapter's optional `extra_mcp_tools` (names stripped of
  their `mcp_` prefix).

**Verified live (2026-07-24):** console SSE answers grounded with ADR
citations; a trace question chained list_recent_runs → get_trace on the
real record; guide-rest cell 6.6s via /api/run; MCP tools/list shows all
seven tools. The guide turns the console from an exhibit into a docent —
and is itself a lab agent whose calls are wire-traced.

## 2026-07-24 — D36: Public console auth — persona passwords in, query-string credentials out

**Decision.** The console's browser surface no longer accepts any
credential in a URL. The public surface is exactly three things: the
static landing page, the persona directory (`/api/users`), and the
password-gated login. Everything else — experiments, Control Panel, Lab
Guide, traces, obs — requires the RS256 persona JWT that login mints,
sent as `Authorization: Bearer`.

- **Per-ROLE passwords from .env** (`A2ALAB_OPERATOR_PASSWORD`,
  `A2ALAB_VIEWER_PASSWORD`): hand colleagues the viewer password; the
  raw `A2ALAB_TOKEN` never reaches a browser. Unset password = that
  role cannot sign in (fail closed). Constant-time comparison; one
  generic 401 (no user/password probing).
- **Query-param auth deleted, not just discouraged**: the live tail was
  the one surface that needed `?token=` (EventSource cannot set
  headers) — replaced with fetch-streaming (same SSE wire format,
  hand-parsed like the Lab Guide chat), so `allow_query_param` is gone
  from the console wrap and a correct token in a URL now 401s.
- **Signed-out UX**: landing page as the public exhibit; interaction
  points (experiment tiles, Lab Guide, Control Panel) funnel to the
  sign-in picker.
- **Unchanged**: the shared token remains the header-borne SERVICE
  credential (matrix.py, bridge→servers, shim); U1's JWT acceptance in
  TokenAuthMiddleware is what makes the persona JWT sufficient
  everywhere.

**Honest limits (the U6 seam):** role passwords are shared secrets with
no per-user revocation and no rotation story — the demo-scale trade,
recorded deliberately. A real IdP federation (Cognito/Entra via
OIDC + RFC 8693 token exchange) is a planned WS6 U6 experiment — as a
measured cell, not invisible plumbing.

**Role model addendum (same day, revised):** viewer = insights, the
observability dashboard read-only, the Lab Guide, experiment Details
tabs, AND the wire traces + live tail (the org serves dummy demo data
only — the wire record is the exhibit); runs, warm-ups, and
harvest/analyze are operator-only. Enforced server-side by a role-gate
middleware in the console (the UI hides what a role can't do, but the
403 is the guard); the header-borne service token carries no user and is
unaffected. This is the console half of WS6 U3 — per-user trace scoping
(operators seeing whose run is whose) remains.

## 2026-07-24 — D37: Anti-pattern remediation pass — what the lab fixed on itself

**Decision.** A colleague's "Headless 360 Anti-Patterns" deck — 34 claims
across security, secrets, OAuth, PII, agent design, and guardrails — was
scored claim-by-claim against the lab's five-platform evidence, backed by
two code audits with `file:line` citations. It produced a backlog of eight
validly-flagged debts (F1–F8). All eight shipped the same day — six in the
first pass, and the two identity fixes that evening, once the assumption
that they were org-side click-ops was checked and found wrong (below). The point of recording it as an ADR: the lab is the thing being
audited, so the remediation is itself a measured result — a "practiced,
not preached" line for the readout deck. The six experiments the audit
also proposed (E1–E6) live in plan/07-workstreams.md; the four insights it
drafted are published in config/insights.yaml (`antipattern-lens`,
`remediation-tax`, `text-rider-legitimacy`, `versioning-not-negotiation`).

**Shipped (all eight — six the same day, F3/F6 later that evening):**
- **F1 — hosted credentials → Secrets Manager.** The two AgentCore
  runtimes and the shim Lambda carried API keys, the Salesforce connected-
  app client id/secret, and the lab bearer token as plaintext environment
  variables on the runtime/function config. `interop.secret_env` ports the
  harvest Lambda's loader (D23): one secret per runtime
  (`a2alab/runtime/{claude,openai,shim}`), a JSON object of env vars,
  resolved at container start from `A2ALAB_RUNTIME_SECRET_ARN`. A no-op
  without that ARN, so local development is untouched; a failed fetch
  raises rather than booting a credential-less runtime. Plain config —
  model names, timeouts, twin ids, endpoints — deliberately stays on the
  runtime description, where it helps debugging. Execution roles get
  `secretsmanager:GetSecretValue` on exactly the one ARN, under
  per-platform policy names (the two runtimes share one role, so a single
  policy name would have each deploy revoking the other's access).
- **F2 — credential scrub in the trace/obs sinks.** Regex redaction of
  bearer tokens, `access_token`, `client_secret`, `sk-…` before write.
  Raw-evidence ethos survives for payload *content*; only credentials go.
- **F4 — versioned MCP ask contract.** Declared output schema for
  `AgentResponse` + a contract version, and defensive validation in
  `AgentRequest.from_dict`.
- **F5 — Agent Engine path → Custom Metadata.** The ADK engine's resource
  path was a compiled-in constant in `A2ALabInvokeAgentEngine`, so
  redeploying the engine meant an Apex deploy — which on this production
  org means a test run at ≥75% coverage. It now reads
  `A2ALab_Setting__mdt.Agent_Engine.Engine_Path__c`, keeping the constant
  as a last-known-good fallback so a missing record degrades to "stale
  target" rather than "no target".
- **F7 — versioned rider grammar.** `rider-version:` in the
  `[A2A-LAB DELEGATION]` block, documented as a mini-spec — which reframes
  the seam from "scraping text" to "parsing a versioned text contract"
  (the honest answer to the A2 critique).
- **F8 — batch guard on both Apex invocables.** Each request costs one
  serial callout of up to 110s, so a multi-request batch would stack past
  the Apex 120s cumulative budget. Batches are refused outright — one
  refusal Response per request, zero callouts — not silently split.

**F3 and F6 — the correction, and then they shipped too (2026-07-24,
same day).** Both were first recorded here as "org config no lab script
owns."
That is wrong, and checking it against Salesforce's own skills library
(`forcedotcom/sf-skills`,
`integration-connectivity-connected-app-configure`) is what corrected it:
ECA configuration is source-controlled metadata across six types
(`ExternalClientApplication`, `ExtlClntAppOauthSettings`,
`ExtlClntAppGlobalOauthSettings`, `ExtlClntAppOauthSecuritySettings`,
`ExtlClntAppOauthConfigurablePolicies`, `ExtlClntAppConfigurablePolicies`),
and this org's `a2a_lab_app` retrieves cleanly. So both were deployable,
not click-ops, and both then shipped:

- **F3 — `RefreshToken` dropped; `Api` kept, deliberately.** Scopes were
  `Api, RefreshToken, Chatbot, SFApiPlatform`. `RefreshToken` was dead
  weight: the app enables only the client-credentials and named-user-JWT
  flows, neither of which issues a refresh token, and no lab code reads
  one (the only `refresh_token` strings in the codebase are F2's scrub
  patterns). Dropped and verified — token mint, an `Api`-scoped Data Cloud
  query, and a full Agentforce consult all still pass. **`Api` stays on
  the shared app by decision, not omission**: the M11 harvest reads the
  agent-session DMOs through `/services/data/vXX/query`
  (`observability/salesforce_source.py`) on the same client-credentials
  token, so dropping it would trade the Salesforce observability column —
  the lab's only window into what the org logs about its own agents — for
  a tighter grant on an app that already only serves the lab. The
  observability is worth more.
- **F6 — four per-caller ECAs, which is where the scope diet actually
  landed.** `a2a_lab_claude`, `a2a_lab_openai`, `a2a_lab_shim` (scoped
  `Chatbot, SFApiPlatform`) and `a2a_lab_obs` (scoped `Api`); the shared
  `a2a_lab_app` stays as the local-development identity. The agent callers
  hit `api.salesforce.com/einstein/ai-agent/v1` and need no `Api` scope at
  all — only the harvest does. **That is the finding worth keeping: with
  one shared app, the grant is the UNION of every caller's needs, so the
  scope diet was never really about the scopes. Separating callers is what
  let each grant shrink.** F1 made the wiring nearly free — every hosted
  seam already had its own Secrets Manager secret, so per-caller identity
  is just a different `SF_CLIENT_ID`/`SF_CLIENT_SECRET` pair inside it;
  the deploy scripts take an optional `SF_CLIENT_ID_<SEAM>` from `.env`
  and fall back to the shared app, so no redeploy can silently revert an
  identity. Remaining human step, once per app: the consumer secret cannot
  be read through the Metadata API — it comes from Setup by hand.

  **The day this actually cost (2026-07-25).** F6 shipped "verified" and was
  not: every new app authenticated, appeared in login history, and had every
  Agent API call refused with a bare 404. What made it expensive was not the
  bug but the diagnosis. Three UI remedies were invented from memory and
  handed to the org admin, none of which existed. The vendor's own setup
  guide, read at last, says two useful things: there is **no app-to-agent
  linking step** at all, and a hand-built External Client App must enable
  **JWT-based access tokens** alongside the client-credentials flow. The
  provisioning template in §11 of the runbooks set `isNamedUserJwtEnabled`
  false. Bisected to be sure: enabling that one flag, with least-privilege
  scopes untouched, turned the shim green — so the scope split documented
  here was correct all along and the 404 was never about scopes. Rule
  earned: read the vendor documentation before describing a vendor's UI.

**What is in source (corrected 2026-07-25):** `ExtlClntAppGlobalOauthSettings`
carries the consumer key and is never committed — retrieved when needed,
deleted after. The `ExtlClntAppOauthSettings` scope files are tracked: they
hold no secret, and the per-caller scope split is the finding. An earlier
version of this paragraph said no ECA metadata was committed at all, which
was wrong on both counts — the scope files had already been committed, and
they are the ones worth keeping. The provisioning recipe is
plan/04-runbooks.md §11.

**Verified (2026-07-24).** F5: deployed to the production org,
`A2ALabInvokeAgentEngineTest` 8/8, 99% class coverage. F1: after redeploy,
no credential-bearing key remains in either runtime's
`environmentVariables` or the Lambda's config — only ARNs, twin ids and
tuning; `matrix.py claude-agentcore openai-agentcore` both PASS (7.3s /
9.9s); `chatgpt-to-agentforce` returns real CRM content over both the
agent-api (18.5s) and a2a-shim (20.7s) channels, which is the path that
actually exercises the moved SF credentials on both seams at once.

**The finding worth keeping** (revised once F3/F6 shipped — the first
version of this paragraph is the mistake): the remediation tax was
asymmetric, but not in the direction either the deck or this ADR's first
draft assumed. The code-side fixes (F1/F2/F4/F7) were hours of ordinary
work inside abstractions the lab already had — one loader module, one
middleware pass. The identity fixes (F3/F6) were deferred here as "org
config no lab script owns", and that sentence was the single most
expensive thing in the audit: it was wrong, it went unchecked, and
checking it took one metadata retrieve. Both shipped the same evening. So
the real asymmetry is not code-versus-platform — it is
verified-versus-assumed. The only irreducible manual step left is one per
app: a consumer secret no API will return, read from Setup by hand.

## 2026-07-25 — D38: Insight sign-off — publishing a claim is a named act, in the console

**Decision.** Insights are the one thing the lab says in public in its own
voice, and until now they went from `config/insights.yaml` straight to the
console and the markdown export with no record of whether anyone had read
them. They now carry an optional `review: required`, and the console's
Insights section grows an **Approve / Request changes** control with a
comment box. Decisions land in `config/insight_reviews.yaml` — diffable,
reviewable, in the repo beside the claims they govern.

Three choices worth recording:

- **`reviewer` is a grant of its own, not a role.** `config/users.yaml`
  gets `reviewer: true` on the lab owner alone. It is deliberately NOT
  implied by `operator`: running an experiment and vouching for a published
  claim are different acts, and the org's other operators (Ana) can do the
  first without the second. The console hides the control for everyone
  else; the 403 on POST is the guard (same shape as the D36 role model).
- **The service token can never approve.** Sign-off requires a verified lab
  JWT with a `sub` — the shared token identifies no person, and "the
  service approved it" answers nobody's question about a published claim.
- **An approval is of WORDS, not of an id.** Each record pins a hash of the
  headline, evidence, advisory, status and refs it approved. Edit the
  insight afterwards and its tile reads *changed since approval* rather
  than carrying the sign-off silently forward. This is the same honesty
  rule as the matrix's status column and the insight `status` legend: the
  lab does not let a stale attestation ride on fresh text.

The five insights from the D37 anti-pattern audit (`antipattern-lens`,
`remediation-tax`, `text-rider-legitimacy`, `versioning-not-negotiation`,
`least-privilege-is-identity`) are marked `review: required` and sit
pending. The markdown export is untouched — sign-off governs what the lab
stands behind, not what it renders.

## 2026-07-25 — D39: One human login — service identities for every other platform

**Decision.** **AWS auth (SSO) is the only interactive human login the lab's
runtime path may depend on.** Every other platform credential is a service
identity whose secret lives in a Secrets Manager secret and is fetched with
that AWS auth. Cloud SDK credentials are constructed **explicitly**; the
convenience chains — `DefaultAzureCredential`, ambient
`google.auth.default()` ADC, anything that can resolve to a developer — are
banned in lab code. `src/observability/credentials.py` is the one place that
implements this, and it refuses when unconfigured rather than falling back.
The rule is written into the workstream rules (plan/07) so it binds every
future platform, not just the two that prompted it.

**Why now.** The Foundry observability harvest passed locally and failed
hosted with `InsufficientAccessError` for over a week, and both results were
correct: `DefaultAzureCredential` walks a chain, found Ryan's `az login` on
the laptop and the Entra service principal in Lambda — which had never been
granted `Log Analytics Reader` on the workspace. Nothing was misconfigured
in a way any test could catch, because the green local run was proving that
a *human* had access. The same pass found the hosted harvest had never
registered ADK or Foundry at all, so Aurora held zero rows for two of five
platforms while the local store looked complete.

Three choices worth recording:

- **`override=True`, deliberately opposite to `interop/secret_env.py` (F1).**
  That module's `setdefault` lets an explicitly-set variable win, which is
  right for a hosted runtime an operator may need to poke at. For harvest
  credentials the secret is the single source of truth: a stale value in
  someone's `.env` silently beating the managed one is precisely the drift
  D37 was about.
- **Failures name the principal.** An access denial that does not say WHICH
  identity was refused sends you to re-check the role you already granted on
  the identity you already verified. The client id is not a secret; the
  harvest now reports it on failure.
- **Registering an obs source is half the job.** A source in
  `scripts/obs_harvest.py` with no matching entry in the Lambda's map and no
  client library in `deploy/obs/build_zips.sh` reads as "blocked" hosted
  forever while local looks healthy. The obs rule in plan/07 now says both.

New service identities created under this decision: `a2alab-obs-harvest`
(GCP, `logging.viewer` + `monitoring.viewer`) and `Log Analytics Reader`
scoped to the single `a2a-lab-logs` workspace for the existing Entra SP.
Least privilege by construction, per D37's F3/F6 lesson — the grant follows
the caller, not the union of callers.

Scope note: `.env` remains the *deploy-time* source for these values
(`deploy/obs/deploy_harvest.sh` reads it to populate the secret, and the ADK
container's Entra SP is deployed from it). What changed is the *runtime*
path — no process authenticates as a person.

## 2026-07-26 — D40: Cross-cloud agent identity — federate, never key

**Context.** The ADK fan-out orchestrator (WS8) runs inside a Vertex AI Agent
Engine container and must reach three clouds: another Agent Engine (Google),
Foundry's incoming A2A (Azure), and a Bedrock AgentCore runtime (AWS). The AWS
leg is the hard one and not by accident — AgentCore's data plane is SigV4-only,
with **no public HTTP endpoint**, so unlike the other two there is no
bearer-token path to fall back on. The container has to hold an AWS identity.

**Decision.** No cloud credential is ever placed in an agent container as a
long-lived secret. Outbound identity is obtained one of two ways, and
`src/interop/cloud_auth.py` is the only place either is constructed:

1. **A service principal the platform already scopes** — Azure gets an explicit
   `ClientSecretCredential`, never `DefaultAzureCredential` (D39's rule; a chain
   that can find a developer login proves a human has access, not the service).
2. **Identity federation** where the destination cloud supports it — AWS trusts
   `accounts.google.com` natively, so the container mints a Google-signed OIDC
   token for its own service account and trades it at STS for one-hour
   credentials. No IAM OIDC provider to register, no key to rotate, nothing
   durable in the container.

`deploy/adk/provision_aws_federation.py` creates the AWS half: a role whose
trust policy pins one Google subject and one audience, granting
`bedrock-agentcore:InvokeAgentRuntime` on the two lab runtimes and nothing else.

**Consequences, and the three things that actually cost time.**

- **A target's NAME is not its ADDRESS.** Shipping `A2ALAB_LEG_*_TARGET` to the
  container without the `${VAR}`s those targets expand from produced empty
  endpoints, and an empty endpoint fails as a *network* error. Config absence
  and connectivity failure are indistinguishable downstream; the deploy manifest
  must ship both.
- **Same cloud is not same permission.** The GCP→GCP leg 403'd. Agent Engine
  runs as a Google-*managed* service agent holding
  `reasoningEngineServiceAgent`, which cannot query a sibling reasoning engine.
  "It's all Google" bought nothing; the grant is explicit
  (`roles/aiplatform.user`) like any other.
- **AWS remaps Google's audience claim, and says nothing when you get it
  wrong.** Service-account tokens carry `azp`, and per the IAM condition-keys
  reference that makes `accounts.google.com:aud` match **azp** while the
  audience lands on `accounts.google.com:oaud`. The intuitive policy — pin
  `:aud` to the audience you requested — fails with a bare `AccessDenied` that
  names no key and is indistinguishable from a wrong `sub` or a propagation
  delay. Pin `:oaud` + `:sub`.

**Corollary that generalises past this decision: an error must have a path out
that does not pass through a model.** These failures first reached us through
the orchestrator's synthesiser, which faithfully paraphrased
`InvalidConfigError: ... CA bundle` into *"a technical error accessing its
tools"* — true, unactionable, and unfixable. Leg markers now also print to
container stdout. Anything you intend to debug from cannot be relayed by an LLM.

Applies to every future platform: a new agent that must call another cloud gets
a federated identity or an explicitly-constructed service principal, added to
`interop/cloud_auth.py`, never an access key in `env_vars`.

---

## 2026-07-26 — D41: The fan-out legs become remote MCP tools, and the model schedules them

**Context.** The CMA fan-out orchestrator's three business units were one
`custom` tool. On Managed Agents that means the HOST executes it, which cost two
things WS7 exists to recover. It needed a laptop attached to the session for the
whole run — the same constraint as the D16 async brief, and the reason WS7 lists
a hosted watcher. And it was not agentic in any load-bearing sense: with a single
tool the model's only decision is *when* to fan out; the order and the
parallelism were `asyncio.gather` in our code. Writing "call unit 1, then unit 2"
is a program with a language model in it.

**Decision.** Each business unit is its own tool on a **remote MCP server**
(`src/fanout_mcp/`, a Lambda behind API Gateway — the D23/D28 pattern, since the
org SCP denies `lambda:AddPermission` and rules out Function URLs). MCP tools
execute on Anthropic's orchestration layer, so the session needs nothing
attached, and the model chooses which units to call, in what order, and whether
to issue them together.

**The host-side variant is kept as a deliberate control, not deprecated.** Same
agent id, two tool inventories selected per run through `agent_with_overrides`
on `sessions.create` — a tool inventory is not something a prompt can change.
One agent rather than two on purpose: two would drift, and the experiment's
claim is that the only difference between the runs is where the tools execute.

**Measured on the first live run** (trace `ede9e3bc`, session `sesn_01VrSn52`):
the model issued **all three units in a single turn** — `parallel — turn 1:
consult_logistics + consult_commercial + consult_customer_operations` — 3/3
units, 50.5s wall, and it reported its own coverage correctly ("Coverage: 3 of
3 units") with every claim attributed to the unit that made it.

That last part was the open question. The host-side tool ends its result with
`[fan-out coverage: n/3]`, computed by code that knows how many legs exist;
three independent tools have no such vantage point, so nothing but the model
knows whether it called all three. We stated the roster in the prompt and asked
it to reconcile — and it did. **Coverage accounting survived being moved from
code into the model, on this model, with the roster stated.** That is a
measurement, not a property to assume, and the exit code checks it rather than
trusting the prose.

**What it costs.** The legs now run inside an HTTP request/response, so they
inherit API Gateway's **29s integration timeout** — not raisable for HTTP APIs
(AWS's >29s support covers Regional and private REST APIs only; a quota request
is filed). Per-leg budget is 25s through this path against 120s host-side. Warm
legs measure 1–13s and fit; a cold platform does not, and is reported unavailable
by the existing partial-failure contract. **Moving a tool from the host to the
orchestration layer imposes a request budget on work that previously had none** —
worth stating plainly, because nothing about the MCP protocol says so.

**Also settled here: AWS→GCP federation** (`interop/cloud_auth.py`). The Lambda
holds no Google key; google-auth signs a `GetCallerIdentity` request with the
function's ambient role, Google replays it at AWS STS, then impersonates a
service account. This is D40 in the mirror, and the asymmetry is the finding:
AWS trusts `accounts.google.com` natively and needs one role, while Google needs
five objects — pool, AWS provider, attribute mapping, attribute condition,
impersonation binding — before it will trust AWS at all. "Keyless federation"
costs very different amounts depending on direction, and Google's side is where
the identity is *shaped* rather than merely accepted.

---

## 2026-07-26 — D42: Chips have two tiers — the vendor tier names who operates the cloud

**Context.** The console's platform chips grew one per tag with no rule about
what a chip *is*, and the inconsistency showed on the vendor tier: `aws`,
`openai`, `ms-foundry` and `google-adk` name companies or their clouds, while
`claude` names a model. It read fine in isolation and wrong in a row.

**Decision.** Two tiers, one rule. **Tier 1 names who operates the cloud** —
`anthropic`, `openai`, `google-adk`, `ms-foundry`, `aws`. **Tier 2 names which
product or model runs on it** — `managed-agents`, `agent-sdk`, `agent-engine`,
`gemini`, `gpt-5-mini`. So the vendor chip reads **anthropic**, not `claude`;
Claude is a model and belongs on the tier where the lab already distinguishes
Managed Agents from the self-hosted SDK.

The argument that settles it is the mirror: picking `claude` for tier 1 commits
you to `gemini` for Google's, which nobody would defend. The same test applies
to marks — **Google ADK and Agent Engine wear the Google Cloud mark, not the
Gemini spark**, because a model's mark on the vendor tier is the same category
error in pictures.

**Scope.** Display only. `claude` remains the tag id in `config/scenarios.yaml`,
the CSS class, and the hue — the rename lives in one `CHIP_LABELS` map, so no
config, no styling and no test moved with it.

**Chips and marks are different instruments, and the subsection headers take
marks.** A chip is a labelled category; a vendor mark is recognition at a
glance. The Experiments group headers ("Claude - Agentforce") get the same
inline marks the experiment cards already use — the pair is legible from the
title itself, so a row of chips restating it was redundant furniture. Marks
follow the same tier rule as labels: the platform name takes the operator's
cloud mark, the model name takes the model's. One exception, by observation
rather than rule: `AWS` earns a mark only in "AWS Strands", never bare — the
"Claude (AWS)" in a scenario title says where the Claude end is hosted, and a
mark there competes with the Anthropic one two words earlier.

**Applies to new platforms.** A new platform contributes a tier-1 chip naming
its operator and tier-2 chips for whatever product and model it runs — not one
chip carrying both.

---

## 2026-07-27 — D43: Environment identity is configuration, not content — and the API is the boundary

**Context.** D39 made AWS SSO the lab's only human login and put every platform
credential in Secrets Manager. One file never complied: `.env` — every
platform's keys, the account ids, the project ids, in plaintext, on one laptop.
Separately, the repo is public and named the employer-provided AWS account, its
SSO profile, and the SSO domain, which is the string that actually identifies
the company.

**Decision.** Four changes, taken together because any one of them alone makes
the others more dangerous.

1. **`.env` moves into Secrets Manager** (`scripts/env_sync.py pull|push|diff`,
   secret from `A2ALAB_ENV_SECRET`). Onboarding is clone → `aws sso login` →
   `pull`. `.env.example` stays the checked-in contract; only values move, and
   only to identities IAM already admits.
2. **No environment identifier is hardcoded anywhere in the repo** — account
   ids, project ids, subscription/resource names, SSO profile names. Explicitly
   including `${VAR:-default}` fallbacks, which are hardcodes that only reveal
   themselves on someone else's machine. Shell uses `${VAR:?set VAR in .env}`.
3. **Every AWS deploy proves its target account** (`deploy/aws_preflight.sh`,
   sourced by every script that calls the AWS CLI). Removing the account label
   makes a wrong-account deploy *easier*, so the guard shipped with the scrub.
4. **The console's public surface publishes no identifiers.**
   `/api/scenarios` and `/api/targets` are unauthenticated and carried vendor
   console deep links containing the Salesforce my-domain, the GCP project and
   an Azure tenant id. Links now resolve for signed-in callers only.

**History was rewritten**, not just HEAD: `git filter-repo` over 10 commits,
then a force-push. That was affordable **only because the repo had one clone, no
forks and no CI** — recorded as a fact about this repository, not a
recommendation. The durable control is the boundary check, which runs over
`git ls-files` and therefore fails before a push, while removing an identifier
is still an edit rather than a rewrite.

**What this cost, honestly.** Four judgment calls, all reversible:
`A2ALab_GCP.externalCredential-meta.xml` is now gitignored (it embeds the
project id in a service-account email and cannot read `.env`), so that
deployable metadata is no longer version-controlled; the GCP project id remains
in git history because only the AWS identifiers were rewritten; component deep
links are invisible to the public landing page; and `.env` now has two homes
that must be kept in step, which `diff` exists to make cheap.

**The distinction worth carrying forward:** the same identifier can be
load-bearing in one place and decoration in another. `?tid=` on a portal URL is
a sign-in hint the browser does not need — stripped. `AZURE_TENANT_ID` in
`.env` authenticates the Entra service principal — kept. The test is whether
anything stops working without it, and answering that is the whole job.

---

## 2026-07-27 — D44: Report consumption in the units the vendor bills, and let the agent explain the movement

**Context.** WS9's telemetry section answered "what did the lab cost to build",
and answered it wrong in a way nothing could catch. The harvest had stored four
token buckets since it was written — uncached input, cache read, cache creation,
output — and `/api/build-telemetry` returned two of them. One harvested day
rendered as *120K input tokens* on a workload that had processed *4.42M prompt
tokens*: a 36x understatement, on exactly the workload shape (long agent
sessions, heavy caching) the section exists to measure. Nothing errored. The
number was smaller than reality and entirely plausible.

**Decision.** Three parts, one principle — *report in the units the vendor
bills, and never in units it does not*.

1. **Four buckets, never one.** The API's unit of account is not "tokens"; it is
   four separately-billed categories. `input_tokens` is the **uncached
   remainder**, not the prompt — the prompt processed is `input + cache_read +
   cache_creation`. They bill at roughly 1x, ~0.1x, and 1.25–2x, so a summed
   "tokens" figure cannot be multiplied by a rate. The console shows four tiles
   and a composition bar; `token_note` travels in the payload so no consumer can
   render them without the reason they are separate.
2. **Cost per unit of work is the engineering number; price per unit is a
   contract.** The dollar column is a client-side estimate at list price, not an
   invoice, and on a subscription it is not money that changed hands. That
   caveat is one constant (`BUILD_TELEMETRY_COST_NOTE`) with two consumers, so
   there is nothing to drift.
3. **WS12's cost sentinel is a scheduled Managed Agent** — the first thing in
   this lab that passes all three of the tests the credential analyst failed
   (D22 postscript): it needs **tools** (the delta is a SQL question against the
   obs store, which already has a hosted MCP server), it can genuinely be
   **scheduled** (collection runs in the harvest Lambda, not on a laptop), and
   it needs **state** (week-over-week comparison). Its job is to explain the
   *movement*, not to report the figure — the tables already do that.

**Consequences.** Briefs share `lab.obs_briefs` with the observability analyst,
separated by a `kind` column rather than a second table — one store, one reader,
one migration; `save_brief` defaults to `observability` so the deployed nightly
agent, whose prompt predates the column, keeps working unchanged. The sentinel
ships **paused**, like every scheduled deployment here, because a firing bills a
real session. The console's Coding Agents Telemetry section gains the Run /
Details tabbar that Credentials established, rather than a third layout.

**The transferable part is not the dashboard.** It is that the honest answer to
"how much will this cost" separates units-per-unit-of-work (measurable,
improvable, ours) from price-per-unit (contractual, timing-dependent, not ours),
and that a number is only decision-grade when its label means what a reader
assumes. `input_tokens` meant something different from "input" and cost this
project a 36x error that no test, log or alert would ever have raised. Written
up for presentation in `build-notes/claude/10-consumption-and-list-price.md`.

**Addendum, 2026-07-30 — moved from weekly-paused to daily-running.** The
shipped cadence was weekly (`0 7 * * 1`) and paused, so in practice no brief ever
fired on the schedule and the console's newest cost brief was the 2026-07-28
manual one — which read as "not provisioned" to a demo viewer even though the
deployment was healthy. It is now **daily** (`0 7 * * *`, America/New_York) and
**resumed**. The trade-off is explicit: one billed session per day rather than
one per week. That is accepted because the point of the panel is a *fresh* read
each morning, and the sentinel's own rules make a quiet day a two-line brief, so
most firings are cheap. The schedule was changed in place via
`deployments.update` (no recreate — the deployment id, vault and run history are
preserved) and the agent prompt was revised to lead with day-over-day rather than
week-over-week (agent v2). Pause/Resume and an ad-hoc Run now live as buttons in
the console's Coding Agents Telemetry section, reading the deployment's live
status so the control reflects reality rather than the local state file.

---

## 2026-07-27 — D45: The credential store covered one file; the other twenty needed a different answer

**Context.** D39 made AWS SSO the only human login and D43 moved `.env` into
Secrets Manager. Both are about `.env`. Neither touched the rest of the lab's
secret-shaped local state: `.a2alab/lab_jwt_private.pem`, the Cloudflare origin
private key, `f6-eca-wiring.md` (four live Salesforce consumer keys), the two
`*_mcp.json` files that carry bearer tokens, and — outside this repo — nine
other projects' `.env` files and the machine's SSH key. All single-copy, on one
laptop. D43's own build note called the same-machine backup script a stopgap
"while a real answer (chezmoi, or a private dotfiles repo) is chosen."

**Decision.** chezmoi, with age encryption, into a private dotfiles repo
(`~/.local/share/chezmoi`); the tooling and the discovery workflow live in a
second private repo, `~/projects/chezmoi-dotfiles-setup`. Secrets are encrypted
at rest in the repo, so a repo compromise is not a credential compromise. A
launchd job re-adds tracked files daily.

**This adds a second root of trust, deliberately.** D39's rule was one human
login; the age private key is a second one, held in 1Password. That is the
price of covering material AWS SSO cannot bootstrap — including the SSH key
used to reach the very repos that would hold it. Nothing moved *out* of Secrets
Manager: `.env` still lives there, and the encrypted mirror is a second copy,
not a replacement.

**What the classification pass found, which is the transferable part.** Deciding
encrypt-vs-plain required reading contents rather than filenames, and filenames
were wrong four times:

| File | Reads as | Actually holds |
|---|---|---|
| `fanout_mcp.json`, `obs_mcp.json` | MCP endpoint config | live bearer tokens |
| `bridge_host.json`, `cloudflare/acm_arn.txt` | hostname / cert reference | ARNs carrying the AWS account id |
| `f6-eca-wiring.md` | wiring notes, header says "Secrets are NOT here" | four production consumer keys |

Each was true-by-its-own-description and misleading in the way that matters for
handling. Fixed at the source: `deploy_fanout.sh` now says the file it writes is
a secret, and `f6-eca-wiring.md`'s header no longer reads as low-sensitivity.

**And the audit reproduced the thing it was auditing.** The findings write-up
stated the AWS account id in full. It sits in `tmp-docs/` — gitignored, so no
exposure — but `tests/unit/test_no_account_identifiers.py` walks `git ls-files`
and structurally could not have flagged it. The guard's scope is tracked files;
the document describing the cleanup was outside it. Redacted, and recorded here
because D43's fourth piece is the same shape one layer out: **a boundary check
only covers the surface it is pointed at.**

**Operational limit, stated because it will bite.** The daily sync runs
`chezmoi re-add`, which picks up *content changes to already-tracked files
only*. A brand-new secret under `.a2alab/` is silently unprotected until someone
runs `chezmoi add --encrypt` once. Nothing in this repo detects that; the
discovery workflow (`chezmoi-tracking/to-track.md`) is the process answer, and
it is a process answer, not a control. Written up in
`build-notes/claude/09-secrets-and-environment-identity.md`.

---

## 2026-07-27 — D46: A build step that produces an artifact must also own shipping it

**Context.** WS12 was recorded as "code-complete, not provisioned" — one setup
script away from done. Provisioning it found three separate gaps between *built*
and *running*, none of which had failed anything:

1. **The obs MCP zip had no deployer.** `deploy/obs/build_zips.sh` built
   `a2alab-obs-mcp.zip`; `expose_mcp.sh` created the API Gateway and never
   pushed code. The function had been running a hand-deployed bundle from
   2026-07-24, so `src/obs_mcp/tools.py` had been writing the briefs `kind`
   column for a day against a deployed function that knew nothing about it.
2. **The schema migration had no execution path at all.** `observability.pg.DDL`
   gained the `kind` ALTER, and `ensure_schema()`'s only caller —
   `pg_backfill.py` — connects as `lab_writer`, while `lab.obs_briefs` is owned
   by the master role. Every DDL statement failed with `must be owner of table`
   (42501), and the caller caught it and printed *"assuming provisioned"*. The
   column existed in the model, in the writer, and in the reader; it never
   existed in Aurora. `pg_backfill.py` had also been unable to write **rows**
   since the reader/writer secret split — it was running `from_env()`, which is
   `lab_reader`.
3. **The PromQL grant was a comment, not a policy.** `lambda_handlers.py` said
   the harvest role "needs cloudwatch:ListMetrics + GetMetricStatistics". The
   role had neither, and the named pair is wrong besides — AWS documents
   `cloudwatch:GetMetricData` + `cloudwatch:ListMetrics` for the PromQL
   `QueryMetrics` operation. Meanwhile the deployed harvest zip predated
   `coding_source.py` entirely. Aurora held **zero** coding rows while the
   console's local view showed the exporters working perfectly.

**The shared failure mode is that each gap reported success.** The zip built.
The backfill printed row counts. The harvest returned `status: blocked` with the
friendly *"no coding metrics yet — switch the exporters on"* detail, which is
what an unauthorized 403 looks like when the caller assumes empty rather than
forbidden. Three green paths over a feature that did not exist end to end.

**Decision.**

1. **Every artifact a build step produces has a script that ships it.** The
   `--code` half of `deploy/obs/expose_mcp.sh` exists now. A `build_*.sh` with no
   corresponding push is an unfinished deploy path, not a convenience.
2. **DDL is owned by `scripts/pg_migrate.py`, which connects as the table
   owner** (`A2ALAB_PG_MASTER_SECRET_ARN`). `pg_backfill.py` moves rows and no
   longer calls `ensure_schema()` at all. The three identities keep the
   privileges they should have — reader reads, writer writes rows, owner
   reshapes tables — and the migration stops depending on the one identity that
   cannot perform it.
3. **A permission a comment claims is a permission a script grants.** The
   `a2alab-obs-promql` role policy is applied by `deploy/obs/deploy_harvest.sh`
   on every mode, because the failure it prevents is silent.
4. **Do not catch a schema error and call it "assuming provisioned".** That line
   is what converted a hard failure into a year-shaped lie. If a migration
   cannot run, it fails.

**Postscript, same day: fixing (1) immediately exposed a fourth instance.** The
first rebuild of the harvest bundle in two days shipped a `foundry_source` that
now reaches `interop.cloud_auth` (the explicit Azure SP credential, D39) —
a module `build_zips.sh` copies no part of, because the packaging list was
written before `cloud_auth` existed. The import is lazy and inside a function,
so the bundle loaded fine and five platforms reported `ok` while Foundry alone
recorded `No module named 'interop.cloud_auth'`. **A hand-curated packaging list
is itself config that rots**, and it rots silently in exactly the direction this
ADR describes: the artifact keeps building, and only the platform that needed
the missing file notices. Worth remembering that the deploy which *found* this
had been unable to find it for two days, because nothing was pushing the zip.

This is D37's rule — *config no script owns is config nobody updates* — reaching
the two places it had not: build artifacts and database schema. The rule holds
for both, and the tell is identical. **The half that runs on someone else's
computer is the half that rots**, because the local half keeps passing.

---

## 2026-07-27 — D47: Fire-then-poll is a first-class client shape, and the runtime decides whether it helps

**Context.** D41 measured the fan-out legs inheriting API Gateway's 29s
integration timeout — 25s per leg against 120s host-side — and the workaround
was to keep legs fast and report the rest as unavailable. WS11 proposed using
A2A's asynchronous half instead: `SendMessage` MUST return immediately, and
processing MAY continue afterwards.

**What building it found first: there was nothing to build on the server.** The
workstream named `AdapterExecutor.execute()` awaiting the adapter inline as the
blocker. That was wrong. The a2a-sdk's `DefaultRequestHandler` already computes
`blocking = not params.configuration.return_immediately` and, when non-blocking,
returns after the first Task event while a tracked background task drains the
event queue into the task store. The executor is its own producer task, so the
inline await never held the response. **The lab had implemented the asynchronous
lifecycle, switched it on, and then never asked for it** — every call left
`return_immediately` unset. That is the published `a2a-async-at-heart` finding
with a sharper edge: the sync default is not a protocol limitation, and it is
not even a server limitation. It is one unset field on the client.

**Decision.**

1. **`submit()` + `poll()` are first-class on `A2AClient`, alongside `ask()`.**
   Not a replacement — `ask()` stays blocking, because every existing leg and
   every matrix latency number would otherwise change meaning silently.
   `tests/e2e/test_a2a_async.py` asserts both: submit must return inside 0.75s
   against a 2s adapter, and `ask()` must still take the full 2s.
2. **The async half is measured per platform, not read off an agent card.**
   `scripts/a2a_async_probe.py` discriminates on `submit / total`. Recorded:
   the hosted shim and both Foundry agents implement it; **Agent Engine is
   submit-only** — it returns a task in 826ms that no tried shape can read back
   (`GET /tasks/{id}` → *"A2A version '0.3' is not supported by this handler"*).
   Recorded as *cause unresolved*, since Google documents a get-task method;
   the operational consequence is unambiguous either way, so Agent Engine legs
   keep using `ask()`.
3. **A polling client on a freeze-between-invocations runtime must poll
   steadily, not back off.** On Lambda the background consumer gets CPU only
   while an invocation is in flight. Submitting and staying quiet for 45s left
   the task WORKING past the ~30s the work takes; it completed after 12 further
   polls at t+67.7s, against 31.1s when polled steadily. **Backing off starves
   the work you are waiting for** — the inverse of ordinary polling advice, and
   a trap for any future orchestrator prompt that tells a model to "check back
   later".
4. **Fire-then-poll on Lambda is not production-shaped until the task store is
   durable.** `InMemoryTaskStore` lives in one warm container and nothing routes
   a later poll back to it. The ceiling is genuinely gone; the durability is
   genuinely missing, and the second is not a reason to understate the first.

**The general result.** The protocol's asynchronous half moves the agent's
runtime off the HTTP request, which is what a request/response gateway actually
constrains — 31.1s of work with a 1.18s longest request, under a 29s limit. But
*where* that background work runs decides whether it progresses on its own. An
always-on host (Foundry, the Fargate bridge) computes while nobody is watching;
a scale-to-zero function does not. **The protocol dissolves the request ceiling;
it does not conjure compute.** Same shape as D41's finding that where the tool
runs decides who orchestrates — here, where the *task* runs decides whether
asynchrony buys anything.

## 2026-07-28 — D48: A hosted service that can serve without its credential must refuse to start

**Context.** WS13 item 1 put the console on ECS Fargate behind the bridge's
ALB. The deploy script was written 2026-07-28 from the proven
`deploy/bridge/deploy_bridge.sh` and, per D46, was explicitly recorded as
*written, NOT run*. It ran clean on the first attempt: image pushed, secret
created, listener rule added, service stable, `/healthz` 200 through the ALB
with a Host header. Every check the runbook asked for passed.

The console was nonetheless serving **every** `/api` surface to an
unauthenticated caller, and accepting a deliberately wrong bearer token.

**Why the checks missed it.** Three independent gaps composed, and none of them
is visible from a healthy container:

1. `deploy_console.sh` wrote `a2alab/runtime/console` to Secrets Manager and
   passed `A2ALAB_RUNTIME_SECRET_ARN` on the task definition — but the console,
   unlike the bridge, never called `load_secret_env_and_log`. The secret was
   created, shipped, and never read. A perfect D46 repeat in a new shape: the
   artifact existed, the wiring did not.
2. `TokenAuthMiddleware` treats an absent `A2ALAB_TOKEN` as *auth is off*. That
   is right on a laptop — the alternative is making local development need AWS —
   and catastrophic behind a public load balancer.
3. Verification used a *valid* token and got 200. A valid token proves nothing
   when the failure mode is that all tokens are accepted. **The test that finds
   this is the negative one**, and it costs one extra curl.

**Decision.**

1. **Fail closed on the hosted path.** If `A2ALAB_RUNTIME_SECRET_ARN` is set
   (the container's own statement that it is hosted) and `A2ALAB_TOKEN` is
   still unset after the secret load, the console exits rather than serving.
   The middleware's open-when-unset behaviour is deliberately left alone: the
   fix belongs where "am I hosted?" is knowable, not in the shared middleware.
   `tests/unit/test_console.py` asserts both halves — hosted-without-token
   exits, local-without-token still starts.
2. **Verify auth with the negative case.** A deploy is not verified until an
   unauthenticated request and a wrong-credential request have both been shown
   to fail. Recorded here because the positive test passed on a wide-open
   console and read as success.
3. **Env derivation cannot see through a constant.** The task-definition env is
   derived by scanning `os.environ["LITERAL"]` in `src/`. `observability/pg.py`
   reads `os.environ.get(SECRET_ARN_ENV)`, so `A2ALAB_PG_SECRET_ARN` was never
   shipped and `PgClient.configured()` was False — `/api/obs/briefs` said so
   outright while `/api/traces` just returned `[]`. Aurora vars are now set
   explicitly, as `deploy_bridge.sh` already did. A heuristic that silently
   under-collects needs a loud consumer, not more regex.
4. **Secret-shaped names are excluded by rule, not by list.** The enumerated
   exclusion predated `SF_CLIENT_ID_OBS`, `SF_CLIENT_SECRET_OBS` and
   `A2ALAB_FANOUT_MCP_TOKEN`, so all three landed in cleartext on the task
   definition — the exact exposure the runtime secret exists to prevent, and
   the same defect is still live on the bridge (F1 follow-up). Now any
   `SECRET|TOKEN|KEY|PASSWORD` variable is treated as sensitive unless it ends
   `_ARN`, since an ARN names a secret rather than being one.

**Consequence.** The first hosted run of a component is not a formality that
confirms a written script; it is the first execution of code paths no test
covers, and it should be scheduled as work. D46 said building is not
deploying. This says deploying is not verifying.

## 2026-07-28 — D49: One store for observability, and one place that chooses it

**Context.** Asked why the hosted console's Observability section was empty, the
honest answer was that it had never been reading the store the lab writes to.
Two selectors existed with **opposite defaults**:

- `scripts/obs_harvest.py` chose Postgres only when `A2ALAB_OBS_STORE=postgres`,
  defaulting to sqlite. That variable was commented out in `.env`, so **local**
  harvests filled `traces/lab.db`.
- The hosted harvest Lambda sets it, so **its** harvests filled Aurora.
- The console's `_obs_store()` returned `ObsStore()` — sqlite — unconditionally,
  ignoring the variable entirely.

So the dashboard rendered the laptop's copy (382 sessions) while the
authoritative one filled up unseen (479 sessions), and the two drifted for as
long as both harvests ran. Nothing errored. The numbers were simply the wrong
ones, and no test could see it because the divergence lived in configuration.

Hosting the console turned the silent version loud: a container has no
`traces/lab.db`, so the section was empty rather than stale.

**Decision.**

1. **Postgres is the source of truth** for the observability section — storage,
   the dashboard, and the analysis briefs the Managed Agent writes. `sqlite`
   remains selectable (`A2ALAB_OBS_STORE=sqlite`) for working on a harvested
   snapshot with no AWS session, and remains the fallback when Postgres is not
   configured so a fresh checkout still runs.
2. **One function chooses.** `observability.make_obs_store()` is the only
   selector; the console and `obs_harvest.py` both call it. Two call sites with
   two defaults is what caused this, and the fix is not "set the variable" —
   it is having one place where the question is answered.
3. **`PgObsStore` grew the read side it never had**: `summary`, `list_sessions`,
   `list_events`, `session_callers`, `session_lab_traces`, `lab_traces_for`.
   It had only ever implemented the harvest's write path plus briefs and state,
   which is why `/api/obs/briefs` worked while everything beside it was empty —
   that one endpoint reached for `PgObsStore` directly and bypassed the
   selector.
4. **Two of the six cannot be written the obvious way.** The RDS Data API
   refuses any result over **1 MB**:
   - the rider joins matched ~3.6 MB of `raw_json`, so extraction moved **into
     SQL** (`substring(... from ...)`) and only the short captured value crosses
     the wire. The Postgres and Python patterns are asserted equal by a test,
     because two dialects of one rule is exactly the shape that drifts.
   - one session's events measured **2.43 MB**, so `list_events` pages itself,
     with the page size derived from the widest row in that session rather than
     guessed — payload sizes differ by two orders of magnitude between
     platforms.

**Consequence.** The hosted console now reports 5 platforms, 200 sessions, 35
caller riders and 29 lab-trace riders, all from Aurora. The general lesson is
narrower than "use one store": a fallback that silently succeeds is worse than
one that fails, because it produces a plausible answer from the wrong source.
Both of this decision's fixes are really the same fix — make the choice once,
and make the wrong choice impossible rather than merely unlikely.

## 2026-07-28 — D50: A sign-off is durable or it is not a sign-off

**Context.** Insight approvals were written to `config/insight_reviews.yaml`,
beside the claims they govern, where they are diffable — the right home while
the console ran on a laptop. Hosting it made that file a layer of the container
image: a sign-off made in the deployed console was written to a filesystem that
does not survive a restart, and it never reached the repo. No error, no trace,
and three insights were sitting in `review: required` waiting to be approved.

An approval is a named human act on a public claim (D38). Losing one silently is
the worst available failure for it — worse than refusing to record it, because
the reviewer believes the job is done.

**Decision.** `lab.lab_state` is the store when Aurora is configured; the file
remains the store on a laptop and the diffable artifact in the repo.
`scripts/insight_reviews_sync.py pull|push|diff` moves the record between them —
`pull` after signing off, then commit.

Two details that are the point rather than incidental:

1. **The store write is not wrapped in a swallow-everything.** Every other
   Aurora read in the console soft-fails to a local source, correctly. A write
   must not: a green tick over an unsaved approval is the exact bug this exists
   to fix, so a broken store surfaces as a 500.
2. **An explicit path is never answered from the store.** The sync script
   compares the two copies; if a caller naming a file got the store's answer,
   `diff` would compare the store with itself and always report "in sync".

## 2026-07-28 — D51: Eleven faces, one process, addressed by path

**Context.** Nine cells in `config/targets.yaml` pointed at `localhost:80xx` —
Claude and OpenAI's MCP/A2A servers, the Lab Guide's three, both Agentforce
shims. Inside a container `localhost` is the container, so every one failed from
the hosted console and the lab still needed `run_local.sh` on a laptop to
exercise a protocol comparison. This was the last runtime dependency on the
operator's machine, and the operator's requirement was that there be none.

**Decision.**

1. **One process, not nine services.** Every face is an ASGI app that
   `interop.adapter.build_app()` already returns without running a server, so
   nine Fargate tasks (~$80/month) would have bought nothing over one. It also
   sidesteps ECS's limit of **five target groups per service**, which nine
   separately-addressed faces would have hit — a constraint that would have
   forced two services purely to satisfy the addressing scheme.
2. **Paths, not nine hostnames.** Host-based routing is what the console uses
   and would have worked. But each hostname is a DNS record a person creates by
   hand, so it is nine records and nine listener rules against one of each, for
   no behavioural difference. The faces answer at
   `https://<faces host>/<target-name>/...`, and the mount prefix IS the target
   name so a failing cell maps to a URL without a lookup table.
3. **The hosted twins are the same objects.** Same adapters, same `build_app`,
   same auth middleware — only the address differs. A face that behaved
   differently hosted would make the protocol comparison meaningless, and the
   comparison is the lab's subject.

**What the work actually cost, which was not the mounting.** Starlette's `Mount`
does **not** run a mounted app's lifespan, and FastMCP starts its
streamable-HTTP session manager there. Every MCP face resolved its route
perfectly and then answered `RuntimeError: Task group is not initialized`. The
parent app now enters each sub-app's lifespan explicitly, reaching through two
of our own ASGI middlewares to find the object that owns it. The lesson
generalises past MCP: **mounting an ASGI app moves its routes, not its
startup** — and the failure appears only on a real call, never on a route check.

The A2A cards needed the same kind of care for the opposite reason: they
advertise an *absolute* URL that a client calls back, and a mounted app cannot
infer its own prefix. They are told their public origin through the same
variable `targets.yaml` expands, so the address advertised and the address
clients are sent to cannot drift.

## 2026-07-28 — D52: The watcher is a loop, so it is a service — not the Lambda the plan assumed

**Context.** WS13 item 3 was written as "hosted watcher — EventBridge Lambda
servicing Managed Agents custom tool calls". It was the last runtime dependency
on the operator's laptop: Anthropic's cron fires a brief session autonomously,
the session then **stalls** awaiting the result of a host-side
`save_account_brief` tool (Salesforce credentials never enter the managed
sandbox, D16/D27), and something has to be watching to service it. That
something was `python -m briefs --watch` inside `scripts/run_local.sh`.

**Decision — a small ECS service, reusing the faces image.** The plan's shape
was assumed before anyone looked at the work. Three things argued against the
Lambda:

1. **The work is a poll loop**, not an event. EventBridge would have imposed a
   schedule on something whose natural form is `while True: poll; sleep`.
2. **It would have needed a third zip** carrying the Anthropic SDK, httpx and
   the Salesforce client — another bundle to build, ship and keep in step,
   which is precisely what D46 is about. The faces image already contains this
   code and every dependency it needs.
3. **It serves nothing**, so it needs no ALB, no target group, no listener rule
   and no ingress at all — a security group with egress only. ~$4/month at 0.25
   vCPU.

So the watcher is the faces image with a different command. Recorded because
the plan's guess survived unexamined into three documents, and the cost of
following it would have been a whole packaging path built to satisfy a word.

**The two file dependencies that actually made it laptop-bound**, neither of
which the plan mentioned:

- `.a2alab/brief.json` — the provisioned ids. Configuration rather than secrets,
  so the environment now supplies them and wins over the file.
- `.a2alab/brief_state.json` — **the set of sessions already serviced**. This is
  the one that matters. In a container it dies with the task, and the next poll
  re-delivers every brief still listed in recent deployment runs: duplicate
  `A2ALab_Account_Brief__c` records in a **production org**. It now lives in
  `lab.lab_state` (D50's table), and the write is deliberately not soft-failed —
  a lost write means a double delivery, so it must surface.

**Verified on first run:** the hosted watcher loaded four credentials from
Secrets Manager, attached to deployment `depl_01C6…`, and immediately picked up
a scheduled session that had been idling with no watcher — which is also the
proof of the design's forgiveness: nothing is lost while it is down.

## 2026-07-28 — D53: Excluding a credential is not relocating it, and the issuer holds the key

**Context.** The hosted console rejected every login with "wrong user or
password", for both personas, using the passwords in `.env`. Two independent
faults, and the second was hidden behind the first.

**Fault 1 — the passwords were deleted, not moved.** D48 replaced the deploy
scripts' enumerated secret-exclusion list with a rule: any
`SECRET|TOKEN|KEY|PASSWORD` variable is kept off the task definition. That is
right, and it correctly caught `A2ALAB_OPERATOR_PASSWORD` and
`A2ALAB_VIEWER_PASSWORD` — which no previous list had named. But the *other*
half of the pattern is the secret's `keys = [...]` list, and it was not widened
to match. A credential excluded from the plain environment and added to nothing
simply ceases to exist. The container held no passwords, and
`identity.authenticate` compared the supplied one against `""`.

**Fault 2 — the issuer had no signing key.** `identity.py` had an env route for
the **public** half of the lab JWT keypair and none for the private, on an
explicit and reasonable premise: *containers have no keypair and must never hold
the signing key*. That premise holds for a seam that only **verifies** a token.
It does not hold for the console, which **issues** them at `/api/login`.

With no private key in the environment, `_private_key()` fell through to
`ensure_keypair()`, which generated a fresh RSA pair into the container's own
ephemeral filesystem. The container then signed with that key and verified
against the configured public one. The observable behaviour is the worst
available: **login succeeds and returns a token**, and every subsequent request
401s, with `InvalidSignatureError` swallowed two layers down by a deliberate
never-raise in the auth middleware.

**Decision.**

1. **Both halves of the keypair travel in the runtime secret**, and
   `_private_key()` reads `A2ALAB_JWT_PRIVATE_KEY` first. The rule is now: a
   seam that only verifies gets the public half; **an issuer gets both**.
2. **A pattern-based exclusion must be paired with the relocation.** Whenever
   `is_secret()` starts catching a new variable, that variable belongs in the
   secret's `keys` list in the same change. The two lists are one mechanism.
3. **Failing closed on the credential is better than failing closed on the
   request.** `ensure_keypair()` generating a key for a container is a silent
   substitution of a wrong answer for a missing one. It is left in place for
   local development, where it is correct and convenient, and the hosted path
   now supplies the key explicitly.

**Why it took three attempts to see.** Each fix was deployed with
`--skip-build`, which rewrites the task definition and the secret but **not the
image**. So the environment was right and the code reading it was a build old —
including `PRIVATE_KEY_ENV`, which did not exist in the running image at all.
The diagnosis only landed after running the deployed image locally and finding a
`lab_jwt_private.pem` the container had written for itself. This is now a
convention in CLAUDE.md and a row in plan/10-operations.md: **when a fix does not
take effect, check the image before re-reading the code.**

**Consequence.** `plan/10-operations.md` exists as of this decision — the
procedures for rotating these credentials had no home, and the reason a rotation
needs a redeploy (the per-seam secret is built by the deploy script, not by
`env_sync`) is worth writing down next to the procedure rather than rediscovering.

## 2026-07-29 — D54: The harvest button fires the Lambda that already does the harvest

**Context.** The console's Harvest button reported
`Harvest failed: SyntaxError: Unexpected token '<', "<!DOCTYPE "...`. That is
not a harvest error — it is a browser parsing an HTML page as JSON. Measured
against the load balancer directly: `POST /api/obs/harvest` returned **HTTP 504
in 120.18s**, the ALB's own timeout page. A full sweep takes longer than the
ALB's 120s idle timeout.

Raising the timeout does not fix it. Cloudflare's proxy limit sits below the
ALB's, so the ceiling would simply move to one the lab does not control, and the
same failure would return as a 524. **A request that cannot finish inside the
front door's budget is the wrong shape, not the wrong setting** — the same
conclusion WS11/D47 reached about A2A.

**Decision.** `/api/obs/harvest` invokes the **existing** `a2alab-obs-harvest`
Lambda with `InvocationType: "Event"` and returns `started_at` immediately; the
console polls `lab.obs_harvest` for a `last_harvest_at` newer than that. The
in-process sweep stays for local development, where nothing is timing the
request out.

**Delegating fixed two faults nothing else had caught**, and they are the
better argument than the timeout:

1. **The console harvested four platforms; the Lambda harvests six.** Foundry
   was never in the console's `sources` dict at all — despite the comment above
   it saying "the five agent platforms". The button claimed "harvested from all
   platforms" and never touched Foundry.
2. **ADK could not work from the console at all.** It failed with
   `DefaultCredentialsError`, because the GCP service-account key lives in the
   harvest secret and is materialised by `observability.credentials.prepare()`
   — which the Lambda calls and the console does not. The console container has
   no Google identity for Cloud Logging, so that cell was never going to
   succeed however long the request was allowed to run.

**Consequence.** The button now does less work in the web app, not more: the
console asks the component that owns the job to do it. The general rule is
worth keeping — *when a hosted seam already performs a job on a schedule, a UI
that wants it on demand should invoke that seam, not reimplement it in the
request path.* The reimplementation had silently drifted to two-thirds of the
platforms and one broken credential.

## 2026-07-29 — D55: A mode remap must not change what an experiment IS

**Context.** The operator asked whether the two Claude↔Agentforce experiments
were still using the **Managed Agent**, or had quietly become the self-hosted
AgentCore twin. They had.

`config/targets.yaml` carried `modes.hosted: claude-rest → claude-agentcore`.
That was correct when written: the protocol faces ran only on a laptop, so in
hosted mode AgentCore was the *only* reachable Claude. Hosting the faces (D51)
made it wrong the same day, and nothing failed — which is the problem.

Proven from a trace hop rather than argued: `claude-rest` resolved to
`claude-agentcore (agentcore-http)` and the response carried
`"raw": {"backend": "sdk"}`. So `claude-to-agentforce` (titled *Claude Managed
Agent → Agentforce*) and `claude-aws-to-agentforce` ran **the same backend**,
while the entire purpose of the pair is to compare Managed Agents against
self-hosted. The lab was showing two cells of one thing and calling it a
comparison.

**Decision.**

1. **Hosted mode maps a face to its hosted TWIN, never to a different
   implementation.** `claude-rest → claude-rest-hosted` and
   `openai-rest → openai-rest-hosted`, both served by the faces container,
   which runs `CLAUDE_BACKEND=managed`. The AgentCore cells keep their own
   scenarios and their own names.
2. **A mode entry may change an address. It may not change a backend, a
   platform, or a protocol.** Those are what the experiment *is*; the mode
   switch is about where it runs. A remap that crosses that line converts an
   honest matrix into a false one silently, because every cell still passes.
3. Two tests pin it: hosted mode may not send the Managed Agent cell to the
   self-hosted runtime, and every remap must name targets that exist.

**Verified after the fix:** `claude-rest` reports `backend=managed`,
`claude-agentcore` reports `backend=sdk`.

**The lesson is about timing, not YAML.** This remap was right for three weeks
and wrong from the moment a better option existed. Nothing re-examines a
workaround when the thing it worked around goes away — so when a capability
lands, the compensations made in its absence are part of the change.

## 2026-07-29 — D56: One table, two authors, and the reader must say which one it wants

**Context.** The operator read the Observability section's analysis brief and
asked why it only discussed **coding-agent telemetry** and nothing about the
lab's own experiments. It was not the observability analyst's brief at all. It
was the **cost sentinel's**, and build cost is exactly its subject.

`lab.obs_briefs` is deliberately one table with a `kind` discriminator — WS12
settled that rather than adding a second table and a second migration. The cost
endpoint (`/api/cost-brief`) filtered on `kind='cost'` from the start.
`/api/obs/briefs` called `list_briefs()` with **no kind at all**, so it returned
whatever was newest across both authors. The moment the sentinel wrote a brief,
the Observability panel started showing it.

**Two failures compounded, and the second is the one worth remembering.**

1. The panel rendered another agent's work under its own heading.
2. **It hid the real state.** The observability analyst's last brief was
   **2026-07-18** — eleven days earlier — because the analyst is a *paused*
   deployment with **no cron**, run only on demand. An empty panel would have
   said so. A panel showing somebody else's fresh brief said the opposite.

**Decision.**

1. `/api/obs/briefs` asks for `kind='observability'`, importing the constant
   from `observability.pg` rather than restating the string.
2. **Both panels name their author and their subject in the header**, and each
   says the other exists. When two producers share a store, a heading that says
   only "Analysis brief" is ambiguous by construction — the reader cannot tell
   whether they are seeing the wrong brief or the right one saying something
   surprising.
3. A test pins the filter.

**The wider point, which is why this got its own decision.** A shared table with
a discriminator is a good design and the lab keeps it. What it costs is that
**every reader must be explicit** — an unfiltered read is not a neutral default
but a silent choice to show whatever arrived last. That failure mode is
invisible while only one producer is writing, and appears the day the second
one does, in a place nobody was looking at that moment.

Recorded alongside `plan/09-deployment-map.md` **L5.7**, the inventory of
scheduled and long-running processes, which exists because this is the second
time in two days that something's *state* — paused, unscheduled, or quietly not
running — was invisible until a person asked a direct question about its output.

## 2026-07-29 — D57: The console canvas template — a thing, and what is behind it

**Context.** The console had grown seven canvas types (`experiment`, `obs`,
`insights`, `arch`, `build`, `creds`, `trace`) with two different ideas of
structure. Experiments had it right — a **Run** tab showing the thing and a
**Details** tab explaining the call path, the planned narrative and the real
agent assets. Everywhere else, provenance was either missing or crammed into
the same pane as the content.

That is not a cosmetic problem. This lab's subject is *how these platforms
actually behave*, and a panel that shows a number without saying where the
number came from is the exact failure the honest matrix exists to prevent. Twice
in two days a reader could not tell what they were looking at: a cost brief
rendered under an observability heading (D56), and an empty dashboard that meant
"paused agent" but looked like "no data".

**Decision — every area of the console follows one template.**

1. **Nav → canvas.** A section is reached from the Control Panel sidebar and
   owns the main canvas. One `view.type` per section.
2. **Top tabs name the THINGS in that section**, as peers. Observability is
   `Dashboard | Observability Analysis | Cost Analysis` — not one "briefs" tab
   with kinds nested inside it. If two things are produced by different
   processes, they are peers, because nesting implies one is a facet of the
   other and readers believe it.
3. **Every tab carries a `Details` sub-tab**, the shape experiments already
   used. The canvas shows **the thing**; Details answers **where the thing came
   from**:
   - what produces it (which agent, script, Lambda or schedule — by name),
   - how it reads its inputs and with what identity,
   - where the output is stored (table, column, bucket),
   - what BOUNDS it — the limitation that stops it saying more,
   - and the caveat that must travel with the numbers, if there is one.
4. **Details is markdown**, so `linkifyDecisions` turns `D<n>` and `plan/*.md`
   into chips for free. **Cite real refs** — the mechanism renders only
   references that exist, and an explanation citing nothing produces no chips.
5. **An empty state explains itself.** "No briefs in the last 7 days — the
   analyst is a paused deployment, so it only runs when you press Analyze" is
   information. A blank panel is a bug report waiting to be filed.
6. **A tab opens on its content, never on its Details.** Switching tabs resets
   the sub-tab; Details is a question the reader chooses to ask.
7. **State is `view.type` + a per-section tab + a per-tab sub-tab**, and the
   render cache key includes all three — or switching a sub-tab silently shows
   the previous pane.

**What this is really enforcing.** The lab publishes claims, and a console that
shows results without provenance is asking to be trusted rather than read. The
Details pane is where a visitor finds out that OpenAI has no list-executions
API, that the cost figure is a client-side estimate at list price, that the
analyst has been paused since the 18th. Those are the interesting facts. The
template exists so that adding a section means being asked, structurally,
"and where does this come from?"

**Applies to new work.** A new area of the console starts as: one `view.type`,
peer tabs for its things, a Details pane per tab, and an empty state that says
why. Recorded in CLAUDE.md as the rule to follow rather than a thing to
rediscover.

---

## 2026-07-29 — D58: The board is generated from the plan, one way

**Decision.** The Jira board (`A2A` — a **space** in the current UI, still a
`project` in the REST API and JQL, which is why `JIRA_PROJECT_KEY` reads the way
it does) is **generated** from
`plan/07-workstreams.md` by `scripts/jira_sync.py`, in one direction only. The
plan stays the source of truth for scope; `plan/00-decisions.md` stays the source
of truth for reasoning; Jira is a delivery *view*. Nothing reads Jira back into
the repo, and `plan/11-delivery.md` records the mapping.

**Why one way.** Two-way sync sounds like the generous option and is the
expensive one: it makes a status editable in a place the repo cannot see, and
the console renders the plan, not the board. A workstream marked done in Jira
and nowhere else would show as done to a person looking at the board and open to
everyone looking at the lab — which is the drift this project already spends a
CLAUDE.md rule and a sweep workflow trying to prevent. One direction means the
board can be wrong only by being stale, and the fix for stale is re-running the
importer.

**What the importer refuses to do**, because each refusal was a real temptation:

- **No issue per ADR.** There are 58 of them and a decision is not a unit of
  work. They ride as `adr-D<n>` labels, so a closed story still names the
  decision that justified it.
- **No sprints.** A sprint is a time box, a workstream is a scope box, and
  fourteen one-workstream sprints would describe a cadence that never existed.
- **No stories invented from prose.** The plan records work in two shapes — a
  work-items table (WS13–15) and a statused numbered line (WS1–WS12) — and
  narrative sections are left alone. Eight workstreams therefore import as epics
  with no stories, several of which shipped. Splitting their prose into tickets
  would manufacture granularity the work never had.
- **No epic closed by reading English.** An epic closes only when it has stories
  and all of them are done. "Everything that does not need AWS is done" (WS9)
  and "PROVISIONED … exit criteria are not met yet" (WS12) both contain the word
  *done*, and neither means finished. Shipped-but-narrative workstreams stay
  open, which is the safe direction to be wrong in: an open epic carrying its
  verbatim status invites a read, a wrongly-closed one buries what is left.

**Credential.** A user API token, not the organization admin key that was tried
first — the admin key authenticates to the org admin API rather than Jira's
issue API, and administering an Atlassian organization is far more authority
than writing issues needs. The board records what one person did, so it is
authored as that person (D39: one human login, everything else a service
identity).

**Result 2026-07-29.** 15 epics, 46 stories, 40 closed. The genuinely open work
is one lab item (WS1.5) and the two operator actions in WS14 that need a human
with Entra directory-admin and GCP project-IAM rights — on the board precisely
so they stop being invisible.

## 2026-07-30 — D59: Collect the coding agent's logs for behaviour, not content

**Decision.** Extend the WS9 coding-agent telemetry from metrics to the OTLP
**logs** signal (and later the beta **traces** signal), exported to the same
telemetry CloudWatch account the metrics already reach (a separate account from
the lab's hosting, pinned by `AWS_PROFILE` in `.env`; never named here, D39), and
derive a second class of
insight from them — edit-acceptance rate, tool mix, per-request latency,
reliability, prompt cadence — that the eight aggregate metrics cannot express.
The whole path runs with every content flag **off**: `OTEL_LOG_USER_PROMPTS`,
`OTEL_LOG_TOOL_DETAILS`, `OTEL_LOG_TOOL_CONTENT`, `OTEL_LOG_RAW_API_BODIES` and
`OTEL_LOG_ASSISTANT_RESPONSES` all stay unset. This is WS16.

**Why the metrics are not enough.** The metrics answer *how much, by whom, on
what* (D44 made the four token buckets honest). They cannot answer *what the
collaboration looked like* — how often the human accepted the agent's edits,
which tools and MCP servers actually did the work, how each model's latency
*felt*, what failed and retried. Those live only in the per-event log stream,
and the single most valuable of them — **edit-acceptance rate**, from the
`claude_code.tool_decision` event's `decision` attribute — is not derivable from
any aggregate the metrics expose. "The lab measuring its own construction" (WS9)
is a cost story today; the logs make it a behaviour story.

**Why content-off is a stronger posture than redaction, not a weaker one.** The
temptation was to store prompts and tool content in CloudWatch for private
retrieval and merely decline to *surface* them. Verified against the Claude Code
monitoring docs, that trade-off is unnecessary: the content flags default to off,
and off means the text is **never emitted** — not emitted-then-masked. The event
still carries `prompt_length`, `tool_name`, `decision`, `duration_ms`, the token
counts and `status_code`; it omits the prompt text, file contents, tool
arguments and raw API bodies. **Every insight above is computed from metadata
that ships regardless of the flags**, so the insight set and the content question
are fully independent — the dashboard is complete with nothing sensitive ever
leaving the laptop. CloudWatch Logs *can* mask matched patterns on ingest, but
that is field-masking after the content already crossed the wire, audited users
can unmask it, and it only fires on patterns you predicted. Source-side omission
has nothing to unmask because nothing was sent. Chosen: store none.

**The consequence is that the publish/store division stops being a discipline and
becomes structural.** WS9 keeps a rule that the console shows derived aggregates
and never raw platform payloads. With content-off there is no raw content
*anywhere* in this pipeline — not in CloudWatch, not in `traces/lab.db`, not in
the console — so the harvest ETL cannot leak what was never transmitted. The
division is enforced by the absence of the data, not by remembering to withhold
it.

**Shape follows the existing two layers, and the split is the point.** The
metrics reader (`src/observability/coding_source.py`, D54's harvest Lambda) reads
aggregates back over PromQL and lands them in `lab.db`; the logs reader is its
sibling — deterministic ETL that reads events, computes the aggregates, and
writes **only aggregates** to new tables. The interpretation layer (the obs
analyst D22/D23, the cost sentinel D44/WS12) stays optional and on top: the
dashboard tiles are computed in the ETL and need no agent, and a behavioural
brief ("edit-acceptance fell on Bash edits this week") is the analyst reading
those tables, added last. The metrics/analyst division of D22 holds unchanged.

**What is not yet proven, and gates the build.** Two facts are unverified and
WS16's Phase 0 is a hard gate on them, in the spirit this subsystem was burned by
twice already (the metrics reader that used `ListMetrics` and found nothing; the
Codex path that exported to the wrong signal for days): (1) the CloudWatch OTLP
**logs** ingestion endpoint, where the data lands, and the read-back API — I
could confirm the Claude Code event schema against the docs but not the AWS logs
endpoint; and (2) whether the existing metrics bearer token
(`a2alab/telemetry/cw-metrics-api-key`) authenticates logs at all. The build note
established a metrics token is metrics-only (its "failure #2"), and
`otelHeadersHelper` returns **one** header set for every OTLP signal — so if logs
needs a separate credential, one helper cannot serve both without putting a
static per-signal token on disk, which D39 forbids. Nothing is configured or
coded until a real POST-then-query round-trip against the telemetry account
answers both. **Traces**
(the `claude_code.llm_request` span's `ttft_ms`, the only home for
time-to-first-token, and the `interaction` turn tree) are a separate signal
behind the `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA` flag and are sequenced after
logs for the same one-signal-at-a-time reason.

## 2026-07-30 — D60: The DevOps project view renders from the repo and only links out to Jira

**Decision.** The console gains a **DevOps** category. Under it sit the
coding-agent telemetry (WS9/WS16) and a new *This A2A Lab Project* section that
surfaces the delivery process — how workstreams and ADRs are authored locally and
generated into the Jira board. That section renders from the **plan and repo**
(`plan/07-workstreams.md`, `plan/00-decisions.md`, `plan/11-delivery.md`,
`build-notes/**` via `/api/docs`) and **only links out to the Jira space; it
never reads the board back into the console.** This is WS17.

**Why, and why it is D58 restated rather than a new idea.** D58 made the board a
one-way delivery *view* generated from the plan, because a status editable in a
place the repo cannot see is drift the console would then render as truth. A
console panel that queried live Jira would reintroduce exactly that: the console
renders the plan, so a board state that disagreed with the plan would put two
different answers in front of one viewer. Rendering the project section from the
same source `jira_sync.py` reads — the plan — means the console, the board and
the importer cannot disagree, because they derive from one file. The Jira link is
a launch point for a human, not a data feed.

**What this refuses, matching D58's list.** No read-back of issue status, no
live counts pulled from the Jira API, no "sync from Jira" button. The board
counts the section shows are computed from the plan the same way the importer
computes them; if they look stale, the fix is re-running `jira_sync.py`, not
teaching the console to read Jira. The one thing that crosses to Atlassian is a
hyperlink built from `JIRA_SITE_URL`.

**Shape.** Both sections follow the canvas template (D57): a thing with a
`Details` sub-tab that names its source docs and states the one-way rule, so a
viewer clicking Details learns that the project view is generated from the repo
and why nothing reads the board back.

## 2026-07-30 — D61: A third supplier orchestrator on Agentforce, which fans out by delegating the fan-out

**Decision.** Build the WS8 supplier-disruption scenario a **third** time, with
the orchestrator an **Agentforce agent authored in Agent Script** (D14):
`salesforce/.../aiAuthoringBundles/A2ALab_Supply_Orchestrator`. It coordinates
the **same three business-unit agents** the Managed Agents and ADK variants use
— Logistics on ADK/Agent Engine (`adk-logistics-a2a`), Commercial/Legal on
Foundry (`foundry-commercial-a2a`), Customer operations on the OpenAI agent on
AgentCore (`openai-agentcore`) — and writes one consolidated brief. The three
orchestrators now span one axis: **who owns the concurrency** — a host tool
(Managed Agents), a declared graph (ADK `ParallelAgent`), and here a **serial
Apex callout budget that constrains it**.

**The topology toggle (`af_topology`), delegated by default.** Agentforce's only
GA outbound is Path A — an Apex callout through the lab bridge — and Apex
callouts are serial, batch-capped at one, inside a single transaction's ~120s
cumulative budget. So the run offers two topologies, picked per run via an
injected `[A2A-LAB ROUTING]` `fanout-topology:` block (the D28 routing-block
pattern, alongside `af_channel`/`af_route`):

- **DELEGATED** (default, and the one that completes): the Agent Script makes
  **one** Apex callout to the bridge's new `fanout:supplier-disruption` route.
  The bridge runs all three legs **concurrently off-platform** — the same
  `orchestration.dispatch()` the Managed Agents host tool runs — and returns the
  three sections plus a coverage line. The orchestrator only synthesises.
- **SERIAL** (the constraint demo): the Agent Script calls its per-leg action
  three times. Two ~110s callouts already threaten the 120s budget; three
  overrun it, so a real three-leg run **degrades by design** — a later leg is
  abandoned mid-turn (HTTP 200, heading present, content silently gone, the
  exact failure measured 2026-07-25). The partial-failure contract reports it as
  an unavailable leg rather than letting a degraded run read as complete.

**Why reuse the three legs rather than build twins.** The trace layer, the D27
delegation rider, and a distinct caller id (`agentforce-orchestrator-via-bridge`)
already separate this orchestrator's runs from the other two in the console and
the native logs. Dedicated twins would triple the cross-cloud deploy surface to
buy only native-log attribution the caller id already provides. So the legs are
shared; only the orchestrator is new.

**Why no new Apex and no new Named Credential.** Both topologies reuse the
existing `A2ALabInvokeRemoteAgent` invocable and the `A2ALab_Bridge` Named
Credential. Delegated is one call with target `fanout:supplier-disruption`;
serial is three calls to the leg targets. The only new server code is the
bridge's `fanout:` verb (`src/bridge/app.py` `_fanout()`), which validates the
scenario, enforces `delegation.allowed()` at the next depth, and dispatches.

**What it measures, and the claim it supports.** That a platform whose native
outbound cannot fan out at all can **still** participate in a 1:many topology —
by delegating the parallelism to a seam that has it — and exactly what the
on-platform serial path costs when it tries to do the fan-out itself. The claim
is WS8's: an enterprise needs one protocol and an honest account of what
crossing platform boundaries costs, here the cost of orchestrating from a
serial-outbound platform. See plan/07-workstreams.md WS8 and plan/09.
