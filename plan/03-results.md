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

### Wiring outbound identity: 0/3 → 2/3, and three different reasons

2026-07-26. The three failures above looked like one problem ("the container
has no credentials"). They were three, and only one of them was about identity
at all — which is the finding, because "cross-cloud auth is hard" is the lazy
version of it.

| Leg | Real cause | Fix |
|---|---|---|
| commercial (Foundry, Azure) | `AZURE_FOUNDRY_PROJECT_ENDPOINT` was never shipped to the container. `targets.yaml` expands `${VAR}`, unset expands to `""`, so the endpoint became a bare path — reported as an agent-card fetch error | ship the var; also swapped `DefaultAzureCredential` → explicit SP (D39) |
| exposure (ADK, same cloud) | endpoint var missing too — but once fixed, **403 Forbidden**. The Agent Engine service agent holds `reasoningEngineServiceAgent`, which cannot query a *sibling* reasoning engine | ship the var; grant `roles/aiplatform.user` to `service-<projnum>@gcp-sa-aiplatform-re` |
| customer_comms (OpenAI, AWS) | two stacked causes — see below | in progress |

**Three lessons worth separating.**

1. **Config absence and network failure are indistinguishable downstream.** An
   unset `${VAR}` expanding to `""` produced "network communication error" and
   "card fetch error" — messages that send you to look at connectivity when the
   problem is a deploy manifest. Shipping a target's NAME without the vars its
   endpoint expands from is a deploy bug the registry cannot catch.
2. **Same-cloud is not same-permission.** The most surprising failure was the
   GCP→GCP leg. A container running as a Google-managed service agent had no
   rights over another agent in the same project. "It's all Google" bought
   nothing.
3. **The base image can break one SDK and no other.** Agent Engine sets
   `REQUESTS_CA_BUNDLE=${AGENT_GATEWAY_ROOT_CERTIFICATES:+/etc/ssl/certs/ca-certificates.crt}`,
   which is the **empty string** when that variable is unset. botocore reads
   `REQUESTS_CA_BUNDLE` directly; httpx and azure-identity ignore it. So AWS
   calls failed on an empty CA path while the Azure leg in the same process
   succeeded — and the error named a CA bundle, not a credential.

**Measured after the fixes:** 2/3 legs, **49.0s wall** (trace
`cb0db142a3204dd0833e022fb44bafbd`), brief named the missing unit and the
decision it left unsupported. Earlier run in the same session: 1/3 at 19.1s.

### GCP→AWS without a key

The remaining leg is the one that is genuinely about identity. Bedrock
AgentCore's data plane is SigV4-only — there is **no HTTP front door** to call
instead — so a GCP container must hold an AWS identity, and the obvious move
(paste an access key into the runtime's env) is the long-lived-secret-in-a-
container pattern D39 exists to remove.

Instead: AWS trusts `accounts.google.com` as a web identity provider natively,
so the container mints a Google-signed OIDC token for its own service account
and trades it at STS for one-hour credentials (`interop/cloud_auth.py`). No IAM
OIDC provider to register, no key to rotate.

The obstacle was pinning the trust policy: `accounts.google.com:sub` is the
service account's numeric id, and Agent Engine runs as a **Google-managed**
service agent in a project you do not own, so you cannot look it up. The
resolution was to make the first federated call fail usefully — it reports the
caller's own claims — so the error teaches you what to pin:

```
[leg unavailable: openai — RuntimeError: AssumeRoleWithWebIdentity ... (AccessDenied)
 ... Caller identity was {'sub': '1147141287970546…', 'aud': 'a2a-interop-lab',
 'email': 'service-536083661167@gcp-sa-aiplatform-re.iam.gserviceaccount.com',
 'iss': 'https://accounts.google.com'}]
```

Worth noting separately: that message only became readable because the leg
markers are now printed to container stdout. Routed through the synthesiser
instead, the same failure arrived as *"an InvalidConfigError related to its CA
bundle configuration"* — true, and useless. **An LLM asked to relay an error
paraphrases it.** Anything you intend to debug from needs a path out that does
not pass through a model.

The trust policy still denied it, and the last obstacle is the one worth
publishing. Google service-account tokens carry an `azp` claim, and AWS's
condition-keys reference says that when `azp` is set,
`accounts.google.com:aud` matches **azp** while the audience you requested
lands on `accounts.google.com:oaud`. So the intuitive policy — pin `:aud` to
your audience — fails with a bare `AccessDenied` naming no key, which reads
exactly like a wrong `sub` or an IAM propagation delay. Both wrong guesses cost
a retry cycle each. Pin `:oaud` + `:sub`.

**Result: 3/3 legs, 16.8s wall** (trace `802a9a3b73f845798b89085abf2ba4e3`;
`802a…`, `47c7…` and `1f65…` are consecutive runs across the fix). Zero leg
failures in the container log. The ADK orchestrator now reaches Google, Azure
and AWS from one GCP container with **no long-lived credential anywhere in it** —
one Entra service principal fetched from config, and an AWS role assumed with a
token the container mints for itself.

For comparison, the CMA orchestrator's 3/3 was 37.4s. Not a like-for-like
latency claim — different orchestrator models, and the CMA number included a
cold managed session — but the declared-graph variant is not paying a penalty
for having no host.

`supplier-disruption-adk` is `status: live`.

### Attribution in the brief: what a citation contract surfaced

2026-07-26. Both orchestrators were producing briefs that read as one voice,
which hid the only interesting thing about them — four agents on four platforms
wrote that text — and, worse, made them unverifiable: a reader could not tell a
claim the Commercial agent made from one the orchestrator inferred.

Two changes, applied identically to both variants so the comparison stays fair:
each unit's section now opens with a machine-generated source header (business
unit, platform, agent name, target, latency), and both orchestrator prompts
carry the same `CITATION_RULE` — tag every statement with its unit, tag your own
additions `[Orchestrator]`, end with a Sources block.

**Measured, CMA (3/3, 62.6s, trace `43836c5a…`)** — and it did something the
contract did not ask for:

> **[Orchestrator] Synthesis:** All three units agree the exposure is real and
> time-sensitive — Logistics' 7-day minimum estimate, Commercial/Legal's FM
> notice requirement, and Customer Ops' 7–14 day delay messaging are broadly
> consistent but not identical (7 vs. 7–14 days), so external communications
> should use the wider, more conservative range.

**That is the finding.** Forcing per-claim attribution made a cross-unit
*inconsistency* visible. Un-attributed, the model had been free to pick one
number and write a clean sentence; required to say who said what, it could not
merge them and had to reconcile them explicitly instead. A citation contract is
usually justified as an audit feature — here it improved the analysis.

**Measured, ADK (3/3, 23.8s, trace `75ac1fa8…`)** — tagged every sentence
correctly and produced a clean Sources block, but no reconciliation paragraph:
it reported all three units' durations side by side without noticing that 7 and
7–14 disagree. Same evidence, same instruction, different orchestrator model.

Worth keeping separate from the platform comparison: this is a *model* result
(Claude vs Gemini as synthesiser), not an architecture one. The
declared-graph-vs-host-side axis is unchanged by it.

---

## 2026-07-26 — The fan-out legs as remote MCP tools: the model schedules them (D41, WS7 item 4)

The CMA orchestrator's three business units moved from one host-side custom
tool to three tools on a hosted MCP server (`src/fanout_mcp/`, a Lambda behind
API Gateway). Two things were being tested, and both now have numbers.

### Does the model actually fan out?

**Yes, on the first attempt, and reproducibly.** Two runs, two different
disruptions, identical shape:

| Run | Trace | Call path | Units | Wall |
|---|---|---|---|---|
| Rotterdam port strike | `ede9e3bc…` | **parallel — turn 1: logistics + commercial + customer_operations** | 3/3 | 50.5s |
| Kaohsiung typhoon | `161d7a46…` | **parallel — turn 1: logistics + commercial + customer_operations** | 3/3 | 42.6s |

Both issued all three units in a **single model turn**, then wrote the brief on
the second. The prompt does not tell it to — prescribing "call all three at
once" would have answered the question by assertion. It says only that the units
are independent and the model may call them in any order and combination.

Parallelism is measured off `span.model_request_start`, the one turn boundary
the event stream offers: tool calls between two model requests were issued
together. A flat list of calls cannot distinguish "three at once" from "three in
a row", which is why the measurement is per-turn rather than per-call.

### Does coverage survive being moved from code into the model?

**Yes, with the roster stated — and this was the real open question.** The
host-side tool ends its result with `[fan-out coverage: n/3 legs answered]`,
computed by code that knows how many legs exist. Three independent tools have no
such vantage point: nothing but the model knows whether it called all three.

Both runs reported **"Coverage: 3 of 3 units"** unprompted, attributed every
claim to the unit that made it, and produced a complete Sources block naming
each unit's platform and agent. The Rotterdam run went further and flagged in
its own Gaps section that Commercial's contract terms were stated as
assumptions rather than verified — an accuracy caveat no instruction asked for.

This is a measurement, not a property. `run_fanout.py --orchestrator cma-mcp`
exits non-zero unless three distinct units were consulted, because a brief that
reads complete while a unit was never called is precisely the failure this lab
measures, and prose is not a check.

### What it costs: a request budget where there was none

Legs now run inside an HTTP request/response and inherit **API Gateway's 29s
integration timeout** — 25s per leg through this path against 120s host-side.
Not raisable for HTTP APIs: AWS's >29s support covers Regional and private REST
APIs only (quota request `7e7325274c…` filed anyway, and it would additionally
require migrating the endpoint from `apigatewayv2` to `apigatewayv1`).

Warm legs measured from inside the Lambda: **Logistics 3.9s (GCP), Commercial
12.8s (Azure), Customer operations 10.9s (AWS)** — all comfortably inside the
budget. A cold platform is not: AgentCore cold-starts at 31–56s and Agent Engine
at ~34s p95, and the first Agent Engine call through the Lambda did time out at
25s before the warm path settled at ~1s. Those legs report as unavailable
through the existing partial-failure contract rather than hanging.

**Moving a tool from the host to the orchestration layer imposes a request
budget on work that previously had none.** Nothing in the MCP protocol says so;
it falls out of where the tool is executed.

### AWS → GCP federation, the mirror of D40

The Lambda holds no Google key. google-auth signs a `GetCallerIdentity` request
with the function's ambient role, Google replays it at AWS STS, then impersonates
a service account. Three failures on the way in, all of which looked like
something else:

- **IAM eventual consistency.** `add-iam-policy-binding` failed with "Service
  account does not exist" immediately after `create` returned success.
- **A 403 that was a clock.** The first impersonation was denied ~1 minute after
  the binding was written and correct ~4 minutes later, with nothing changed.
- **A wrong var name that read as connectivity.** The deploy manifest carried
  `FOUNDRY_PROJECT_ENDPOINT` where the target expands
  `AZURE_FOUNDRY_PROJECT_ENDPOINT`; the endpoint collapsed to a relative path
  and the leg reported a network error — the same shape as the bug 7f0f625
  fixed. The env list is now derived from `targets.yaml`'s own `${VAR}`s.

**The asymmetry is the finding.** D40's GCP→AWS direction needed one AWS object
(a role whose trust policy pins the Google subject and audience), because AWS
trusts `accounts.google.com` natively. This direction needed five Google
objects — pool, AWS provider, attribute mapping, attribute condition,
impersonation binding — before Google would trust AWS at all. "Keyless
federation" costs very different amounts depending on which way you are going,
and Google's side is where the identity is *shaped* rather than merely accepted.

---

## 2026-07-26 — The bridge moves to Fargate, and Path A keeps its 45s budget (WS7 item 7)

The Path A bridge was the last lab component running on a laptop: Apex resolved
`A2ALab_Bridge` to the D20 Cloudflare tunnel, which terminated at
`uv run python -m bridge` on `:8100`. It now runs as an ECS Fargate service
behind an ALB.

**Deliberately not the D23/D28 pattern.** Every other hosted lab component is a
Lambda behind an API Gateway HTTP API. That fits the shim, whose work measures
10–19s, and does not fit the bridge, whose client timeout is 45s against an HTTP
API's hard 30s ceiling. The ALB's `idle_timeout.timeout_seconds` is set to 120s,
so the measured budget survived the move. Same cloud, same team, two components,
opposite hosting decisions — driven entirely by what each one's work costs.

**Six of six pathways verified through the hosted bridge**, second run, all warm:

| Target | Latency | Path from AWS |
|---|---|---|
| `claude-rest` | 4.6s | AgentCore runtime, SigV4 via the task role |
| `openai-rest` | 10.4s | AgentCore runtime, SigV4 via the task role |
| `google-adk-a2a` | 1.2s | Agent Engine A2A, AWS→GCP federated |
| `foundry-commercial-a2a` | 12.6s | Foundry A2A, Entra service principal |
| `agentforce-rest` | 7.6s | Agentforce Agent API |
| `agentforce-a2a-shim` | 10.2s | bridge → hosted shim → Agentforce (two hosted hops) |

Every one is inside the 30s an API Gateway would have allowed, which is worth
saying plainly: the ALB was not needed for *these* calls. It is needed for the
45s budget Path A is engineered around, and for the delegating turns that budget
exists to cover. Hosting to the measured ceiling rather than the observed
average is the point.

### Three failures on the way, and only one was about the bridge

**1. Under-specified deploy manifest, again — but a new variant.** The fan-out
server's fix was to derive env vars from `targets.yaml`'s own `${VAR}`s instead
of a hand-written list. That was necessary and *still insufficient* here:
`SF_AGENT_ID` is read straight from `os.environ` by the Agentforce client, so it
appears nowhere in `targets.yaml`. The route 500'd with "Agentforce is not
configured" while every endpoint in the manifest was correct. The deploy now
derives from two sources — the config file's `${VAR}`s **and** a scan of
`os.environ` reads across `src/` — because the config file only describes what
the *registry* expands, not what the *code* reads.

**2. The ambient variable that has now misdirected three components.** Adding
that source scan immediately shipped `AWS_DEFAULT_REGION=us-west-2` into the
task — a value that is not in `.env` at all, but exported ambiently by the
operator's shell. boto3 prefers it over `AWS_REGION`, so the container looked
for its secret in the wrong region and died at startup with **AccessDenied**,
which reads unambiguously as a broken IAM policy. It was not: `aws iam
simulate-principal-policy` returned `allowed` against the exact resource ARN.
`observability/promql.py` already documents this same variable misdirecting the
Secrets Manager client (2026-07-25) and the PromQL client (2026-07-26); this is
the third. The deploy now excludes the ambient AWS identity/region vars
explicitly and sets `AWS_REGION` from the deploy region.

**The general rule this earns:** a deploy manifest derived from the operator's
environment inherits everything the operator's environment happens to contain.
Deriving is still right — a hand-maintained list drifts — but the derivation
needs a deny-list for the host's own identity, or you ship the laptop into the
cloud.

**3. Keyless federation is not portable across AWS compute types.** The
AWS→GCP federation built for the fan-out Lambda (D41) failed on Fargate with a
`TransportError` reaching `169.254.169.254`. google-auth's built-in AWS supplier
looks in exactly two places — the `AWS_ACCESS_KEY_ID` env vars, then the EC2
metadata service. Lambda sets those env vars; **Fargate sets neither**, because
an ECS task's credentials come from the container credentials endpoint at
`$AWS_CONTAINER_CREDENTIALS_RELATIVE_URI`. The same code, the same role, the
same pool, a different compute shape, and it stops working.

Fixed by supplying google-auth a custom `AwsSecurityCredentialsSupplier` that
delegates to **botocore**, which already resolves env vars, the container
endpoint, IMDS, profiles and assumed roles. The federation now works on any AWS
compute rather than on the two shapes that happened to get tested. Worth
generalising: "workload identity federation works on AWS" is not one fact —
each compute service presents credentials differently, and a library's default
supplier encodes assumptions about which ones you are on.

### Still manual, deliberately

TLS and DNS are not scripted. `A2ALab_Bridge` points at
`https://bridge-lab.agenticthings.com`, a Salesforce-visible hostname; cutting
Path A over needs a certificate the ALB can serve and a DNS change in
Cloudflare. Everything above was verified on the ALB's own hostname over HTTP
first, so the cutover is a DNS change against a known-good target rather than a
deploy and a hope.

### Cutover: Path A off the laptop, and the 45s budget earning its keep on day one

DNS moved `bridge-lab.agenticthings.com` from the Cloudflare tunnel to the ALB,
staying proxied, with the zone on Full (strict). Verified rather than assumed:

- **Traffic really reaches Fargate.** A probe marker sent to the public hostname
  appeared in the ECS task's CloudWatch stream. Both bridges were running, so
  "it answered" would have proved nothing on its own.
- **Full (strict) did not break the tunnel hostnames.** `claude-rest-lab`,
  `claude-mcp-lab`, `claude-a2a-lab` and `console-lab` returned 401/401/401/200
  before and after — matching Cloudflare's documentation that `cloudflared`
  manages origin TLS itself (`originServerName`, `noTLSVerify`, `caPool`), so
  the zone encryption mode does not govern that hop.
- **Six of six pathways** over public TLS on the production hostname.
- **Path A end-to-end: 27.5s** (trace `dfb600f6`), the answer carrying both its
  CRM section and the delegated "External market research (from the Claude
  research agent)" section — so Apex → bridge → Claude works with no laptop in
  the path.

