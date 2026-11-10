# WS27 batch 1 proposal — supplier-disruption shared memory

**Station:** proposal only. Per D79, no implementation starts until the other
agent critiques this plan and the operator settles the scope in an ADR.

## Outcome

Add a two-run memory arm to the existing `supplier-disruption-cma` experiment:

1. A **seed run** asks each of the three existing specialist agents to nominate
   one bounded, reusable observation from its own answer. The host validates,
   source-labels, redacts, and writes the three observations to one case-scoped
   Data 360 Agentic Memory conversation.
2. After Data 360 extraction, a **recall run** reads that case conversation and
   injects the source-labelled observations into selected readers: the three
   specialists, the coordinating agent, or both. The final brief must say which
   saved observations it used and cite their specialist source and seed trace.
3. An **off run** remains byte-for-byte equivalent to today's experiment and is
   the control. Empty, late, or refused memory is surfaced as a measured result,
   never described as recall.

This is intentionally a two-run experiment. `plan/18-agentic-memory-context.md`
records an approximately 30-second availability lag and a measured extraction
lag of roughly 64 seconds. Feeding a seed write back into the same turn would
measure a lab-side cache, not Data 360 memory.

## Scope boundary

This batch ships the **Claude Managed Agents host-side tool topology** first.
It still exercises four agents across four vendors: the existing ADK logistics,
Foundry commercial/legal, and OpenAI customer-operations specialists plus the
Claude coordinator. It does not yet add memory to the remote-MCP topology, the
ADK-primary graph, or the Agentforce-primary controller. Those controllers put
the orchestration loop and credential boundary in different runtimes; adding
all three in one batch would confound the first memory result with three new
distribution mechanisms.

The reusable implementation belongs at the shared orchestration seam so the
bridge and ADK-primary follow-ups can adopt the same contract without changing
what a memory candidate means.

## Decisions for settlement

1. **Memory unit: case-scoped episodic context, not the operator's profile.**
   The owner remains the verified signed-in lab user's `sub`, as required by
   `interop.memory_adapter.owner_of`. A case key is namespaced under that owner
   and hashed into a Data 360 conversation id. This prevents two operators who
   choose the same display key from reading each other's case. Automatic
   profile-memory extraction stays **off** for these operational registrations,
   and case reads explicitly exclude owner-level saved facts/preferences; the
   experiment reads only the case conversation summary. This avoids mixing a
   transient disruption into the operator's general profile memory.
2. **The specialist chooses; the host governs.** In seed mode only,
   `Leg.prompt()` requests one structured `memory_candidate` containing an
   observation, reuse reason, confidence, and invalidation condition. The host
   parses a strict bounded block, strips it before synthesis, applies the
   existing memory redaction, and adds trusted provenance (`role`, target,
   platform, seed trace, timestamp). Malformed or absent candidates are named
   per leg and are not silently replaced by the full answer.
3. **Ingestion is the supported conversation path.** Each specialist candidate
   is ingested as that registered specialist's response in a separate session
   inside the shared case conversation, then the session is closed to trigger
   extraction. Do not invent an unprobed direct-memory CRUD write contract.
4. **Recall targets are explicit experiment arms.** `coordinator`,
   `specialists`, and `both` are valid readers. The coordinator arm is the
   minimum useful first measurement; the other two answer whether earlier
   cross-specialist knowledge changes a specialist answer before synthesis.
5. **Utilization is evidenced, not guessed, after a provenance go/no-go.** Before
   building citation UI, ingest one source-labelled candidate through one
   registration and read it through another. If Data 360 preserves the exact
   observation id and source, reader prompts emit a short `Memory used` list and
   response metadata validates those ids. If it does not, the settled fallback
   metric is “recalled content used, provenance unavailable,” supported by the
   Data 360 conversation read rather than a host cache. A fluent answer alone
   never proves causal use.
6. **Fail open, report closed.** A memory timeout/refusal never fails the fan-out,
   but the coverage line and response metadata report read/write status per
   agent. The existing partial-failure contract remains unchanged.

## Implementation items

### 1. Case-scoped memory lifecycle and registrations

Files:

- `src/interop/memory.py`
- `scripts/setup_agentic_memory.py`
- `.env.example`
- `tests/unit/test_memory.py`

Changes:

- Generalize the existing conversation helpers to accept an optional explicit
  conversation id while retaining `owner_conversation(owner_id)` as the default.
- Add a deterministic `case_conversation(owner_id, scenario, case_key)` helper
  whose returned id reveals neither the owner nor the user-entered case key.
- Register four Agent API names in the existing setup script: one for each
  specialist role and one for the supply coordinator. Extend
  `A2ALAB_MEMORY_SHARED_AGENTS` documentation rather than hard-coding a deploy's
  registration inventory into orchestration code.
