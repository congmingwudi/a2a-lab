# Results

Latency + transcript results per milestone. `scripts/matrix.py` appends
matrix runs below; manual measurements (action-timeout probes, managed vs
sdk first-turn latency) are recorded by hand with date + setup.

## Timeout probes (M6) — measured 2026-07-25

`scripts/probe_action_timeout.py`, run against the live chain (Agent API →
A2ALab_Research_Assistant_Script → Apex → Named Credential → tunnel → bridge
→ local Claude, `CLAUDE_BACKEND=managed`, `A2ALAB_MODE=local`). Delay is
injected at the bridge (`A2ALAB_DELAY_S`, src/bridge/app.py), so the custom
action simply takes longer to return. "Action duration" = injected delay +
the Claude leg (the bridge's own Hop starts *after* the sleep, so its
recorded latency is the Claude leg alone).

| Injected delay | Action duration | Turn wall time | Agentforce action outcome |
|---|---|---|---|
| 10s | ~26.9s | 38.5s | completed — external answer used |
| 30s | ~46.1s | 58.4s | completed — external answer used |
| 60s | ~76.1s | 87.9s | completed — external answer used |
| 65s | ~84.3s | 95.9s | completed — external answer used |
| 70s | ~84.7s | 96.0s | completed — external answer used |
| 75s | ~89.7s / ~93.2s | 99.8s / 100.7s | **abandoned** — answer dropped (2 runs) |
| 80s | ~98.9s | 100.3s | **abandoned** — answer dropped |
| 90s | ~107.4s | 99.8s | **abandoned** — answer dropped |

**The reported ~60s action timeout is wrong.** The real cutoff sits between
~85s and ~90s of action duration: 84.7s was still used, 89.7s was not. The
lab's Path A budget chain (plan/01-architecture.md) was engineered against a
number roughly 25s tighter than reality — conservative, so nothing broke, but
the sync research depth it caps was set by a figure nobody had measured.

Two things the probe found that the table alone does not say:

- **Failure is graceful and silent, and it costs the full budget.** Every
  abandoned run still returned a complete, well-formed answer at ~100s wall
  (99.8 / 100.3 / 100.7 / 99.8 — a strikingly consistent ceiling), with the
  twin's own "External market research (from the Claude research agent):"
  heading present and filled with *"External research is temporarily
  unavailable."* Nothing in the transport says anything: the Agent API
  returns 200, the bridge hop completes normally seconds later, and the
  delegated answer is simply discarded. A caller that checks status codes —
  or greps for the section heading — records these as successes.
- **The heading proves nothing.** An earlier version of this probe classified
  on the presence of that heading and scored two timeouts as passes. The
  section BODY is the signal. This is the `fabricated-attribution` insight
  reappearing as a measurement bug in the lab's own instrument.

Method note worth keeping: the first probe attempt used an improvised
question and the twin answered from nothing at all — `"result":[]`, no
actions invoked, both sections confabulated, 7.6s wall against a 10s injected
delay. The probe now sends the console's `DEFAULT_QUESTION` verbatim and
verifies the action fired by looking for the bridge hop in the trace log
rather than trusting the reply.

## Managed vs SDK backend latency — measured 2026-07-25

`scripts/probe_backend_latency.py --runs 5`, one Claude adapter behind three
hostings, same matrix question. Backends verified on the wire per condition
(`raw.backend` = managed / sdk).

| Backend | Turn | p50 | p95 | n | Notes |
|---|---|---|---|---|---|
| managed | first (cold session) | 5.2s | 5.4s | 5 | new session id per run — provisioning included |
| managed | follow-up (warm session) | 3.2s | 3.8s | 5 | one session reused; first turn discarded |
| sdk | first (warm server) | 11.7s | 19.2s | 5 | long-running local server, no network hop |
| sdk-agentcore | warm runtime | 7.3s | 25.1s | 5 | same sdk image on Bedrock AgentCore (D26) |

**Managed-session provisioning costs ~2s, not the 5–10s the lab assumed** —
and cold managed (5.2s p50) is *less than half* warm self-hosted sdk (11.7s
p50). The intuition that a managed sandbox must be the slow option is
backwards here: the sdk backend runs an agentic loop with tool calls per
turn, and that loop, not the hosting, dominates. Its spread says the same
thing — managed's p50→p95 is essentially flat (5.2→5.4, 3.2→3.8) while the
sdk columns fan out (11.7→19.2, 7.3→25.1), which is turn-count variance, not
infrastructure variance. AgentCore's p95 (25.1s) is the one number that
should inform a sync budget.