**The architecture decision paid off within minutes, and by accident.** On the
first production sweep `google-adk-a2a` took **39.8s** — an Agent Engine
scale-to-zero cold start. That call is 10s past API Gateway's 30s ceiling and
would have been killed had the bridge been hosted the way every other lab
component is. The 45s budget was defended on paper as "the measured number";
this is the first recorded instance of it actually being needed, and it arrived
unprompted on day one.

Worth keeping in proportion: the other five calls that sweep ran between 4.1s
and 10.3s, all of which a gateway would have served fine. The ceiling does not
bite often. It bites when a platform is cold, which is exactly when a demo is
most likely to hit it.

## Honesty sweeps — run 2026-07-27 (`insights-audit`, `matrix-honesty-sweep`)

Both audit workflows re-run against the whole record before the day's push.
They are adversarial by construction: a finder agent per item, then an
independent agent whose job is to *refute* each claimed problem, so what
survives has been argued against.

| Sweep | Items | Clean | Flagged | Survived refutation |
|---|---|---|---|---|
| insights-audit | 30 insights | 22 | 8 | **4** |
| matrix-honesty-sweep | 51 claims | 50 | 1 | **1** |

**Zero overclaims.** Not one item claimed a capability the lab does not have —
the failure mode was the opposite in every case. All five confirmed defects were
**staleness**: a claim that was true when written and had been overtaken by the
lab's own later work.

