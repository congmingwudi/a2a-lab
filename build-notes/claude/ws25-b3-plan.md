# WS25 b3 — CRM writes and session ownership (plan)

Batch 3 of the Codex readiness response (D79/D80). Answers three P1 findings:
**F07** (A4 — retries/interruption duplicate or lose brief delivery), **F03
write-half** (A5 — model-chosen partial account match on a real write), and
**F02** (A11 — conversations not isolated by authenticated caller). Scope is
the three item lines in `plan/07-workstreams.md` §WS25 (items 6, 7, 8).

Built test-first on branch `ws25-b3-crm-writes-sessions`, one branch for the
batch. `uv run pytest` + `ruff` green and the CLAUDE.md done-definition met
before `handoff built`. This file is the plan for the **critique** station —
the code citations are current `main` (commit `474913d`); the design decisions
flagged **[DECISION]** are the ones I most want Codex to push on before I open
a file, per plan/16 ("scope recorded before an implementer opens a file").

Every item touches `src/` (and A4 touches Salesforce metadata), so the deploy
is a full rebuild of the affected images — no `--skip-build`. Deploy footprint
is listed per item and consolidated at the end. Deploys run from the operator's
work machine per the b1/b2 runbook pattern.

---

## A4 — brief watcher outcomes + idempotent CRM delivery (F07)

### What exists (main)
- `src/briefs/__main__.py:104-131` `watch()`: on each deployment run it calls
  `service_scheduled_session`, and at **:127-128** runs `serviced.add(session_id)`
  + `_save_serviced(serviced)` **unconditionally**, after the `finally` — so a
  caught failure (`:123-124`, print only) is recorded as serviced and never
  retried. State is an id-only list `brief_serviced_sessions` (bounded 500),
  stored via `PgObsStore.put_state/get_state` (`lab_state` table) or the file
  `.a2alab/brief_state.json` (`WATCH_STATE`, `:36`).
- `src/briefs/runner.py:328-371`: the `save_account_brief` tool branch calls
  `save_brief(..., research_session_id=session_id)` (`:345-351`) and **swallows**
  a delivery failure at `:359-360` (empty `deliveries`, no exception raised) — so
  the watcher never even sees a Salesforce failure as an exception.
- `src/briefs/salesforce.py:97-229` `save_brief`: separate REST inserts —
  `A2ALab_Account_Brief__c` (`:144-146`) then `Task` (`:176`). `Research_Session_Id__c`
  is written (`:134`) but the field is `unique=false`
  (`salesforce/force-app/main/default/objects/A2ALab_Account_Brief__c/fields/Research_Session_Id__c.field-meta.xml`).
  No pre-insert existence check; no upsert. Two runs for one session ⇒ two briefs + two Tasks.
- Console: no per-session outcome view; only a static list-view link (`console/app.py:896-907`).

### Change
1. **Outcome record replaces the id-only set.** New shape under the same state
   key/store: `{session_id: {"outcome": "delivered"|"failed"|"pending",
   "attempts": N, "last_error": str|None, "at": iso}}`. Migration: an existing
   list of ids loads as `{id: {"outcome":"delivered","attempts":1}}` so no
   session is re-serviced on first deploy. Keep the 500-entry bound (evict
   oldest `at`).
2. **The watch loop stops marking failures serviced.** Move the outcome write
   inside the flow: success ⇒ `delivered`; a caught failure ⇒ increment
   `attempts`, store `last_error`, and set `pending` (retryable) until
   `A2ALAB_BRIEF_MAX_ATTEMPTS` (default 3), then `failed` (terminal). A
   `pending` session is retried on the next tick; `delivered`/`failed` are
   skipped. `__main__.py:127-128`'s unconditional add is removed.
3. **Surface the delivery failure the runner swallows.**
   `service_scheduled_session`/`_drive` return a structured result carrying
   `delivered: int` and `error: str|None` so the watcher can tell a real
   delivery from a no-op. `runner.py:359-360` still returns a tool result to the
   model (so the agent turn completes) but records the failure in that result
   rather than dropping it. **[DECISION]** return-value vs raise: I propose a
   return value (the model turn should still finish cleanly) with the watcher
   deciding retry/terminal — Codex: is a raise cleaner for the durability story?
4. **Idempotent delivery keyed by session.** In `save_brief`, before inserting,
   query `A2ALab_Account_Brief__c WHERE Research_Session_Id__c = :sid`. If a
   brief exists, do not insert a second; **repair** the partial pair — if the
   brief exists but its `Task` (matched by `WhatId` = brief's `Account__c` +
   `Research_Session_Id__c` echoed in the Task, or a Task lookup field) is
   missing, create only the Task, and vice-versa. Return the existing/repaired
   ids. **[DECISION]** the Task has no session-id field today; to make the pair
   individually idempotent I plan to add `Research_Session_Id__c` echo to the
   Task subject or a custom field — Codex: acceptable, or key the Task off the
   brief Id via a lookup?
