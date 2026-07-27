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
