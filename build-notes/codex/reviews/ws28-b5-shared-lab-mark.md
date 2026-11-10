# WS28 B5 implementation — shared A2A Exchange Mark

## Scope and settlement

D101 adopts the operator-approved **A2A Exchange Mark** as the one lab identity in Default,
Skyline, Canvas and Parchment. The implementation incorporates every Claude Opus proposal
finding: exact Default gradient, deep-navy high-contrast artwork, fixed 20-unit geometry,
CSS-owned field without SVG IDs, `themes.css?v=2`, all-selector override regression, stable
semantic part hooks, ADR/workstream records and browser evidence.

Implementation commit: `7b4a56e`.

## Baseline

Before implementation on branch `ws28-b5-shared-lab-mark`:

- `uv sync --all-extras`: resolved 172 packages; audited 166.
- `uv run pytest -q`: **781 passed, 3 skipped, 6 deselected**.
- Existing unrelated working-tree edits listed by `git status` were preserved and not folded
  into this batch.

## Changes

| Area | Change |
|---|---|
| Shared header identity | Replaced the empty CSS tile with one decorative 20×20 inline SVG: two endpoint nodes and two opposing open-chevron message paths. |
| Fixed visual identity | Retained the exact five-stop Default gradient and added fixed `#0b1f33` artwork with 1.5-unit round-capped strokes. The SVG has no definitions or IDs. |
| Theme isolation | Removed every `.brandmark` selector from `themes.css`, including Skyline's narrow resize and Canvas/Parchment artwork. Theme-specific header spacing remains. |
| Cache correctness | Bumped the shared optional-theme asset from `themes.css?v=1` to `?v=2`; shell tests now accept an explicit numeric version instead of pinning a stale literal. |
| Regression coverage | Added accessible SVG, fixed dimension, palette, stable `data-part`, stroke, base-rule and nested-selector override assertions. |
| Delivery record | Added D101, WS28 items 15–17 and the settled-plan correction; Jira created A2A-380 through A2A-382. |
| Evidence | Saved the approved concept sheet, eight actual header captures, one 390px Skyline capture and computed browser evidence under `build-notes/codex/screenshots/ws28-b5/`. |

## Validation

- Focused red/green proof: the new shared-mark test initially failed because the old empty span
  had no SVG; it passes after implementation.
- `uv run pytest -q tests/unit/test_console.py`: **97 passed**.
- `uv run pytest -q`: **782 passed, 3 skipped, 6 deselected**.
- `uv run ruff check src tests scripts`: passed.
- `git diff --check`: passed.
- `python3 -B -S build-notes/codex/codebase-review/eval-harness/run.py --case
  'regression.*'`: **2 passed**, 0 negative controls.
- `python3 -B build-notes/codex/codebase-review/readiness_probes.py`: expected historical red
  assertion in `redaction_probe`. Per the handoff skill this probe asserts the old defect and is
  intentionally not weakened; this UI-only batch does not touch redaction.
- `uv run python scripts/jira_sync.py`: dry run parsed 28 workstreams / 288 work items.
- `uv run python scripts/jira_sync.py --apply`: completed; 28 epics synced and D101 stories
  A2A-380, A2A-381 and A2A-382 exist under WS28.

## Browser evidence

Headless Chrome rendered the current local console at `http://127.0.0.1:8200/design/axs`.
For Default, Skyline, Canvas and Parchment in both light and dark mode, computed values were
identical:

- `width: 20px`, `height: 20px`, `transform: none`;
- the same five-stop computed gradient;
- two `data-part="agent"` and two `data-part="message"` groups;
- the same SVG outer-HTML SHA-256:
  `62a023917ae7ca79b32f8adbb1d02ba62d0390c2cd95113821212eccca7bc506`.

At 390×180, Skyline remained 20×20 with no transform. No browser console error was observed.
Machine-readable results are in `browser-evidence.json`; captures are named
`<design>-<mode>-header.png` plus `skyline-light-narrow-header.png`.

## Definition-of-done impact

- No console Details copy describes the old mark.
- No component, hosting topology, Dockerfile `COPY`, architecture diagram or deployment-map
  change is involved.
- `src/` and `plan/` are already included in the console image, so publication requires a full
  console rebuild. `--skip-build` would ship the old mark.

## Deployment runbook

Implementation is local and not deployed by this batch handoff. After Claude PASS, publish only
if the operator chooses to do so:

```bash
deploy/console/deploy_console.sh
```

Then smoke `/`, `/guide` and `/design/axs`, switch all four themes and both modes, confirm the
mark remains 20×20, and record the hosted smoke/security-scan evidence before running the
operator-only `handoff deployed` command.
