# Architecture

```
Salesforce Agentforce (custom Service agent; Apex action → Named Credential)
        │ REST (always, GA)                     ▲ Agent API (REST, GA)
        ▼                                       │
   bridge (FastAPI, :8100) ──rest|mcp|a2a──►  claude-researcher     Path B: ask_agentforce
        protocol per config/targets.yaml        │  backend:          custom tool → Agentforce
        (no SF redeploy to switch)              │  managed (CMA beta, default)
                                                │  sdk (claude-agent-sdk)
                                                ▼
   claude servers  REST :8001 | MCP :8002 | A2A :8003    (openai :8011/12/13, M9)
   agentforce shims        MCP :8021 | A2A :8023          (proxy → Agent API)
   lab console :8200 (trace viewer, SSE live tail)
   cloudflared tunnel → *.lab.agenticthings.com
```

## The two seams

- **Inbound — `interop.adapter.AgentAdapter`**: an agent we host implements
  `handle(AgentRequest) -> AgentResponse` once; `serve(adapter, protocol,
  port)` mounts it behind REST, MCP, or A2A. Implementations:
  `platforms/claude/core.py` (backends: managed | sdk),
  `platforms/agentforce/proxy.py` (the shim), later `platforms/openai`.
- **Outbound — `interop.clients.base.RemoteAgentClient`**: `ask(AgentRequest)
  -> AgentResponse` per protocol: `RestClient`, `McpClient`, `A2AClient`,
  plus the platform-native `AgentforceClient` (Agent API). Resolved by name
  through `interop.registry.Registry` (config/targets.yaml).

Because both seams share the canonical `AgentRequest`/`AgentResponse`
(src/interop/models.py), every client is testable against our own served
adapters — the loopback e2e suite proves all three client×server pairings
with a deterministic EchoAdapter before any external platform is involved.

## Protocol mapping rules

| Concept | REST | MCP | A2A | Agentforce Agent API |
|---|---|---|---|---|
| Ask | `POST /invoke` AgentRequest JSON | `tools/call ask(message, session_id, trace_id)` | JSON-RPC `SendMessage` (a2a v1.0 proto naming), user Message with one text Part | `POST /sessions/{id}/messages` |
| Answer | AgentResponse JSON | tool text content = AgentResponse JSON | completed Task, one text artifact | `messages[].type == "Inform"` |
| Session | `session_id` field | tool argument (no protocol-level session — a finding) | `contextId` ↔ `session_id` | Agent API session, lazily created + cached |
| Trace correlation | `X-Trace-Id` header | tool argument passthrough | message `metadata.trace_id` | `X-Trace-Id` forwarded by Apex |
| Errors | HTTP status | `isError` tool result | Task state `TASK_STATE_FAILED` | HTTP status |

## Trace layer

`interop/trace.py` — every hop appends a `TraceEvent` `{trace_id, hop_seq,
source, target, protocol, transport_detail, request_payload_raw,
response_payload_raw, status, latency_ms, ts}` to `traces/YYYY-MM-DD.jsonl`.
Raw payloads are the actual wire bytes: handler-level for REST (body is the
payload), and a WireTap ASGI middleware for MCP/A2A (the JSON-RPC envelopes
live inside the frameworks, so the middleware tees the real request/response
bytes). The console (:8200) groups events by trace_id and live-tails the
JSONL over SSE.

## Platform plugin convention

A platform = one directory under `src/platforms/<name>/` contributing an
`AgentAdapter` (agents we host) and/or a `RemoteAgentClient` (agents hosted
elsewhere), plus one entry per exposure in `config/targets.yaml`. Nothing in
`interop/` or other platforms changes. ADK note: Google ADK speaks A2A
natively, so onboarding it may need no custom client at all — the first true
native×native A2A cell (M10).

## Timeout engineering (Path A)

Budget chain: Agentforce action ~60s (reported — **measure in M6**) → Apex
`setTimeout(110000)` → bridge client timeout 45s → Claude backend
(`CLAUDE_ANSWER_TIMEOUT_S=100`; the tighter bridge/action chain upstream
governs Path A regardless — the Claude-side cap needs headroom because the
Claude → Agentforce scenario runs an Agent API round trip INSIDE Claude's
turn, plus managed-session cold start; 40s proved too tight in practice and
500'd that scenario. If managed first-turn p95 blows the Path A budget,
Path A pins `CLAUDE_BACKEND=sdk` while Path B and direct calls keep
exercising managed). Speed levers: Haiku-tier `CLAUDE_AGENT_MODEL`, concise
system prompt, warm long-running servers.
