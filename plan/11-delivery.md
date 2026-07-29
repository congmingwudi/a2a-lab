# Delivery record — the Jira board and what it is allowed to say

The lab's scope lives in `plan/07-workstreams.md` and its reasoning in
`plan/00-decisions.md`. This file is the third thing: the map between those
documents and the **A2A** Jira board, so the board can be a delivery view
without quietly becoming a second source of truth (WS15, D58).

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

## Open items on the board, 2026-07-29

The import produced **15 epics and 46 stories**; 40 stories closed, and these are
genuinely open:

| Issue | Item | Kind |
|---|---|---|
| WS1.5 | Full Agentforce→bridge hosted-mode pass, plus the managed-vs-sdk latency table | lab work |
| WS14.4 | Entra: grant `Application.Read.All` + admin consent | **operator action** |
| WS14.5 | GCP: grant the harvest service account `iam.serviceAccounts.list` | **operator action** |
| WS15.1–7 | This workstream — the board itself | in flight |

The two operator actions are the ones a script cannot do: they need a human with
directory-admin rights in Entra and project-IAM rights in GCP. They are on the
board precisely so they stop being invisible (WS14).

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
