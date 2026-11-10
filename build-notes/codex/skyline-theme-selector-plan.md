# Skyline theme selector and experiment-heading identity — proposal

**Proposed batch:** `ws28-b2-skyline-selector`
**Proposer:** Codex
**Status:** station 1 proposal; no implementation before operator settlement (D79)

This proposal requires operator decision **D98**, superseding only D97's clauses that kept
`/` and `/guide` unchanged and deferred promotion of the design study. D97's local-only
deployment record remains historical fact. B2 is based on the reviewed B1 branch because the
Skyline stylesheet does not exist on `main` yet; B1 must be merged before B2 can be based on
`main`.

## Goal

Promote the D97 local design study into a named **Skyline** theme selectable from the
normal console header, while keeping the currently deployed design as the default. Make
experiment identity consistent by rendering the existing platform icons in main-canvas
experiment headings and removing the one-off `aws` hosting chip.

## Scope

### 1. Add an independent design-theme selector

- **Files:** `src/console/static/index.html`, rename `src/console/static/axs-theme.css` to
  `src/console/static/skyline-theme.css`, and
  `src/console/app.py`.
- **Change:** Add a compact `Theme` selector immediately left of the existing light/dark
  control with `Default` and `Skyline` choices. Rename the existing light/dark group's
  accessible label from `Theme` to `Color mode`, so the adjacent controls have distinct names.
  Persist the design choice under its own
  versioned local-storage key; light/dark remains a separate persisted axis and works in
  either design.
- **Default:** An absent/invalid saved design selects `Default`, preserving the deployed
  console exactly. Skyline applies `data-design="skyline"` before first paint to avoid a
  flash and loads the existing theme stylesheet on every SPA shell response. Both the
  pre-paint and runtime paths explicitly allow-list only `skyline`; any other stored value
  removes the marker. Use a version query on the stylesheet href to prevent stale cached CSS
  across console rebuilds.
- **Compatibility:** Keep `/design/axs` as a backward-compatible preview alias that renders
  Skyline without changing the normal route's default. Rename CSS selectors and internal
  design tokens from `axs` to `skyline`; do not expose AXS as a product/theme name. The
  server-forced alias marker wins for that document and never writes local storage. If a user
  selects `Default` while on the alias, navigate to `/`; selecting Skyline or loading the alias
  does not overwrite the saved preference. Retitle the auth-exemption comment as the legacy
  Skyline preview alias while keeping the route exempt when `A2ALAB_TOKEN` is configured.
- **Responsive/accessibility:** Label the selector, retain keyboard/native-select behavior,
  fit the compact header at narrow widths, and preserve reduced-motion handling.
- **Tests:** Replace D97's superseded stylesheet-isolation assertion under D98, while retaining
  the invariant that normal `/` and `/guide` include no server-forced design marker. Assert
  normal shells include the versioned Skyline stylesheet;
  the legacy preview alias forces `data-design="skyline"`; the header exposes exactly the
  two named choices; the controls have distinct accessible names; the pre-paint script
  defaults invalid/missing values to Default; alias precedence and Default navigation work as
  specified; and theme CSS is fully scoped to Skyline.
- **Deploy:** This changes `src/` and requires a full console rebuild if the operator later
  publishes it. No deployment-map change: the same console component and route are used.

### 2. Normalize experiment-heading identity

- **File:** `src/console/static/index.html`.
- **Change:** Render scenario titles in the main canvas through the existing trusted
  `withLogos()` helper, matching Control Panel and landing experiment titles. Protocol-cell
  names stay escaped as today unless their title contract explicitly carries platform names.
- **Change:** Delete the main-heading conditional that emits the standalone `aws` badge for
  `agentcore` scenarios. Hosting remains visible in the configured title text such as `(AWS)`.
  The badge is inconsistent because OpenAI-on-AWS scenarios lack the `agentcore` tag, and its
  Claude-specific tooltip is factually wrong on Strands scenarios that do carry that tag.
- **Change:** Add `LangGraph` to `LOGO_RE`/`LOGO_KEY`, using the already-defined LangGraph mark,
  so every scenario heading—including both Heroku scenarios—shows both platform icons on all
  existing `withLogos()` surfaces.
- **Tests:** Static regression coverage asserts the main scenario heading uses
  `withLogos(title)`, LangGraph participates in title logo replacement, and the one-off
  `badge aws` heading branch is gone. Browser evidence covers at least an AWS-hosted
  Claude → Agentforce scenario and a LangGraph pair.

### 3. Evidence and handoff

- **Files:** focused console tests, new WS28 `⏳` item lines (without rewording existing Jira-keyed
  item text), implementation notes, and desktop/mobile
  screenshots under `build-notes/codex/screenshots/`.
- **Validation:** Required baseline, focused tests, full pytest, Ruff, diff checks, and visual
  checks for Default/Skyline × light/dark plus the corrected experiment heading.
- **Safety:** Preserve all unrelated dirty edits in `index.html`; change only the header,
  theme bootstrap/runtime functions, experiment heading render, and directly related styles.

## Non-goals

- No change to protocol/platform semantic colors, auth, experiments, APIs, or information
  architecture.
- No hosted deployment in this batch without a separate operator deploy step.
- No removal of the existing `/design/axs` bookmark in this compatibility pass.
