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
| Fan-out contract | New `orchestration.memory` implements Off/Seed/Recall, strict bounded candidates, redaction, source/trace labels, bounded fail-open tenant calls, reader arms, and honest evidence lines. Candidate blocks are stripped before coordinator synthesis. |
| Registrations | `setup_agentic_memory.py` defines Logistics, Commercial, Customer Ops and Coordinator registrations. D102 requires profile extraction off for all four. |
| Controller | Host-side `CmaOrchestrator` threads the memory run into shared dispatch and refuses memory on remote MCP. CLI flags are explicit and require an owner/case. |
| Trust boundary | The console resolves the verified human before its early CMA branch, derives `a2alab-<sub>`, rejects machine/missing owners and invalid case/reader/mode values, and never reads an owner id from JSON. |
| Console | `supplier-disruption-cma` alone exposes Off/Seed/Recall, reader and case controls; remote MCP resets memory off. Per-turn badges distinguish seeded, recalled and empty results. Details explain timing, isolation, extraction policy and placement. |
| Hosted identity | The console deploy relocates `SF_CLIENT_ID_MEMORY` and `memory_jwt_private.pem` into `a2alab/runtime/console`; `SF_MEMORY_RUNAS_USER` remains ordinary env. Faces are not part of this batch. |
| Provenance | `scripts/supplier_memory_probe.py` is dry-run by default. `--apply` writes an exact id/source through Logistics and polls through Coordinator, returning GO, NO-GO, or timeout without a host-cache join. |
| Architecture | Identity diagram/README, deployment-map L2/L6, plan/14 and plan/18 describe the console→Data 360 edge and two-run experiment. |

## Validation

- Focused WS27/core set: **217 passed**.
- Final full suite: **799 passed, 3 skipped, 6 deselected** in 89.85 s.
- `uv run ruff check src tests scripts` — clean.
- `git diff --check` — clean.
- Provenance probe dry run — printed opaque conversation, observation, write/read agent ids; made no tenant call.

Not run in this build session:

- no `--apply` provenance probe;
- no Salesforce/Data 360 registration or extraction-setting mutation;
- no hosted secret update, image build, deployment or live smoke;
- no browser-level desktop/narrow screenshot. Static accessibility/control
  contracts and console request tests pass; visual evidence remains an operator
  deployment step.

## Deployment and live experiment runbook

1. Register/read back the four agents:
   `setup_agentic_memory.py register supply-{logistics,commercial,customer-ops,coordinator} --apply`.
   Run `extraction <name> off --apply` for each and read it back; do not enable
   automatic profile extraction.
2. Verify the memory ECA can mint for every exact `AgentApiName` with
   `setup_jwt_eca.py verify memory --agent-id <AgentApiName>`.
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
