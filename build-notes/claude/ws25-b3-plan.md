# WS25 b3 — CRM writes and session ownership (plan, rev 2)

Batch 3 of the Codex readiness response (D79/D80). Answers three P1 findings:
**F07** (A4 — retries/interruption duplicate or lose brief delivery), **F03
write-half** (A5 — model-chosen partial account match on a real write), and
**F02** (A11 — conversations not isolated by authenticated caller). Scope is
the three item lines in `plan/07-workstreams.md` §WS25 (items 6, 7, 8).

**Rev 2** answers Codex's REQUEST-CHANGES critique
(`build-notes/codex/reviews/ws25-b3.md`) — four blockers, all verified against
source and all resolved concretely below. The `▲ was-blocker N` markers show
where. **[ADR]** marks the two decisions the settling ADR must own because they
change the production Salesforce org.

Built test-first on branch `ws25-b3-crm-writes-sessions`, one branch for the
batch. `uv run pytest` + `ruff` green and the CLAUDE.md done-definition met
before `handoff built`. Code citations are current `main` (verified this
round: `runner.py:225-242`, `runner.py:343-371`). Deploys run from the operator's
work machine; every affected image bakes `src/`, so each is a FULL rebuild.

---

## A4 — brief watcher outcomes + idempotent, replayable CRM delivery (F07)

### What exists (main, re-verified)
- `src/briefs/runner.py:225-242` `_drive`: on a scheduled (kickoff=None) tick it
  builds `answered` from every `user.custom_tool_result` in history and passes
  it as `skip_tool_ids`, so an already-answered tool call is skipped
  (`:329-330`).
- `src/briefs/runner.py:343-371`: the `save_account_brief` branch catches a
  writer exception (`:359-360`) and **still sends** a `user.custom_tool_result`
  (`:361-370`). That marks the failed call *answered* — so a later tick skips it
  and **Salesforce is never retried**. The model is instructed one call per
  account (`:42-67`), so one session can carry several save calls that succeed
  or fail independently.
- `src/briefs/__main__.py:127-128` marks `session_id` serviced unconditionally
  after the `finally`; state is an id-only list `brief_serviced_sessions`
  (bounded 500) with **no `at`** on any entry (`:59-89`), in `lab_state`
  (Aurora) or `.a2alab/brief_state.json`.
- `src/briefs/salesforce.py:97-229` `save_brief`: separate REST inserts —
  `A2ALab_Account_Brief__c` (`:144-146`), then `Task` related to the Account by
  `WhatId` (`:150-176`), with **no session key on the Task**. `Research_Session_Id__c`
  is written (`:134`) but `unique=false`. The brief object has **activities
  disabled** (`A2ALab_Account_Brief__c.object-meta.xml:20-27`), so a Task cannot
  be a child activity of the brief.

### Change

**1. Per-call delivery ledger, replayed directly — not via the managed session.**
`▲ was-blocker 1.` The retry unit is the individual `save_account_brief` call,
not the session. Add a durable ledger to the watcher outcome state:
```
{ session_id: { "status": "pending"|"delivered"|"failed",
                "attempts": N, "at": iso, "last_error": str|None,
                "calls": { tool_use_id: { "status": "ok"|"failed",
                                          "account": str, "account_id": str|None,
                                          "brief_id": str|None, "task_id": str|None,
                                          "error": str|None } } } }
```
- The `save_account_brief` branch (`runner.py:343-371`) is rewritten to consult
  the ledger by `tool_use_id`: a call already `ok` is **skipped** (no re-write,
  no duplicate); a `failed`/unseen call **attempts `save_brief`** and records the
  per-call result. This **replaces** the managed-history `answered` skip *for the
  save tool only* — "answered by a managed tool-result" is not "delivered to
  Salesforce". Non-save tool calls keep the existing `answered` skip.
- On failure the branch still sends a `user.custom_tool_result` (so the managed
  turn completes — Codex agreed a structured return beats raising), but the
  ledger, not the tool-result, is the source of truth for retry.
- The tool **input survives in session history**, so a retry re-reads the failed
  call's `input` from the managed events; no brief body is persisted in state.
- `service_scheduled_session`/`_drive` return a **per-call** structured result
  (`{calls: [...], delivered: int, failed: int, error: str|None}`), not one
  `delivered` integer. `runner.py:359-360` no longer silently swallows.

