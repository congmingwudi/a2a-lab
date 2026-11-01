**Verdict: REQUEST CHANGES**

# WS27 batch 1 critique — supplier-disruption shared memory

The two-run seed/recall shape is the right experiment boundary, the verified-user
owner rule is consistent with the existing seam, and host-side CMA is a sensible
first controller. Four corrections are required before the operator can settle an
implementable scope.

## Findings

### 1. The runtime and deploy plan target the wrong ECS service and omit its credential path

The hosted `supplier-disruption-cma` route constructs and runs `CmaOrchestrator`
inside `src/console/app.py`, before it constructs any ordinary target request
(`src/console/app.py#L2604-L2652`). Therefore the proposed memory client runs in
the **console task**, not the faces task. The plan instead calls for a faces
rebuild twice (`build-notes/codex/ws27-supplier-memory-plan.md#L188-L191` and
`#L271-L275`).

That is not only an unnecessary deploy. It leaves the real host unable to mint a
memory token: `MemoryClient` requires `SF_CLIENT_ID_MEMORY`,
`SF_MEMORY_RUNAS_USER`, `SF_MY_DOMAIN`, and the memory signing key
(`src/interop/memory.py#L143-L165`), while the console secret allowlist contains
no memory identity (`deploy/console/deploy_console.sh#L68-L125`) and only imports
the lab and Tableau signing keys (`deploy/console/deploy_console.sh#L127-L148`).

**Required correction:** make `deploy/console/deploy_console.sh` an explicit
implementation file; add the three memory identity values and the
`memory_jwt_private.pem`/`A2ALAB_MEMORY_JWT_KEY` relocation to
`a2alab/runtime/console`; remove the faces deploy from this batch unless another
listed code path actually executes there. Add a hosted preflight that proves the
console task mints for each new registered `aid` before a billable fan-out.

### 2. “Case-scoped episodic context, not profile memory” contradicts enabling automatic extraction

Hashing a case into `conversation_id` scopes the **conversation summary**; it does
not scope profile memory. The current memory API reads extracted memories by
`profile_id=owner_id` and category, with no conversation filter
(`src/interop/memory.py#L351-L367`). `open_and_read` then unconditionally merges
those owner-level saved facts/preferences with GetContext
(`src/interop/memory.py#L411-L449`). The plan nevertheless says the case design
“avoids placing a transient port disruption into general profile
facts/preferences” and then enables automatic extraction on all four new agents
(`build-notes/codex/ws27-supplier-memory-plan.md#L44-L50`, `#L106-L109`). The
repository also explicitly warns that extraction consumes flex credits and must
be cost-gated before it is left on broadly
(`plan/18-agentic-memory-context.md#L704-L706`).

**Required correction:** settle one honest policy before implementation:

- keep automatic profile-memory extraction **off** for these operational agents
  and read only the case conversation summary; or
- accept owner-profile extraction as part of the experiment, name the pollution
  risk, include deletion/cleanup and cost-sentinel steps, and stop claiming that
  the case id prevents it.

If the first option is chosen, add an `include_saved_memories=False` (or
equivalent) case-read path: reusing `open_and_read` as written still mixes every
shared agent's owner-level profile facts into the case.

### 3. The proposed observation-ID utilization proof is not supported by the current read contract

The proposal requires readers to cite immutable saved-observation ids and counts
valid citations as utilization evidence
(`build-notes/codex/ws27-supplier-memory-plan.md#L66-L70`). But GetContext returns
an LLM-generated conversation summary, and the renderer preserves only returned
string leaves while explicitly discarding envelope ids
(`src/interop/memory.py#L481-L504`). The saved-memory path is no stronger:
`list_memories` throws away every memory record field except `content`
(`src/interop/memory.py#L351-L364`). Nothing in current live evidence proves that
an id embedded in an ingested candidate survives extraction/summarization
verbatim. D94 proves that a cross-agent summary can be recalled, not that
provenance tokens survive it (`plan/07-workstreams.md#L3635-L3636`).

