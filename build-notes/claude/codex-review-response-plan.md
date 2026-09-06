# Response plan: Codex codebase review (2026-09-05/06)

> **Corrected 2026-09-06** after Codex's critique
> (`../codex/codebase-review/model-selection-and-plan-response.md`), every
> point verified against source. Scope settled in D80; delivery tracked as
> WS25 in `plan/07-workstreams.md`; the joint process is D79 /
> `plan/16-joint-agent-workflow.md`. Corrections are applied inline below and
> marked *[corrected]*.

Source: `build-notes/codex/codebase-review/` — `README.md` (module map + six risk
areas), `production-readiness.md` (F01–F13), `test-and-eval-review.md` (six
test-estate findings + an independent eval harness). Reviewed at `a806b6d`,
static only; Codex ran no pytest.

Verified here on 2026-09-06 before planning: every cited line was re-read, the
review's `readiness_probes.py` reproduced all seven claims, and the harness's two
`regression.*` cases fail against real `TraceRecorder`/`JsonlFileSink`/`SqliteSink`.
Nothing in the review was found to be wrong. What the review does NOT weigh is the
lab's stated operating model: NFR-201 / X8 exclude production, the org serves
dummy demo data, and several "findings" are recorded design choices (D27, D36,
D37, `without sharing` in `A2ALabGetAccountSummary.cls`). So the plan sorts
findings into (A) defects to fix, (B) accepted risks to write down as an ADR, and
(C) test/eval changes — rather than treating all 13 as a production backlog.

## A. Defects to fix (real bugs under the lab's own rules)

Ordered by impact. Each item names the seam, the fix, the test, and the deploy.

### A1. Trace redaction misses serialized JSON secrets and `user_token` (F04, test-review #1)

- **Where:** `src/interop/trace.py` `redact()` — strings go only to `_redact_str`,
  so a raw wire body `{"client_secret":"…"}` (what `wiretap.py` records) is stored
  intact. `_SECRET_KEYS` lacks `user_token`.
- **Fix:** in `_redact_str`, add JSON-key-aware scrubbing. *[corrected]* The
  naive `"(<key>)"\s*:\s*"[^"]*"` form is insufficient: it stops at an escaped
  quote and misses JSON serialized inside a JSON string. Choose the
  implementation only after escape-aware, nested, truncated and case-variant
  cases exist against both sinks. Extend
  `_SECRET_KEYS` with `user_token`, `session_token`, `x-bridge-token`. Keep
  credentials-only policy (D37): business content stays raw.
- **Test:** port the harness's `regression.serialized-secret` and
  `regression.user-token` into `tests/unit/test_models_and_trace.py` next to
  `test_trace_event_writes_are_redacted` (assert against JSONL AND SQLite).
- **Deploy:** every image that records traces — faces, bridge, console, shim
  Lambda, fan-out Lambda. Full rebuilds (`src/` changed, not `--skip-build`).
- **Also:** one-off scan of existing `traces/*.jsonl` and Aurora `trace_events`
  for the pattern; scrub in place if anything real is found.

### A2. Viewer role is not enforced at protocol faces; `DELETE /api/traces` ungated (F01)

- **Where:** `src/interop/servers/auth.py` admits any verified lab JWT regardless
  of role; `src/console/app.py` `_OPERATOR_ONLY` omits `DELETE /api/traces`.
- **Fix:** (a) add the trace DELETE to the operator gate — trivially. (b) In
  `TokenAuthMiddleware`, after `_verify_lab_jwt`, resolve role via `identity`
  (directory, not claim, matching `_viewer_forbidden`) and reject `viewer` on
  the invoking routes — *[corrected]* the REAL mounted surface: REST
  `POST /invoke` and `/invocations`, MCP `tools/call`, A2A v1 `SendMessage` AND
  the 0.3 `message/send` spelling; tests exercise the mounted aliases;
  leave discovery routes (`/.well-known/agent.json`, `tools/list`, `/healthz`)
  open. Keep `machine` allowed (WS10 SP1 gateway identity is a faces caller).
- **Test:** `tests/unit/test_auth*.py` — viewer JWT → 403 on invoke, 200 on
  discovery; operator JWT and shared token unchanged. Console: viewer DELETE → 403.
- **Deploy:** console + faces + shim (shim mounts `TokenAuthMiddleware`).
- **Update:** the console's role Details copy and the `_OPERATOR_ONLY` comment
  ("traces stay viewer-visible" remains true; deletion is not viewing).

