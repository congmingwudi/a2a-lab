# AXS-inspired local console redesign — proposal

**Proposed batch:** `ws28-b1-axs-console-theme`  
**Proposer:** Codex  
**Status:** station 1 proposal; no implementation before operator settlement (D79)

## Goal and evidence

Create an opt-in, local-only visual treatment for the A2A Lab console inspired by the
smoothness and visual hierarchy of the AXS site, without replacing the released console
or disturbing the uncommitted work already present in `src/console/static/index.html`.

The reference site was inspected directly on 2026-10-29. The reusable cues are its
collapsible white icon rail, navy-to-electric-blue radial hero, pale-blue page field,
rounded white cards, restrained lift shadows, 4px spacing scale, short 120–200ms
transitions, scroll/entry motion, responsive overlay navigation, visible focus states,
and `prefers-reduced-motion` opt-out. This proposal borrows those interaction and visual
principles, not the AXS brand, copy, proprietary fonts, or source assets.

## Scope

### 1. Add an opt-in local theme layer

- **File:** `src/console/static/axs-theme.css` (new).
- **Fix:** Define an A2A-owned light visual system over the current semantic classes:
  pale blue canvas, white elevated surfaces, navy/blue hierarchy, more generous radii,
  compact shadows, polished hover/focus states, an upgraded landing hero, and responsive
  control-panel treatment. Keep protocol and platform colors semantically unchanged.
- **Constraint:** Scope every override below `html[data-design="axs"]` so the existing
  light/dark themes and hosted console remain byte-for-byte in behavior when the design
  experiment is not selected.
- **Accessibility:** Preserve keyboard navigation, add no motion required for meaning,
  and disable nonessential animation under `prefers-reduced-motion: reduce`.
- **Test:** A static contract test asserts the theme is scoped, contains the reduced-motion
  rule, and does not redefine protocol/platform meaning tokens.
- **Deploy:** None. This is a local experiment only unless a later decision promotes it.

### 2. Serve the experiment without duplicating the SPA

- **File:** `src/console/app.py`.
- **Fix:** Add `GET /design/axs`, serving the same `index.html` shell after injecting one
  stylesheet link and a tiny pre-paint `data-design="axs"` marker. Do not copy the
  10,835-line SPA, fork API behavior, or edit the currently dirty `index.html`.
- **Behavior:** `/` and `/guide` remain unchanged. `/design/axs` uses the same origin,
  API routes, sign-in state, local storage, and no-cache shell policy. The experiment is
  explicit and bookmarkable; it does not persist into the normal route.
- **Auth correction from the independent critique:** Add `/design/axs` to
  `TokenAuthMiddleware.exempt_paths`, beside `/` and `/guide`. Initial browser navigation
  cannot attach the persona JWT held in local storage; the data-free shell must load first
  so its fetch wrapper can authenticate API requests. This exemption does not make any API
  or live data public.
- **Test:** FastAPI client coverage proves `/design/axs` includes the marker and theme
  stylesheet while `/` and `/guide` do not, and that shell cache headers remain correct.
  With `A2ALAB_TOKEN` set, an unauthenticated request to `/design/axs` must return 200 and
  a non-exempt route must still return 401.
- **Deploy:** None for the requested local trial. If later promoted, the existing
  `deploy/console/Dockerfile` already copies all of `src/`, but promotion requires a full
  console image rebuild because the route and stylesheet are baked into that image.

### 3. Add visual regression evidence and a local run note

- **Files:** `tests/unit/test_console.py`,
  `build-notes/codex/reviews/ws28-b1-axs-console-theme.md` (implementation evidence),
  and the settled WS28 item lines.
- **Fix:** Add a focused route/theme contract test and capture desktop plus narrow-viewport
  screenshots after running `scripts/run_console.sh`, covering landing, control panel,
  an experiment canvas, and the Lab Guide drawer. Store only the chosen evidence images,
  not Playwright state or browser profiles.
- **Test:** Baseline first (`uv sync --all-extras`, `uv run pytest`), then focused console
  tests, full `uv run pytest`, and `uv run ruff check src tests scripts`. Manually verify
  1440px and 390px layouts, keyboard focus, dark/light isolation, and reduced motion.
- **Deploy:** None. Hand off the local URL `http://localhost:8200/design/axs`. A later
  promotion decision would update console copy/ADR as applicable and use a full rebuild;
  no deployment-map change is needed for a visual-only route on the same component.

## Non-goals

- No hosted deployment, AXS naming, AXS logos, Salesforce Sans redistribution, copied
  illustrations, or copied content.
- No API, trace, role, experiment, or navigation-information-architecture changes. The
  sole auth edit is the reviewed public-shell exemption above; all data routes remain gated.
- No rewrite into a frontend framework and no duplicate console HTML.
- No edits to the current uncommitted `index.html` work unless the operator explicitly
  broadens scope after the critique.

## Settlement questions

1. Accept the experiment as local-only under proposed WS28, or promote it immediately to
   a selectable released-console theme (larger scope and deploy obligation)?
2. Accept the route-layer approach, or require the redesign to replace the current shell?
3. Confirm that visual evidence may be checked in under `build-notes/codex/`.
