**Verdict: PASS**

# WS27 batch 2 critique — customer-memory curation, fresh-start reset, closed-session fix

Reviewer: Claude Code (critique station, round 0), 2026-11-03.
Plan under review: `build-notes/codex/ws27-customer-memory-clear-plan.md`.

## Summary

Proposals 2, 3, 5 and 6 are mostly sound. The Memory Management API shapes are correct, and
`memoryId` really does come back on the live wire. Two problems block settling:

1. **The Proposal 1 diagnosis does not hold up against the hosted traces.** The 403 is not caused by
   reusing a closed session. The plan cites the wrong session and the wrong earlier trace. The same
   403 hit a newly opened session that had never been closed. And the only evidence that varies
   between the 202 and the 403 calls is the *content* of the entries body.
2. **The hosted console cannot resolve a customer profile id.** `A2ALAB_MEMORY_CUSTOMER_MAP` is
   delivered to the faces task but not to the console task, and the plan's deploy section doesn't
   cover that.

There are also several design gaps: where the generation lives, who validates it, delete
404/pagination semantics, and which auth gate to use. Each needs a decision before implementation.

## Findings

### F1 — BLOCKER: the closed-session diagnosis is wrong; the evidence points at the request content

I pulled the cited traces and the neighbouring ones from the hosted console's windowless
`/api/traces/{id}` and the 6-hour `/api/traces` list (read-only, operator JWT minted locally):

| trace | UTC | lab user | lab `session_id` | memory session | lifecycle calls | entries POST |
|---|---|---|---|---|---|---|
| `7066fb9c…` | 11-03 00:30 | ryan | **none** (response `session_id: null`) | `a2alab-c7bab25e9576` (12-hex **random** form, `src/interop/memory.py#L302`) | conv/session/participants 2xx | **202**, close 202 |
| `ff19e135…` | 19:11:59 | ana | `ui-chat-customer-memory-claude-1793733108554` | `a2alab-79cbf2ec763c5438d8d23884` (24-hex `_derived_session`, `src/interop/memory_adapter.py#L102`) | conv **201**, session **201**, participants 202 — **freshly opened** | **403 HTML** — so never closed |
| `35418b3b…` | 19:12:49 | ana | same chat | same `a2alab-79cb…` (cached, `memory_adapter.py#L187`, `#L193` `start=not known`) | none | **403 HTML** |
| `61b89fbc…` | 19:18:54 | ana | same chat | same `a2alab-79cb…` | none | **403 HTML** |

Corrections to the plan's "Fix closed-session reuse" paragraph:

- The correction turn `61b89fbc` did **not** reuse `a2alab-c7bab25e9576`, the session from
  `7066fb9c`. That was a different chat, a different lab user, ~18.8 h earlier. It also had no lab
  session, so `memory_adapter.py#L199`/`#L228` never cached anything for it. The 403'd session is
  `a2alab-79cbf2ec763c5438d8d23884`.
- That session was **never closed**. `write_turn` only closes after a successful ingest
  (`src/interop/memory.py#L531-L535`), and the first ingest in this chat (`ff19e135`) was already
  refused, on a session opened moments earlier in the same trace. Since the very first write to a
  never-closed session 403'd, a closed session can't be the trigger.
- This matches the repo's own prior measurement. Closed sessions accept entries and re-close with
  202 and re-extract (`plan/03-results.md#L1619`). The code says the same
  (`src/interop/memory.py#L405-L408`), and so does D93 (`plan/00-decisions.md#L4124`). The plan
  contradicts all three without citing new evidence.
- "Both governed context and Memory API reads succeeded, ruling out the JWT, xid, aid…" doesn't rule
  out a body-content refusal on the POST. The 403 body is a bare nginx
  `<html><center><h1>403 Forbidden</h1></center></html>`, not the API's JSON error shape. That looks
  like an edge or WAF refusal. In all three 403s the `agent_response` contains
  `https://runbooks.example.invalid/supplier-api` plus the backticked paths `/v1/orders` and
  `/v1/inventory`. The one 202 (`7066fb9c`) has no URL and no path segments. This is a correlation
  across n=4, not proof: an extracted fact for the same customer contains the same URL, so some
  earlier write carrying it got through on some path.

**Required before settle:** a headless repro that tells the hypotheses apart, run against the
`memprobe` test owner (`config/users.yaml#L46-L53` exists for exactly this), never Maya:

- (a) the exact refused body into a fresh session;
- (b) the same body with the URL and paths removed, into a fresh session;
- (c) a benign body into a session closed earlier.