Caveat on the sdk rows: the known WS1 flake applies — the sdk agent sometimes
tries to delegate a factual question to Agentforce and burns turns against
`CLAUDE_MAX_TURNS=3`. That is part of what the spread measures, and it is a
property of that agent's prompt, not of the hosting.

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

## Matrix run — 2026-07-19 11:39:42 MDT

```
target             platform    protocol        status       result   p50ms  p95ms  detail
-----------------------------------------------------------------------------------------
openai-agentcore   openai      agentcore-http  native       PASS     10306  11920  MCP is a brokered, envelope-based protocol for interoperable message exchange that supports mediation, routing, transfor
```

## Matrix run — 2026-07-19 11:53:30 MDT

```
target             platform    protocol        status       result   p50ms  p95ms  detail
-----------------------------------------------------------------------------------------
claude-agentcore   claude      agentcore-http  native       FAIL      8025   8025  RuntimeClientError: An error occurred (RuntimeClientError) when calling the InvokeAgentRuntime operation: Received error (500) from runtime. Please check your CloudWatch logs for more information.
```

## Matrix run — 2026-07-19 11:57:47 MDT

```
target             platform    protocol        status       result   p50ms  p95ms  detail
-----------------------------------------------------------------------------------------
claude-agentcore   claude      agentcore-http  native       FAIL     26492  29589  TimeoutError: 
```

## Matrix run — 2026-07-19 11:59:01 MDT

```
target             platform    protocol        status       result   p50ms  p95ms  detail
-----------------------------------------------------------------------------------------
claude-agentcore   claude      agentcore-http  native       FAIL         -      -  TimeoutError: 
```

## Matrix run — 2026-07-19 11:59:57 MDT

```
target             platform    protocol        status       result   p50ms  p95ms  detail
-----------------------------------------------------------------------------------------
claude-agentcore   claude      agentcore-http  native       PASS      8444  15446  **MCP (Model Context Protocol)** is a standard that enables LLM applications to securely connect to external data source
```

## Matrix run — 2026-07-19 19:14:56 MDT

```
target             platform    protocol        status       result   p50ms  p95ms  detail
-----------------------------------------------------------------------------------------
adk-a2a            adk         a2a             native       PASS      9315  34536  The primary difference lies in their communication paradigms: MCP is message-based and asynchronous, focusing on workflo
```

## Matrix run — 2026-07-19 19:48:24 MDT

```
target             platform    protocol        status       result   p50ms  p95ms  detail
-----------------------------------------------------------------------------------------
agentforce-adk-rest agentforce  agentforce-api  native       PASS      8375   9606  The MCP (Message Coordination Protocol) is a message-based protocol used for orchestrating complex, multi-step interacti
```

## Matrix run — 2026-07-22 20:03:31 MDT

```
target             platform    protocol        status       result   p50ms  p95ms  detail
-----------------------------------------------------------------------------------------
foundry-a2a        foundry     a2a             native       PASS     11874  12773  MCP is a lightweight message/transport specification that standardizes how agents exchange envelopes, content parts, and
foundry-rest       foundry     foundry-api     native       PASS     10375  11909  MCP is a brokered, message-centric interoperability protocol designed to route, mediate, and multiplex agent communicati
agentforce-foundry-rest agentforce  agentforce-api  native       PASS      7227   8819  The MCP (Message Communication Protocol) is a message-based protocol designed for asynchronous, decoupled agent communic
```

## Cross-hyperscaler capstone — 2026-07-23 (WS3)

`google-adk-to-foundry` scenario, run live from the console: GCP Gemini
(Vertex AI Agent Engine, gemini-2.5-flash-lite) consulting Azure gpt-5-mini
(Microsoft Foundry) over BOTH platforms' native A2A endpoints — the lab's
first native×native cross-hyperscaler cell. **16.9s** end to end, both
answer sections labeled, D27 rider honored. Auth: Entra service principal
in the engine env (EnvironmentCredential); the reverse direction
(Foundry→ADK) is auth-blocked — Foundry connections cannot mint Google IAM
tokens. Detail in plan/07-workstreams.md WS3 item 11 and ADR D34's change-set.