- `a2a-async-at-heart` said the D11 SSE demo "exercised" streaming. D11 only
  *scoped* it; the servers advertise `AgentCapabilities(streaming=True)` and the
  client hard-codes `ClientConfig(streaming=False)`, and there is no streaming
  exchange anywhere in the trace archive. The same phrasing had propagated into
  README.md and CLAUDE.md, which is how a single unbacked clause becomes three.
- `orchestration-topology` still published the **prediction** that 3 of 4 legs
  would be joinable "and THAT NUMBER is the deliverable". It was measured the
  next day at **1 of 4**. Predicting a number then measuring it is the method;
  leaving the prediction up as though it were the result is not.
- `telemetry-config-is-not-evidence` reported Codex landing zero datapoints,
  fixed since — under metric names that were never a mirror of Claude Code's.
- `least-privilege-is-identity` claimed the split was verified by a *negative*
  probe (a scope-less token being refused). `identity_preflight.py` is a
  positive check: it proves each identity is sufficient, not minimal — and the
  refusal would be 403, not the 401 the entry claimed.
- The matrix justified the scheduled Claude(AgentCore)↔ADK cell as crossing an
  AWS↔GCP boundary "that no current cell crosses" and as making the GCP→Azure
  result three-cloud. **D40/D41 did both, measured**, a day before the sweep.

