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
| MCP | **via-shim** | Claude as MCP client → `agentforce-mcp` shim (:8021) → Agent API |
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
  provisioning — measure vs. warm sdk backend. (to measure, M5/M6)
- Real Agentforce action timeout: reported ~60s — measure with injected
  10/30/60/90s delays in M6.
