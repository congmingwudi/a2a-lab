**Verdict: PASS**

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

## Round 1 — Claude Opus headless cross-review

Reviewed implementation commit `63a712b` against the settled scope (D102,
`plan/00-decisions.md`), Codex's notes
(`build-notes/codex/reviews/ws27-b1-supplier-memory.md`) and the round-0
critique above. **Verdict: REQUEST CHANGES** — three of the four critique
corrections hold. D102's own "fail-closed in evidence" clause does not, the
provenance instrument can produce a false NO-GO, and the architecture
definition of done is incomplete.

### Critique corrections — status

| # | Correction | Status |
|---|---|---|
| 1 | Console task, not faces; memory identity in `a2alab/runtime/console`; hosted per-`aid` mint preflight before a billable fan-out | **Partial.** `deploy/console/deploy_console.sh` adds `SF_CLIENT_ID_MEMORY` to the secret and the PEM as `A2ALAB_MEMORY_JWT_KEY`, matching `interop.memory.KEY_ENV`. `SF_MEMORY_RUNAS_USER`/`SF_MY_DOMAIN` reach plain env through the `os.environ` scan. Faces dropped. **The hosted preflight was not built** — see F3. |
| 2 | Profile extraction off and a case read without the owner-level merge | **Met.** `open_and_read(..., include_saved_memories=False)`; `CaseMemory.read` uses it; test `test_recall_routes_case_summary_only_to_selected_readers` asserts no `list_memories`. Extraction-off is a runbook readback, honestly labelled as not runtime-enforceable. |
| 3 | Provenance go/no-go before citation; utilization defined under both outcomes | **Design met, instrument defective** — see F2. D102 defines both outcomes, and the coordinator prompt falls back to "provenance unavailable". |
| 4 | Verified identity before the early CMA branch; owner never from JSON; deploy/diagram/Details files | **Identity met.** `src/console/app.py` resolves `lab_user` before the scenario branch, rejects missing/machine/sub-less users (L2663) and derives `a2alab-<sub>` the same way as `memory_adapter.owner_of`. `test_supplier_memory_owner_comes_from_verified_user_before_fanout` proves a body `owner_id` is ignored. **Diagram half incomplete** — see F4. |

### Blocking findings

