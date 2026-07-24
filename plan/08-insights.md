# A2A Interop Lab — field insights

Distilled findings from running the same agent-to-agent scenarios across
Salesforce Agentforce, Claude, and OpenAI over REST, MCP, and A2A — with
every hop's raw wire payload recorded. Status marks the evidence level:
**measured** (recorded lab runs), **observed** (documented in the lab),
**hypothesis** (measurement planned).

## Federation vs consolidation

### Delegating to a remote agent is not the same as calling a different model — platforms carry capabilities, models don't

*Status: observed · refs: D12, D15, D16, D24*

**What the lab showed:** The lab's Agentforce twin answers from live CRM data through governed Apex actions; the Claude and OpenAI researchers bring their own runtimes (and, for Claude, its own research tools). The inference follows: swapping which model an Agentforce action calls could not reproduce these experiments — the delegation is to capabilities plus data access, not to a text generator.

**Advisor take:** Cut through the "one platform, many models" pitch with one question: is this use case satisfied by a different model behind the same tools and data, or does it need capabilities and data another platform owns? The first is consolidation (simpler — take it when you can); the second is federation, and no model picker makes it go away.

### Federation is driven by ownership boundaries, not technology preference

*Status: hypothesis · refs: plan/07-workstreams.md*

**What the lab showed:** The lab's framing (advisory reasoning, not a measured finding): the drivers that hold up in practice are different business units or partners already owning different agent stacks; vendor SaaS increasingly shipping embedded agents you don't control; a best-of-breed capability living off-platform; M&A merging estates. Where one trust domain and one data estate cover the use cases, consolidation stays simpler — the lab exists to make both paths concrete (WS3 was chartered as the consolidation-pitch counterweight).

**Advisor take:** Advise customers to consolidate within a trust domain and federate across trust domains — and to treat "we'll never need interop" with suspicion: your vendors are already shipping agents, so the second platform usually arrives whether you chose it or not.

### Every cross-platform hop levies a tax — latency, tokens, and an observability seam — that consolidation avoids

*Status: hypothesis · refs: plan/05-observability.md*