**2. Watcher outcome + bounded retry, honest terminal states.** `▲ was-blocker 1/2.`
- `__main__.py:127-128`'s unconditional serviced-add is removed. After a tick the
  watcher sets: **`delivered`** only when *every* observed save call is `ok`;
  **`pending`** (retry next tick) while any call is `failed` and
  `attempts < A2ALAB_BRIEF_MAX_ATTEMPTS` (default 3); **`failed`** (terminal)
  once attempts hit the cap. **A session with zero save calls and no error is
  NOT `delivered`** — it is recorded `pending`/anomalous, never silently
  serviced.
- **Legacy migration is deterministic.** `▲ was-blocker 2.` An old id-only list
  loads as `delivered` with `attempts=1`; because legacy entries have no `at`,
  they are assigned an explicit ascending ordinal from their existing list
  position (oldest = lowest), and the 500-entry bound evicts by
  `(at or ordinal)` so mixed legacy/new records evict in a defined order.

**3. Race-safe, org-enforced idempotency — the required uniqueness boundary.**
`▲ was-blocker 2.` D80 and WS25 item 6 make the Salesforce uniqueness boundary
**mandatory** (`plan/00-decisions.md` D80, `plan/07-workstreams.md:3528-3532`);
rev 1 wrongly made it optional. The key is **per (session, account)**, not
`research_session_id` alone — one session writes multiple accounts.
- **[ADR] New external-id field `A2ALab_Delivery_Key__c` (Text 255, External Id,
  Unique)** on `A2ALab_Account_Brief__c`, value `<research_session_id>#<account_id>`.
  The brief is written by **external-id upsert**
  (`PATCH /sobjects/A2ALab_Account_Brief__c/A2ALab_Delivery_Key__c/<key>`), which
  is atomic at the org: the losing writer's upsert *matches* the winner's row and
  returns it — no second brief. `Research_Session_Id__c` stays as a plain
  queryable field for the console/traceability.
- **[ADR] The Task gets the same idempotency boundary.** Because activities are
  disabled on the brief, the Task cannot hang off the brief; instead add
  **`A2ALab_Delivery_Key__c` (Text 255, External Id, Unique) on `Task`** with the
  same value, and **upsert the Task** on it. This is what makes the *pair*
  idempotent under concurrent writers, not just a Subject echo. Both fields need
  FLS on the integration user's permission set.
- **Deploy gating unchanged:** a pre-deploy SOQL duplicate scan on the composite
  key (group-by having count > 1) must be clean before the unique fields deploy;
  document any manual cleanup. **[ADR]** confirms the two production field
  additions + permset FLS are authorized.

**4. Console surfaces terminal-failed.** A reader of the outcome state in the
console listing `status=="failed"` sessions and `last_error`; empty state
explains itself. Needs a **console** image rebuild (A4 view only — see deploy).

### Tests (first)
- **two-tick retry:** first tick `save_brief` raises → session `pending`,
  call `failed`, **not serviced**; second tick asserts **the writer is called
  again** (twice total) and, on success, session flips `delivered`.
- **two-account partial success:** call A ok, call B fails → session `pending`,
  next tick re-attempts **only B**; assert A's writer is **not** called twice
  (no duplicate) and B is repaired.
- **terminal cap:** the Nth consecutive failure flips `failed`; a `failed`
  session is skipped thereafter.
- **zero-save session:** no save calls, no error ⇒ **not** `delivered`.
- **idempotent upsert / losing race:** two concurrent writers on one delivery
  key ⇒ exactly one brief and one Task; the loser returns the winner's ids, no
  second record (fake SF asserting one create per key).
- **partial-pair repair, both directions:** brief present/Task absent ⇒ one Task
  upsert, no brief insert; and the reverse.
- **legacy migration + eviction:** old list migrates to `delivered`; the 500
  bound with mixed legacy(no-`at`)/new records evicts in the defined order.
- console reader shows a `failed` session, hides `delivered`.

### Deploy footprint
Hosted watcher/runner image (WS14) + **console** (A4 view); Salesforce Metadata
API deploy of the two `A2ALab_Delivery_Key__c` fields + permset FLS **after** the
duplicate scan is clean.

---

## A5 — deterministic account resolution via a trusted name→Id map (F03 write-half)

### What exists (main)
- `src/briefs/salesforce.py:107,121-126`: `Name LIKE '%{safe_name}%' LIMIT 1` →
  `records[0]`. Empty name ⇒ `LIKE '%%'` (matches all); multiple ⇒ arbitrary
  first row; `save_brief` takes only `account_name`.