If (a) is 403, (b) is 202 and (c) is 202, Proposal 1 is the wrong fix. The real issue is then
ingest-content handling, which is an operator decision because it changes what is stored. In that
case, at minimum make `MemoryCallError` label an HTML 403 as a probable edge/content refusal so the
console doesn't present it as an auth failure.

As written, hosted acceptance step 2 could pass or fail regardless of the fix, depending on what
Claude happens to write.

If the operator keeps per-turn sessions anyway, settle these points too:

- D94 defines "each chat a session" (`plan/00-decisions.md#L4174`). Per-turn sessions change that
  model and need ADR text.
- `entry_id` dedupe is keyed on the session (`memory.py#L115-L127`), so it would narrow to per-turn.
- "A retry keeps the same derived session" only holds for a transport retry that reuses the trace
  id. Every console send mints a new trace id (`src/console/static/index.html#L9907`), and the user
  re-sends above (`ff19e135` → `35418b3b`) are exactly that case.
- The derivation must also cover the first-turn/no-lab-session path (`7066fb9c`), where the session
  id is currently random.

### F2 — BLOCKER: the console task has no `A2ALAB_MEMORY_CUSTOMER_MAP`

Proposal 3 resolves the profile id in `src/console/app.py` "only through
`A2ALAB_MEMORY_CUSTOMER_MAP`". Today only `deploy/faces/deploy_faces.sh#L95` relocates that map
into a runtime secret, with a shape check at `#L98`. `deploy/console/deploy_console.sh` doesn't
list it in its secret keys (`#L73-L127`). Its plain-env scan (`#L291-L293`) only matches literal
`os.environ["…"]` reads, and the adapter reads the map through the constant `CUSTOMER_MAP_ENV`
(`src/interop/memory_adapter.py#L66`), so the scan never sees it. It is also profile-id data that
the faces treat as secret.

On a hosted console the management endpoints would therefore have no mapping for any customer.

The memory token path itself is present in the console: `SF_CLIENT_ID_MEMORY` (`#L80`), the PEM
(`#L155-L158`), and `SF_MEMORY_RUNAS_USER`/`SF_MY_DOMAIN` via the literal scan of
`memory.py#L178-L180`.

**Required:**
- Add the map to the console secret keys with the same shape check.
- Add a test mirroring `tests/unit/test_console.py#L497-L500` for `deploy_console.sh`.
- List the change in the plan's deploy section.

### F3 — MAJOR: a browser-local generation does not match a tenant-wide Start fresh

Start fresh **permanently deletes** the customer's curated memories for everyone, but the new
namespace lives in one browser's localStorage. Other operators, browsers or private windows keep
sending no generation. They keep reading the D94 owner conversation (`memory.py#L54-L64`) and its
`CONVERSATION_SUMMARY`, which is exactly what the reset was meant to stop. Clearing localStorage
silently undoes the reset in the same way.

This breaks the plan's own acceptance steps 4 and 5 (Claude-teach → OpenAI-recall pairing) for any
second operator. Either store the generation server-side per customer and have the console stamp
it, or accept the per-browser scope explicitly in the ADR and the UI copy.

The re-entrancy rule in CLAUDE.md also applies here. Reopening an archived chat restores
`selectedMemoryCustomer` from the turn toggles (`index.html#L9746`). With a per-customer
generation, a pre-reset transcript would continue under the post-reset namespace. Stamp the
generation in the turn's `toggles` (as `memory_customer` already is, `#L9966-L9968`), then either
restore it or refuse to continue a chat from an earlier generation.

### F4 — MAJOR: the generation must be validated and hashed at the face

The faces are a separate trust boundary. Any operator JWT can call them directly over REST, MCP or
A2A, without going through the console. So `MemoryAdapter` must validate `memory_generation`
itself, not trust "the console validates" (Proposal 4). It should also hash it together with the
owner, as `case_conversation` already does (`memory.py#L67-L77`), so a caller-chosen value never
becomes a tenant id verbatim. Reuse that helper's shape rather than inventing a new derivation.

The plan also doesn't mention plumbing that has to change:

- `MemoryAdapter.handle` passes no `conversation_id` to `open_and_read` (`memory_adapter.py#L193`)
  or to `write_turn` (`#L212-L221`).
- The evidence field is hard-coded to `owner_conversation(owner)` (`#L236`).

All three must carry the effective conversation, or the "conversation ID in response evidence"
will claim the old conversation.

### F5 — MAJOR: delete and list semantics need 404, pagination and caps settled