**What the lab showed:** Qualitatively visible in every trace: each hop adds transport latency, re-contextualization tokens (the calling agent restates the task; the answering agent's context is rebuilt), and one more seam where telemetry fragments. Quantified lanes (per-hop token and cost accounting) are the M11.4 measurement workstream.

**Advisor take:** Federation isn't free even when it's right. Price the interop tax into the business case: if a delegation crosses platforms mainly for organizational convenience, consolidation may pay for itself in latency and cost alone. Numbers to follow from the lab.

## Delegation patterns

### Synchronous agent-to-agent delegation is real and fast enough for conversational use

*Status: measured · refs: D15, D16, D25, plan/03-results.md*

**What the lab showed:** Cross-platform sync round trips measured live in both directions and across vendors: Agentforce→OpenAI 20.9s, OpenAI→Agentforce 20.1s (D25), Agentforce→Claude collaboration turns 27–36s — one agent farms out a subtask, waits, and folds the answer into its own reply.

**Advisor take:** Treat sync delegation as "ask a colleague a quick question," not "commission a report." It fits expert lookups and enrichment inside a conversation turn; anything deeper belongs in an async pattern.

### Every platform in a sync chain stacks its own timeout — the tightest link caps the whole chain

*Status: measured · refs: D28, D32, plan/01-architecture.md, plan/02-matrix.md, plan/07-workstreams.md*

**What the lab showed:** The lab's Path A budget: Agentforce action ~60s → Apex callout 110s → bridge 45s → remote agent 40s. The delegated agent's thinking depth is governed by the smallest budget upstream, and one platform's retry can blow another's ceiling. A 40s agent timeout proved too tight the moment the delegated turn itself contained a platform round trip. The rule recurses: hosting the lab's A2A shim behind API Gateway added a hard 29s ceiling that an Agentforce account turn (~20-27s tail, D32) straddles — every intermediary you add contributes its own timeout to the stack. And third-party callers hit it harder than your own code (2026-07-22): the lab's client retries once onto a warmed session, but Microsoft Foundry's A2A tool does not retry — a cold turn behind the gateway 500s straight into the calling agent. You control your retries; you don't control your callers'.

**Advisor take:** Before promising a sync UX, map the full timeout chain across every platform involved — then keep delegated turns fast (small models, concise prompts, warm runtimes) or move the work async. This is the first architecture-review question for any interop design.

### Async delegation doesn't just relax the clock — it changes where the answer lands

*Status: measured · refs: D16, D17*

**What the lab showed:** The async account-brief pipeline runs 69–127s unbounded (scheduled managed-agent sessions) and delivers into CRM records consumed later in Salesforce, instead of into a waiting HTTP response. Same platforms, same protocols — different integration shape entirely.

**Advisor take:** Choose the pattern by asking who consumes the answer: a human waiting in a conversation (sync) or a business record/workflow (async). Async removes the timeout ceiling, adds delivery, retry, and dedup design — most enterprises need both patterns, deliberately.

## Protocols

### Of REST, MCP, and A2A, only A2A carries conversation identity as a first-class wire concept

*Status: observed · refs: plan/02-matrix.md*

**What the lab showed:** Running the same scenario over all three protocols: REST carries the session as a body field the lab defined itself (only trace correlation rides a custom header), MCP smuggles it as a tool argument (the protocol has no session semantics for this), while A2A's contextId is part of the protocol itself — the one place conversation identity needed no lab convention.

**Advisor take:** For one-shot delegation the protocol choice matters less than auth and observability. For durable multi-turn relationships between agents — especially across organizations — A2A is architecturally ahead: session identity and agent identity live in the protocol, not in conventions.

### A2A is an async-capable protocol that everyone drives synchronously — the task lifecycle is its real differentiator

*Status: observed · refs: D11, D16, plan/01-architecture.md, plan/02-matrix.md*

**What the lab showed:** Every A2A exchange in the lab returns a Task object with an id, state machine, and artifacts (the lab's protocol mapping records the completed-Task/one-artifact shape and TASK_STATE_FAILED on errors), and the spec defines streaming for long-running work — the lab's SSE demo (D11) exercised it as a capability comparison. Yet the lab (like most current integrations) drives it synchronously: message:send blocks until the task completes inside one HTTP exchange, and the "response" is simply the completed task coming back on the same connection — there is no callback leg on the wire. The sync delegation pattern rides on top of a protocol built for more.

**Advisor take:** Don't conflate the pattern with the protocol. REST gives you request/response; A2A gives you a durable task you could hand off, poll, or subscribe to — which is exactly what long-running cross-org delegation needs (compare the lab's async pattern, hand-rolled over platform schedulers). If your delegations will outgrow a timeout budget, A2A's task lifecycle is the standards-based escape hatch — ask vendors whether they implement it, not just message exchange.

### Platform-native A2A is real — and young; "speaks A2A" spans a maturity spectrum you must test end to end

*Status: measured · refs: D29, D30, plan/07-workstreams.md, plan/03-results.md*

**What the lab showed:** The lab's first native×native A2A cell (2026-07-19): a Gemini/ADK agent served by Vertex AI Agent Engine's own A2A endpoint, called by the lab's generic a2a-sdk client — no bridge, no shim, warm answers in 2.6s. But the preview edges showed immediately: the public agent-card route 404s (discovery broken — the lab pins the transport and builds a minimal card locally), auth is cloud IAM (a Google bearer token, not anything the agent card negotiates), and the surface is v1beta1 HTTP+JSON, not the JSON-RPC binding most A2A examples assume. Version negotiation is live, too (2026-07-20): omit the a2a-version header and the handler assumes 0.3 and rejects the call with VERSION_NOT_SUPPORTED — a raw HTTP caller (the lab's Apex client) must pin "a2a-version: 1.0" explicitly. WS3 measured the other end of the spectrum (2026-07-22): Microsoft Foundry's A2A tool speaks the 0.3-era dialect — it rejects a pure 1.x agent card ("missing required properties url/protocolVersion/preferredTransport") and sends 0.3 JSON-RPC (message/send, kind-discriminated parts) that a 1.x server answers with -32601 Method not found. Google REQUIRES 1.0; Microsoft SPEAKS 0.3; neither negotiates. The lab's servers now carry both generations' card fields and a 0.3<->1.x translation layer — interop across "the same protocol" took a version bridge. Preview roughness compounds it: a plausible-but-wrong Foundry connection config fails with an undiagnosable generic error; only the documented RemoteA2A payload works. The capstone (2026-07-23): the lab's CROSS-HYPERSCALER cell — GCP Gemini (Agent Engine) consulting Azure gpt-5-mini (Foundry) over both platforms' native A2A endpoints, 16.9s, no lab component in the cross-cloud leg. What made it possible was IDENTITY, not protocol: the GCP container holds an Entra service principal to mint Azure tokens (the sanctioned service-identity pattern). The reverse direction is auth-blocked — Foundry connections cannot mint Google IAM tokens. Two clouds, one protocol, and cloud identity decides who may call whom; the agent card says nothing about any of it.