- Keep every runtime call traced through `MemoryClient._call`; never log the
  bearer token or unhashed owner/case material.
- Add a case-read option that excludes `saved_memories`; do not reuse the
  current `open_and_read` behavior that merges owner-level profile facts.

Tests:

- stable case id, owner and scenario separation, no raw identity/key leakage;
- explicit conversation id on create/session/ingest/context calls;
- default callers retain the D94 one-conversation-per-owner behavior;
- one refused registered agent does not suppress the others.

Deploy:

- run `setup_agentic_memory.py register <name> --apply`, explicitly keep
  automatic profile-memory extraction off, and read back all four registrations;
- memory registration is control-plane only; runtime code ships with the
  console image in items 3–4.

### 2. Memory candidate and recall contract at the fan-out seam

Files:

- new `src/orchestration/memory.py`
- `src/orchestration/legs.py`
- `src/orchestration/runner.py`
- `src/orchestration/agents.py`
- `tests/unit/test_orchestration_memory.py`

Changes:

- Define `MemoryMode = off | seed | recall`, `MemoryReader = coordinator |
  specialists | both`, a bounded `MemoryCandidate`, and per-leg read/write
  evidence. Unknown values are rejected before agent work; absence defaults to
  `off`.
- In `off`, preserve the exact current leg prompts, rendered hand-off, and
  coordinator prompt.
- In `seed`, append a strict candidate request to each leg prompt; parse and
  strip candidates after all legs return; ingest valid candidates concurrently
  under the corresponding specialist registration. The ordinary business-unit
  answer passed to the coordinator must remain the same 3-bullet surface.
- In `recall`, read the case conversation summary before dispatch—never
  owner-level saved memories—and inject
  only source-labelled, bounded context into configured readers. Coordinator
  recall is added to its system/session instruction, not to the user-reported
  disruption. Specialist recall is appended after the role question in a
  clearly delimited lab-memory block.
- Extend `FanOutResult` with memory evidence and render one honest line such as
  `[memory: recalled 3 observations; readers=coordinator; cited=2; errors=0]`.
  No candidate text is put in metadata or the coverage line.

Tests:

- exact off-mode regression for prompts and rendered output;
- one valid, malformed, missing, oversized, and redaction-requiring candidate;
- concurrent writes use the correct agent registration and one case
  conversation, with stable provenance and content-derived idempotency;
- read timeout and partial write failure preserve all normal leg answers;
- recalled context reaches only the selected readers;
- citations to unknown observation ids do not count as utilization;
- provenance probe records whether exact ids survive; the fallback never joins
  against host-cached candidate text while claiming Data 360 attribution;
- no memory context or candidate control block leaks into the visible brief.

Deploy:

- no independent component; bundled into faces/console in item 4.

### 3. Thread the experiment through the CMA host-side controller

Files:

- `src/orchestration/cma.py`
- `scripts/run_fanout.py`
- `tests/unit/test_orchestration.py`
- `tests/unit/test_fanout_mcp.py`

Changes:

- Add memory mode, reader, verified owner, and case key to `CmaOrchestrator` and
  its host-side `dispatch()` call. Reject memory on `variant=mcp` in this batch
  instead of accepting a toggle that cannot work in the remote Lambda.
- Require a verified owner for seed/recall in the hosted console. For the CLI,
  require an explicit lab-user token or a documented test owner flag; do not
  silently pool CLI runs under a machine identity.
- Return memory evidence alongside current variant, dispatch, gate, and fan-out
  evidence.

Tests:

- host-side mode threads the contract and preserves existing gate/dispatch
  combinations;
- MCP + memory and missing verified owner fail before starting billable agent
  work with actionable errors;
- coordinator prompt contains recalled entries and requires valid source ids;
- existing CMA/MCP behavior is unchanged when memory is off.

Deploy:

- console task full rebuild; this CMA controller executes inside
  `src/console/app.py`, so a faces rebuild is outside this batch.

### 4. Console experiment controls, evidence, and repeatable two-run preset

Files:

- `config/scenarios.yaml`
- `src/console/app.py`
- `src/console/static/index.html`
- `deploy/console/deploy_console.sh`
- `tests/unit/test_console.py`
- `plan/14-supplier-disruption-orchestrators.md`
- `plan/09-deployment-map.md`
- `config/diagrams.yaml`

Changes:

- Add a supplier-memory control available only for
  `supplier-disruption-cma` + host-side tool: Off, Seed, Recall; reader choice
  on Recall; and a case key generated once per browser experiment with a reset
  action. The server derives the real conversation id from verified identity;
  the browser never supplies an owner id or conversation id.