I checked the official Delete Memories reference. It confirms `DELETE /api/v1/agentic/memory`, the
required `profile_id`, `profile_type` and `category` params, a body of
`{"memories":[{"memoryId"}]}` with at least one entry, a `200 {"statusCode":"deleted",…}` response,
and **`404` "the specified memory entry doesn't exist"**. The plan's request shape is right.

Gaps:

- **404 on a batch.** The page doesn't say whether one stale id fails the whole call. A retried or
  half-failed Start fresh, or extraction replacing entries in between, would then show as a tenant
  "refusal". On a 404, re-list and report what is still there; don't fail opaquely.
- **Pagination.** The Read reference documents no paging, but the live envelope carries
  `totalCount`. Maya currently returns 21 of 21 facts and 1 of 1 preferences (trace `61b89fbc`). If
  `totalCount` exceeds the number returned, refuse to call the namespace "fresh".
- **Caps.** Management must not reuse `saved_memories`. Its `MAX_SAVED = 20` (`memory.py#L435`) is
  *already* truncating Maya's 21 facts in the prompt today. That is a side finding worth noting in
  the ADR: the 90-day and 60-day facts coexist, which is the curation problem this batch exists to
  solve.
- **Header and category names.** Keep the `agent_id` header the existing read sends
  (`memory.py#L427`). The docs' example category is `PROFILE_FACT`, but the live categories are
  `sys_PROFILE_FACT`/`sys_PROFILE_PREFERENCES` (`memory.py#L434`), and the allowlist should cite
  the live names.

### F6 — MAJOR: name the authorization gate

The console has two gates with different semantics:

- `_OPERATOR_ONLY` + `_viewer_forbidden` (`src/console/app.py#L1222-L1270`, middleware `#L1333`).
  Any path not listed there is **open to viewers**. It **admits the shared service token** (no
  persona).
- `_is_operator` (`#L1873-L1884`) requires a verified operator persona.

Recommendation:

- Use `_is_operator` in both handlers, so a permanent customer-data delete is always attributable
  to a person.
- Also add an `_OPERATOR_ONLY` entry ("manage customer memory") for defence in depth.
- Test viewer, machine and shared-token callers against list and delete.

### F7 — MINOR: the faces rebuild is not conditional

Proposal 4 changes `MemoryAdapter`, which runs inside the faces image (`src/faces/__init__.py#L148`).
A **full faces rebuild** is therefore required, not "if". The console full rebuild is correctly
called out. No new Dockerfile `COPY` is needed: `src` and `config` are already copied
(`deploy/console/Dockerfile#L34-L35`).

### F8 — MINOR: the stale-claim inventory is narrower than the plan implies

- The "no delete short of deleting the agent registration" claim exists at `config/users.yaml#L48`.
- I found no equivalent sentence in `plan/18-agentic-memory-context.md` or `config/scenarios.yaml`.
  Those files only need *new* curation copy.
- If the F1 repro confirms content filtering, the docs that actually need correcting are the plan's
  own Problem/Proposal 1 text. `memory.py#L405-L408` and D93 stay as they are.

## Verified as claimed

- Live Read Memories responses carry `memoryId`, `content`, `metadata` and `additionalAttributes`
  under `memories[]`, plus `totalCount` (trace `61b89fbc` hops 1–2). The typed-entry change is
  grounded.
- Customer scoping: `memory_customer` → `A2ALAB_MEMORY_CUSTOMER_MAP` in `owner_of`
  (`memory_adapter.py#L73-L95`). The console rejects unknown keys (`app.py#L2919-L2924`) and stamps
  the key (`#L2979-L2980`).
- Run All stays memory-off (`index.html#L9961-L9965`).
- A customer switch resets the visible chat (`index.html#L4587-L4593`).
- The shared `A2ALab_Memory_Case` identity is in `memory_adapter.py#L58-L62`.

## Reviewer evidence

- `uv run pytest -q` → **832 passed, 3 skipped, 6 deselected, 1 failed** (98.99 s). The failure is
  `tests/unit/test_diagrams.py::test_readme_diagrams_match_the_config_verbatim`: the
  `identity-and-secrets` diagram differs between `README.md` and `config/diagrams.yaml`. Both files
  carry uncommitted operator edits in this worktree (`git diff --stat`: README.md +40/−15,
  `config/diagrams.yaml` +52/−14), and this batch has no implementation yet. The failure is pre-existing and unrelated, and
  was left untouched.
- Hosted trace reads (GET only) of `7066fb9c`, `61b89fbc`, `35418b3b`, `ff19e135` and `c56b5dc1` →
  the table in F1. No tenant writes were made.