### A3. Missing auth config is silently "auth off" on hosted entry points (F06)

- **Where:** `src/bridge/app.py` `check_auth` (no `BRIDGE_TOKEN` → open),
  `src/mcp_http/http.py` `_auth_ok` (falsy token → True),
  `deploy/fanout/deploy_fanout.sh` builds the secret by dropping unset keys.
- **Fix:** fail closed when hosted: at startup, if `A2ALAB_MODE=hosted` (or the
  Secrets-Manager path was used) and the token is empty, exit non-zero with a
  clear message. Local dev keeps the open mode behind an explicit
  `A2ALAB_ALLOW_UNAUTH=1`. In the deploy script, `${VAR:?set VAR in .env}` for
  `A2ALAB_FANOUT_MCP_TOKEN` — which is the repo's existing convention anyway.
- **Test:** unit tests for both check functions with `None`/`""` under hosted
  and local; a `tests/unit/test_deploy_scripts.py`-style grep that every deploy
  script's secret construction uses `:?` for token keys.
- **Deploy:** bridge, fan-out Lambda (config-only for the script; code for
  `mcp_http`).

### A4. Brief watcher marks a FAILED session as serviced; delivery is not idempotent (F07 briefs half, README risk 1)

- **Where:** `src/briefs/__main__.py` watch loop — exception is caught, session
  still added to `serviced`. `src/briefs/salesforce.py` `save_brief` does
  Account lookup + brief insert + Task insert as separate calls.
- **Fix:** record outcome per session (`serviced_sessions` → map of
  `session_id → {state: done|failed, attempts, last_error}`); retry failed
  sessions up to N, then mark terminal-failed and surface in the console brief
  view. Make delivery idempotent on `A2ALab_Account_Brief__c` *[corrected:
  object name]* — and *[corrected]* query-before-insert is NOT concurrent-safe
  (two workers can both see nothing and insert): use a uniqueness boundary on
  `Research_Session_Id__c` on the Salesforce side and specify repair of a
  partial brief/Task pair.
- **Test:** `tests/unit/test_briefs*.py` — runner raises → session not serviced,
  retried next tick; second delivery of the same session inserts nothing.
- **Deploy:** the brief watcher host (see `plan/10-operations.md` "moving the
  brief watcher"); a Salesforce field if `Research_Session_Id__c` does not exist
  (Metadata API deploy, headless).

### A5. Brief delivery trusts a model-chosen partial Account name (F03 delivery half)

- **Where:** `src/briefs/salesforce.py` `LIKE '%name%' LIMIT 1`.
- **Fix:** exact-name match first; if zero or >1 results, fail the delivery with
  the candidates listed rather than picking the first. Reject empty names in
  `runner.py` before the tool runs. Prefer passing the Account Id from the
  scheduled session's inputs when available (the schedule knows which account it
  briefs) so the model never chooses.
- **Test:** ambiguous/empty/exact cases with a fake `_query`.

### A6. Fan-out durable tasks: no atomic claim, no lease (F07 fan-out half, README risk 2)

- **Where:** `src/fanout_mcp/tasks.py` `run_task` → `get` then `mark_working`.
- **Fix:** make the claim atomic:
  `UPDATE … SET state=WORKING, claimed_at=now() WHERE task_id=:id AND state=SUBMITTED`
  and only run when rowcount == 1. Add `claimed_at`; a `reap` path (called from
  `check` on read) flips WORKING rows older than the Lambda's max duration to
  FAILED with `error="worker lost"`. DDL via `scripts/pg_migrate.py` (D46 —
  `lab_writer` cannot ALTER).
- **Test:** extend `tests/unit/test_fanout_tasks.py` fake SQL client to honour
  the WHERE state predicate; overlapping `run_task` calls → runner invoked once.
- **Not doing:** replacing `InMemoryTaskStore` in the generic A2A server. Each
  face is a single Fargate task and the async experiments (WS11/D47) already
  record that as a finding; note it in B instead.

### A7. Async leg budget is not one absolute deadline (F08)

- **Where:** `src/orchestration/runner.py` `_run_leg_async` (deadline starts
  after submit; poll awaits unbounded) and `run_target_async` (fallback gets a
  fresh full timeout).
- **Fix:** compute `deadline = monotonic() + timeout_s` before submit; bound
  submit, every poll, and the sleep by `remaining()`; on
  `AsyncLifecycleUnsupported` fall back with `remaining()` not `timeout_s`. Late
  terminal poll after deadline → TimeoutError (the honest result under the
  Path A budget rules in CLAUDE.md). *[corrected]* One deadline bounds latency
  but a blocking fallback can still duplicate work the remote already accepted:
  define and test an accepted-work replay policy (cancel, idempotent, or refuse).
