# WS27 batch 1 implementation — supplier-disruption shared memory

## Baseline

Before runtime edits:

- `uv sync --all-extras` — resolved 172, audited 166.
- `uv run pytest -q` with the inherited SOCKS proxy variables removed —
  **786 passed, 3 skipped, 6 deselected** in 93.73 s.

The proxy variables were removed because Claude's critique reproduced 47
failures/10 errors when HTTPX tried to use an unavailable `socksio`; the same
suite is clean without that environmental proxy.

## What changed

| Area | Change |
|---|---|
| Settlement | D102 and WS27 items 37–43 record the two-run, host-side-CMA scope and Claude's four corrections. |
| Memory runtime | `interop.memory` accepts an explicit opaque case conversation and can read it without merging owner-profile saved facts/preferences. Existing D94 callers keep their owner conversation by default. |
| Fan-out contract | New `orchestration.memory` implements Off/Seed/Recall, strict bounded candidates, reserved-label stripping, redaction, source/trace labels, hosted four-aid preflight, bounded fail-open tenant calls, reader arms, and fail-closed evidence that distinguishes unavailable, empty, partial, seeded and recalled states. Candidate blocks, including incomplete suffixes, are stripped before coordinator synthesis. |
| Registrations | `setup_agentic_memory.py` defines Logistics, Commercial, Customer Ops and Coordinator registrations. D102 requires profile extraction off for all four. |
| Controller | Host-side `CmaOrchestrator` threads the memory run into shared dispatch and refuses memory on remote MCP. CLI flags are explicit and require an owner/case. |
| Trust boundary | The console resolves the verified human before its early CMA branch, derives `a2alab-<sub>`, rejects machine/missing owners and invalid case/reader/mode values, and never reads an owner id from JSON. |
| Console | `supplier-disruption-cma` alone exposes Off/Seed/Recall, reader and case controls; remote MCP resets memory off. Per-turn badges distinguish written/nominated, partial, unavailable, recalled and empty results. Details explain timing, isolation, extraction policy and placement. |
| Hosted identity | The console deploy relocates `SF_CLIENT_ID_MEMORY` and `memory_jwt_private.pem` into `a2alab/runtime/console`; `SF_MEMORY_RUNAS_USER` remains ordinary env. Faces are not part of this batch. |
| Provenance | `scripts/supplier_memory_probe.py` is dry-run by default. `--apply` uses a unique default case, writes an exact id/source through Logistics and polls through Coordinator until the deadline, returning GO, NO-GO, or no-context timeout without a host-cache join. |
| Architecture | Identity diagram/README/generated insights, deployment-map L2/L4/L6, plan/14 and plan/18 describe the console→Data 360 edge and two-run experiment. |

## Validation

- Initial focused WS27/core set: **217 passed**.
- Round-2 focused set: **151 passed**.
- Final round-2 full suite: **804 passed, 3 skipped, 6 deselected** in 94.45 s.
- `uv run ruff check src tests scripts` — clean.
- `git diff --check` — clean.
- Provenance probe dry run — printed opaque conversation, observation, write/read agent ids; made no tenant call.

Not run in this build session:

- no `--apply` provenance probe;
- no Salesforce/Data 360 registration or extraction-setting mutation;
- no hosted secret update, image build, deployment or live smoke;
- no deployment or external tenant mutation.

Local browser evidence (no business turn sent):

- `build-notes/codex/screenshots/ws27-b1/supplier-memory-desktop.png` (1440×1000)
- `build-notes/codex/screenshots/ws27-b1/supplier-memory-narrow.png` (390×844)
- `build-notes/codex/screenshots/ws27-b1/browser-evidence.json` records that the
  case-memory row, reader select and case-key input are visible at both widths.

## Deployment and live experiment runbook

1. Register/read back the four agents:
   `setup_agentic_memory.py register supply-{logistics,commercial,customer-ops,coordinator} --apply`.
   Run `extraction <name> off --apply` for each and read it back; do not enable
   automatic profile extraction.
2. Verify the memory ECA can mint for every exact `AgentApiName` with
   `setup_jwt_eca.py verify memory --agent-id <AgentApiName>`. Every hosted
   seed/recall run also preflights all four aids from inside the console task
   before dispatching a specialist, so the runtime secret/key path is proven
   rather than inferred from the operator's laptop.
3. Run the provenance go/no-go:
   `uv run python scripts/supplier_memory_probe.py --owner <controlled-a2alab-owner> --apply`.
   Record whether the exact id and source survive. A NO-GO is a valid finding;
   the UI/prompt already falls back to “provenance unavailable.”
4. Full console rebuild: `deploy/console/deploy_console.sh` (never
   `--skip-build`; `src/`, `config/` and `plan/` changed). Confirm the runtime
   secret contains the memory client id/key and the task has the run-as user.
5. Hosted smokes in order: memory Off control; unsigned/malformed isolation;
   Seed on a fresh case; wait for observed non-empty context; Recall with
   coordinator, specialists and both; different-case and different-owner
   negative controls.
6. Capture desktop/narrow browser screenshots and accessibility behavior. Add
   trace ids, availability lag, context size, candidate/error counts and brief
   differences to `plan/03-results.md`; record the memory finding in
   `plan/02-matrix.md`. Only then flip WS27 items 37–43 to done.

