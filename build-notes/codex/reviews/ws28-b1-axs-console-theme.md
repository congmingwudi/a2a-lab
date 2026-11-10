# WS28 B1 implementation — AXS-inspired local console design

## Baseline

- `uv sync --all-extras`: passed, 166 packages audited.
- Initial sandboxed `uv run pytest -q`: reproduced environment failures from the critique
  (proxy-backed HTTP clients and loopback socket restrictions), not batch failures.
- With proxy variables removed, the pre-edit console suite reproduced the critique's one
  existing harvest-sweep failure.

## Changes

| Area | Change |
|---|---|
| Shared shell | `src/console/app.py` now renders the existing SPA through one helper and serves an opt-in `/design/axs` response with a pre-paint marker and one extra stylesheet. `/` and `/guide` use the same helper without transformation. |
| Auth boundary | `/design/axs` joins `/` and `/guide` in the exact public-shell exemptions identified by the critique. No API or live-data prefix was widened. |
| Visual system | `src/console/static/axs-theme.css` adds the isolated light/dark AXS-inspired treatment: pale-blue field, white surfaces, radial blue hero, refined control panel, lifted cards, responsive phone layout, visible focus, and reduced-motion handling. Existing protocol/platform color tokens are not redefined. |
| Regression tests | `tests/unit/test_console.py` covers route injection/isolation, no-cache behavior, the configured-token auth boundary, semantic-token preservation, reduced motion, and the signed-out grid state found during screenshot review. |
| Decision record | D97 and WS28 record the operator's local-only settlement. The existing dirty `src/console/static/index.html` was not edited by this batch. |

## Visual validation

- Desktop, 1440×1000: `build-notes/codex/screenshots/ws28-axs-desktop.png`.
- Phone, 390×844: `build-notes/codex/screenshots/ws28-axs-mobile.png`.
- Signed-in Control Panel, 1440×1000:
  `build-notes/codex/screenshots/ws28-axs-control-panel.png`.
- Signed-in experiment canvas, 1440×1000:
  `build-notes/codex/screenshots/ws28-axs-experiment.png`.
- Signed-in Lab Guide, 1440×1000: `build-notes/codex/screenshots/ws28-axs-guide.png`.
- Responsive Control Panel overlay and Lab Guide, 390×844:
  `build-notes/codex/screenshots/ws28-axs-mobile-control-panel.png` and
  `build-notes/codex/screenshots/ws28-axs-mobile-guide.png`.
- First render exposed a specificity bug: the theme's two-column grid overrode
  `main.side-closed`, squeezing the signed-out hero into 336px. Added the explicit scoped
  state rule, added a static regression assertion, and recaptured both images.
- Verified `/design/axs` and `/static/axs-theme.css` return 200 while unsigned live-data
  requests remain 401 with the configured console token.

## Validation

- Focused theme/route tests: `3 passed, 89 deselected`.
- Full suite outside the socket-restricted sandbox, with proxy variables removed:
  `777 passed, 3 skipped, 6 deselected`.
- `.venv/bin/ruff check src tests scripts`: passed.
- `git diff --check`: passed.

## Local run

```sh
scripts/run_console.sh
```

Open `http://localhost:8200/design/axs`. This batch is local-only: no deployment command
was run. If a later ADR promotes it, use a full console rebuild because both the route and
stylesheet live under `src/`, which the console image bakes in.

## Round 1 response

| finding | fixed / disputed (evidence) / deferred to D<n> | what changed |
|---|---|---|
| Text-level width caps conflict with the console prose rule | fixed | Removed `max-width` from `.hero-body`, `.hero-ethos`, and `.landing .lede`; the page wrappers remain the only reading-width boundary. |
| Interactive-state visual evidence was missing | fixed | Added signed-in desktop captures for the Control Panel, experiment Run canvas, and Lab Guide, plus 390×844 Control Panel overlay and full-width Guide captures. Login used the configured local persona endpoint; no credential or token is stored in the evidence. |
| Implementation commit-range whitespace check failed | fixed | Removed the two Markdown hard-break spaces and the blank line at EOF. Verified the complete batch range with `git diff 2b3a1b6^ --check`, not only the unstaged worktree. |
