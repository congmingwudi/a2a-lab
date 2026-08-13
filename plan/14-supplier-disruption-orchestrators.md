# The supplier-disruption orchestrators — three controllers, and where async runs

This doc is the single narration of the WS8 supplier-disruption scenario built
**three times**, once per primary controller, and — the WS11/D76 part — of how
each one takes (or cannot take) the *asynchronous* half of A2A. The short claim:
there is **one** submit/poll implementation, and it runs in a **different place**
under each controller; one controller cannot run it at all and takes it only by
proxy.

Sources: D61 (the third orchestrator on Agentforce and the fan-out topology),
D41 (the fan-out as remote MCP tools + the API-Gateway ceiling), D47
(fire-then-poll as a first-class client shape + the separate-invocation worker),
D74 (the bespoke MCP async tools + the flapping-404 ride-through), D75
(the worker's non-gateway per-leg budget), D76 (fire-then-poll for all three
controllers). Console copy lives in `fanoutControlsDetailHtml`
(`src/console/static/index.html`) and the scenarios in `config/scenarios.yaml`.
The estate diagram is `plan/09-deployment-map.md`.

## One scenario, one axis

All three orchestrators coordinate the **same three business-unit agents** and
write one consolidated brief:

- **Logistics** — Google ADK on Vertex AI Agent Engine (`adk-logistics-a2a`)
- **Commercial / Legal** — Microsoft Foundry, gpt-5-mini (`foundry-commercial-a2a`)
- **Customer operations / comms** — OpenAI agent on AgentCore (`openai-agentcore`)

Only the **orchestrator** differs, and the axis it varies is **who owns the
concurrency** (D61):

| Orchestrator | Controller | Who schedules the legs | Where a poll loop can run |
|---|---|---|---|
| `supplier-disruption-cma` | Claude Managed Agents | a **host tool** (our code) OR the **model** (remote MCP tools) | in our tool, or in the model's turns |
| `supplier-disruption-adk` | Google ADK on Agent Engine | a **declared graph** (`ParallelAgent`) | inside the graph branch, in the GCP container |
| `supplier-disruption-agentforce` | Agentforce (Agent Script) | a **serial Apex callout budget** | nowhere on-platform — at the bridge, by proxy |

The business-unit legs, the trace layer, the D27 delegation rider, and the
`orchestration.runner` seam are shared; only the controller and *where its loop
turns* change. That shared seam is the point: `run_one` / `dispatch`
(`src/orchestration/runner.py`) carry the capability detection, the flapping-404
ride-through, the `async→sync` fallback for a leg with no task lifecycle, and the
partial-failure contract — written once, reused three times.

## Sync vs async — the shape being reused

**Synchronous** dispatch is one blocking call per leg: the call returns only when
the business-unit agent is done. Every run before WS11 used this.

**Asynchronous (fire-then-poll)** is the A2A long-running-task pattern: *submit*
the work and get a task id back in ~1s without the leg having run, then *poll*
`tasks/get` until the task is terminal. No single request is held open across the
agent's work, which is what dissolves the API-Gateway integration ceiling that
bit the remote-MCP fan-out (D41: 29s HTTP-API ceiling → 25s/leg, not raisable; a
cold platform overruns it). Async is selected per run from the console's dispatch
radio; absent/unknown → sync (clamped), never an error.

Two per-platform behaviours are the same under all three controllers, because all
three poll the same two A2A legs — these are WS11 findings, not bugs to smooth:

- **Google Vertex AI Agent Engine — eventually-consistent store.** A `tasks/get`
  can return `MethodNotFoundError` (a bare 404) not just right after submit but
  *flapping* between later polls, even after an earlier poll already read the task
  (2026-08-12). A single successful read PROVES the endpoint serves tasks, so the
  runner records the 404 as `waiting for task` (pending, not a hard failure) and
  keeps polling to completion rather than falling back to sync. Agent Engine also
  *appeared* submit-only until 2026-08-11, when the poll failure was traced to a
  missing `A2A-Version: 1.0` request header (a client default), not a platform gap
  — pinned per target, fire-then-poll works there (D47, WS11 item 12).
- **Microsoft Foundry — steady store, slow cold start.** Its polls return a
  fully-formed status every call (no flapping). The catch is latency: a cold
  Foundry leg (gpt-5-mini) measures ~26.5s, and async is what lets it finish
  rather than one held-open request timing out.

The **AgentCore comms leg** has no task lifecycle; async falls back to a blocking
call, recorded `async→sync` in the coverage line rather than hidden.

## Controller 1 — Claude Managed Agents (`supplier-disruption-cma`)

Two topologies, swapped per run via `agent_with_overrides` (D41):

