**Verdict: REQUEST CHANGES**

# WS31-b1 implementation cross-review

Headless Claude Opus reviewed the actual uncommitted diff on 2026-11-06 in read-only plan mode.
It edited no files and ran no handoff verb.

## Findings and response

| finding | response |
|---|---|
| Formal batch settlement and branch workflow were skipped; WS31 delivery markers were premature. | Operator settlement remains open and handoff state was not altered. Items 3–6 returned to pending until review/deploy completes. |
| Unknown generation state could ask for saved-memory hops that fresh-generation isolation skips. | Unknown state now gives neutral guidance and tells the presenter to inspect only observed hops. |
| Scene 8 claimed the seeded prompt was loaded even after a prior send emptied the draft. | Presentation handoff now explicitly reloads the selected scenario's existing default prompt before opening it. |
| Rebuilt live region might not announce scene changes; focused stage lacked an accessible name. | The live region starts empty and is updated after render; the stage is a named region. Decorative labels were removed or given a role. |
| Exit and Previous/Next could scroll out of view. | Presentation owns a viewport-height internal layout; top bar and bottom controls are sticky and the stage scrolls. |
| One internal source path remained in a served result note. | The remaining demo and fit-suite source paths were generalized; the served confidentiality sweep is empty. |
| Negative acceptance criteria were not asserted. | Tests now pin the three approved launch surfaces, one view constructor, absence of identifier values, and generation-unknown guidance. |

## Revalidation after fixes

- Focused console suite: **125 passed**.
- Ruff: **passed**.
- Inline JavaScript parse: **passed**.
- Served/deployable confidentiality sweep: **no matches**.
- `git diff --check`: **passed**.

The remaining request-change item is procedural: the operator must settle D116/implementer and
decide how to reconcile the already-written uncommitted implementation with the batch branch rule.
Browser design QA remains separately blocked as recorded in `design-qa.md`.
