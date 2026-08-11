# Delivery record — the Jira board and what it is allowed to say

The lab's scope lives in `plan/07-workstreams.md` and its reasoning in
`plan/00-decisions.md`. This file is the third thing: the map between those
documents and the **A2A** Jira board, so the board can be a delivery view
without quietly becoming a second source of truth (WS15, D58).

**A note on the word, because the UI and the API disagree.** Atlassian's current
Jira UI calls the container a **space**; the REST API and JQL still call it a
`project` — `/rest/api/3/project`, `project = A2A`. So `JIRA_PROJECT_KEY` and
every `project =` clause below are correct as written and are not stale
terminology. Where this file talks about clicking something, it is a space;
where it talks about a field or a query, it is a project.

The board is generated, not maintained by hand:

```sh
uv run python scripts/jira_sync.py            # DRY RUN — prints every issue it would touch
uv run python scripts/jira_sync.py --apply    # create/update in Jira
```

It runs **one way, repo → Jira**, and it is idempotent: issues are matched by
summary and updated, so re-running after editing the plan moves the board rather
than duplicating it. Nothing reads Jira back into the repo. That direction is the
whole design — a status edited in Jira and nowhere else is a status the plan
does not know about, and the plan is what the console renders.

## What maps to what

| Jira | Source in this repo | Rule |
|---|---|---|
| **Epic** | one per `## WS<n> — <title>` heading in `plan/07-workstreams.md` | the workstream's own title becomes the epic summary |
| **Epic description** | that workstream's `Status` paragraph, verbatim | not a paraphrase — the hedges in the original are the information |
| **Story** | one per numbered work item, in either of the plan's two shapes | see below |
| **Story description** | the item's recorded state, verbatim, plus repo links | evidence travels with the claim |
| **Label `adr-D<n>`** | every `D<n>` reference in the workstream body, highest 12 | an ADR is a DECISION, not a task — link it, never convert it |
| **Label `workstream` / `workstream-item`** | the level of the issue | lets a filter separate structure from work |
| **Done** | the item's own recorded state | never a guess about what "should" be finished by now |

### The two item shapes, and the third that is deliberately not imported

The plan was written over two weeks and changed shape midway, so work items exist
in two forms and the importer reads both:

1. **A work-items table row** (WS13–WS15) — `| 3 | Grant iam:… | **done** |`.
2. **A statused numbered line** (WS1–WS12) — `3. ✅ Matrix cells recorded → …`.

Everything else in a workstream — goals, credential setup, cost notes, the
"why" — is narrative, and **none of it becomes an issue**. That is why WS4, WS5,
WS6, WS8, WS9, WS10, WS11 and WS12 import as epics with no stories even though
several of them shipped. Splitting prose into stories would manufacture a
granularity the work never had, and a board that invents structure is exactly the
failure this project spends its effort avoiding elsewhere. Their detail is in the
plan section, one click from the epic, not lost.

## Where the board is deliberately less tidy than it could be

The import ran on 2026-07-29 over work done between 2026-07-19 and 2026-07-29.
Three things about it are honest rather than neat, and should stay that way:

- **An epic closes only on the arithmetic of its stories** — it has stories and
  every one is done. Nothing tries to read the status prose for a verdict,
  because "Everything that does not need AWS is done" (WS9) and "PROVISIONED …
  exit criteria are not met yet" (WS12) both contain the word *done* and neither
  means finished. The consequence is that shipped-but-narrative workstreams sit
  **open**. That is the safe direction to be wrong in: an open epic carrying its
  real status invites someone to read it, while a wrongly-closed one buries what
  is still outstanding. Closing those is a human call made in Jira.
- **The board shows no sprints**, because there were none. A sprint is a time
  box and a workstream is a scope box; fourteen one-workstream sprints would
  describe a cadence that never happened. The board is Kanban.
- **The history is not straightened.** WS7 was folded into WS13 rather than
  completed as written; WS1 still carries an open item from 2026-07-19; the obs
  analyst's `always_allow` fix sat undeployed for days (D46). The issues say so,
  because the plan says so.

## Open items on the board, 2026-07-30

The board now holds **16 epics and 53 stories** (WS16 added its epic and seven
stories on 2026-07-30). These are genuinely open:

| Issue | Item | Kind |
|---|---|---|
| WS1.5 | Full Agentforce→bridge hosted-mode pass, plus the managed-vs-sdk latency table | lab work |
| WS14.4 | Entra: grant `Application.Read.All` + admin consent | **operator action** |
| WS14.5 | GCP: grant the harvest service account `iam.serviceAccounts.list` | **operator action** |
| WS15.1–7 | The board itself | in flight |
| WS16.1–7 | Behavioural telemetry from Claude Code logs/traces (D59) — all open, Phase 0 a hard gate | lab work |