- **host-side tool** — one **custom tool**. On Managed Agents a custom tool is
  *host-executed*: the model decides only WHEN to call it; the tool body runs in
  the backend that drives the session (the console's own Fargate container, or
  `scripts/run_fanout.py` on a laptop), never in Anthropic's sandbox. The fan-out
  (`orchestration.dispatch` over `asyncio.gather`) runs there and the host picks
  the order. Async here means **our client code** runs submit+poll inside one
  custom-tool call, so the whole fan-out is one model turn and the polling is
  invisible.
- **remote MCP tools** — one tool per business unit on the lab's hosted fan-out
  MCP server on AWS Lambda (`src/fanout_mcp`, D41). Nothing is attached to the
  session, so the **model** chooses what to call. Async swaps the session's SYSTEM
  prompt so the model calls `submit_<unit>` (task id in ~1s, leg not yet run) then
  `check_task` until every unit is terminal — the A2A submit/poll shape
  re-expressed as MCP tools that MIMIC the lifecycle (D74; not the native
  `io.modelcontextprotocol/tasks` extension, whose client support we could not
  verify). Because the model is the poll loop, **each `check_task` is one model
  turn**, so the turn count IS the poll count — the measurement of who schedules
  the async half, made visible rather than hidden in our code.

The MCP async worker runs **off** the gateway path — a self-invoke
`InvocationType='Event'` Lambda backed by a durable task store
(`src/fanout_mcp/tasks.py`), because Lambda freezes background work after the
response returns (D47). It gets its own larger per-leg budget
(`async_leg_timeout_s()`, default 120s) rather than inheriting the 25s the sync
`consult_*` tools take from the gateway ceiling — inheriting the sync budget
killed a cold Foundry leg at 25s though its task reached COMPLETED (run
`7ef510e2`, fixed in D75).

## Controller 2 — Google ADK on Agent Engine (`supplier-disruption-adk`)

The fan-out is **declared** in the agent graph (a `ParallelAgent`), so no host and
no model schedules the legs — the ADK framework does. What the dispatch radio
changes is what each unit's A2A tool does *inside* that graph.

`_leg_tool` (`src/platforms/adk/agent.py`) routes through the shared
`orchestration.runner.run_one` rather than a bespoke A2A call, so
`dispatch_mode`/`trace_id` thread from the inbound A2A task metadata (`execute()`)
→ `build_fanout_orchestrator` → each `ParallelAgent` unit's tool. The poll loop
therefore runs **inside the `ParallelAgent` branch, in the GCP container**, off
any gateway. Nothing calls back to a lab host, so async gets the full off-request
per-leg budget (`async_leg_timeout_s()`), bounded only by the orchestrator's own
A2A deadline. `sync` leaves `timeout_s` None so `run_one` reads the small,
gateway-safe env; `async` passes the full budget.

## Controller 3 — Agentforce (`supplier-disruption-agentforce`) — async by proxy

Agentforce is the interesting one: **it cannot poll**. Its only GA outbound is
Path A — one **serial** Apex callout through the lab bridge
(`A2ALabInvokeRemoteAgent.ask()`, `@InvocableMethod(callout=true)`,
`MAX_BATCH_SIZE=1`, `setTimeout` 110s), buffered inside a single transaction's
~120s budget. There is no on-platform place for a fire-then-poll loop:

- An invocable action must return its value **in the same synchronous agent
  turn** — the orchestrator's turn does not complete until the action does.