- Official docs fetched: Delete Memories and Read Memories references (F5).
- Honesty skills (`workstream-honesty`, `architecture-sweep`, `matrix-honesty-sweep`,
  `insights-audit`): **not run**. This is a plan-stage critique, so no workstream state, deployment,
  matrix cell or insight has changed and there is nothing yet for them to audit. I ran the targeted
  equivalent instead, a grep of the stale delete claim across `config/`, `plan/18`, `plan/00` and
  `index.html` (F8). They should run at cross-review once code and plan edits exist.
- The worktree's existing user changes were preserved. This file is the only one written, plus the
  handoff state.

## Round 1 — Claude Opus headless cross-review

Reviewer: Claude Code (cross-review station, round 1), 2026-11-03.
Reviewed: implementation commit `6902685`, against D107 (`plan/00-decisions.md#L4766`), WS27 items
58–63 (`plan/07-workstreams.md#L3660-L3665`), the settled plan, Codex's notes and my round-0 critique.

**Verdict for this round: REQUEST CHANGES.** The core design is right, and most of my round-0
findings are resolved. Specifically:

- the probe-backed content-refusal diagnosis;
- the D94 lifecycle is kept;
- the generation marker is validated and hashed at the face;
- the conversation id is plumbed through open/read/write/evidence;
- the console secret path;
- both auth gates;
- the complete typed listing.

Two defects are confirmed by execution, though:

1. a caller of `write_turn` broken by the new return type;
2. a Start-fresh failure path that returns a bare plain-text 500.

Beyond those, three D107 promises are stated as shipped but are not implemented:

- archived-chat protection;
- the normalization badge;
- a verified saga report on failure.

And the custom generation category, on which every customer-memory turn now depends, has never
been exercised against the tenant.

### Round-0 findings — status

| Round-0 finding | Status in `6902685` |
|---|---|
| F1 closed-session diagnosis | **Resolved.** The three-arm memprobe probe (`build-notes/codex/ws27-memory-403-probe.md`) is recorded. D94's per-chat session is retained. The HTML-403-only classification is at `src/interop/memory.py` (`MemoryCallError.is_html_403`). |
| F2 console customer map | **Resolved.** `deploy/console/deploy_console.sh` adds the map to the keys and shape-checks it. A regression assert is in `tests/unit/test_console.py`. |
| F3 browser-local generation | **Partly.** The marker is now tenant-side (good). The archived-chat half is not done; see R1-3. |
| F4 validate and hash at the face | **Resolved.** `GENERATION_RE` + `generation_conversation` → `case_conversation`. The conversation id reaches `open_and_read`, `write_turn`, the evidence field and the session cache key (`memory_adapter.py#L200-L285`). |
| F5 404, pagination, caps | **Mostly.** `MemoryListing.complete` and the 404 re-list/retry are in place. Management does not use `saved_memories`/`MAX_SAVED`. The failure reporting is still missing (R1-2). |
| F6 auth gate | **Resolved.** `_is_operator` is in both handlers, plus an `_OPERATOR_ONLY` entry. Viewer and shared-token callers are tested to get 403. Machine callers are not tested (R1-6). |
| F7 faces rebuild | **Resolved.** The notes' deploy runbook says full faces + console rebuild. |
| F8 stale claim | **Resolved.** `config/users.yaml#L48` was rewritten. No other stale "no delete" claim remains (grep below). |

### New findings

#### R1-1 — MAJOR (confirmed by execution): `scripts/supplier_memory_probe.py` breaks on the new `write_turn` return type

`write_turn` now returns `tuple[list[str], bool]` (`src/interop/memory.py`, `write_turn`). The batch
updated three callers: `src/bridge/app.py#L231`, `src/interop/memory_adapter.py#L258` and
`src/orchestration/memory.py#L273`. It missed `scripts/supplier_memory_probe.py#L61`:

```python
errors = await write_turn(...)
if errors:
    print("write failed: " + "; ".join(errors), file=sys.stderr)
```

A successful write now returns `([], False)`. That tuple is truthy, so every `--apply` run takes
the failure branch. The branch then raises `TypeError: sequence item 0: expected str instance,
list found` inside the `join`.

I reproduced both facts with a throwaway test under `/tmp/claude-b2-review/`: `write_turn` on a
successful fake returned `([], False)`, and `bool(...)` was `True`. The unit test missed it because
`tests/unit/test_supplier_memory_probe.py#L65-L66` stubs `_write_ok` to return `[]`, the old shape.