- The name is model-provided (`runner.py:343-346`); one deployment can request
  **multiple** accounts, one save call each (`setup_brief_agent.py:59-63`,
  `runner.py:64-67`); the scheduled watcher receives only a session id
  (`runner.py:185-199`), so a single preauthorized `account_id` can't be threaded
  into every call.
- The hosted watcher has **no `.a2alab/brief.json`**; its env is an explicit
  allow-list (`runner.py:105-139`, `deploy/briefs/deploy_briefs.sh:117-140`).

### Change
**1. Operator-authored name→Account-Id map, resolved per call.** `▲ was-blocker 3.`
- **Config is a JSON env value** `A2ALAB_BRIEF_ACCOUNT_MAP`
  (e.g. `{"Apple Inc.":"001...","Acme, Inc.":"001..."}`) — JSON, not a delimiter,
  because account names contain commas. No hardcoded ids (`${VAR}` per CLAUDE.md).
- `save_brief` resolves **each** call's model name against the map. On a hit:
  validate the Id shape (15/18-char, correct key-prefix) and retrieve the
  Account by `WHERE Id`; if the retrieved Account's name **conflicts** with the
  model's asserted name → **reject the call, write nothing** (a mismatch is *not*
  merely logged — logging it would attach one account's brief to another).
- On a miss (name not in the map): fall back to the deterministic name resolver
  below.

**2. Deterministic, injection-safe name resolver.** `▲ was-blocker 3.`
- Reject empty/whitespace names (kills `LIKE '%%'`).
- Exact match first: `WHERE Name = :name` fetching **≥2 rows** to *detect
  duplicate exact names*; exactly one ⇒ use it; two+ ⇒ **raise/ambiguous, no
  write**, listing candidates.
- Optional single `LIKE` fallback only when the exact query returns zero, and it
  must **escape `'`, `\`, `%`, `_`** so a crafted name can't recreate `LIKE '%%'`
  or inject; it too requires exactly one candidate or it is ambiguous → no write.

**3. Hosted env plumbed.** `▲ was-blocker 3.` Add `A2ALAB_BRIEF_ACCOUNT_MAP` and
`A2ALAB_BRIEF_MAX_ATTEMPTS` to **`.env.example`** and to the
**`deploy/briefs/deploy_briefs.sh` allow-list (`:117-140`)**, or the feature works
locally and vanishes on Fargate.

### Tests (first)
- map hit ⇒ `WHERE Id` path, validated; **binding mismatch** (mapped Id's
  Account name ≠ asserted name) ⇒ **no write**.
- empty/whitespace name ⇒ no query / guarded reject.
- two exact `Name` matches ⇒ ambiguous, **no write**, candidates named.
- adversarial names — containing `'`, `\`, `%`, `_` — are escaped; assert the
  emitted SOQL is never `LIKE '%%'` and never breaks the quoting.
- exactly one exact match (no map entry) ⇒ used.

### Deploy footprint
Hosted watcher/runner image only. `.env` carries the map + cap (no hardcoded ids).

---

## A11 — shim session isolation by *verified* caller (F02)

### What exists (main, re-verified)
- `src/interop/servers/a2a.py:75-101` `AdapterExecutor.execute` **always** copies
  `context.context_id` into `AgentRequest.session_id`. With the pinned a2a-sdk
  1.1, `RequestContext` **generates** a context id when the inbound message omits
  one — so `req.session_id` is essentially never `None` on a real hosted call.
- `src/platforms/agentforce/proxy.py:61-66`: the reuse key is gated on
  `if req.session_id is None and self.session_reuse` — **dead on the real server**
  (blocker 4): the SDK-generated context id means the `(platform, subject)` key
  is never installed. Today it collapses every caller on a platform to
  `shim-shared-{platform}`.
- The verified JWT subject lands in `scope["state"]["lab_user"]`
  (`auth.py:105-131`) but is **not** threaded into the request; a caller could
  also *assert* a `user_context` in the message body (untrusted).
- `client.py:141-146` `ensure_session` is a lock-free check-then-create.

### Change
**1. Reuse mode overrides the SDK context id with the D80 reuse key.**
`▲ was-blocker 4.` The `if req.session_id is None` guard is **removed**; when the
shim runs `session_reuse=True`, the proxy **deliberately overrides** whatever
`session_id` the SDK generated with the reuse key. Key =
`shim-shared-{platform}-{verified_subject}` when a verified subject is present;
**fallback** `shim-shared-{platform}` **only** for the legacy shared service
token (no verified subject). `session_reuse=False` (local/faces) is unchanged.

