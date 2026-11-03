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
| Shared reset generation | A versioned reserved marker in the tenant-supported `sys_PROFILE_FACT` category is selected deterministically, removed from both prompt paths, and hashed into the effective customer conversation. Missing markers keep D94 behavior; malformed reserved markers suppress memory. Conversation id reaches open/read/write/evidence and session-cache keys. |
| Console API | Named operators can list, delete selected entries, or run the verified Start-fresh saga for configured demo customers. Browser-supplied ids are intersected with a fresh governed listing; incomplete lists refuse reset; 404s re-list/retry. Profile ids never return to the browser. |
| Console UI | Customer context gains Manage memories, complete checkbox selection, confirmations, incomplete/partial/error states, trace ids, retention copy, and Start fresh. Old chats remain inspectable but are read-only after their stamped generation becomes stale. |
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

## Round 2 — response to Claude review

| finding | fixed / disputed / deferred | what changed |
|---|---|---|
| R1-1 probe caller used the old `write_turn` shape | **fixed** | `supplier_memory_probe.py` now unpacks `(errors, normalized)` and its fake returns the real tuple. |
| R1-2 reset failures returned opaque 500s and did not verify the selected marker | **fixed** | Every mutation step records success/error; non-404 failures remain JSON with trace id, errors and the re-listed remaining entries. Success requires the exact new valid marker, completeness and absence of requested semantic rows. Tests reproduce a failed delete, marker mismatch, incomplete list and 404 re-list/retry. |
| R1-3 pre-reset chats could continue and Maya reset purged Jordan archives | **fixed** | Each customer-memory chat stamps the observed generation. Subsequent turns send it as a precondition; `MemoryAdapter` rejects a mismatch before invoking the inner platform session. Local stale archives are read-only with an explicit new-chat fork. Reset archives only the active chat and never scans/deletes the other customer's archives. |
| R1-4 normalized retry was not surfaced | **fixed** | `memory.normalized` now renders a `memory: normalized retry` badge explaining that only the tenant copy changed. |
| R1-5 custom generation category was unverified | **fixed with live evidence and design correction** | The synthetic probe proved custom `A2ALAB_EXPERIMENT_GENERATION` and documented `PROFILE_FACT` both 403. Supported `sys_PROFILE_FACT` passed create/read/delete/cleanup. Reserved markers are filtered from both GetContext rendering and saved-memory prompts. Evidence: `build-notes/codex/ws27-generation-category-probe.md`, final trace `e3c30627c7f945a99a54b6e7535f20a3`. D107 and plan/18 now state the actual boundary. |
| R1-6 promised edge tests were missing | **fixed** | Added JSON-403 no-retry, second closed-session write, 404 retry, incomplete listing, non-governed id, Maya/Jordan isolation, machine denial, no-customer behavior, generation precondition forwarding/refusal and Run All forced-memory-off coverage. |
| R1-7 confirmation/copy gaps | **fixed** | Both confirmations name the selected customer; Start fresh includes the count. Panel copy explains extraction delay and complete management listing versus the 20-entry prompt cap. |
| R1-8 blank rows disabled reset and `/api/v1` escaped normalization | **fixed** | Typed listings retain blank rows and their ids so management can delete them, while prompt rendering ignores blank content. Path normalization handles both `/v1/...` and `/api/v1/...`. |

### Round 2 validation

- Focused memory/adapter/console/probe suite: **173 passed**.
- Full repository suite: **858 passed, 3 skipped, 6 deselected, 1 failed**. The sole failure is
  the unchanged, pre-existing README/config diagram mismatch from unrelated worktree edits.
- Ruff passed on every changed Python/script/test file; `git diff --check` and console deploy-script
  syntax passed.
- Codex eval harness: both `regression.*` cases passed. The readiness probe stops at its historical
  old-behavior redaction assertion, as recorded in the baseline; it was not weakened.
- Live generation probe: final supported-category lifecycle/cleanup trace
  `e3c30627c7f945a99a54b6e7535f20a3`; rendered context and saved prompt both excluded the marker.
- No deployment was performed.