- **Test:** the review's probe shape (20 ms budget, slow fake poll) as a unit
  test in `tests/unit/test_orchestration.py`.
- **Deploy:** bridge + console (both run legs).

### A8. `matrix.py` marks an empty answer PASS (F09 / test-review #2)

- **Where:** `scripts/matrix.py` `run_cell` — success = no exception.
- **Fix:** PASS requires non-empty `text`; add `--expect <substring>` per
  scenario (from `config/scenarios.yaml`) so a cell can assert the business
  fact; record `runs`, model/backend, and mode in the appended `plan/03` row.
  Default `--runs` stays 1 but the row must say so (it already does).
- **Test:** unit test with a fake client returning `""` → FAIL.
- **Follow-up:** the "honest matrix" claim in `plan/02-matrix.md` — run the
  `matrix-honesty-sweep` skill after this lands, since prior PASS rows may have
  been empty answers.

### A9. Observability completeness gaps (F11)

- **LangSmith pagination:** `langgraph_source.py` reads one page of 100; loop on
  the cursor and set `HarvestResult.status="partial"` (new value) with the count
  if a cap is hit. Console harvest chip shows partial as amber.
- **Guide bypasses the Hop:** `/api/guide` calls `answer_stream()` directly;
  wrap the stream in a `Hop` (or open one inside `answer_stream`) and record
  `usage` from the final message so Guide spend appears in traces/cost.
- **Wiretap status by HTTP code:** after decoding the response, if it parses as
  JSON-RPC with an `error` member, record `status="error"` even on 200.
- **Tests:** canned two-page LangSmith payload; Guide route emits a hop; wiretap
  JSON-RPC error → error status. Loopback MCP tool-error test already exists —
  extend its assertion.

### A10. Packaging omits four packages (README risk 4)

- **Where:** `pyproject.toml` `[tool.hatch.build.targets.wheel]` lists 7 of 11
  `src/` packages; `briefs`, `faces`, `fanout_mcp`, `mcp_http` are missing.
- **Fix:** list all; add a unit test that walks `src/*/__init__.py` and asserts
  each is in the wheel list, so the next package cannot be forgotten. Nothing
  deploys from the wheel today (images `COPY src`), so this is hygiene, but it is
  the kind of drift the CLAUDE.md "in the image" rule exists for.

### A11. Hosted shim shares one Agentforce conversation across callers (F02)

- **Where:** `deploy/shim/handler.py` `session_reuse=True` →
  `shim-shared-<platform>`.
- **Fix:** key the reused session by `(platform, caller subject)` where the
  subject comes from the verified JWT in `scope["state"]["lab_user"]`, falling
  back to the shared key only for the legacy service token. Add a per-key
  `asyncio.Lock` around session creation in `AgentforceClient`.
- **Why it matters for the lab, not just production:** two concurrent
  experiments from different personas currently contaminate each other's
  Agentforce context, which corrupts the very comparisons the lab records.
- **Deploy:** shim Lambda (`deploy/shim/build_zip.sh && deploy_shim.sh`).

## B. Accepted risks — record, do not build (one ADR, D79)

These are true statements about the code that the lab's operating model already
answers. Recorded as **D80** (D79 is the joint-workflow process) so the next
reviewer does not re-raise them, and cite it from the console's Architecture /
Observability Details panes where the relevant component is narrated.