## Review cautions

- A Data 360 conversation summary may omit the source/id even while preserving
  semantic content. That is exactly what the live probe must decide; unit tests
  do not promote it to a measured result.
- Profile extraction being off is an operator-visible configuration fact, not
  something the runtime API can enforce. Deployment must read it back.
- The current workspace contained unrelated uncommitted WS28/observability
  edits before this batch. This implementation commit stages only WS27 hunks;
  those unrelated edits remain in the working tree.

## Round 2 — response to Claude cross-review

| finding | fixed / disputed / deferred | what changed |
|---|---|---|
| F1 seed/recall evidence reported failures as success | fixed | `MemoryEvidence` now tracks expected, nominated and successfully written observations and exposes an explicit state. Seed renders/wears a badge as `k/n`, all-refused and refused-read become `memory: unavailable`, partial writes are `partial`, and a successful empty read is separately `empty`. Regression tests cover partial/all-refused writes, refused preflight/read and empty recall. |
| F2 probe could return false NO-GO on a reused case | fixed | The default case is unique per observation, is printed, and the probe keeps polling through non-matching context. NO-GO occurs only at the deadline after non-empty context; no-context remains timeout. Two regression tests cover stale-first context and deadline behavior. |
| F3 no hosted console credential preflight | fixed | `MemoryClient.preflight_agent` mints without a data mutation. Every non-off `dispatch()` preflights all four D102 aids inside the runtime before specialist dispatch and records any refusal in unavailable evidence; cached shared tokens are reused by subsequent reads/writes. The operator-side check remains a separate registration/ECA check. |
| F4 identity diagrams/export incomplete | fixed | Added console→`a2a_lab_memory` D102 edges to the readme-backed identity diagram and deployment-map L4, retained L2/L6, and regenerated `plan/08-insights.md` so the identity edge and supplier case-memory diagram are in the export. The fix is layered on the operator's existing diagram refresh. |
| N1 D99 assertions nested in D102 test | fixed | Restored the platform-mark/label/Strands assertions to `test_d99_platform_identity_contract_and_shared_renderers`; D102 now contains only its own controls/deploy/badge contract. |
| N2 missing auth/failure tests | fixed | Added unsigned and machine-caller rejection coverage plus preflight, partial/all-refused write, refused-read and empty-read cases. |
| N3 per-run `httpx.AsyncClient` leak | fixed | Supplier memory now lazily shares one process-level `MemoryClient`, matching the existing memory adapter pattern; its per-aid token cache is reused across runs. Explicit test clients remain injectable. |
| N4 forged provenance/end markers | fixed | Candidate fields strip reserved observation-id and A2A-LAB control labels before ids are derived or content is stored; tests cover both forged label forms. |
| N5 incomplete candidate block leaks to coordinator | fixed | A suffix containing START without END is stripped from visible text and reported as `candidate incomplete`; regression covered. |
| N6 desktop/narrow evidence absent | fixed | Captured 1440×1000 and 390×844 screenshots from the local console without sending a turn. Browser evidence confirms the row, readers and case input are visible at both widths. |

## Deployment and live evidence — 2026-11-01/02

- Claude's second headless review returned **PASS** after independently running
  the full suite (**804 passed, 3 skipped, 6 deselected**). Review commit:
  `0c40c3d`.
- The console received a full image rebuild and deployment. The public health
  check returned HTTP 200 after the service stabilized.
- All four D102 registrations are ACTIVE. Automatic Memory Extraction read
  back `False` on every registration, and the memory ECA completed both its
  core-token and Data 360 token exchanges for every exact agent API name.
- Hosted isolation passed: unsigned memory runs returned 401 and an invalid
  case key returned 400. The memory-Off control completed successfully under
  trace `e57ebf20e4b2412881a60379e32399c7`, with memory state `off` and 3/3
  business-unit coverage.
- A hosted Seed run for case `ws27-live-20261102` completed under trace
  `148ef9a05157498b956f10c3ba0f3efb`. Two of two nominated observations were
  written with no memory errors; the independent Logistics business leg
  failed, so the business result correctly reported 2/3 coverage.
- The archived trace proves that Data 360 returned 201/202 for both
  conversation creates, both session creates, both participant additions,
  both entry writes, and both session closes. The accepted observations were
  `obs-9cde147377477c8b` (Commercial) and `obs-206673de01c4caa6` (Customer
  Operations).
- Retrieval did **not** become available. The unique provenance probe polled
  for seven minutes and the hosted case polled for ten minutes; GetContext
  remained a successful empty response through Coordinator and each writer
  registration. A subsequent raw `scope=all` conversation read returned 404
  through all relevant registrations, despite the archived 201/202 writes.
  Therefore provenance is **undetermined**, not NO-GO, and the Recall arms and
  negative controls were not run: there was no observed context to consume.

This is the same accepted-write/missing-read signature previously documented
for WS27 tenant incidents. The application build, identity, deployment,
controls and Seed path are complete; end-to-end Recall remains blocked at the
Data 360 conversation/GetContext materialization boundary.