**Fix:** unpack `errors, _ = await write_turn(...)` in the script, and update the stub to return
`([], False)` so a future signature change goes red.

#### R1-2 — MAJOR (confirmed by execution): the Start-fresh saga has no failure report; D107 §3 is unmet

D107 §3 says partial results "report every remaining entry, and never say 'fresh' until
verification passes". `manage_customer_memory` (`src/console/app.py#L1970-L2050`) only meets that
when every tenant call succeeds:

- The pre-listing at `#L1987` is unwrapped. The GET route wraps the same call into a 502 with a
  detail (`#L1966`), but this one is not wrapped.
- A non-404 `MemoryCallError` from any delete (`#L2008`, `#L2013`, `#L2022`) propagates out of the
  `try/finally`.
- With a fake whose delete raises HTTP 500, my probe got **`500 text/plain` `Internal Server
  Error`**, with no trace id, no step results and no remaining entries. By that point the new
  marker is already written, which is the "partial" state D107 promises to explain.
- The UI then does `await r.json()` on that body (`index.html#L4687`) and shows a JSON parse error
  instead of the tenant error and trace id that plan §5 requires.

Also, `result["ok"]` for `fresh` (`#L2047`) checks that no semantic entries remain and that a marker
was created. It does not check `result["generation"] == marker` or `not
result["generation_invalid"]`. D107 says success requires the new marker to be the selected valid
generation.

**Fix:**
- Catch `MemoryCallError` per step.
- Always return a JSON body with `trace_id`, per-step outcomes, the remaining entries and
  `ok: false`.
- Compare the selected generation to the marker just created.
- Add tests for a failing delete (fresh and selective) and for the non-matching-generation case.

#### R1-3 — MAJOR: archived pre-reset chats can still continue; D107's Consequences overclaims

D107 Consequences (`plan/00-decisions.md#L4818-L4819`) says, in the present tense: "Archived pre-reset
chats cannot silently continue under the current generation." Item 61 and plan §4 require the
generation to be stamped in turn toggles, with a stale chat made read-only or forced to fork.

The implementation does neither:

- No generation is stamped in the turn's toggles.
- A successful Start fresh deletes archives **in this browser only** (`index.html#L4691-L4696`).
- Any other operator's browser, or this browser after a reload from server-side history, can reopen
  a pre-reset chat and keep sending.

Since the face scopes by the tenant marker, that turn lands in the new conversation. The inner
platform's own session (for example the Claude managed-agent session for that lab `session_id`)
still holds the pre-reset teaching. So the old content flows into the new namespace through the
agent's replies, which is exactly the leak §4 was meant to stop.

There is also a side defect: the archive loop matches every key containing
`customer-memory-claude`/`customer-memory-openai`. It therefore also deletes the **other**
customer's (e.g. Jordan's) archived chats when Maya is reset.

**Fix:** either implement the stamp plus a read-only/fork guard, with a test, or amend the D107
text and item 61 to the per-browser behaviour actually shipped. Scope the archive purge to the
reset customer either way.

#### R1-4 — MAJOR: normalization is traced but never surfaced in the console

The face sets `metadata.memory.normalized` (`memory_adapter.py#L295`) and the bridge sets
`payload["normalized"]`. However, `index.html` contains no read of `normalized`: `grep -n
normalized src/console/static/index.html` hits only the unrelated design-theme code at
`#L10962-L10971`.

D107 §5 says "both attempts and the normalization outcome are traced **and surfaced**". Item 58 says
"trace and badge the adaptation", and plan acceptance step 2 says "the console labels
normalization". As shipped, a normalized write shows the same green memory badge as a
byte-for-byte write. That is the "successful memory badge hides the wire event" outcome D107
Consequences explicitly rules out.

**Fix:** render a badge/note in the memory evidence strip when `memory.normalized` is true, and add
an `index.html` text test.

#### R1-5 — MAJOR (deploy-gating): the custom generation category is unproven, and failure is fail-closed for every customer

`MemoryAdapter.handle` now issues `list_memories(category="A2ALAB_EXPERIMENT_GENERATION")` on
**every** customer-memory turn. It suppresses memory entirely on any read error or timeout
(`memory_adapter.py#L203-L226`), including for customers that have never been reset. The console's
GET snapshot also lists that category, so any refusal there 502s the whole manager.

No evidence shows the tenant accepts a custom category on Read/Create/Delete Memories:

- The 403 probe exercised only the conversation `entries` POST.
- The official Create Memories reference (fetched this round) documents `category` only as
  "Partition key … such as `PROFILE_FACT`", with no statement that it is free-form.