| Finding | Accepted because | Where to say it |
|---|---|---|
| F03 `without sharing`, `bypassUser`, no per-user CRM policy | Deliberate (class header comment); org holds demo data only *[corrected: an operating assumption the operator asserts, not established by source inspection]*; identity propagation is an *attribution* experiment (D36/D37), not an authorization one. A5 still fixes the write-target half. | ADR + `plan/02-matrix.md` ledger |
| F05 full managed toolset + open network | The research brief *is* the web-research experiment (WS7); Salesforce creds stay host-side by design. Note prompt-injection exposure as an untested hypothesis in `config/insights.yaml`. | ADR + insight entry |
| F10 no admission-time spend controls | Cost sentinel (WS12/D44) is retrospective on purpose; viewer-callable Guide is bounded per turn. Add body-size limit to `AgentRequest.from_dict` (cheap, do it in A) and leave quotas out. | ADR |
| F12 synchronous trace sinks, no retry queue | Evidence-first ethos: a request that cannot record must still answer. *[corrected]* There is NO `to_thread` mitigation — `TraceRecorder.record` and `PostgresSink.emit` are synchronous; accept that as-is. Add a dropped-event counter to `/api/obs/summary` (small) and stop there. | ADR + obs Details pane |
| F13 analyst SELECT on all tables, no purge | `lab_reader` read-only + 15 s timeout is the control. *[corrected]* `DEFAULT_TRACE_TTL_DAYS=14` is the DynamoDB sink's TTL, not a JSONL purge — JSONL and Aurora have no purge; say so in `plan/05-observability.md`. Aurora retention is a WS19 concern. | ADR + plan/05 |
| F07 A2A `InMemoryTaskStore` | Single replica per face; restart-loses-task is a recorded platform finding (WS11), not a defect to engineer around. | ADR |
| No CI workflow | Solo lab; tests run locally before every deploy (`plan/10-operations.md`). Decide separately whether a GitHub Actions `uv run pytest` is worth adding — it probably is, and it is ~20 lines. | ADR (or just add it) |
| README risk 5 delegation depth is metadata-borne | D27 is a *cooperating* guard against accidental loops, not tamper-proof; documented as such. | ADR |

## C. Test and eval estate

1. **Adopt the two regression cases into pytest** (done as part of A1). The
   harness's other 16 `repository` cases execute real modules and are worth
   porting where they add assertions the suite lacks: duplicate-hop replay,
   sink-outage warning, obs upsert idempotency, platform-qualified native-id
   joins (test-review #4 — `store.py` `list_sessions`/`lab_traces_for` compare
   `platform_ref` without a platform predicate; add the predicate and the test).
2. **Keep the harness where it is** (`build-notes/codex/codebase-review/eval-harness/`),
   stdlib-only, as Codex's artifact. Do not wire it into `uv run pytest`; the 71
   synthetic-replay cases grade invented evidence and would read as coverage the
   lab does not have. Reference it from `plan/05-observability.md` as the
   evidence-grading rubric for WS20 (actor-critic over insights).
3. **Insights numeric certification** (test-review #5) is WS20's job: export
   real measurement JSONL for each `measured` insight and grade with the
   harness's `insight` area, then run the `insights-audit` skill. Any measured
   claim without a retained sample demotes to observed.
4. **Do not** add browser automation now (README risk 6). The console's
   re-entrancy rules in CLAUDE.md are the current control; revisit if the
   console gains a second contributor.

## D. Record-keeping (the CLAUDE.md discipline, done in the same change)

- `plan/00-decisions.md`: D80 as above (done 2026-09-06).
- `plan/07-workstreams.md`: `## WS25` with the items as `N. ⏳` lines (done
  2026-09-06) so `jira_sync.py` and the Project page see
  stories, not prose. Dry-run the sync; `--apply` is the operator's call.
- `plan/02-matrix.md` ledger: the F03/F05 accepted-risk entries.
- `plan/09-deployment-map.md`: no new components, but A6 adds a column and A4
  may add a Salesforce field — note both in the L6 table rows that own them.
- `build-notes/codex/README.md`: link this plan from the review's entry.
- Console Details panes touched: role gating (A2), fan-out task lifecycle (A6),
  Guide instrumentation (A9), plus the D79 chip where B-items are narrated.

## Suggested order and sizing

| Batch | Items | Why first | Rough size |
|---|---|---|---|
| 1 | A1, A2, A3, A10 | Security-shaped, small, all unit-testable offline; one faces/console/bridge/shim/fan-out rebuild covers them | 1 day incl. deploys |
| 2 | A4, A5, A11 | Salesforce-touching; A4/A5 protect a real org write, A11 protects experiment validity | 1 day |
| 3 | A6, A7, A8 | Orchestration correctness; needs `pg_migrate` for A6 | 1 day |
| 4 | A9, C1, C3 | Observability completeness, then re-run insights-audit | 1–2 days |
| 5 | B, D | ADR, WS25 lines, Details copy, plan/02 ledger, deploy console | ½ day |

Each batch ends with `uv run pytest`, `ruff check`, the affected deploy scripts,
a hosted smoke of the changed route, and a Codex cross-review that reruns
`readiness_probes.py` (expected to go RED where the defect is fixed — it asserts
the old behaviour) and the harness `regression.*` cases (expected to go green).
The review ran no tests, so this plan is the first time its findings meet the
suite.