## Rider-provenance harvest counts — 2026-07-23 (D27/D34)

Measured against traces/lab.db after the 2026-07-23 harvests (five-platform
coverage: claude, salesforce, openai, adk, foundry):

- **62 of 220** platform-logged sessions self-attribute their caller via the
  D27 rider text visible in the platform's own logs — 58 in Salesforce's
  session logs, 4 in Anthropic's; exactly the delegated turns.
- **188** Salesforce-logged interaction events contain the rider text
  verbatim.
- **9** sessions (8 salesforce, 1 claude) additionally join to a specific
  lab run via the D34 `lab-trace:` rider line — re-counted 2026-07-24
  against `traces/lab.db` (was 2 at the first live links; the count grows
  as post-D34 runs accumulate).
  **The platform set has not grown, and structurally cannot by this
  mechanism:** foundry sessions are harvested (5 rows) but Azure Monitor
  gives spans only — timings, tokens, model, operation ids, no prompt text
  — so the rider regex has nothing to match and that column joins by
  response id (`platform_ref`) instead. Only platforms that log the
  utterance text can be joined by a text convention.

Counts move with every harvest; re-measure with
`ObsStore.session_callers()` / `session_lab_traces()` before quoting.

## Matrix run — 2026-07-25 14:12:40 MDT

```
target             platform    protocol        status       result   p50ms  p95ms  detail
-----------------------------------------------------------------------------------------
claude-rest        claude      rest            native       PASS      4732   4732  MCP (Model Context Protocol) is a protocol for connecting AI models to external tools, data sources, and services throug
claude-mcp         claude      mcp             native       PASS      5072   5072  **MCP (Model Context Protocol)** is a protocol for connecting AI models to external tools, data sources, and services th
claude-a2a         claude      a2a             native       PASS      5731   5731  MCP (Model Context Protocol) is a standardized protocol that enables AI models to connect to external tools, data source
guide-rest         guide       rest            native       PASS      2611   2611  Based on the lab's findings: **MCP has no protocol-level session semantics—session identity rides as a tool argument—whi
guide-mcp          guide       mcp             native       PASS      2460   2460  MCP has no protocol-level session semantics — the session id rides as a tool argument — while A2A's `contextId` is first
guide-a2a          guide       a2a             native       PASS      2305   2305  MCP has no protocol-level session semantics — session identity rides as a tool argument only — while A2A's `contextId` i
claude-agentcore   claude      agentcore-http  native       PASS      9187   9187  **MCP (Model Context Protocol)** is a protocol that connects AI models and agents to external tools, APIs, and data sour
agentforce-rest    agentforce  agentforce-api  native       PASS      8746   8746  The MCP (Message Channel Protocol) is a message-based protocol designed for flexible, asynchronous communication between
agentforce-mcp     agentforce  mcp             via-shim     PASS      6076   6076  The MCP (Message Channel Protocol) is a message-based protocol designed for flexible, event-driven communication between
agentforce-a2a     agentforce  a2a             via-shim     PASS      5096   5096  The MCP (Message Coordination Protocol) is designed for orchestrating message flows and coordinating tasks between agent
agentforce-openai-rest agentforce  agentforce-api  native       PASS      7357   7357  The MCP (Message Coordination Protocol) focuses on structured, message-based coordination between agents, often using a 
openai-rest        openai      rest            native       PASS      5588   5588  MCP is a brokered, event-oriented protocol for mediated, asynchronous multi-party message exchange with standardized env
openai-mcp         openai      mcp             native       PASS      5715   5715  MCP is a mediated, multi-channel coordination protocol that routes and orchestrates messages through a central hub to su
openai-a2a         openai      a2a             native       PASS      5280   5280  Direct answer: MCP is a brokered, channel-agnostic messaging protocol that standardizes and routes messages through a ce
openai-agentcore   openai      agentcore-http  native       PASS      9045   9045  Assuming MCP here refers to a mediated messaging protocol and A2A to direct agent-to-agent calls: MCP is a brokered, mes
agentforce-google-adk-rest agentforce  agentforce-api  native       PASS      7659   7659  The MCP (Message Coordination Protocol) is a message-based protocol used for orchestrating complex workflows and statefu
agentforce-a2a-shim agentforce  a2a             via-shim     PASS     10147  10147  The MCP (Message Coordination Protocol) is designed for orchestrating message flows and coordinating multi-step interact
agentforce-foundry-rest agentforce  agentforce-api  native       PASS      6661   6661  The MCP (Message Communication Protocol) is a message-based protocol designed for asynchronous, decoupled agent communic
foundry-rest       foundry     foundry-api     native       PASS     24311  24311  MCP is a message-conversion/bridging protocol that adapts or translates messages between agents using different formats,
foundry-a2a        foundry     a2a             native       PASS      9737   9737  MCP is a lightweight, message-envelope protocol focused on reliably exchanging and sequencing conversational content acr
google-adk-a2a     adk         a2a             native       PASS     38293  38293  The MCP (Messaging and Conversation Protocol) is a general-purpose communication standard for agent interoperability, wh
```