**The pattern worth keeping:** in a lab that publishes its findings, the durable
risk is not lying about what works — the honesty rules and the wire archive
catch that. It is the record falling behind the work, and it fails toward
*understating* the lab. A sweep that finds only stale self-underclaims is a
good result, and it is only visible because the audit compares every published
claim against the current state rather than against the day it was written.

---

## 2026-07-27 — WS11: who implements A2A's asynchronous half

The matrix records who *speaks* A2A. It has never recorded who implements the
half that decides hosting shape: `SendMessage` MUST return immediately and
processing MAY continue afterwards. **"MAY" is not a guarantee, and an agent
card cannot tell you** — so this is measured, with
`scripts/a2a_async_probe.py` (submit with `configuration.return_immediately`,
then poll `tasks/get`). `ratio` = submit / total: near 0 means the work outlived
the request, near 1 means the request *was* the work.

| Target | Verdict | submit | total | ratio | polls | state@submit → final |
|---|---|---:|---:|---:|---:|---|
| `agentforce-a2a-shim` (Lambda + API GW) | **async** | 1180ms | 31084ms | 0.04 | 21 | SUBMITTED → COMPLETED |
| `foundry-a2a` (Azure Foundry) | **async** | 4731ms | 19169ms | 0.25 | 6 | SUBMITTED → COMPLETED |
| `foundry-commercial-a2a` | **async** | 3231ms | 9817ms | 0.33 | 3 | SUBMITTED → COMPLETED |
| `google-adk-a2a` (Agent Engine), header missing | **submit-only** | 826ms | — | — | 0 | SUBMITTED → *400 version mismatch* |
| `google-adk-a2a` (Agent Engine), `A2A-Version: 1.0` | **async** | ~500ms | ~4.2s | 0.12 | 1–2 | SUBMITTED → COMPLETED |
| `adk-logistics-a2a` (Agent Engine, warm), `A2A-Version: 1.0` | **async** | ~500ms | ~4.2s | 0.12 | 1 | SUBMITTED → COMPLETED |

