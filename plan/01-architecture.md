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
   guide servers   REST :8031 | MCP :8032 | A2A :8033    (the lab docent, D35)
   strands servers REST :8041 | MCP :8042 | A2A :8043    (WS5; hosted twin → AgentCore)
   langgraph srvrs REST :8051 | MCP :8052 | A2A :8053    (WS4; hosted → Heroku, D77)
   lab console :8200 (trace viewer, SSE live tail)
   cloudflared tunnel → claude-*-lab.agenticthings.com  (local dev stack; run_local.sh)

Hosted (the deployed topology): bridge, console & the 14 protocol faces → ECS
Fargate behind one ALB (bridge-lab / console-lab / faces-lab, host-header rules;
the brief watcher a2alab-briefs also on Fargate); claude/openai/strands agents →
Bedrock AgentCore. The ports above are the local dev stack; hosted, the faces are
reached via faces-lab (A2ALAB_MODE=hosted, WS13). See plan/09-deployment-map.md.
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

Budget chain: Agentforce action **~85–90s (measured 2026-07-25**, not the
~60s previously reported — see plan/03-results.md; an action returning at
84.7s was still used, one at 89.7s was abandoned) → Apex
`setTimeout(110000)` → bridge client timeout 45s → Claude backend
(`CLAUDE_ANSWER_TIMEOUT_S=100`; the tighter bridge/action chain upstream
governs Path A regardless — the Claude-side cap needs headroom because the
Claude → Agentforce scenario runs an Agent API round trip INSIDE Claude's
turn, plus managed-session cold start; 40s proved too tight in practice and
500'd that scenario. If managed first-turn p95 blows the Path A budget,
Path A pins `CLAUDE_BACKEND=sdk` while Path B and direct calls keep
exercising managed). Speed levers: Haiku-tier `CLAUDE_AGENT_MODEL`, concise
system prompt, warm long-running servers.

## Lab method: session-forking (WS20)

A named, repeatable way the lab compares approaches: **one scenario, one
baseline, N variants, all differences reported against the shared origin.**
Fix everything the comparison is *not* about — the same task, the same input,
the same trace correlation — fork only the one axis under test, and read the
results as deltas from the common baseline rather than as N independent runs.
The shared origin is what makes a difference attributable: a number that is not
measured against a fixed baseline is a number you cannot defend.

This is not a new capability — it is the naming of a move the lab already ran
by hand. The supplier-disruption fan-out was built three times over three
orchestrators (model-scheduled MCP, deterministic script, and the A2A
fire-then-poll variant — WS8), same scenario each time, and the interesting
result was the *difference* between them, not any single run. Naming the move
turns three one-off builds into a method you can point at and re-run: it is how
the matrix compares protocols (one cell per protocol, one scenario, D-referenced
deltas in `plan/02-matrix.md`), and how a future "which orchestrator/model/
prompt is better" question should be posed.

The convention is borrowed from Anthropic's Claude Science workbench, where a
figure ships with the exact code, environment, and message history so a
comparison re-executes rather than being asserted — **a convention and a
vocabulary, not a standard** (nothing in Claude Science is a published spec).
What transfers is the epistemics: the baseline is the artifact, and a variant's
claim earns its evidence tier by being re-runnable against that baseline, not by
a label an author typed. See the artifact-derived evidence ladder in
`config/insights.yaml` and the actor-critic reviewer (`insights-audit`) that
demotes any claim its artifact no longer backs — the same trust-under-pressure
move as the cost sentinel refusing a comparison it could not support (WS12/D44).