- The live categories this lab has ever read are the `sys_`-prefixed extraction outputs.
- If the tenant answers 400/404/500 for an unknown or empty custom category, this deploy turns
  memory **off** for Maya and Jordan on every turn of the live experiment. That would be a
  regression of the shipped D105 experiment, not just a broken reset.

**Required before the faces deploy:** a headless memprobe-only probe recorded under
`build-notes/codex/`:

- (a) GET the empty custom category;
- (b) POST a marker;
- (c) GET it back with `memoryId`/`totalCount`;
- (d) DELETE it;
- (e) check whether `GetContext`/`saved_memories` surfaces the marker text into the prompt.

If (a) is a non-2xx, either treat an "empty category" refusal as "absent" (fail-open to D94
semantics), or pre-seed the category by design. Either way, say so in D107.

#### R1-6 — MINOR: tests promised by plan §§1–5 are missing

Covered: HTML-403 retry, normalization text, typed listing, create/delete request shape, generation
→ new conversation/session, malformed marker suppression, and the happy-path list/delete/fresh
flow for operator vs viewer/shared token. Not covered:

- no retry for a JSON 403;
- a second write to a closed session;
- the delete 404 → re-list/retry path (`app.py#L2013-L2026`);
- the 409 on an incomplete listing;
- `requested <= allowed` rejecting a non-governed id;
- Maya/Jordan isolation;
- requests without `memory_customer` making no generation read;
- a machine-persona caller;
- Run All not mutating generations.

The stale `_write_ok` stub (R1-1) shows why the signature-change paths need real-shape fakes.

#### R1-7 — MINOR: confirmation and copy fall short of plan §5

- The delete confirmation (`index.html#L4705`) doesn't name the customer.
- The Start-fresh confirmation (`#L4710`) doesn't state the count.
- The panel/Details copy omits two of the four distinctions plan §5 lists: asynchronous extraction
  (a just-finished teach may not be listed yet) and the 20-entry prompt cap vs. the complete
  management listing. Both matter when an operator opens the manager right after a teaching turn.

#### R1-8 — MINOR: edge cases in listing and normalization

- `list_memories` drops blank-content entries but keeps the tenant's `totalCount`
  (`memory.py`, `list_memories`). A single blank entry therefore marks the listing incomplete
  forever and permanently disables Start fresh. The existing test even asserts `complete is False`
  for that case. Count the dropped blanks, or compare against the raw `memories` length.
- `API_PATH_RE`'s `(?<!\w)` lookbehind does not normalize `/api/v1/orders`: the `/v1` follows
  `i`. The fix is a heuristic tuned to one synthetic pattern. That is fine as a narrow retry, but
  record it as a limitation in D107 rather than implying general coverage.

### Verified as claimed

- HTML-403 classification requires status 403 and the nginx HTML shape. JSON 403s re-raise
  (`ingest`). The retry derives new entry ids from the normalized content, so idempotency holds.
  Normalization applies only to the tenant copy; the inner agent gets `req.message` unchanged.
- The generation marker is server-generated (`new_generation_marker`) and fullmatched against
  `GENERATION_RE` at the face. It is hashed via `case_conversation`, so no caller text becomes a
  tenant id. Selecting the newest valid marker is deterministic.
- `_derived_session` and the session-cache key both include the effective conversation, so a reset
  can't resurrect a cached pre-reset session.
- Profile ids never leave the server: `_safe_memory_snapshot` returns only the customer key,
  memory ids, categories and content. Delete ids are intersected with a fresh governed listing.
  Rendered content goes through `esc()`.
- `deploy_console.sh` passes `bash -n` (per Codex). The new key and shape check match the faces
  script.

### Reviewer evidence

- `uv run pytest -q` (proxy variables unset) → **841 passed, 3 skipped, 6 deselected, 1 failed**
  (99.96 s). The failure is the same pre-existing
  `tests/unit/test_diagrams.py::test_readme_diagrams_match_the_config_verbatim`, caused by the
  operator's uncommitted `README.md`/`config/diagrams.yaml` edits. It is identical to round 0 and to
  Codex's baseline, and was left untouched.
- `uv run ruff check` over every changed Python file plus `scripts/supplier_memory_probe.py` → all
  checks passed. Lint cannot see R1-1, because the bug is a type change, not syntax.
- Execution probes (throwaway, `/tmp/claude-b2-review/test_b2_probe.py`, outside the repo) → 2
  passed:
  - (1) a failing delete during `fresh` returns `500 text/plain "Internal Server Error"` (R1-2);
  - (2) `write_turn` success returns a truthy `([], False)` (R1-1).