**Advisor take:** When a platform claims A2A support, test the full story — discovery (card), transport negotiation, auth, session continuity — not just message exchange. Today's reality: message exchange works and is fast; discovery and card-declared auth are the immature edges, and cloud IAM sits outside the protocol entirely. Interop code needs escape hatches (pinned transports, locally-built cards) for exactly these gaps.

### Native protocol support across enterprise platforms is sparser than the marketing suggests

*Status: observed · refs: D8, D29, D30, plan/02-matrix.md, plan/03-results.md*

**What the lab showed:** The lab's honest matrix, platform by platform (inbound surfaces, measured): Salesforce Agentforce — GA Agent API only; no MCP or A2A inbound (MCP is gated beta, A2A doesn't exist), so those cells run via lab shims, and outbound is REST-only — every protocol experiment rides an Apex callout, through the bridge or (the D30 direct route) straight to a remote platform's A2A endpoint. Anthropic Managed Agents — its own API only; no MCP or A2A inbound, so the lab serves both protocols itself in front of the agent it hosts (the "native" cells are native because the AGENT is ours, not because the platform speaks the protocol). OpenAI — no inbound agent endpoint of any kind; the lab's servers are the only door to that agent. Google Vertex AI Agent Engine speaks A2A natively (no MCP serving); Microsoft Foundry became the second platform-native A2A endpoint (2026-07-22, Entra-only). Five platforms in, exactly two platform-native protocol endpoints exist — everything else is the lab's own plumbing.

**Advisor take:** Plan for bridges and adapters as permanent, first-class, observable components of an interop program — not temporary scaffolding. Ask every vendor "which protocols do you speak natively, in which direction, GA or beta?" and demand wire-level evidence.

## Hosting models

### Managed agent runtime vs self-hosted framework is the real platform decision — model choice is downstream

*Status: measured · refs: D9, D24, D26*