**F1 — Seed and recall evidence are not fail-closed (D102: "empty, late or
refused context is reported as such").**
- `CaseMemory.seed` builds its evidence from the *parsed* candidates before any
  write (`src/orchestration/memory.py:193`). `MemoryEvidence.render` reports
  `seeded {len(candidates)}` (`:105`). The console returns
  `candidate_count = len(fan.memory.candidates)` (`src/console/app.py:2740`), and
  the badge prints `memory: seeded N` in the success colour
  (`src/console/static/index.html`, HEAD L9614).
- Recall: a refused read and an empty conversation render the same
  `no memory available` (`memory.py:110`) and the same badge `memory: nothing yet`
  (HEAD L9615). The error count only appears in a tooltip.
- **Reproduced** with a scratch client whose ingest and context calls raise
  `MemoryCallError` (`/tmp/ws27-review/repro.py`, outside the repo):
  ```
  SEED  : [memory: seeded 3 observation(s); errors=3; ...] | candidate_count(badge)= 3
  RECALL: [memory: no memory available; readers=coordinator; errors=1] | context_chars= 0
  ```
- This matters most in exactly the failure the batch adds: a console secret
  missing the memory key turns every Seed run into three billable specialist
  calls that report "seeded 3".
- **Fix:** count *written* observations (candidates whose `one()` returned no
  errors) separately from nominated ones. Render and badge `seeded k/n`, and show
  `memory: unavailable` (non-success colour) when every write or the read errored.
  Distinguish `refused` from `empty` in both `render()` and the badge. Add unit
  tests for all-writes-refused, partial-write and refused-read.

**F2 — The provenance probe can return a false NO-GO.**
- `scripts/supplier_memory_probe.py` defaults `--case` to the constant
  `d102-provenance-probe` (L114). It mints a fresh `observation_id` each run and
  returns NO-GO (exit 1) on the **first** non-empty context (L89–102).
- On any second run, the case conversation already holds the previous run's
  summary. The first poll is therefore non-empty, lacks the new id, and reports
  NO-GO without waiting for the new entry. The runbook command (notes, step 3)
  uses the default case.
- This script decides D102's citation policy, so a false NO-GO silently
  downgrades the metric.
- **Fix:** default the case key to a per-run unique value (e.g. include the
  observation id), and/or keep polling until the id appears or the timeout
  passes. Report NO-GO only after a context that post-dates the write.
  Print the case key so a rerun is deliberate.

**F3 — Critique #1's hosted preflight is absent.**
- The runbook's step 2 (`setup_jwt_eca.py verify memory --agent-id`) runs on the
  operator's machine with the operator's `.env` and `.a2alab` key. It proves the
  ECA and `aid` registration. It does not prove that the **console task's**
  secret and key mint, which is what the critique asked for.
- Step 4 ("confirm the runtime secret contains…") is a config readback, not a
  mint. The first hosted mint attempt is a billable Seed run, and F1 then
  misreports its outcome.
- **Fix (either):**
  - (a) in seed/recall mode, `dispatch()` mints the case agents' tokens before
    the legs run and records a refusal up front in the evidence; the business
    turn stays fail-open; or
  - (b) an operator-gated console route or CLI mode that mints for all four
    `aid`s inside the task, listed in the runbook before step 5.

**F4 — Architecture definition of done (CLAUDE.md: diagrams and console copy
change with the component) is incomplete, and the notes overclaim it.**
- The notes say "Identity diagram/README … describe the console→Data 360 edge".
  At HEAD neither does:
  - `config/diagrams.yaml` `identity-and-secrets` (`readme: true`) still shows
    `SCON` reaching only `a2a_lab_tab_embed` (HEAD L41).
  - `README.md` is not in `63a712b`.
  - `plan/09-deployment-map.md` L4 shows `E5 a2a_lab_memory` with no console
    caller (HEAD L431).
- The new `supplier-disruption-case-memory` diagram is attached to insight
  `orchestration-topology`, but `plan/08-insights.md` was not regenerated, so the
  committed generated export is stale against the committed source.
- **Coordination note:** the operator's uncommitted worktree edits are rewriting
  these same diagrams. They add bridge/faces→`a2a_lab_memory` edges, but no
  console edge. The fix should land on top of those edits, not over them.

### Non-blocking (fix while there)

- **N1.** `test_d102_supplier_memory_controls_are_scoped_and_accessible` was
  inserted in the middle of `test_d99_platform_identity_contract_and_shared_renderers`
  (`tests/unit/test_console.py:457`). The d99 test's trailing asserts (HEAD
  L474–485: `seen` Set, `PLATFORM_MARK`, `WARM_LOGO`, label blocks, Strands
  titles) now belong to the D102 test. Nothing is lost, but the d99 contract is
  split and a D102 failure masks it. Move them back.
- **N2.** No test covers the 401 path (unsigned / shared-token / `role:
  machine`), although the notes claim it. No test covers the F1 failure cases.
- **N3.** `CaseMemory(memory_client)` constructs a new `MemoryClient`, and with it
  an `httpx.AsyncClient`, for every non-off dispatch and never closes it
  (`src/orchestration/runner.py:679`, `memory.py:164`). Each run leaks a client
  and re-mints 2 tokens per `aid`. Share a client (as `memory_adapter._shared`
  does) or `aclose()` in a `finally`.
- **N4.** Model-authored candidate text can contain a forged
  `[observation_id=…; source=…]` prefix or a literal `[END A2A-LAB CASE MEMORY]`.
  Under a GO outcome a cited id *is* the utilization metric, so `parse_candidate`
  should strip or escape bracketed label patterns from the four fields.
- **N5.** A seed reply with `START` but no `END` returns "candidate missing" and
  leaves the partial block in the text forwarded to the coordinator.
- **N6.** Browser desktop/narrow accessibility screenshots were deferred to
  "deployment". They need no deploy: `scripts/run_console.sh` serves the new
  controls locally against the hosted lab. Item 41 correctly stays ⏳.

### Verified as correct

- **Off is a true control:** the prompt is unchanged with an empty instruction,
  `MemoryEvidence()` renders `""`, and no memory call is made (test asserts).
- **Recall never writes**, so there is no same-turn feedback.
- **Seed strips candidate blocks** before coordinator synthesis.
- **Remote MCP refuses memory at three layers:** UI reset, console 409, and the
  `CmaOrchestrator` ValueError, plus the CLI guard.
- **The fan-out Lambda bundle is unaffected** by the now-unconditional
  `orchestration.memory` import in `dispatch()`: `deploy/fanout/build_zip.sh`
  copies all of `src/orchestration` and `src/interop`, and bundles httpx.
- **Case conversation id is opaque** and hashes owner, scenario and case;
  `case_conversation` is tested for separation.