## Fan-out orchestration — first live dispatch (WS8) — measured 2026-07-25

`scripts/run_fanout.py`, dispatching one supplier-disruption task to the
scenario's legs concurrently. The orchestrator model is not in the loop here —
this drives `orchestration.dispatch` directly so the FAN-OUT is what gets
measured. Both orchestrator variants call the same code, so these numbers
describe either.

**The customer-comms leg (`openai-agentcore`) could not run: the AWS SSO token
had expired.** That turned out to be the most useful part of the run — it gave
a real failure to test the partial-failure contract against, rather than an
injected one.

| Run | Legs | Slowest leg | Wall | Serial equivalent | Coverage |
|---|---|---|---|---|---|
| 1 | adk + foundry | 36.7s (adk) | **36.7s** | ~50.7s | 2/2 |
| 2 | adk + foundry + openai | 11.9s (foundry) | **12.8s** | ~14.6s | 2/3 |

**Parallelism does what it claims.** Wall time tracks the slowest leg, not the
sum: run 1 finished in 36.7s against a 50.7s serial equivalent — 28% saved on
two legs. The saving grows with leg count and with variance between legs, which
is exactly the disruption-response case (one slow research leg, two fast
lookups).

**The partial-failure contract held against a real failure.** The dead leg
rendered inline —

```
Customer operations (openai):
[leg unavailable: openai — TokenRetrievalError: Error when retrieving token
 from sso: Token has expired and refresh failed]

[fan-out coverage: 2/3 legs answered]
```

— and `run_fanout.py` exited **1**, not 0. Both halves matter: a scheduled
fan-out that returns a short brief with a success code is the multi-agent form
of the Agentforce failure measured earlier the same day. Note also that the
failing leg failed *fast* (245ms) and cost the turn nothing; a leg that fails
by hanging is the expensive case, which is what `LEG_TIMEOUT_S` bounds.

**Six hops recorded per three-leg run**, all under one trace id — the
orchestrator's outbound hop plus each client's own inner hop, with the two
`openai-agentcore` hops correctly marked `error`. Every leg carried the D27
rider at `delegation-depth: 1` and the D34 `lab-trace:` line, confirming the
guard and the text-level trace convention both survive parallel dispatch as
they do a chain.