- Workstream-honesty equivalent: `uv run python scripts/jira_sync.py` (DRY RUN; nothing created)
  parses items 58–63 as six **open** stories, consistent with their ⏳ state. Those states are
  honest today. Item 61 (archived chats) and item 58 (badge) must not flip to ✅ until R1-3/R1-4 are
  closed. The full `workstream-honesty` workflow was not run; the auto-memory records that it stalls
  on the full `parse_plan()` output, so the dry run was done by hand as that note advises.
- Architecture-sweep equivalent: I grepped the component and claim names (`A2ALAB_MEMORY_CUSTOMER_MAP`,
  `Memory Management`, `/api/memory/customer`, `Start fresh`, the old "no delete" wording) across
  `plan/`, `config/`, `index.html`, `README.md`, `docs/` and the guide corpus. Results:
  - No component moved host or identity.
  - The plan/09 L6 console row now names the map and the management seam, and its edge already
    reads `AWS → Salesforce Data 360`.
  - No diagram draws the console-side memory seam differently.
  - No stale "no delete" claim remains.
  - The only architecture-copy problems are the D107 overclaims in R1-3/R1-4.
  - The full multi-agent `architecture-sweep` workflow was not run; the targeted grep covers the
    batch's surface.
- `matrix-honesty-sweep` and `insights-audit`: not applicable. The commit changes neither
  `plan/02-matrix.md`, `config/targets.yaml` nor `config/insights.yaml`.
- Official docs fetched: Memory Management guide (no category or Create detail) and the Create
  Memories reference (`POST /api/v1/agentic/memory`; `category` documented only by the
  `PROFILE_FACT` example), for R1-5.

### Limitations

- No tenant calls this round, read or write. The review station does not deploy, and R1-5's probe
  belongs to the implementer, against memprobe. Hosted behaviour (edge filter, custom category,
  extraction latency) is therefore unverified by me.
- The UI was reviewed from source and text tests. I did not drive it in a browser.
- I did not modify implementation files, stage anything, or touch the operator's unrelated dirty
  worktree changes. This section and the handoff state are the only writes.

## Round 2 — Claude Code cross-review

Reviewer: Claude Code (cross-review station, round 2), 2026-11-03. Reviewed at `3f7ff0c`, which
contains Codex's round-2 answer (`8df42f6`) and the D107 follow-ups `1253cd1`, `cceb398`, `c4c4fe8`.
Codex deployed under an operator override before this round, so this verdict reviews code that is
already live. It does not gate a deploy.

**Verdict for this round: PASS.** Every R1 finding is closed in code and covered by a test. D107
now describes the shipped behaviour, including the profile-quarantine redesign (§7), with live
trace evidence. One new MINOR finding is below. It does not block.

### R1 findings — status

| Finding | Status | My evidence |
|---|---|---|
| R1-1 probe `write_turn` shape | **Closed** | `scripts/supplier_memory_probe.py#L61` unpacks `errors, _normalized`. The `_write_ok` stub (`tests/unit/test_supplier_memory_probe.py#L65-L66`) returns `[], False`, so a future signature change goes red. |
| R1-2 saga failure report / marker check | **Closed** | `manage_customer_memory` records a JSON step result for every mutation (`src/console/app.py#L2191-L2228`). `ok` requires `generation == marker` and `not generation_invalid` (`#L2244-L2250`). Tests: `test_customer_memory_reset_reports_partial_delete_and_selected_generation` and `…_refuses_incomplete_listing`. |
| R1-3 archived chats / cross-customer purge | **Closed** (see R2-1) | The chat stamps `memory_generation` into body and toggles (`index.html#L10261-L10263`, `#L10320`). The face refuses a mismatch before `inner.handle` (`memory_adapter.py#L221-L232`). Test: `test_stale_customer_chat_is_refused_before_the_inner_agent_runs`. |
| R1-4 normalization badge | **Closed** | `memory: normalized retry` chip at `index.html#L10117`. |
| R1-5 custom category unproven | **Closed by redesign** | The custom category and `PROFILE_FACT` both returned 403 live. The marker moved to the supported `sys_PROFILE_FACT` and is filtered from rendering and saved prompts (tests `test_generation_control_fact_*`). Probe: `build-notes/codex/ws27-generation-category-probe.md`. |
| R1-6 missing edge tests | **Closed** | JSON-403 no-retry, second closed-session write, incomplete listing, machine callers, generation forwarding/refusal and profile quarantine are all present (`tests/unit/test_memory.py`, `test_memory_adapter.py`, `test_console.py`). |
| R1-7 confirmation/copy | **Closed** | Per Codex. Covered by `test_customer_memory_panel_has_selection_reset_and_retention_copy`. |
| R1-8 blank rows, `/api/v1` | **Closed** | `API_PATH_RE = (?:/api)?/v(\d+)/…` (`memory.py#L181`). Typed listings keep blank rows for deletion. |