**The headline: the API Gateway ceiling is gone, on the component that hit it.**
D41 measured the fan-out legs inheriting API Gateway's 29s integration timeout.
Here the same hosted shim ran **31.1 seconds of work** with a longest single
request of **1.18s**. Nothing was raised, no quota was granted, and the
apigatewayv2→v1 migration D41 costed out is not needed: the work simply stopped
living inside one request.

**Loopback control** (`tests/e2e/test_a2a_async.py`, deterministic slow adapter):

| Adapter work | submit | state at submit | polls | total |
|---:|---:|---|---:|---:|
| 2.0s | 29ms | SUBMITTED | 5 | 2.1s |
| 10.0s | 14ms | SUBMITTED | 21 | 10.3s |
| 45.0s | 13ms | SUBMITTED | 89 | 45.2s |

Submit latency is **flat at 13–29ms while the work grows 22x**. That is the
property being bought: request duration decoupled from agent duration.

### The premise WS11 was written on was wrong, in the lab's favour

The workstream said the blocker was `AdapterExecutor.execute()` awaiting the
adapter inline. **No server change was required.** The a2a-sdk's
`DefaultRequestHandler` already reads `blocking = not
params.configuration.return_immediately` and, when non-blocking, returns after
the first Task event while a tracked background task drains the queue into the
task store. The executor runs as its own producer task, so awaiting inline never
held the response. The lab had the asynchronous half switched on the whole time
and was calling it with `return_immediately` unset — which is a sharper version
of the published `a2a-async-at-heart` finding than the one it replaces.

