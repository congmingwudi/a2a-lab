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