- **Honesty:** WS27 items 37–43 are all ⏳, and no `plan/02`/`plan/03` claims
  were added. That is consistent with nothing being registered, deployed or
  measured.

### Evidence run (this session)

1. `uv run pytest -q` (environment as-is; no SOCKS proxy issue this session):
   **799 passed, 3 skipped, 6 deselected** in 94.16 s. This matches Codex's
   final count.
2. `uv run ruff check src tests scripts`: clean. `git diff --check
   63a712b^ 63a712b`: clean.
3. Scratch reproduction of F1 (`/tmp/ws27-review/repro.py`, outside the repo),
   output quoted above.
4. Honesty checks were targeted, not full skill runs:
   - an architecture-sweep–style grep for the console memory identity across
     `plan/`, `config/diagrams.yaml` and `index.html` (F4);
   - a workstream-honesty read of items 37–43 against the build evidence
     (consistent);
   - a matrix check: no `plan/02` change, which is consistent.

   The full `workstream-honesty` workflow was not run; it is known to stall on
   `parse_plan()` output.
5. **Incident, disclosed:** `uv run python scripts/export_insights.py --help`
   ignores `--help` and regenerated `plan/08-insights.md`. That file is
   user-owned and was already dirty. No snapshot existed. I restored it by
   regenerating from the current worktree sources minus the D102 diagram entry,
   and the only diff against the accidental export was that block. The prior
   dirty copy did not contain it (counted before the export). It is the closest
   reconstruction, not a byte-verified restore. The operator should eyeball
   `git diff plan/08-insights.md`, and Codex should not stage it.

No implementation code, plan, deploy or tenant state was changed by this review.

## Round 2 — Claude Opus headless cross-review

I reviewed response commit `b97d7ff` (plus the state-only `82d84ae`) against
the Round 1 findings, the settled D102 scope (`plan/00-decisions.md`) and the
response table in `build-notes/codex/reviews/ws27-b1-supplier-memory.md`. I
inspected the committed diff, not the dirty worktree. **Verdict: PASS.** Every
blocking finding is fixed in code and backed by a regression test. The
residuals below are non-blocking and can land in the next batch or alongside
the deploy.

### Round 1 findings — status

| # | Status | Evidence (committed code) |
|---|---|---|
| F1 evidence not fail-closed | **Fixed** | `MemoryEvidence` gains `written` + `expected_count` and a derived `state` (`src/orchestration/memory.py:96-118`). Seed now counts only candidates whose `write_turn` returned no errors (`:290-293`). Render prints `seeded k/n` with the state. The console returns `candidate_count = len(written)` and `nominated_count` (`src/console/app.py:2738-2742`). The badge shows `memory: unavailable` without the success colour, and recall separates `empty` from `unavailable` (`index.html` `memoryBadgeHtml`). I re-ran the Round 1 scenario through the new tests: all-refused gives `memory: unavailable; seeded 0/3`, partial gives `seeded 2/3`, and a refused read or preflight gives `memory unavailable`, never `no memory available`. |
| F2 probe false NO-GO | **Fixed** | The default case is `d102-provenance-<observation_id>`, unique per run, and it is printed. Non-matching context keeps polling. NO-GO is returned only at the deadline, and only if non-empty context was ever seen. No context gives a TIMEOUT (exit 2). `tests/unit/test_supplier_memory_probe.py` covers stale-first-context → GO, and NO-GO only after more than one poll. |
| F3 hosted preflight | **Fixed (option a)** | `MemoryClient.preflight_agent` runs the full JWT-bearer → `/services/a360/token` exchange with `aid` (`src/interop/memory.py:158-165`). That exchange is the step that refuses an unregistered aid, so it proves both the task's credential and the registration. Every non-off `dispatch()` preflights all four aids before the legs run (`runner.py:681-688`). A refusal turns the evidence `unavailable` and suppresses the read/writes, while the business turn stays fail-open. The tokens are cached on the shared client and reused. |
| F4 diagrams / export | **Fixed** | `config/diagrams.yaml` `identity-and-secrets` and its `README.md` copy have the `SCON → a2a_lab_memory` edge. `plan/09` L4 adds a console task role with `CT → E5`, and the L2/L6 text already named the console host. **Checked independently:** I extracted HEAD with `git archive` into `/tmp`, ran `export_insights.py` **there**, and the output is byte-identical to the committed `plan/08-insights.md`. The export is not stale. The user's worktree copy was not touched. |
| N1 d99 asserts nested | **Fixed** | Moved back into `test_d99_…` (`tests/unit/test_console.py:456-467`). |
| N2 auth/failure tests | **Fixed** | `test_supplier_memory_rejects_unsigned_and_machine_callers_before_agent_work`, plus `FailingMemory` cases for preflight, partial, all-refused, refused-read and empty. |
| N3 client leak | **Fixed** | A lazily-created module-level `_shared_client` (the same pattern as `memory_adapter._shared`). Injected clients still win. |
| N4 forged labels | **Fixed for the canonical forms; residual below** | `_RESERVED_LABEL` strips `[observation_id=…]` and every `A2A-LAB` control marker, case-insensitively. |
| N5 START without END | **Fixed** | The suffix is stripped and reported as `candidate incomplete`. Tested. |
| N6 screenshots | **Fixed** | `build-notes/codex/screenshots/ws27-b1/` contains a 1440×1000 and a 390×844 PNG plus `browser-evidence.json`. No turn was sent. Hosted accessibility behaviour stays in runbook step 6, and item 41 stays ⏳. |