### Agent Engine's "submit-only" was OUR missing header (resolved 2026-08-11)

The first probe recorded Agent Engine as **submit-only**: it returned a task in
826ms with `TASK_STATE_SUBMITTED`, and every attempt to read the task back
failed. That verdict was wrong, and the cause was ours. Re-probed live
2026-08-11:

- `GET {endpoint}/tasks/{id}` with **no** `A2A-Version` header → 400
  `FAILED_PRECONDITION`: **"A2A version '0.3' is not supported by this handler.
  Expected version '1.0'."** The a2a-python REST handlers are decorated
  `@validate_version("1.0")`, and a *missing* header is interpreted as "0.3"
  (`a2a.utils.constants`) — so the header-less request our client sent was
  rejected as an old-version call. The SDK never sends this header on its own.
- `GET {endpoint}/tasks/{id}` **with `A2A-Version: 1.0`** → the request is
  accepted; a task that exists returns `TASK_STATE_COMPLETED` with its artifact,
  and a task that does not returns a structured 404 `TASK_NOT_FOUND`. **This is
  the fix.**
- `GET {endpoint}/v1/tasks/{id}` (the `/v1`-prefixed path in Google's newer
  Agent Runtime docs) returns 404 on our `v1beta1` reasoningEngines deployment —
  the deployed app serves the **unprefixed** `/tasks/{id}`, which is exactly what
  the a2a-sdk's native REST transport builds. So no path change was needed,
  only the header.

The fix is one line of config: `options.protocol_version: "1.0"` on the
`google-adk-a2a` and `adk-logistics-a2a` targets, which the A2A client turns
into an `A2A-Version: 1.0` header on **every** request (submit, poll, card) —
scoped per-target, never global, because our own Agentforce A2A shim speaks 0.3.
With it, fire-then-poll works end-to-end against the managed Agent Engine: the
supplier-disruption async run now shows **exposure (ADK) → async, 1 poll**
alongside Foundry, with only the OpenAI AgentCore leg falling back (it exposes no
A2A task lifecycle at all — a genuine platform difference, not our bug).

One live-measured wrinkle the header alone did not cover: right after submit, the
first `tasks/get` can still 404 while Agent Engine's task store catches up (the
store is eventually consistent), and a later poll succeeds — a *warm* logistics
deployment took two transient 404s, a cold one takes many more during the ~34s
cold start. The runner rides through a not-yet-visible task for a bounded grace
window (`POLL_NOT_FOUND_GRACE_S`, 45s, wide enough to cover a cold start) before
treating an unretrievable poll as the genuine submit-only degradation. The
distinction is *position*, not error code: the same 404 means "not visible yet"
right after submit and "will never appear" once the task has been read once.

