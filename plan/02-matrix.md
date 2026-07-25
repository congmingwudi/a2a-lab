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