**2. Thread the *verified* subject so it survives the SDK's background hop.**
`▲ was-blocker 4.` Because the a2a-sdk runs the executor in a background task,
the trusted subject is threaded on a path that travels with the request, not one
that can leak across concurrent requests:
- `TokenAuthMiddleware`, on a successful **JWT verify only**, publishes the
  subject. Primary mechanism: stamp it where the executor reads it when building
  the request, and **overwrite** any caller-supplied `user_context`
  (`req.metadata["verified_subject"]`) — caller-asserted identity is never
  trusted for the key. A `contextvar` is acceptable *only* if set solely from
  verified claims and **reset in `finally` with its token on every request**; the
  overlapping-request test below is the gate that decides mechanism.
- The shared token sets **no** verified subject (so it takes the fallback key).

**3. Serialize session creation.** `▲ (rev-1 item, retained).` Add an
`asyncio.Lock` per lab session key around `ensure_session`/`start_session`
(double-checked inside the lock) so concurrent calls on one key create exactly
one Agentforce session.

### Tests (first) — through the real stack, not the proxy in isolation
- **through `TokenAuthMiddleware` + the real `AdapterExecutor`** (not
  `proxy(session_id=None)`): two different verified subjects on one platform ⇒
  two distinct keys/SF sessions; two calls for one subject ⇒ reuse; the legacy
  shared token ⇒ the chosen fallback key.
- **overlapping ASGI requests / SDK background path:** concurrent requests — a
  JWT subject alongside a shared-token request, and two different JWT subjects —
  prove **no subject leaks** into the other (the `finally` reset / request-bound
  threading works under concurrency).
- **caller-asserted `user_context` is overwritten**, never preserved, when the
  verified field is stamped.
- concurrency: two coroutines racing `ensure_session` ⇒ exactly one
  `start_session` (extends `test_agentforce_client.py`).

### Deploy footprint
`▲ was-blocker 4 (deploy map corrected).` `create_a2a_app` is used by the
**faces and the shim** (`adapter.py:38-55`, `deploy/shim/handler.py:41-55`) —
**not** the bridge or console. So the a2a.py/proxy/client change ships to
**faces** (`deploy/faces/deploy_faces.sh`) and the **shim**
(`deploy/shim/build_zip.sh && deploy/shim/deploy_shim.sh`). The **bridge has no
b3 runtime change and is removed from the deploy list.**

---

## Consolidated deploy footprint (work machine, full rebuilds)
- **Faces** — a2a.py verified-subject threading: `deploy/faces/deploy_faces.sh`.
- **Shim** — proxy/client reuse key + lock: `deploy/shim/build_zip.sh && deploy/shim/deploy_shim.sh`.
- **Console** — A4 terminal-failed view only: `deploy/console/deploy_console.sh`.
- **Hosted watcher/runner** — A4 ledger/retry + A5 resolver: the WS14 briefs deploy.
- **Salesforce Metadata API** — the two `A2ALab_Delivery_Key__c` external-id/unique
  fields + permset FLS, **after** the composite-key duplicate scan is clean.
- **Bridge: no b3 change** (removed).
- Hosted smoke: two verified subjects isolate at the shim; a failed brief is
  retried (writer called again) then goes terminal at the cap; a duplicate
  delivery key is a no-op (one brief, one Task).

## The two decisions the ADR must own **[ADR]**
1. The **production Salesforce schema addition**: `A2ALab_Delivery_Key__c`
   external-id/unique on **both** `A2ALab_Account_Brief__c` and `Task`, + permset
   FLS. This is the mandatory D80 uniqueness boundary (per session×account) and
   the Task-pair idempotency key — a real prod org change.
2. Confirm **`A2ALAB_BRIEF_ACCOUNT_MAP`** as the trusted name→Id map form (JSON
   env), and that a name/Id **binding mismatch rejects the write**.

## Not in scope (guarding against drift)
- F07's fan-out atomic claim / lease / reaper (`fanout_mcp/tasks.py`) and durable
  A2A task state are **b4/A6**.
- F03's **read** half (`without sharing` Apex, `bypassUser`) is an accepted risk
  (b5/D, NFR-201); b3 is the write half only.
- F08 async deadline is b4/A7.
