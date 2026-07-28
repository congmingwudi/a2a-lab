# Protocol matrix

Statuses are honest: `native` (platform speaks the protocol itself),
`via-bridge` (Agentforce REST callout → bridge → protocol), `via-shim`
(protocol → shim → Agent API), `blocked-beta` (native path exists but gated).
`scripts/matrix.py` runs every runnable cell and appends results to
plan/03-results.md.

## Path A — Agentforce → Claude

| Cell | Status | How |
|---|---|---|
| REST | **via-bridge** | Apex `A2ALabInvokeRemoteAgent` → bridge → `claude-rest` |
| MCP | **via-bridge** | same Apex action; bridge target switched to `claude-mcp` — no SF redeploy |
| A2A | **via-bridge** | bridge target `claude-a2a` |
| MCP (native SF MCP-client action) | **blocked-beta** | Salesforce native MCP actions are gated beta; ask the AE — a prod org may qualify. If access lands, add a `native-mcp` cell |
| A2A (native) | **blocked** | no native A2A client in Agentforce |

## Path B — Claude → Agentforce

| Cell | Status | How |
|---|---|---|
| Agent API (REST) | **native** | `ask_agentforce` tool → `AgentforceClient` → Agent API directly |
| MCP | **via-shim** | lab harness MCP client → `agentforce-mcp` shim (:8021) → Agent API (no Claude component speaks MCP to the shim — the Claude agent's own consult uses the Agent API or A2A channels) |
| A2A | **via-shim** | Claude as A2A client → `agentforce-a2a` shim (:8023) → Agent API |

Claude backend note: each Path B cell can run under `CLAUDE_BACKEND=managed`
(CMA custom tool, host-side execution) or `sdk` (in-process SDK MCP tool) —
record which backend produced each result row.

## Local loopback cells (protocol plumbing proof, no external platforms)

| Cell | Status |
|---|---|
| RestClient → claude-rest | native |
| McpClient → claude-mcp | native |
| A2AClient → claude-a2a | native |

## Path C — Agentforce ↔ OpenAI (M9)

Same table shape as Paths A/B with `openai-*` targets; OpenAI has no A2A —
our wrapper serves it (still recorded `native` for the *serving* since we
host the agent, with a note that the platform itself lacks A2A).

## Cross-cloud — Google ADK ↔ Microsoft Foundry (WS3 capstone)

The lab's only cell with **no Agentforce in it**, and its only native×native
cross-hyperscaler pair: GCP-hosted Gemini (Vertex AI Agent Engine) calling
Azure-hosted gpt-5-mini (Foundry Agent Service) over both platforms' own A2A
endpoints — no bridge, no shim, no lab component in the cross-cloud leg.

| Cell | Status | How |
|---|---|---|
| ADK → Foundry (A2A) | **native × native** | Agent Engine A2A in; the GCP container's `ask_foundry_agent` tool calls Foundry's incoming A2A. Measured 16.9s end to end (2026-07-23, plan/03-results.md) |
| Foundry → ADK (A2A) | **blocked-auth** | Foundry connections cannot mint Google IAM tokens. Not a protocol failure — an identity one, and recorded as such |

Scenario: `google-adk-to-foundry` (`config/scenarios.yaml`, group `cross-cloud`).

**Why this section had to be added separately (2026-07-25):** Paths A, B and C
are each shaped as "X ↔ Agentforce", so a cell involving neither end had no
place in the matrix and was absent from it for two days while being live in the
console, recorded in results, and drawn in the README diagram. The matrix's
structure had quietly encoded the assumption that Agentforce is always one end
of the call — worth noting before adding more cross-platform cells, since the
same gap will reappear for any many-to-many topology.

## Considered and declined (cells we chose not to run, and why)

A lab that records which experiments it declined is more honest than one that
runs everything — and the reasoning is itself a finding about how to test
interop.

- **Claude (AgentCore) ↔ OpenAI (AgentCore), both directions — declined
  2026-07-25.** Same SDK-shaped adapter, same AWS account, same runtime
  (Bedrock AgentCore), same inbound auth (SigV4), same transport
  (`agentcore-http`). The only variable left is the model vendor, which is not
  a protocol-interop variable at all. The loopback suite already proves the
  three client×server pairings deterministically, and the cross-vendor
  comparison worth having was already measured without an A2A cell between
  them: identical runtimes, ~31s vs ~56s cold start, warm p50 ~10.3s vs ~8.4s
  (the `sdk-footprint` insight). **The general rule:** a same-runtime,
  same-cloud, same-auth pair tests the runtime, not the interop. Cells earn
  their place by isolating a variable no existing cell isolates.
- **Claude (AgentCore) ↔ Google ADK — NOT declined, scheduled (WS8).** Kept
  because it isolates a variable no shipped cell isolates — but **the reason
  narrowed on 2026-07-26, and the original one is now stale.** It was justified
  as crossing an AWS ↔ GCP boundary "that no current cell crosses" and as
  turning the one-directional GCP→Azure capstone into a three-cloud pattern.
  Both have since been done and measured: the ADK fan-out orchestrator reaches
  Google, Azure **and** AWS from one GCP container with no long-lived
  credential in it — GCP→AgentCore over SigV4 via Google-OIDC→STS federation,
  3/3 legs, 16.8s wall (D40/D41; plan/03-results.md, trace `802a9a3b`).
  The **AWS→GCP direction is also measured** as of 2026-07-26 (D41): the
  fan-out MCP Lambda holds no Google key, federates its ambient IAM role into a
  GCP service account, and calls `adk-logistics-a2a` on Agent Engine — 3/3 legs
  in two recorded runs (traces `ede9e3bc` 50.5s, `161d7a46` 42.6s), plus a
  1.2s leg through the hosted bridge. This paragraph said that direction was
  unmeasured until 2026-07-28; it was written before D41 landed and then not
  revisited, which is the failure mode this ledger exists to catch.
  What remains genuinely unmeasured is narrower still, and still worth a cell:
  an **AgentCore runtime** (rather than a Lambda) as the AWS caller, and a
  **direct vendor-to-vendor A2A pairing** between the two agent runtimes rather
  than an orchestrator calling hosted legs.

## Findings ledger (grow as measured)

- MCP has no protocol-level session semantics — session_id rides as a tool
  argument; A2A's `contextId` is first-class. (design-time finding)
- Managed Agents first-turn latency includes per-session container
  provisioning — **measured 2026-07-25**: provisioning costs ~2s (cold 5.2s
  p50 vs warm 3.2s), and cold managed still beats the warm self-hosted sdk
  backend (11.7s p50) by more than half. The agentic turn loop dominates,
  not the hosting; managed's p50→p95 is flat while both sdk columns fan out.
- Real Agentforce action timeout: **measured 2026-07-25 at ~85–90s**, not the
  ~60s previously reported (84.7s used, 89.7s abandoned). Failure is silent:
  the Agent API returns 200, the twin still writes its external-research
  heading, and the body reads "External research is temporarily unavailable"
  — every abandoned turn cost the full ~100s wall regardless. A caller
  checking status codes, or grepping for the heading, scores these as
  successes.
- Sync vs async delegation (D15/D16, measured): the sync collaboration turn
  (Agent API + Apex CRM + bridge + Claude) lands in 27–36s — viable but the
  timeout chain caps research depth; async managed sessions run 69–127s
  unbounded and deliver into CRM records instead of a waiting response.
  CLAUDE_ANSWER_TIMEOUT_S=40 was too tight once Claude's turn contained an
  Agent API round trip (raised to 100; upstream chain still governs sync).
- Salesforce Metadata API gotchas (D15/D16): new custom fields deploy with
  NO field-level security for any profile (grant via permission set, assign
  to the API run-as user); NamedCredential metadata has no calloutOptions
  wrapper and HttpHeader params need sequenceNumber (D15).
- Anthropic Managed Agents gotcha (D17): scheduled deployments (the
  platform-native cron) are immutable — archive + recreate to change
  accounts/cron.
- Observability harvest (M11, first live run 2026-07-17): CMA delivered 50
  sessions / 1043 events (thinking, tool_use, per-request token usage —
  2.09M tokens aggregated locally; no platform-side aggregation API).
  Salesforce STDM started half-provisioned — a state worth naming: the
  `ssot__AiAgentSession__dlm` DMO *entity* exists (invalid columns get clean
  parse errors) but any valid query dies with UNKNOWN_EXCEPTION — DMO shells
  ship with Data Cloud licensing, the query runtime only materializes once
  Session Tracing/audit collection is enabled in Setup (enabled during the
  same run: queries went live in ~10 min and the first pull delivered 3
  sessions / 9 interaction events). OpenAI: nothing to pull by design
  (traces write-only). The coverage panel renders each platform's live
  harvest state.
- Anti-pattern remediation pass (D37, 2026-07-24): all eight self-audited
  debts shipped (F1 hosted credentials → Secrets Manager, F2 trace
  credential scrub, F3 scope diet, F4 versioned MCP ask contract, F5 Agent
  Engine path → Custom Metadata, F6 per-caller ECAs, F7 versioned rider
  grammar, F8 Apex batch guard). No matrix cell changed status — every fix
  was internal to a seam the matrix already claimed, which is itself the
  finding: the honest-status discipline held under an audit it did not
  anticipate.
- Hosted observability is not the local harvest with a bigger disk
  (measured 2026-07-25): the Aurora store looked healthy while holding zero
  ADK and zero Foundry rows, because the harvest Lambda registered neither
  source and bundled neither client library — and Foundry in particular
  *passed locally for the wrong reason*, since `DefaultAzureCredential`
  resolves to the developer's Azure CLI login on a laptop and to a service
  principal in Lambda (which lacked `Log Analytics Reader`). The same pass
  found 823 "orphaned" Salesforce events that were a data-model error, not
  stale data: `SELECT FIELDS(ALL)` caps at 200 rows, and step rows reach
  their session only through an interaction — while STDM writes the literal
  string `"NOT_SET"` for unset foreign keys, so a heuristic column match
  filed every step under a session id that does not exist. Repaired to zero
  orphans without deleting a row. Two general findings: a credential chain
  that can fall back to a human tests nothing about production, and
  "orphaned records" is a hypothesis about a schema, not a verdict on data.
- Scope diet, the real shape of it (F3/F6, measured 2026-07-24): the
  shared External Client App held `Api, RefreshToken, Chatbot,
  SFApiPlatform` because that was the UNION of four callers' needs, so
  auditing the scope list was never the fix. `RefreshToken` was simply
  dead (client-credentials and JWT-bearer flows issue none; no lab code
  reads one) and dropped. `Api` is load-bearing for exactly ONE caller —
  the M11 harvest's Data Cloud DMO reads through `/services/data/vXX/query`
  — and is kept deliberately: least privilege is not worth the org's only
  window into its own agent logs. Splitting the callers into per-app
  identities (F6) is what actually shrank the grants: the three agent
  callers reach only the Agent API and now carry `Chatbot, SFApiPlatform`
  with no `Api` at all. Least privilege was an identity-modelling problem
  wearing a scope-configuration costume.
- **Speaking A2A and implementing its async half are different claims**
  (measured 2026-07-27, WS11/D47, `scripts/a2a_async_probe.py`). Every
  `protocol: a2a` row in this matrix means the endpoint accepts
  `message/send`. It says nothing about whether the endpoint honours
  `configuration.return_immediately` and serves `tasks/get` — and the
  agent card does not say either, because the spec's "MAY continue
  asynchronously" lets a blocking implementation be fully conformant.
  Measured: the hosted Agentforce shim and both Foundry agents implement
  it; **Vertex AI Agent Engine is submit-only** — it returns a task id in
  826ms that no tried request shape reads back (`GET /tasks/{id}` →
  *"A2A version '0.3' is not supported by this handler"*), so an async
  submit there silently loses the answer and its legs must keep the
  blocking `ask()`. The matrix deliberately does NOT grow an "async"
  column yet: one probe per endpoint on one day is thin evidence for a
  standing claim, and Agent Engine's cause is unresolved rather than
  established. plan/03-results.md carries the numbers.