The two operator actions are the ones a script cannot do: they need a human with
directory-admin rights in Entra and project-IAM rights in GCP. They are on the
board precisely so they stop being invisible (WS14).

WS16 imports as an epic with seven stories because the plan records it in the
work-items table shape (D58); Phase 0 is labelled a hard gate in its own state
cell, so the board shows *why* the later phases are blocked rather than leaving
them as bare open tickets.

## Re-syncing after the plan changes, and pruning what that orphans

The sync matches issues **by exact summary** — `load_index` keys the board on the
summary string and keeps only the first issue of any duplicate. That is what makes
re-runs idempotent (§the top of this file), and it is also the one sharp edge:
**an epic or story whose text is reworded in the plan no longer matches its old
Jira issue, so the sync creates a NEW one and leaves the old sitting there.** A
renamed `## WS<n>` heading orphans the epic *and* strips its children of a parent;
a reworded `N. ✅ …` line orphans that story. Nothing deletes the stale issue,
because the sync only ever creates and updates — it never removes (D58: the plan
is the source of truth, but the board is a place a human also works, so the
importer will not delete what it did not just fail to recognise).

So editing the plan for honesty — which is the normal reason to touch
`plan/07-workstreams.md` — leaves a board that is *correct plus some ghosts*. The
2026-08-10 status pass created seven such orphans (a renamed WS1 epic and its
child, plus reworded stories) on top of pre-existing ones from earlier renames.

**Reconcile, then prune — and prune only an enumerated, reviewed list.** The safe
procedure, in order:

1. **Re-sync first** so every currently-named issue exists and is up to date:
   `uv run python scripts/jira_sync.py` (read the dry-run diff), then `--apply`.
2. **Compute the expected board from the plan** using the *same parser the sync
   uses* — walk `parse_workstreams()` and collect every epic and story summary it
   would emit. This is the set the board is allowed to contain. Do not hand-list
   it; deriving it from `jira_sync`'s own parser is what proves nothing unique is
   about to be lost.
3. **Diff the live board against that set.** An issue on the board whose summary
   is *not* in the expected set is a stale orphan. Before trusting the list,
   confirm **missing = 0** (every expected summary is present) — a non-zero
   missing count means step 1 did not fully apply, and deleting now would remove
   something that has no replacement yet.
4. **Delete only the enumerated stale keys, each guarded.** Jira deletion is an
   outward, irreversible publish, so it is **not** a heuristic sweep: produce the
   specific list of `A2A-<n>` keys, get explicit approval for that exact set
   (the auto-mode classifier will — correctly — refuse an open-ended
   "delete everything that doesn't match"), then delete key by key, re-checking
   each is still orphaned immediately before removing it.

The end state is verified by re-running the step-3 diff: **stale 0, missing 0,
duplicate summaries 0**. That the board and the plan then hold the same counts is
the check that the prune removed ghosts and nothing else.

Steps 1–3 are automated read-only by the **workstream-honesty** workflow
(`.claude/workflows/workstream-honesty.js`, plan/04-runbooks.md §9): it audits
the item states against the build, re-runs the parser to compute the expected
board, and reports `stale` / `missing` / duplicate summaries. It stops there —
step 4 (the delete) stays the operator's, over the reviewed key list.

**The cheaper habit that avoids most of this:** when a workstream's wording only
needs a *tweak*, prefer editing the state cell over renaming the heading or the
item line — the summary is the join key, so keeping it stable updates the existing
issue instead of orphaning it. Renames are sometimes right (the WS1 title genuinely
changed meaning); just know each one is a prune waiting to happen.

## Credentials

`JIRA_SITE_URL`, `JIRA_EMAIL` and `JIRA_API_TOKEN` live in `.env` like every
other credential (D39), and the token is a **user API token scoped to one
person** — the board records what Ryan did, so it is authored as Ryan. An
organization-level admin key was tried first and deliberately abandoned: it
authenticates to the org admin API, not to Jira's issue API, and a key that can
administer the whole Atlassian organization is far more authority than writing
issues needs.

## Related

- `plan/07-workstreams.md` — scope, and the source this board is generated from
- `plan/00-decisions.md` — D58 (the board is generated one-way from the plan)
- `plan/09-deployment-map.md` — what is deployed, where, and why there
- `scripts/jira_sync.py` — the importer, and the reasoning for what it refuses to do