### Non-blocking residuals (fix with the deploy or the next batch)

- **R1 — Label stripping is pattern-exact.** A scratch run, outside the repo,
  shows that these forms survive `parse_candidate`:
  - `[ observation_id=obs-f] …` (leading space);
  - `[source=f; observation_id=obs-f] …` (reordered);
  - a bare `observation_id=obs-f; …`.

  The canonical forged prefix and `[END A2A-LAB CASE MEMORY]` *are* stripped.
  Under a GO outcome, the citation scorer should accept only ids present in
  that case's seed-trace `written` set, rather than trust any id it sees. That
  is the robust fix; a broader regex is the cheap one.
- **R2 — Two edge states are mislabelled.**
  - When every leg fails, `expected_count=0`, the state is `seeded` and the
    badge shows `seeded 0/0` in the success colour.
  - When all three specialists omit their candidate blocks, the state is
    `unavailable`, which attributes model non-compliance to Data 360.

  The per-leg errors carry the truth, but a `nothing nominated` state would be
  more honest for both.
- **R3 — The preflight is all-or-nothing.** One refused aid (say, the
  coordinator) blocks every seed write, and one refused specialist aid blocks
  a coordinator-only recall. This is conservative and honestly reported, so it
  is acceptable for the experiment. Note it in the runbook so a single
  registration miss is not read as "Data 360 down".
- **R4 — Format drift.** `uv run ruff format --check src tests scripts` would
  reformat 7 files at HEAD. The pre-batch base (`63a712b^`) had only 1
  (`tests/unit/test_console.py`). The batch adds `scripts/supplier_memory_probe.py`,
  `src/orchestration/memory.py`, `src/orchestration/runner.py`,
  `tests/unit/test_memory.py`, `tests/unit/test_orchestration_memory.py` and
  `tests/unit/test_supplier_memory_probe.py`. Also, the D102 console test now
  runs straight into `def test_targets_listed` with no blank lines.
  `ruff check` is clean.

### Evidence run (this session)

1. `uv run pytest -q` on the worktree, with the proxy variables unset: **804
   passed, 3 skipped, 6 deselected** in 90.28 s. This matches Codex's count.
2. `pytest -q` on a clean `git archive HEAD` in `/tmp`: **797 passed, 4
   failed, 6 skipped**. The 4 failures are the `git ls-files` guards
   (`test_no_account_identifiers` ×3, `test_render_eca_metadata` ×1). They need
   a git checkout, which an archive is not, so this is environmental. The
   committed tree passes on its own, independent of the user's dirty edits.
3. `uv run ruff check src tests scripts`: clean. `ruff format --check`: see
   R4.
4. A `plan/08` export reproduction inside the `/tmp` archive is byte-identical
   to the committed file. The real worktree `plan/08-insights.md` was not
   regenerated; its status is still the user's pre-existing ` M`.
5. A scratch script (`/tmp/ws27-r2/scratch/r2.py`, outside the repo) checked
   the label-strip bypasses and evidence-state edges behind R1 and R2.
6. Honesty checks were targeted: an architecture-sweep–style grep for the
   console memory edge across `plan/09`, `config/diagrams.yaml`, `README.md`
   and `index.html`, which is consistent. WS27 items 37–43 remain ⏳, with no
   `plan/02` or `plan/03` claims, which matches "nothing registered, deployed
   or measured".

Only this review file was changed. No deploy, no tenant call, no
`export_insights.py` run in the repo, and nothing was staged or committed.
