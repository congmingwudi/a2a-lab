# Results

Latency + transcript results per milestone. `scripts/matrix.py` appends
matrix runs below; manual measurements (action-timeout probes, managed vs
sdk first-turn latency) are recorded by hand with date + setup.

## Timeout probes (M6 — pending)

| Injected delay | Agentforce action outcome | Notes |
|---|---|---|
| 10s | — | |
| 30s | — | |
| 60s | — | |
| 90s | — | |

## Managed vs SDK backend latency (pending)

| Backend | Turn | p50 | p95 | Notes |
|---|---|---|---|---|
| managed | first (cold session) | — | — | includes container provisioning |
| managed | follow-up (warm session) | — | — | |
| sdk | first (warm server) | — | — | |

## Matrix run — 2026-07-09 22:41:56 MDT

```
target             platform    protocol        status       result   p50ms  p95ms  detail
-----------------------------------------------------------------------------------------
claude-rest        claude      rest            native       PASS      4638   4638  **MCP** (Model Context Protocol) is a standardized protocol that connects AI models to external data sources and tools t
claude-mcp         claude      mcp             native       PASS      4473   4473  MCP (Model Context Protocol) is a protocol designed to connect AI models with external tools, data sources, and services
claude-a2a         claude      a2a             native       PASS      5357   5357  **MCP (Model Context Protocol)** is a protocol that enables AI models to connect to external tools, data sources, and sy
agentforce-rest    agentforce  agentforce-api  native       PASS      5111   5111  I'm sorry, but I couldn't access the necessary information to answer your question about the difference between the MCP 
agentforce-mcp     agentforce  mcp             via-shim     PASS      4474   4474  I'm sorry, but I couldn't access the necessary information to answer your question about the difference between the MCP 
agentforce-a2a     agentforce  a2a             via-shim     PASS      4153   4153  I'm sorry, but it seems there was an issue accessing the necessary information to answer your question. Unfortunately, I
```

## Sync vs async delegation (D15/D16) — measured 2026-07-11/12

Hand-recorded from console runs against the live platforms
(CLAUDE_AGENT_MODEL=claude-haiku-4-5 sync; CLAUDE_BRIEF_MODEL=claude-sonnet-5 async):

| Run | Pattern | Wall time | Outcome |
|---|---|---|---|
| Agentforce → Claude (sync), Omega Inc. | one turn: Agent API + Apex CRM + bridge + Claude research | 27.0–35.9 s | PASS — two-section reply (CRM + external), inside the ~60s action budget |
| Claude → Agentforce (sync) | Claude turn incl. Agent API round trip | ~30–40 s | PASS after raising CLAUDE_ANSWER_TIMEOUT_S 40→100 (40s 500'd the turn) |
| Async brief #1 (Omega) | ad-hoc managed session, 8+ web lookups | 126.8 s | Research OK; Salesforce insert 400'd — custom fields deploy with NO FLS for anyone (fixed by permset assignment) |
| Async brief #2/#3 (Omega) | same | 90.2 s / 93.2 s | Delivered: brief + Task + in-app alert |
| Async brief #4 (Apple Inc.) | same, real-world account | 69.4 s | Delivered — real current intel (earnings, Apple v. OpenAI, DMA, tariffs) |

Takeaway: the sync pattern fits the action-timeout chain only because research
is capped shallow; the async pattern runs 1–2+ min unbounded and delivers into
CRM instead of a waiting HTTP response. Managed-session cold start ~5–10s is
noise for async, but material inside the sync budget.

## Observability harvest + analyst first run (M11) — measured 2026-07-17

- STDM enablement→queryable: DMO query runtime went live within ~10 min of
  flipping the Setup toggles; first traced sessions appeared in
  `ssot__AiAgentSession__dlm` **~5 min after the Agent API session ran**
  (ingestion lag, poll-measured at 3-min intervals).
- Join key confirmed: the Agent API `sessionId` **is** STDM's
  `ssot__Id__c` — 3/3 harvested STDM sessions matched the ids in the day's
  wire traces exactly. `platform_ref` now stamps it at emit time on both
  platforms (managed backend + AgentforceClient).
- Field-name drift (real org vs docs): a2alab-prod uses
  `ssot__StartTimestamp__c`/`ssot__EndTimestamp__c`, not the documented
  `*Dttm` variants — harvester discovers columns via `SELECT FIELDS(ALL)`
  instead of hardcoding.
- Harvest volumes (first full pull): CMA 50 sessions / 1,043 events
  (2.09M tokens aggregated locally — no platform-side usage API);
  Salesforce 3 sessions / 9 interaction events (message/step child DMOs
  still empty at harvest time); OpenAI n/a by design.
- Analyst first run (Sonnet 5, read-only SQL tool): 15 queries →
  findings brief. Its top finding (platform_ref NULL on all 319 historic
  hops) was a real instrumentation gap, fixed same-day; it also correctly
  separated timeout errors (~45,010 ms, bridge cap) from fast auth-style
  failures, and flagged >45s "ok" hops that turned out to be direct client
  calls that legitimately bypass the bridge cap.