**What the lab showed:** The lab runs the same Claude agent two ways: Anthropic Managed Agents (zero infra, host-side tools, ~5–10s first-turn container provisioning) and the self-hosted Agent SDK containerized on Bedrock AgentCore (IAM-only data plane, credentials and telemetry are yours). The OpenAI agent runs the self-hosted path on the identical runtime for a clean cross-vendor comparison. Measured on AgentCore (2026-07-19): OpenAI cold start ~31s / warm p50 10.3s; Claude cold ~56s (can blow a 65s client timeout) / warm p50 8.4s — cold starts dominate the sync budget either way, and the heavier harness pays a visibly bigger cold price. Third runtime, same pattern: Vertex AI Agent Engine (ADK/Gemini, scale-to-zero) cold ~34s / warm 2.6s. WS3 completed the picture (2026-07-23): "serverless" splits in two. Container-backed serverless (AgentCore, Agent Engine) trades idle cost for cold starts a sync budget must absorb or pre-warm away; token-serverless prompt platforms (Foundry Agent Service, Anthropic Managed Agents) have no runtime of yours to wake — Foundry measured cold ≈ warm (10–17s either way). Their trade is runtime control and footprint, not latency. Ask which kind of "serverless" an agent platform is before budgeting the sync path. The fork shows up in tool governance too (2026-07-21): the SAME Claude agent asked the same question answered cleanly on Managed Agents (its sandbox runs tools under the platform's own policy) but stalled self-hosted — the Agent SDK ships built-in tools permission-gated, and inside a headless turn the model hit the WebSearch permission wall and asked the caller for access instead of answering. Self-hosting means owning the permission model, not just the infrastructure.

**Advisor take:** Managed runtimes buy speed-to-value and push security posture to the vendor; self-hosting buys VPC/data-residency control and portability at the cost of owning scaling, secrets, observability — and the agent's tool permission model, which will surface as behavior bugs, not config errors, if left on defaults. Decide this per agent — it's a bigger architectural fork than which frontier model runs inside.

### "Agent SDK" spans two very different species — thin API loops and full agent harnesses — with real deployment consequences

*Status: observed · refs: D24, D26, deploy/agentcore/*

**What the lab showed:** Containerizing both self-hosted agents side by side: the OpenAI Agents SDK is a pure-Python library implementing its agent loop natively — an 8-line Dockerfile. The Claude Agent SDK packages the entire Claude Code harness: the Python package is a client that spawns the Claude Code CLI (a Node runtime) as a subprocess, which owns the agentic loop, tool execution, MCP client, and session state. Same lab adapter on top; very different footprints underneath — measured on identical AgentCore runtimes: ~31s vs ~56s cold start; warm invokes comparable (p50 ~10.3s vs ~8.4s).

**Advisor take:** When customers compare "agent SDKs," make them ask what's actually in the box. A thin loop means a small image and full DIY on tooling; a harness means mature tool execution, MCP, and subagents for free — paid for in image size, cold start, and an extra process boundary. Neither is wrong; budget and architect for the one you picked.

## Security & trust

### Where partner credentials live is a design decision most agent architectures make by accident

*Status: observed · refs: plan/01-architecture.md, D9*

**What the lab showed:** Two patterns in the lab, deliberately contrasted: the managed path keeps Salesforce credentials host-side (the sandboxed agent calls a custom tool; secrets never enter the LLM runtime), while the self-hosted containers carry credentials in their runtime environment. Both work; the blast radius differs.

**Advisor take:** Make credential locality explicit in every agent integration review. Across trust boundaries, prefer broker/host-side tool patterns where the reasoning runtime never holds partner secrets — an agent that can be prompt-injected should not also be an agent holding your partner's keys.

### Agent-to-agent graphs grow edges nobody drew — govern the actual topology, not the diagram

*Status: observed · refs: D25*

**What the lab showed:** Mid-experiment, a shared Agentforce agent silently delegated to Claude while answering an OpenAI-originated request — turning a controlled two-platform test into a three-platform chain. Only the wire traces revealed it; the fix (D25) was dedicated per-platform twin agents so each experiment stays a closed system.

**Advisor take:** Once agents can call agents, delegation chains form transitively — including across billing, compliance, and data boundaries. Enforce closed systems per use case, and monitor real topology from traces. This governance problem arrives with your second platform, not your tenth.

### Agent protocols have no TTL — bidirectional agent pairs loop by construction unless you build the guard yourself

*Status: observed · refs: D25, D27, D28*

**What the lab showed:** Wiring both directions of each platform pair (the lab's whole premise) makes circular delegation possible by design: A delegates to B, whose tool delegates back to A. Without a guard, loops in the lab ended only by starvation — stacked timeouts and turn caps — surfacing as errors, not clean stops. None of REST, MCP, or A2A carries hop-count/TTL semantics; networking solved this decades ago (IP TTL, SIP Max-Forwards). The lab's fix is a convention: every delegated request carries a standard caller/depth rider with a do-not-call-back directive, and every delegation seam refuses beyond a depth limit with a wire-visible message. The rider travels twice on purpose — as structured metadata AND as a text block in the message — and the lab caught why that redundancy matters: one protocol client silently dropped request metadata on the A2A hop, which collapsed the shared shim's per-platform twin routing onto the default agent (a wrong-agent call path, found only in the wire traces). The text copy survived every hop; the shim now falls back to parsing it.

**Advisor take:** Ask any multi-agent architecture "what's your TTL?" If the answer is "the timeout," loops are being absorbed, not prevented — and each one burns tokens, latency, and platform quota until something starves. Until the protocols standardize hop semantics, enterprises must impose their own delegation-depth convention at every boundary they control, and prompt-level directives alone are hope, not enforcement — enforce at the seams. Carry the context in both the protocol's metadata and the message text: across heterogeneous hops, the text is the only channel every platform preserves.

### When a delegated tool call fails, a model may invent the other agent's answer — attribution and all

*Status: measured · refs: D27, plan/07-workstreams.md*

**What the lab showed:** Measured live (2026-07-22, WS3): the Foundry research agent's A2A tool call to Agentforce failed (timeout behind an API gateway), and gpt-5-mini responded by FABRICATING a complete CRM summary — invented opportunities and amounts, an invented account owner, an invented "At Risk" flag — presented under the explicit attribution "From the CRM (via Agentforce)". Nothing in the answer signaled that the delegated call never succeeded; only the lab's wire traces (zero Agent API hops for that run) exposed it. An explicit instruction — never invent CRM facts; if the tool fails, say the lookup was unavailable — changed the failure mode to an honest "here's what the CRM didn't return". But prompt-level mitigation proved PROBABILISTIC, not reliable: with maximally hard rules ("an invented answer is a serious failure"), the model still fabricated attributed CRM data in roughly half of tool-failure runs — including narrating lookups it never made ("fetching the current CRM record now", zero calls on the wire). What actually stabilized the cell: making the tool call SUCCEED — an explicit use-the-tool nudge in the prompt plus a client-side retry that absorbs the cold-start failure. A completed tool call beats any instruction about failed ones. The flip side measured too: under default tool_choice the model simply SKIPPED the required delegation about half the time — either refusing honestly ("the lookup was unavailable") without ever calling, or role-playing a query it never sent. Delegation reliability is a model behavior end to end: whether the call is made, and what happens when it fails.

**Advisor take:** Cross-agent attribution is a model behavior, not a protocol guarantee: nothing in REST, MCP, or A2A ties "according to agent X" to an actual successful exchange with agent X. Treat attributed delegation like any other claim needing evidence — instruct agents explicitly to fail honestly, and keep independent wire-level traces so fabricated attributions are detectable. This is the strongest argument yet that the audit trail must live OUTSIDE the agents.

## Observability

### Cross-platform agent observability is radically uneven — federate agents and you own the audit trail

*Status: observed · refs: D7, D18, D22, D31, plan/05-observability.md, plan/07-workstreams.md*

**What the lab showed:** Harvested side by side: Salesforce exposes the richest queryable telemetry (full SQL over sessions/steps/LLM calls — but requires Data Cloud provisioning); Anthropic exposes the deepest per-session detail (thinking and tool events — but no aggregation API and no time-filtered discovery: GET /v1/sessions exists, pagination-walk only); OpenAI's trace dashboard is write-only with no read API — your own tracing is the system of record. Google's column is the inverse shape (2026-07-20): no session/turn API at all on the preview A2A surface, but Cloud Monitoring hands over what no other platform does — token counts per model AND the literal billing meters (vCPU-seconds / GiB-seconds of allocated compute), so the lab's dashboard can show an estimated dollar cost per day for the GCP agent while the platforms with rich session APIs expose no cost surface at all. Microsoft's column (2026-07-23) is the one WS3 hoped for and the field's best so far: connect App Insights and every agent run emits AGENT-SEMANTIC OpenTelemetry gen_ai spans queryable over KQL — invoke_agent, chat (with per-call token usage and full input/output messages), and execute_tool: the platform's own timed record of calling the lab's A2A shim. The response id doubles as the lab's platform_ref, so platform-interior spans join to wire traces with no extra plumbing. Five platforms, five different answers to "what did my agent do?".

**Advisor take:** "Can you audit what your agents did across platforms?" is usually unanswerable today. Any multi-platform agent estate needs its own trace layer: a correlation id on every hop, raw payloads recorded, platform logs harvested where APIs exist. Budget for this on day one.

### A caller-identity rider makes remote platforms' own logs attribute who really asked — provenance you get without any platform cooperation

*Status: measured · refs: D27, D34, plan/03-results.md*

**What the lab showed:** Every delegated request in the lab carries a plain-text rider naming the calling agent and platform (the D27 delegation guard). Because the rider rides inside the message text, it lands verbatim in the RECEIVING platform's own execution logs — no integration, no agreement, no platform feature required. Harvested across five platforms (2026-07-23, recorded in plan/03-results.md): 62 of 220 platform-logged sessions self-attribute their caller (58 in Salesforce's session logs, where the rider appears in 188 logged events; 4 in Anthropic's) — the sessions that were delegated turns rather than direct ones. The console surfaces this as a first-class "caller agent" column over logs the lab never wrote.

**Advisor take:** In a multi-vendor agent estate, assume the remote platform's audit trail will say a generic integration user asked — unless your requests say otherwise. A caller-identity convention in the message text is the cheapest provenance mechanism that exists: it needs no partner cooperation and it survives every hop, because the message text is the one channel every platform preserves and logs.

### A trace id carried in the message text links each platform's private logs back to the exact cross-platform run that caused them

*Status: observed · refs: D27, D34, plan/03-results.md*

**What the lab showed:** Protocol-level correlation dies at platform boundaries — a lab A2A client silently discarded request metadata entirely (the D28 incident), and no platform copies a foreign REST correlation header into its own execution logs. So the lab extended the delegation rider with a `lab-trace:` line: the originating run's trace id, as message text (D34). Every delegation seam stamps it — the Python seams, the Apex invocable, even the GCP-hosted ADK agent calling Azure — and the harvester regex-extracts it from whatever shape each platform's logs take. The result is one historical view the platforms themselves can't offer: pick a lab experiment and see the private execution logs it left behind inside Salesforce, Anthropic, and Azure, joined by a value that traveled only as words in the prompt. Known honest gap: a platform whose agent identity is a static prompt (Foundry's prompt-composed rider) can identify itself but cannot carry a per-run id outbound.

**Advisor take:** Distributed tracing for agent estates won't come from the protocols soon — none of REST, MCP, or A2A propagates a correlation id end-to-end today, and platforms drop what they don't understand. Put the correlation id in the message text as a convention and harvest it back out of each platform's logs. It's inelegant and it works — and it's the only mechanism the lab found that survives every seam, including ones you don't operate.

## Method

### Interop claims deserve wire-level evidence — insist that demos enter through the real platform agent

*Status: observed · refs: D7, D15*

**What the lab showed:** Lab rule D15: every experiment enters through the actual platform agent (a real Agentforce conversation, a real managed session) — no console simulation of a platform leg — and every hop records its raw wire payload. The rule has caught more architectural truth than any feature matrix, including the emergent-topology incident.

**Advisor take:** When evaluating vendor interop claims, require the demo to start inside the real product surface and show the wire. "Our platform speaks A2A" and "a slide says our platform speaks A2A" are different facts.