5. **Defense-in-depth uniqueness in the org.** Metadata change: make
   `Research_Session_Id__c` an **External Id + unique** text field so a duplicate
   insert fails at the org even if the app check races. **[DECISION / risk]** a
   unique-field deploy to production **fails if historical duplicates exist**;
   the plan includes a pre-deploy SOQL dupe-scan (group by `Research_Session_Id__c`
   having count > 1) and, if any, a documented manual cleanup before the field
   flips unique. If Codex/operator prefer to avoid the prod schema change, the
   app-level pre-insert check (step 4) alone satisfies the acceptance criterion;
   the unique field is the belt-and-braces I recommend but will gate on the scan.
6. **Console surfaces terminal-failed.** A small reader of the outcome state in
   the console (Details/DevOps area) listing sessions with `outcome=="failed"`
   and their `last_error`. Empty state explains itself ("no failed brief
   deliveries recorded"). Needs the console image rebuild.

### Tests (first)
- watcher: a caught failure sets `pending`+`attempts=1`, not serviced, and is
  retried next tick; the 3rd failure flips `failed` and is then skipped; a
  success sets `delivered`.
- old-format state (list of ids) migrates to `delivered` with no re-service.
- `save_brief` second call for the same `research_session_id` inserts nothing
  and returns the existing ids (fake SF client asserting one insert).
- repair: brief present, Task absent ⇒ exactly one Task insert, no brief insert.
- console reader shows a `failed` session and hides `delivered`.

### Deploy footprint
Watcher/runner image (WS14 hosted watcher) + console (full rebuild); Salesforce
Metadata API deploy for the `Research_Session_Id__c` unique/externalId change
(after the dupe-scan). No Apex, so no coverage gate for the field itself.

---

## A5 — deterministic account resolution (F03 write-half)

### What exists (main)
- `src/briefs/salesforce.py:107,121-126`: `Name LIKE '%{safe_name}%' LIMIT 1`
  then `records[0]`. Empty name ⇒ `LIKE '%%'` matches **every** Account and one
  arbitrary row is written against. Multiple matches ⇒ silent first-row pick, no
  ordering. `save_brief` takes only `account_name` (no id, `:97-105`).
- The name is **model-provided** (`runner.py:343-346`, `tool_input["account_name"]`).
- No preauthorized Account Id anywhere: the scheduled session's `initial_events`
  and `.a2alab/brief.json` carry account **names** only (`setup_brief_agent.py:99-124`);
  `A2ALAB_BRIEF_ACCOUNTS` default `'Apple Inc.'` (`.env.example:290`).
- No tests exercise this path.
- Query runs as the client-credentials run-as user over REST `/query` v62.0;
  a `WHERE Id =` retrieve is possible but unused.

### Change
1. **Deterministic resolution in `save_brief`.** New order:
   (a) if a preauthorized `account_id` is supplied, retrieve `WHERE Id = :id`
   and use it (reject if it doesn't exist); the model's name is advisory and a
   mismatch is logged, not fatal.
   (b) else require a non-empty, non-whitespace name (reject empty — kills the
   `LIKE '%%'` path); query `WHERE Name = :name` (exact) first; exactly one ⇒
   use it; zero exact ⇒ optional single `LIKE` fallback that **also** requires
   exactly one candidate; **two or more ⇒ raise** listing the candidate names
   (no arbitrary pick). Never emit `LIKE '%%'`.
2. **Thread a preauthorized id from session inputs.** Extend the brief config to
   carry ids: `A2ALAB_BRIEF_ACCOUNTS` entries may be `Name` or `Name::<AccountId>`
   (or a parallel `A2ALAB_BRIEF_ACCOUNT_IDS`); `setup_brief_agent.py` persists the
   id into `.a2alab/brief.json` state and the kickoff, and `service_scheduled_session`
   passes it through `_drive`/`_handle_event` into `save_brief` as `account_id`.
   **[DECISION]** exact form of the config (inline `Name::Id` vs a separate map)
   — I lean to a separate `A2ALAB_BRIEF_ACCOUNTS` map so names stay readable in
   the kickoff prompt; Codex's call welcome.

### Tests (first)
- empty/whitespace name ⇒ rejected (no query emitted, or a guarded raise).
- two Name matches ⇒ raise naming both candidates; nothing written.
- exactly one exact match ⇒ used.
- preauthorized `account_id` present ⇒ `WHERE Id =` path taken, model name ignored.
- assert the emitted SOQL is never `LIKE '%%'`.

### Deploy footprint
Watcher/runner image only (Python). No Salesforce metadata. If the preauthorized
id is configured, `.env`/state carries it (no hardcoded ids — `${VAR}` per CLAUDE.md).

---

## A11 — shim session isolation by verified caller (F02)

### What exists (main)
- `deploy/shim/handler.py:36`: `AgentforceProxyAdapter(session_reuse=True)`.
- `src/platforms/agentforce/proxy.py:64-65`: a sessionless request gets
  `req.session_id = f"shim-shared-{platform or 'direct'}"` — **platform only**, so
  every caller on a platform shares one Agentforce conversation.
- The verified JWT subject lands in `scope["state"]["lab_user"]` (auth.py:113)
  but is **not** threaded into the `AgentRequest` (a2a.py:96-101 builds `req`
  from message metadata only), so proxy.py cannot see it. WireTap reads it for
  the trace source (`wiretap.py:52-64`).
- `src/platforms/agentforce/client.py:141-146` `ensure_session`: lock-free
  check-then-create; `:60-61` `_sessions` keyed by lab `session_id`; concurrent
  sessionless calls on one shared key can race to create duplicate SF sessions.
- Legacy shared `A2ALAB_TOKEN` vs minted JWT is distinguished at `auth.py:110`
  (`supplied != expected and _looks_like_jwt`): the shared token sets **no**
  `lab_user`; a JWT (human or machine) sets `lab_user` with a `sub`.

### Change
1. **Thread the verified subject into the request.** In `TokenAuthMiddleware`,
   after a successful verify, publish the verified `sub` where the A2A executor
   can read it when building `req` — via a `contextvar` in `interop.identity`
   (or `servers/auth.py`) set per-request and read in `AdapterExecutor.execute`
   (a2a.py) to stamp `req.metadata["verified_subject"]`. **Verified**, not
   caller-asserted: it comes from the RS256-checked claim, not the message body.
   **[DECISION]** contextvar vs passing `scope` into the adapter: the executor
   is created without the ASGI scope in hand, so a contextvar set by the auth
   middleware is the least-invasive seam that keeps the value trustworthy —
   Codex: is there a cleaner threading point (e.g. the A2A app injecting scope)?
2. **Key the reused session by (platform, verified subject).** proxy.py:
   `shim-shared-{platform}-{subject}` when a verified subject exists; fall back
   to the platform-only `shim-shared-{platform}` **only** for the legacy shared
   service token (no verified subject). Read the subject via
   `req.metadata["verified_subject"]` (set in step 1); a caller-asserted
   `user_context` is **not** trusted for the key.
3. **Serialize session creation.** Add an `asyncio.Lock` per lab session key in
   `AgentforceClient` around `ensure_session`/`start_session` so concurrent
   sessionless calls on one key create exactly one SF session (double-checked
   inside the lock).

### Tests (first)
- two different verified subjects on the same platform ⇒ two distinct session
  keys (`shim-shared-adk-<subA>` vs `-<subB>`), two SF sessions.
- the legacy shared token (no verified subject) ⇒ still collapses to
  `shim-shared-{platform}` (reuse preserved for the service path).
- a caller-asserted `user_context` with no verified subject does **not** change
  the key (asserted ≠ verified).
- concurrency: two coroutines racing `ensure_session` on one key ⇒ exactly one
  `start_session` (lock works); extend `test_agentforce_client.py`.

### Deploy footprint
`a2a.py` is the shared A2A server, so the executor change ships to **faces,
bridge, console, and the shim** (all full rebuilds). proxy.py + client.py ride
the shim image; **shim Lambda redeploy** (`deploy/shim/build_zip.sh &&
deploy/shim/deploy_shim.sh`). Verify the executor change is a no-op for callers
that present no JWT (existing shared-token and cloud-IAM callers unchanged).

---

## Consolidated deploy footprint (work machine, full rebuilds)
- Faces, bridge, console (a2a.py verified-subject threading; console also for the
  A4 outcome view): `deploy/faces/deploy_faces.sh`, `deploy/bridge/deploy_bridge.sh`,
  `deploy/console/deploy_console.sh`.
- Shim: `deploy/shim/build_zip.sh && deploy/shim/deploy_shim.sh`.
- Hosted watcher/runner image (A4/A5): the WS14 watcher deploy.
- Salesforce Metadata API deploy for `Research_Session_Id__c` (A4 step 5) —
  **only after** the historical-duplicate scan is clean.
- Hosted smoke: two verified subjects get isolated shim sessions; a re-serviced
  failed brief retries then goes terminal; a duplicate delivery is a no-op.

## Cross-review asks for Codex
- A4 step 3: return-value vs raise for the swallowed delivery failure.
- A4 step 4/5: the Task idempotency key, and whether to make the prod field
  unique (schema change + dupe-scan) or rely on the app-level check alone.
- A5 step 2: config form for the preauthorized account id.
- A11 step 1: the contextvar threading of the verified subject — cleaner seam?
- Rerun your own evidence: `readiness_probes.py` (F02/F07 probes should still
  reproduce on `main`, and I expect the F02 shared-key probe and the F07 worker
  overlap to be the shapes b3 must move — b3 does not touch F08's runner, which
  is b4/A7).

## Not in scope (guarding against drift)
- F07's fan-out atomic claim / lease / reaper (`fanout_mcp/tasks.py`) and durable
  A2A task state are **b4/A6**, not here — b3 is only the brief-watcher and CRM
  half of F07.
- F03's **read** half (`without sharing` Apex, `bypassUser`) is an accepted risk
  recorded in b5/D (NFR-201 demo-org operating model); b3 is the write half only.
- F08 async deadline is b4/A7.