**An unplanned finding — instruction adherence is not stable across platforms
or across runs.** Every leg is sent the same shaping instruction ("at most 3
short bullets, 60 words, do not ask clarifying questions — state your
assumptions instead"). Foundry's gpt-5-mini honored it on both runs, explicitly
writing its assumptions inline ("assume DAP/DDP via Rotterdam"). The ADK/Gemini
leg honored it on run 1 and **ignored it on run 2**, replying "I need more
information to assess the impact… Please provide details on which specific
shipments…" — a clarifying question, which is the one thing the prompt
forbade, and useless to an orchestrator that cannot answer it.

That matters more than it looks. Determinism shaping is what makes these runs
comparable, and it is enforced only by asking politely. A leg that returns a
question instead of an answer is not a failure any status code can catch: it
was HTTP 200, non-empty, fast, and structurally fine. **The lab now has two
independent instances of the same pattern — Agentforce's empty section and
Gemini's clarifying question — where the transport says success and the content
is unusable.** Detecting that needs a content check, and no protocol in the
matrix offers one.

## Fan-out join rate — measured 2026-07-26 (WS8's deliverable number)

The question WS8 exists to answer: when one task fans out across four
platforms, how many of them can be joined back to that run **from their own
execution logs** — not from the lab's wire trace, which of course has all four.

Run: `scripts/run_fanout.py --orchestrator cma`, trace
`9024af03e4f34b7498fd39d6222d4d9a`, 3/3 legs answered, 37.4s wall, against the
three DEDICATED per-leg agents (see below). Measured with
`scripts/fanout_join_rate.py` after harvesting all four platforms.

### **Join rate: 1 of 4.**

| Platform | Joined? | Why |
|---|---|---|
| Anthropic CMA (orchestrator) | ✅ | `platform_ref` = the managed session id, stamped on the hop at emit time. Its sessions also carry the D34 rider text |
| Google ADK / Agent Engine | ❌ **structurally** | The platform exposes Cloud Logging entries and Monitoring rollups, not per-turn sessions. The harvest holds **one** obs session per deployed engine — 482 log entries, one row. There is no per-run object to join *to*, and no amount of lab instrumentation creates one |
| Microsoft Foundry | ❌ **fixable** | Foundry DOES emit per-turn sessions (20 harvested, keyed by `resp_…`). The lab simply never captures that response id as `platform_ref` on the A2A hop, so the key exists on both sides and is not written down |
| OpenAI on AgentCore | ❌ **fixable-ish** | Also per-turn (`resp_…`), but the hop's `platform_ref` is the **AgentCore** session id (`a2alab-adhoc-…`), not the OpenAI response id. Two different identifiers for the same turn, and the lab records the outer one |

**The 1-of-4 is not one problem, it is three different ones**, and separating
them is the actual result:

1. **One platform is structurally unjoinable.** ADK's preview A2A surface has
   no session/turn API, so its telemetry is request-shaped rather than
   conversation-shaped. This is not a gap in the lab's instrumentation and
   cannot be closed by better bookkeeping.
2. **Two are bookkeeping failures, not platform failures.** Foundry and OpenAI
   both hand back a per-turn id that the lab does not persist on the hop. The
   join key exists on both sides; nobody wrote it down. Those are fixable, and
   fixing them would take the rate to 3/4.
3. **The D34 text rider does not help here at all.** It joins 12 Claude and 31
   Salesforce sessions elsewhere in the lab, because those platforms log the
   utterance. None of the three fan-out legs' platforms log prompt text, so the
   convention that works across Path A contributes nothing to a fan-out.

**Why this matters more than the number.** Every platform in this run returned
HTTP 200 and a good answer. The orchestrator produced a complete, correct
brief. Nothing anywhere reported a problem — and afterwards, three of the four
platforms could not tell you they had participated. Cross-platform
observability degrades silently and it degrades *as the topology widens*: a 1:1
cell hides this because two platforms are easy to correlate by hand.

### Dedicated per-leg agents — and one that earns nothing

Deployed for this run, each its own agent on its own platform so the platform's
logs attribute this experiment rather than the lab's general researchers:

| Leg | Agent | Platform | Effect |
|---|---|---|---|
| Logistics | `a2alab-logistics-agent` | ADK / Agent Engine (own deployment) | **2.5s vs 39.8s** for the researcher on the same question |
| Commercial | `a2alab-commercial-agent` | Foundry (own agent, inbound A2A) | answered in role, stated assumptions rather than asking |
| Customer comms | — | shared `openai-agentcore` | **not deployed, deliberately** |

The customer-comms leg has no dedicated agent because it would buy nothing
measurable: OpenAI's traces are write-only by design, so a dedicated agent
there improves attribution the lab cannot read back. That is worth stating as a
finding rather than hiding as an omission — *"give every leg its own agent" is
good advice exactly as far as the platform's telemetry can repay it.*

The logistics number is the strongest argument for dedicated agents: same
question, same prompt shaping, **16× faster** because the agent has no research
toolset to reach for. The general researcher spent its time deciding whether to
go looking for data it did not have.

### A silent bug worth recording

The first join-rate measurement returned 1/4 **against the wrong agents**.
`orchestration/legs.py` resolved its per-leg target overrides into a
module-level tuple at import time, while `scripts/run_fanout.py` imports the
module at the top and calls `load_dotenv()` inside `main()` — so every override
in `.env` was read as absent and each run quietly used the default targets. The
run looked perfect: 3/3 legs, a good brief, a clean exit. It surfaced only by
reading the recorded hops and noticing the target names were wrong. Fixed
(targets resolve in `legs_for()` now) with a regression test. **Config that is
read at import time is config that silently ignores your configuration.**

## Two orchestrators, one scenario — ADK's ParallelAgent (WS8) — 2026-07-26

The CMA variant runs clean (3/3 legs, 37.4s, brief reports its own coverage).
The ADK variant — the same scenario with concurrency **declared** as
`SequentialAgent[ParallelAgent[3 units], synthesiser]` — produced two findings
before it produced a brief.

### ADK's own telemetry is not concurrency-safe (google-adk 1.x)

Every invocation failed in ~3s with the agent never reaching its legs:

```
ValueError: <Token var=<ContextVar name='current_context' ...>>
            was created in a different Context
  google/adk/telemetry/_instrumentation.py  record_agent_invocation
  google/adk/agents/base_agent.py:296       run_async
```

`ParallelAgent` runs its sub-agents in separate asyncio tasks; ADK's
OpenTelemetry instrumentation creates a context token in one and detaches it in
another, which OTel refuses. **Setting `OTEL_SDK_DISABLED=true` removes the
error**, confirming the diagnosis — the framework's flagship concurrency
primitive and its own tracing cannot both be on.

That is worth stating plainly because of the direction it cuts: the strongest
argument for declared parallelism is that the framework handles the hard parts.
Here the framework's *observability* is what the concurrency broke, and the only
lever the runtime exposes is to turn tracing off — trading the visibility the
lab exists to measure for the topology it exists to demonstrate.

### And one of the failures was ours

With telemetry off, the container reached the model four times (three units plus
the synthesiser) and still failed the A2A task. Cause: in the per-leg tool,
`Registry.load()` and `client_for()` sat **outside** the `try`, so a client that
cannot even be constructed — `openai-agentcore` needs AWS credentials, which a
GCP container does not have — raised past the handler and killed the whole
ParallelAgent branch instead of degrading to one dead leg.

The partial-failure contract was written for calls that fail. This failed
*before* the call, which the contract did not cover. **A leg can fail at
construction, not just in flight**, and a fan-out that only guards the request
has a gap exactly where the cross-cloud identity problems live.

### The identity edge this exposed

The ADK orchestrator's customer-comms leg needs **SigV4 from inside a GCP
container** — the AWS←GCP direction the lab has never wired. The WS3 capstone
established GCP→Azure works (the container holds an Entra SP) and Azure→GCP is
blocked; this is the third edge of that triangle and it is unwired rather than
impossible. Until it is, that leg reports as unavailable, which is the contract
working rather than a broken scenario — and the ADK variant stays
`status: coming-soon` in the console for exactly that reason.

### It runs now — and every leg fails from inside the container

With both fixes in, the ADK orchestrator completes: **5.0s wall**, the graph
executes, the synthesiser writes a brief. And **0 of 3 legs answered**:

| Leg | Failure from inside the GCP container |
|---|---|
| commercial (Foundry, Azure) | agent-card fetch error |
| exposure (ADK, same cloud) | network communication error |
| customer_comms (OpenAI, AWS) | runtime configuration error — no AWS credentials, as predicted |

**The important part is what the orchestrator did with that.** It named all
three units, marked each as a gap, said explicitly what decision each gap left
unsupported, and invented nothing:

> The `exposure` unit's response is unavailable, meaning the operational and
> financial exposure to our EU manufacturing customers is unassessed.

So the partial-failure contract holds in the declared-graph variant too, at the
hardest possible coverage — 0/3 — where the temptation to confabulate is
greatest. That is a stronger result than the 3/3 CMA run, because a brief that
degrades honestly under total failure is the one property you cannot test with a
happy path.

**Why the legs fail is the lab's own central finding, arriving again.** The
CMA orchestrator reaches all three from the laptop, which holds every
credential. The ADK orchestrator reaches none from a GCP container, which holds
one. Same code, same targets, same prompts — different identity context. Nothing
about the protocol changed.

Left at `status: coming-soon`. Closing it means wiring outbound identity from
the Agent Engine container to two other clouds, which is WS8.1's cross-cloud
identity triangle rather than an orchestration problem.