- Extract and verify the signed-in lab user before the early fan-out branch,
  then derive the memory owner from those verified claims. Never accept an
  owner id from the JSON body.
- Add a two-run preset: the existing Rotterdam seed situation, then a follow-up
  that deliberately omits prior specialist details and asks for an update. The
  UI explains the extraction wait and does not enable/label a result as recall
  merely because a timer elapsed.
- Surface candidate count, Data 360 read/write status, recalled observation
  count, readers, and valid citation count in the run result and Details pane.
  Keep candidate content in traced memory wire and the model brief, not in
  browser persistence.
- Document why this first arm is CMA host-side and how later controller arms
  differ.
- Extend the console deployment secret allowlist/relocation with the memory ECA
  identity and signing key. Update the deployment map, architecture diagram,
  and matching console Details/diagram copy for the console's new outbound
  Data 360 dependency and credential.

Tests:

- unsigned requests cannot seed/read memory and cannot choose another owner;
- invalid case/mode/reader inputs clamp or reject before agent execution;
- off/seed/recall request shapes and UI gating;
- seed status never claims extraction or recall;
- recall with empty Data 360 context displays “no memory available”;
- current fan-out, gate, dispatch, run-all, and narrow-layout controls regress.
- browser-level desktop/narrow accessibility checks and screenshots for the new
  controls, in addition to unit tests.

Deploy:

- console full rebuild (`src/`, `config/`, and `plan/` are baked into its
  image); hosted smoke the off control before any live memory arm.

### 5. Live experiment and repository definition of done

Files:

- `plan/00-decisions.md`
- `plan/07-workstreams.md`
- `plan/18-agentic-memory-context.md`
- `plan/03-results.md`
- `plan/02-matrix.md`
- `config/insights.yaml` only if the measured result supports a new insight
- `plan/09-deployment-map.md`, `config/diagrams.yaml`, and matching console
  diagram/Details constants because the console gains a Data 360 dependency
  and memory credential
- implementer notes under `build-notes/<implementer>/`

Changes and evidence:

- ADR settles case-scoped episodic memory, two-run timing, reader arms, and the
  host-side-first boundary. Add WS27 item lines before implementation.
- Record one off run, one seed run, and recall runs for coordinator,
  specialists, and both using the same case. Record trace ids, extraction wait,
  available observations, valid citations, brief differences, latency, and
  all partial failures. Keep synthetic unit evidence distinct from the live
  Data 360 result.
- Verify that a different signed-in owner and a different case key recall none
  of the seeded observations.
- Update the plan and console Details copy with what was measured, including a
  negative or mixed result.

Validation before cross-review:

- baseline first: `uv sync --all-extras && uv run pytest`;
- focused memory/orchestration/console suites;
- `uv run ruff check src tests scripts`;
- full `uv run pytest` with actual pass/fail/skip counts recorded;
- live Data 360 smoke only after registration read-back and the off-mode hosted
  smoke pass.

Deploy order:

1. register/read back the four memory agents with profile extraction off;
2. run the cross-agent provenance go/no-go and record whether exact ids survive;
3. full console rebuild, hosted token-mint preflight for every new `aid`, then
   off-mode and auth/isolation smokes;
4. seed, wait for observed availability (not a fixed green timer), recall;
5. record results and reconcile the deployment map, diagrams and Details copy.

## Follow-up batches, not hidden scope

1. Remote-MCP CMA: memory commands and credentials execute in the fan-out
   Lambda/durable-task path; the model-driven poll loop must retain provenance.
2. ADK-primary: callbacks or orchestration graph state carry case memory inside
   the Agent Engine deployment.
3. Agentforce-primary: the bridge performs the memory lifecycle on behalf of
   the serial controller, with an Agent Script routing block that is stripped
   before specialist prompts.
4. Compare those three controller placements as the memory analogue of D76:
   same shared memory contract, different owner of the read/write loop.

## Risks the critique should challenge

- Whether Data 360 permits several registered agents to append sessions to one
  case conversation and whether GetContext summarizes those sessions across
  agent registrations. The existing D94 result proves cross-agent recall for
  the owner conversation, but this case-conversation variant still needs a
  live pre-implementation probe or an explicit go/no-go item.
- Whether automatic extraction treats operational observations as useful
  episodic context rather than profile facts. The plan relies primarily on the
  conversation summary and source-labelled entries; saved profile memory is a
  secondary read, not the only success path.
- Whether the 60-word specialist cap leaves room for a structured candidate
  without changing the visible business answer. The candidate block must be
  excluded from the cap and stripped before synthesis.
- Whether “utilized” should require a valid explicit citation, a counterfactual
  brief difference, or both. This proposal records both and treats citation as
  necessary evidence, not proof of causal value by itself.