This is still a measured cost of the A2A version spectrum `a2a_compat.py` exists
for — but the cost is "the SDK client must know to send `A2A-Version: 1.0`", not
"Agent Engine cannot serve the async half". It can, and does.

### On Lambda, polling is not free — it is what buys the CPU

The shim result above needs a caveat that changes how it should be used.
Submitting and then **staying quiet for 45s** left the task at
`TASK_STATE_WORKING` — well past the ~30s the underlying Agentforce call takes.
It reached COMPLETED only after 12 further polls, at t+67.7s.

**Lambda freezes the execution environment when the response is sent**, so the
background consumer gets no CPU between invocations. Each poll thaws the
container and the work advances in that slice. So:

- The ceiling is genuinely dissolved — no single request is long.
- But there is **no free background execution**. Total wall-clock got *worse*
  (67.7s quiet-then-poll vs 31.1s polled steadily); a client that backs off
  aggressively starves the work it is waiting for. This exactly inverts the
  usual polling advice.
- And it rides on `InMemoryTaskStore` in one warm container. Nothing guarantees
  a later poll reaches the same instance, so a task can become unreadable
  through no fault of the protocol. A durable task store is the prerequisite for
  treating this as production-shaped, and it is not built.

The always-on hosts (Foundry, and the lab's own Fargate bridge) have neither
problem: their work progresses whether or not anyone is looking.

---

## 2026-07-28 — WS7: why the console has to move, and what the proxy actually blocks

**The operator's corporate proxy blocks the lab's domain at DNS — the whole
domain, not a hostname.** Measured on one machine within one minute:

| Host | With the proxy on |
|---|---|
| `console-lab.agenticthings.com` | no resolution, **30s timeout** |
| `agenticthings.com` (apex) | **30s timeout** |
| `zzz-nonexistent.agenticthings.com` — never existed | **30s timeout** |
| `zzz-nonexistent.cloudfront.net` — never existed | **NXDOMAIN in 0.05s** |
| `*.execute-api.<region>.amazonaws.com` | HTTP 200 in 0.1s |

The discriminator is **timeout vs NXDOMAIN**. A fast "no such host" proves the
resolver is answering for that domain; a 30s hang is policy. A name that has
never existed on the lab domain still hangs, so the block cannot be worked
around by pointing the name somewhere else — DNS resolution happens before any
of that.

**Two things follow, and the second is the one that matters.**

1. A custom domain and this proxy are mutually exclusive until the domain is
   recategorised. Not a lab problem to solve in code.
2. **Hosting is not a front-door problem.** A CDN in front of the console was
   built, measured working (HTTP 200, 301,525 bytes, 0.45s with the proxy on)
   and then **reverted the same day**: it removed a toggle, not a dependency.
   The dependency is that the console, nine protocol faces and the Managed
   Agents watcher run on a laptop — which is WS13, and which makes the toggle
   irrelevant, because with nothing running locally there is no cost to
   dropping the proxy while looking at the console.

The measurement is kept because it is the evidence for WS13's shape: **the
browser-facing surface is the only one the proxy touches at all.** Apex reaches
the bridge, Foundry and Agent Engine reach the shim, and AgentCore is SigV4 —
all from clouds, none through the proxy. So the lab domain keeps working for
every machine-to-machine hop, and the console is the single exception.

### The SSE finding that survived the revert

The live tail emits only when trace hops land, so a quiet lab sends no bytes at
all. Every intermediary reads that as a dead connection — the ALB the console
moves behind idles at 120s, proxies and CDNs commonly at 30s. The drop itself is
harmless; **the data loss is not.** The browser's `EventSource` reconnects, but
the new generator rebuilds its per-file offsets from current EOF, so hops that
landed during the gap are never sent and the tail under-reports with no error
anywhere. Fixed with a 15s keepalive comment; the same applies to the Lab Guide
chat, which is SSE on the same origin.

---

## 2026-07-28 — Honesty sweeps after WS11/WS12/WS13

Both audit workflows run against the record as it stood after the overnight
session. **Insights: 33 audited, 26 backed, 7 problems. Matrix: 53 cells
audited, 51 consistent, 2 discrepancies** — both upheld under adversarial
verification. Every finding was a claim the record had let drift, and **all
seven insight problems were the record lagging the work, not overclaiming it**
— the same direction the previous sweep found.

| Entry | Problem | Fix |
|---|---|---|
| `a2a-async-at-heart` | Still said the lab drives A2A synchronously with "no callback leg on the wire" — WS11/D47 made that false **the day before** | Rewritten around the measurement; status `observed` → `measured` |
| `timeout-budget-stack` | Published `→ remote agent 40s` while the shipped `.env` sets `CLAUDE_ANSWER_TIMEOUT_S=100`, and contradicted itself two sentences later | Chain corrected to 100s, with 40s named as the superseded value |
| `managed-vs-self-hosted` | Carried the pre-measurement **assumption** of ~5–10s managed provisioning, refuted at ~2s on 2026-07-25 | Replaced with the measured numbers; re-opened for sign-off |
| `orchestration-topology` | Leading comment still read "not yet measured — the platform agents are not deployed" after WS8's live runs | Comment corrected; both named preconditions had been met |
| `fabricated-attribution` | No `review:` key at all, despite growing new findings since it was written | `review: required` added |
| `least-privilege-is-identity` | Cited D25, which does not discuss the topic | Refs corrected |
| `antipattern-lens` | Said "Salesforce's MCP inbound is gated beta" — the matrix's `blocked-beta` row is Agentforce as an MCP **client**, the opposite direction | Reworded to the direction the record supports |
| `plan/02-matrix.md` | Said the **AWS→GCP** federation direction was unmeasured; D41 measured it the same day the paragraph was last touched | Corrected; residual gap narrowed to AgentCore-as-caller |
| `config/targets.yaml` | Described `foundry-rest` as "Entra ADC auth" — the exact thing `platforms/foundry/core.py` refuses to do (D39) | Corrected to "explicit service principal" |

**The pattern, now seen three sweeps running:** the durable risk in a lab that
publishes its findings is not that a claim was too strong when written — it is
that the claim stopped being true and nobody re-read it. Two of these were
falsified **by work done in the same 24 hours**, which is the shortest
staleness interval yet recorded and the strongest argument for running the
sweep at the end of a session rather than before a demo.

## Data 360 Zero Copy federation (M10 / WS19) — measured 2026-08-08

First read of the `lab.trace_events` obs store *through* Salesforce Data 360,
after the Zero Copy data stream was created (DLO `A2A_Lab_Trace_Events__dll`,
primary key `hop_seq`, acceleration OFF so rows stay resident in us-east-1).
Queried over the **core REST API** (`/services/data/v62.0/query`) as the
`a2a_lab_obs` client-credentials identity — the same credential and path the
M11 harvest already uses for STDM DMOs, so no new auth. The tenant is in
**eu-central-1**, the Aurora cluster in **us-east-1**, so every row crossed the
Atlantic live (no copy, no ETL):

| Query | Result | Wall time |
|---|---|---|
| Full-table paginated pull (`SELECT` 7 cols, 2 pages of ≤2000) | **2,999 rows**, 957 distinct traces | **7.4s** |
| 5-row `ORDER BY ts_at__c DESC LIMIT 5` | 5 rows | sub-second |

Aggregate shape (computed client-side from the 2,999 federated rows — see the
caveat below on why not server-side): 8 protocols (rest 756, agentforce-api
611, internal 585, a2a 367, mcp 276, agentcore-http 234, managed-agents-api
139, foundry-api 31); status ok 2,828 / error 171; avg latency by protocol
ranged mcp ~1.8s … foundry-api ~21.5s. These match the console's own view of
the same table — **the point of M10: two independent readers, one table, and
Zero Copy means they never diverge.**

**What works and what doesn't, over the core REST API against a federated DLO:**
- **Row `SELECT` (with or without `WHERE`/`ORDER BY`/`LIMIT`) federates cleanly.**
  This is the end-to-end proof the connection, data stream, and query path all
  work.
- **`COUNT()` → `UNSUPPORTED_QUERY`; `FIELDS(ALL)` and `GROUP BY`/aggregates →
  `UNKNOWN_EXCEPTION`.** Aggregation over a Zero Copy DLO is not served by core
  SOQL — it wants the **Data Cloud Query API** (`/api/v2/query`, ANSI SQL) or
  Tableau's own query pushdown. Not a federation fault; a SOQL-surface limit.

**This is NOT the L5.8 headline number.** 7.4s is a REST full-table pull, useful
as a floor. The number WS19 exists to publish is the **Tableau Next dashboard's
end-to-end render** (item 6c) — the true EU→US round trip a business user sees —
recorded here once the dashboard is built.
