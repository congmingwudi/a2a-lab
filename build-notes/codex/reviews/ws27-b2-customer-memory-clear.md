# WS27 batch 2 — Codex implementation notes

## Baseline

- `uv sync --all-extras` — resolved 172, audited 166.
- The first sandbox run inherited a SOCKS proxy without `socksio` and was not a repository
  baseline. With only proxy variables removed: `834 passed, 3 skipped, 6 deselected, 1 failed`.
- The one baseline failure is pre-existing and outside this batch:
  `tests/unit/test_diagrams.py::test_readme_diagrams_match_the_config_verbatim`, from unrelated
  uncommitted `README.md` / `config/diagrams.yaml` changes.

## Changes

| Area | Implementation |
|---|---|
| Confirmed 403 boundary | `MemoryCallError` retains status/body; only the observed HTML/nginx 403 retries once. The retry normalizes URLs and slash-form API paths into readable text, gets new content-derived entry ids, leaves JSON authorization 403s untouched, and reports `memory.normalized`. |
| Memory Management client | Typed `MemoryEntry` / `MemoryListing` preserve `memoryId` and `totalCount`; create/delete methods use the documented profile/category scope. |
| Shared reset generation | A versioned marker in `A2ALAB_EXPERIMENT_GENERATION` is selected deterministically and hashed into the effective customer conversation. Missing markers keep D94 behavior; malformed present markers suppress memory. Conversation id reaches open/read/write/evidence and session-cache keys. |
| Console API | Named operators can list, delete selected entries, or run the verified Start-fresh saga for configured demo customers. Browser-supplied ids are intersected with a fresh governed listing; incomplete lists refuse reset; 404s re-list/retry. Profile ids never return to the browser. |
| Console UI | Customer context gains Manage memories, complete checkbox selection, confirmations, incomplete/partial/error states, trace ids, retention copy, and Start fresh. Successful reset clears old customer-memory chat archives. |
| Deployment/docs | Console secret packaging now includes and shape-validates `A2ALAB_MEMORY_CUSTOMER_MAP`. D107, WS27 items 58–63, scenario Details, plan/18, config/users and the L6 deployment row describe the shipped boundary. |

## Validation

- Focused: `163 passed` across `test_memory.py`, `test_memory_adapter.py`, and
  `test_console.py`; final reset/delete slice `2 passed`.
- Ruff: all changed Python/test paths pass.
- `config/scenarios.yaml` parses; `deploy/console/deploy_console.sh` passes `bash -n`.
- Full suite after implementation: `841 passed, 3 skipped, 6 deselected, 1 failed`; the sole
  failure is the identical pre-existing diagram mismatch from baseline.
- Codex eval harness: `regression.serialized-secret` and `regression.user-token` pass.
- Codex readiness probe stops at its historical old-behavior redaction assertion. The handoff
  contract says an old-defect probe is expected to remain red after that defect is fixed; it was
  recorded, not weakened.
- Live diagnostic evidence (before implementation): patterned synthetic write 403
  `8254320eebe7432fbc09cf99e665ba2d`; sanitized twin accepted
  `44243dc24a9c4cba9d6a62e3a9f13b2b`; post-close benign write accepted
  `811667c7fa1440a3b2e6455836860fb0`.

## Deploy and acceptance runbook

This batch is built and locally validated, **not deployed**.

1. Full faces rebuild/deploy (no `--skip-build`) because `MemoryAdapter` and `memory.py` are baked
   into that image.
2. Full console rebuild/deploy (no `--skip-build`) because app/config/plan/static assets and the
   console secret shape changed.
3. Verify the console secret carries valid `A2ALAB_MEMORY_CUSTOMER_MAP` without printing its ids.
4. Hosted acceptance in order: correction with URL/path normalization evidence; selective Maya
   deletion; Start fresh with old summary absent; Claude teach → OpenAI recall under the new marker;
   Jordan unchanged; viewer/shared-token mutation denied.
5. Record hosted trace ids and observed extraction delay in `plan/03-results.md`. Deployment and
   flipping WS27 items to complete remain operator/cross-review steps.