- `@future` / `Queueable` *can* run async, but they **decouple from the turn**:
  they cannot return a value back into the action's result, so the orchestrator
  would synthesise over nothing. (This is the D16/D17 "kick off, deliver to a
  record later" shape — a different pattern, see the Flow section below.)

So under DELEGATED topology (D61 — the one that completes; SERIAL overruns the
120s budget by design) the async loop runs at the **bridge**, on the
orchestrator's behalf, during the single callout Apex holds open. The bridge runs
the SAME `orchestration.dispatch` the Managed Agents host tool runs — submit + poll
the two A2A legs, `async→sync` for AgentCore — then returns the finished sections
in the one callout response. **Agentforce never sees a task id.** The bound is
that ~110s callout, NOT an API-Gateway ceiling, because the bridge is a long-lived
Fargate service on an ALB (`idle_timeout` 120s), not a Lambda. This is the honest
shape: a serial-outbound platform can take the asynchronous half of A2A **but only
one layer down, never itself** — by proxy.

### How the mode reaches the bridge

The Apex body is `{message, session_id, trace_id}` and carries **no dispatch
field**, so the mode rides the *situation text* as a `fanout-dispatch:`
`[A2A-LAB ROUTING]` block (`af_channel.dispatch_block`), exactly like the existing
channel/route/topology toggles (D28/D61). The bridge's `_fanout`
(`src/bridge/app.py`) reads it (`read_dispatch_mode`) and **strips every routing
block** (`strip_routing_blocks`) before the legs see the situation, so no lab
directive leaks into a business-unit prompt. Absent or stripped → `sync`: the
block is only strictly required for async, and its absence degrades to the
blocking path, never to an error.

**Forwarding is now shipped (2026-08-12).** The `A2ALab_Supply_Orchestrator`
Agent Script's DELEGATED path was updated to append any `[A2A-LAB ROUTING]`
block(s) from the user message UNCHANGED after the situation when it calls the
fan-out action, and the `question` input description says the same — published
and activated as v2 in the prod org (`sf agent validate|publish|activate
authoring-bundle -n A2ALab_Supply_Orchestrator`). The safe default still holds:
an absent or dropped block runs sync, never errors. What remains is one **live
async run** to confirm the block actually survives the model turn into the Apex
`question` value (an LLM instruction, not a guaranteed wire contract) — until
that run is recorded, treat AF-async as "wired, not yet measured".

## Could Salesforce do the poll loop itself, in a Flow?

The idea: a long-running Flow with a poll callout in a loop with a Wait, so
Salesforce runs the fire-then-poll natively instead of by proxy at the bridge.

**Headlessly buildable — yes, mostly.** A Flow is a first-class Metadata type
(`*.flow-meta.xml` under `flows/`), deployable through `sf project deploy` / the
Metadata API and cheaply testable with `sf project deploy validate` (check-only).
Flow Builder is **not strictly required** to create or ship one — this is the
CLAUDE.md "headless first" rule: "no Metadata type for X" is not the bar here
because there *is* a Metadata type. What Flow Builder buys is authoring ergonomics
for a large branchy flow, not a capability the API lacks.

**But the semantics do not fit in-turn orchestration:**

- A **Pause** element resumes **asynchronously across a new transaction**, on
  coarse/batched resume granularity (minutes, not the seconds a poll cadence
  wants), and paused interviews count against org limits. You cannot tune it to a
  tight poll-every-2s loop.
- Once a Flow pauses, it has **left the agent's synchronous turn** — the same wall
  the Apex `@future`/`Queueable` path hits. A resumed Flow can write a record or
  fire a Platform Event; it **cannot hand a value back into the action result** the
  orchestrator is waiting on.

So a Flow fits the **deliver-to-a-record** model (D16/D17: kick the work off, poll
in the background, write the consolidated brief to a record/notification when
done) — genuinely useful, and a legitimate fourth shape worth a scenario — but it
is **not** a way to make Agentforce poll *within* the orchestrator's turn. For
in-turn orchestration the by-proxy-at-the-bridge design stands: the only place a
tight poll loop can run on Agentforce's behalf, inside the one held-open callout,
is the bridge.

If we want to demonstrate the Flow path, the honest framing is a **separate
async-brief scenario** (submit → Flow polls → record write), not a variant of the
in-turn fan-out. Prototyping it headlessly is feasible; it would need a Flow that
calls the bridge's `submit`, loops on a poll callout with a Pause, and writes the
brief to a record on completion.

## Tests

- `tests/unit/test_af_channel.py` — the `[A2A-LAB ROUTING]` `fanout-dispatch`
  block round-trip: carries the mode + marker, clamps unknowns to sync, defaults
  absent to sync, strips every block in one pass.
- `tests/unit/test_bridge.py` — `_fanout` reads the dispatch mode from the
  situation, threads it into `dispatch`, and strips the block so no directive
  reaches a leg; defaults to sync when no block is present.
- `tests/unit/test_adk_core.py` — `_leg_tool` async threads `dispatch_mode` /
  `trace_id` / the async budget / `caller_platform=adk` into `run_one`; sync
  passes no timeout override; mints a trace id when none is passed.

All pass without a live platform.

## Deploys (held for the operator)

All three are code/config changes, so **full rebuild** (not `--skip-build`, which
ships the old image):

- ADK orchestrator — `deploy/adk/deploy_adk.py --role orchestrator` (ships the new
  `agent.py`). *(Done 2026-08-12; the others remain.)*
- Bridge — `deploy/bridge/deploy_bridge.sh` (ships `_fanout` + `af_channel`).
- Console — `deploy/console/deploy_console.sh` (bakes `config/scenarios.yaml`,
  `index.html`, `app.py`, and this doc under `plan/`).

No Salesforce deploy is needed for the happy path; the optional Agent Script
follow-up above would remove the "unverified" caveat. Then one live async run per
controller to record the poll cadence and per-leg mode.