### Design change I checked: quarantine instead of "zero rows"

`c4c4fe8` relaxes reset success: re-materialized profile rows no longer fail Start fresh. The hard
boundary moves to read time. With a valid generation, both faces drop `sourceType: Profile` and
skip the profile merge (`include_saved_memories` / `include_profile_context` = `not generation`).
I agree with this design. Trace `19b1eca3…` shows deletion cannot be made to stick, so a
read-side boundary is the only one the lab controls. Its cost is stated honestly in D107 §7 and
Consequences: after a reset, recall comes only from the generation's conversation summary.
Profile facts extracted from new teaching are also quarantined, so post-reset recall is
conversation-scoped by design, not a regression.

### New finding

#### R2-1 — MINOR: hosted, the stale-chat refusal arrives untyped, so the fork prompt depends on the client-side check

The console maps a refusal to `error_code: "stale_memory_generation"` only when the exception type
is `StaleMemoryGeneration` in-process (`src/console/app.py#L3494-L3495`). Hosted, the REST face
returns it as HTTP 500, the remote client wraps it, and the type is lost. Codex's smoke trace
`4b5bc448…` shows exactly this. `index.html#L10351` therefore never fires hosted.

The guard is still fail-closed: no model call and no memory mutation happen. The client-side
comparison (`index.html#L10035-L10036`) marks the chat stale whenever the manager snapshot has
loaded. The only gap is a stale chat opened before that snapshot. It shows a generic error instead
of the "start a new chat" fork until reload. **Suggested fix:** have the face return a typed body
(for example a 409 with `error_code`) and let the REST client keep the code. Alternatively, match
on the `StaleMemoryGeneration` text. Not required for this batch.

**Fixed 2026-11-04 (Claude), both suggestions combined.** `StaleMemoryGeneration` declares
`error_code = "stale_memory_generation"` and `http_status = 409` (`src/interop/memory.py`). The
REST face answers a typed exception with its own status and puts `error_code` in the body; the
trace hop records the same body (`src/interop/servers/rest.py`). `RestClient` raises the new
`RemoteAgentError(RuntimeError)`, which carries `status` and `error_code`. The operator message
is unchanged (`src/interop/clients/base.py`, `clients/rest.py`). `/api/run` reads the code through
`_run_error_code`: first the exception attribute, then the class name in the message as a fallback
for MCP/A2A hops, which carry only text (`src/console/app.py`). Tests:
`tests/unit/test_typed_remote_errors.py` (409 + code on the wire, untyped stays 500, client keeps
the code) and `test_run_keeps_a_typed_refusal_across_the_hosted_hop`.

Deployed 2026-11-04 (faces task def 32, console 153, both rollouts COMPLETED; commit `76c1b6f`).
Hosted smoke: `/api/run` on `customer-memory-claude` / `maya` with a bogus `memory_generation`.
The console returned `ok: false, error_code: "stale_memory_generation"`, carried by
`RemoteAgentError: HTTP 409 from …/claude-rest/invoke`, trace `a54e569a…`. Codex's `4b5bc448…`
had shown an untyped 500. No model call and no memory write happened.

### Reviewer evidence

- `uv run pytest -q` (proxy and `ANTHROPIC_BASE_URL` unset) → **870 passed, 3 skipped, 6
  deselected, 2 failed** at `65bf6bf`. Both failures came from the operator's WS30/sweep commit,
  not this batch:
  - the README `identity-and-secrets` diagram lagged `config/diagrams.yaml`;
  - `.env` lacked the two new `TABLEAU_CONSUMPTION_*` keys.

  I fixed both (`3f7ff0c`, plus a local `.env` edit) and re-ran them: 14 passed.
- `uv run ruff check` on `memory.py`, `memory_adapter.py`, `console/app.py` and the probe script →
  clean.
- Code reading of every R1 site cited above, at HEAD.
- No tenant calls this round. The live evidence is Codex's: traces `e3c30627…`, `13202e8b…` and
  `91aa0a88…`. My round-1 `/tmp` execution probes did not survive the machine restart and were not
  re-run. The R1-2 and R1-1 regressions are now permanent repo tests instead.