**Required correction:** add a tenant go/no-go before building the citation UI:
ingest one source-labelled candidate through agent A, then read through agent B
and record whether the exact observation id and source survive. If they do not,
define a Data-360-backed provenance read that preserves them (for example the
conversation `scope=all` route already used by `memory_arms.py`) or downgrade the
metric to “recalled content used, provenance unavailable.” Do not join against a
host-side cache and still label the result Data 360 recall. The settled ADR must
say what constitutes utilization under both outcomes.

### 4. Identity threading and the architectural definition of done need explicit files

The owner principle is correct, but the current console fan-out branch returns
before the ordinary request path reads `request.state.lab_user` and forwards the
verified token (`src/console/app.py#L2604-L2652` versus
`src/console/app.py#L2818-L2829`). Merely adding fields to `CmaOrchestrator` does
not establish their trust source. The plan must explicitly move/duplicate the
verified-claims extraction ahead of the fan-out branch and derive the owner from
those claims; `owner_id` must never be accepted from the JSON body.

The proposal also says `plan/09-deployment-map.md` and diagrams change only if a
component is added or moved (`build-notes/codex/ws27-supplier-memory-plan.md#L243-L245`).
This change gives the existing console a new outbound Data 360 dependency and a
new credential. The current estate draws memory only from bridge and faces
(`plan/09-deployment-map.md#L336-L368`; `config/diagrams.yaml#L55-L56`), and the
console L6 row describes no memory identity (`plan/09-deployment-map.md#L1195`).
CLAUDE.md requires diagrams, Details copy, and the deployment map when a hosted
component gains a dependency or changes identity (`CLAUDE.md#L155-L156`).

**Required correction:** add `deploy/console/deploy_console.sh`,
`plan/09-deployment-map.md`, `config/diagrams.yaml`, and the matching console
diagram/Details constants to the implementation file list. Add `plan/02-matrix.md`
to the measured-result files because Phase 4 already routes findings there
(`plan/18-agentic-memory-context.md#L649-L661`). For the new controls, require a
browser-level desktop/narrow accessibility check and screenshots; unit tests
alone cannot validate the proposed responsive control surface.

## Claims that source inspection supports

- A two-run experiment is honest: the existing plan requires waiting through
  availability/extraction before recall (`plan/18-agentic-memory-context.md#L649-L655`).
- Case-specific conversation ids are mechanically feasible after generalizing
  the four hard-coded `owner_conversation` call sites: create/session, ingest,
  context read, and close currently all bind to the owner conversation
  (`src/interop/memory.py#L248-L349`).
- Cross-agent conversation summary recall has live evidence, but the plan is
  right to retain a case-conversation go/no-go because D94 measured only the
  owner-derived conversation (`plan/07-workstreams.md#L3635-L3636`).
- The existing memory adapter's owner model is the verified non-machine JWT
  subject and fails closed to “no owner” while the turn itself fails open
  (`src/interop/memory_adapter.py#L55-L75`, `#L149-L164`).
- Host-side CMA is a clean first boundary: `dispatch()` already owns the three
  concurrent leg results and the partial-failure contract
  (`src/orchestration/runner.py#L603-L684`).

## Evidence run

1. `uv run pytest -q` completed with **728 passed, 47 failed, 10 errors, 3
   skipped, 6 deselected**. The dominant failure is environmental: this session
   exports a SOCKS proxy but the environment lacks `socksio`; no proposal code
   exists yet.
2. Re-running the relevant suites directly with proxy variables removed:
   `.venv/bin/pytest -q tests/unit/test_memory.py
   tests/unit/test_memory_adapter.py tests/unit/test_orchestration.py
   tests/unit/test_fanout_mcp.py tests/unit/test_console.py` produced **188
   passed, 1 failed**. The remaining existing failure is
   `test_coding_harvest_is_reachable_by_name_but_not_in_the_sweep`, unrelated to
   this proposal.

No implementation files or proposal text were changed during this critique.
