**Verdict: REQUEST CHANGES**

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
