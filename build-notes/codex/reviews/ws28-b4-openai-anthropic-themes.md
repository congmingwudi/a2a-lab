# WS28 B4 — Canvas and Parchment implementation

## Outcome

D100 adds two original optional console themes to the existing Theme selector:

- **Canvas** translates the current OpenAI site's broad visual language into a sparse,
  monochrome A2A Lab skin with generous space, quiet outlines and rounded controls.
- **Parchment** translates the current Anthropic site's broad visual language into a shifted
  warm-paper A2A Lab skin with forest chrome and selective system-serif hierarchy.

No vendor logos, site media, copy, proprietary fonts, exact components, exact Anthropic
paper/ink values, or Anthropic coral are included. Default remains unmarked; Skyline, Canvas
and Parchment are opt-in values. Platform and protocol colors remain owned by the base console.

## Baseline

- `uv sync --all-extras`: resolved 172 packages; audited 166.
- The first `uv run pytest -q` inherited the work wrapper's local SOCKS proxy and failed before
  meaningful execution in HTTPX (`socksio` is not installed): 47 failed, 722 passed, 3 skipped,
  6 deselected, 10 errors. This is environment contamination, not a repository baseline.
- Repeated with upper- and lower-case proxy variables removed: **779 passed, 3 skipped,
  6 deselected** in 90.64s.
- New theme contracts were then added first and observed red: **4 failed, 91 deselected** because
  `themes.css`, the options and the four-value state model did not exist yet.

## Changes

| Area | Change |
|---|---|
| Theme state | A strict `default`, `skyline`, `canvas`, `parchment` allow-list now drives both pre-paint and runtime application. Invalid storage returns to Default. |
| Forced alias | `/design/axs` still renders Skyline and displays Skyline regardless of storage. Choosing any non-Skyline value stores it and returns to `/`. |
| Asset | The optional systems share one versioned, render-blocking `themes.css` request. Every concrete selector is rooted in a design marker, including selectors nested in media queries. |
| Skyline correction | Its light and dark root blocks now include both design and mode attributes, so the base light palette no longer wins the cascade. |
| Canvas | Original white/black and charcoal neutral ramps, compact system sans, large rounded surfaces, almost-flat shadows, monochrome hero and periwinkle focus/bar accent. |
| Parchment | Deliberately shifted warm neutrals, forest primary/focus accent, restrained square editorial cards, system-serif major headings and warm-charcoal dark mode. |
| Semantic identity | Neither optional stylesheet defines protocol or platform-chip tokens. Browser evidence places colored protocol/platform identity beside neutral theme chrome. |
| Accessibility | Root foreground and muted tokens meet 4.5:1 against `--bg`, `--card` and `--card2`; input/focus boundaries remain explicit; reduced-motion rules cover both themes. |
| Hero/diagrams | Both themes replace the inherited navy hero. The landing architecture SVG's inline dark-hero neutral colors are selectively overridden while protocol colors stay intact. Parchment dark Mermaid output was checked in the full Architecture view. |
| Delivery record | D100 cites D97–D99 and WS28 now has item lines 11–14 for both themes, the Skyline correction and validation/cross-review. |

## Browser evidence

The local console was restarted with `scripts/run_console.sh` and checked with installed
headless Chromium at 1440×1000 and 390×844. The ad hoc harness was removed after capture.

- `build-notes/codex/screenshots/ws28-b4/canvas-light-desktop.png`
- `build-notes/codex/screenshots/ws28-b4/canvas-dark-desktop.png`
- `build-notes/codex/screenshots/ws28-b4/parchment-light-desktop.png`
- `build-notes/codex/screenshots/ws28-b4/parchment-dark-desktop.png`
- `build-notes/codex/screenshots/ws28-b4/canvas-light-narrow.png`
- `build-notes/codex/screenshots/ws28-b4/parchment-light-narrow.png`
- `build-notes/codex/screenshots/ws28-b4/canvas-light-experiment.png`
- `build-notes/codex/screenshots/ws28-b4/parchment-dark-architecture.png`

Computed root evidence (all with no horizontal overflow):

| Design/mode | `--bg` | `--card` | `--fg` | `--muted` | `--primary` |
|---|---|---|---|---|---|
| Skyline light | `#f2f7fd` | `#ffffff` | `#172033` | `#5d6d81` | `#0b5cff` |
| Skyline dark | `#06182f` | `#0d2544` | `#e8f2fc` | `#9db1c8` | `#7ad3ff` |
| Canvas light | `#ffffff` | `#ffffff` | `#111111` | `#5f5f5f` | `#111111` |
| Canvas dark | `#0f0f0f` | `#171717` | `#f2f2f2` | `#ababab` | `#ffffff` |
| Parchment light | `#f7f4ec` | `#fffdf7` | `#211f1b` | `#625b50` | `#355b4b` |
| Parchment dark | `#211f1b` | `#292620` | `#f3eee2` | `#b9b09f` | `#9fc8b2` |

Additional browser assertions passed:

- invalid `a2a-design-v1` → no marker and selector `default`;
- stored Canvas on `/design/axs` → applied/selected Skyline while storage remains Canvas;
- choosing Parchment there → navigation to `/`, stored/applied/selected Parchment;
- signed-in Canvas experiment → `Claude Managed Agent → Agentforce`, two title marks;
- signed-in Parchment dark Architecture → heading present and 11 rendered diagrams.

## Validation

- `uv run pytest -q tests/unit/test_console.py`: **95 passed**.
- `uv run pytest -q`: **780 passed, 3 skipped, 6 deselected** in 90.54s.
- `uv run ruff check src tests scripts`: passed.
- `git diff --check`: passed.
- Codex regression harness: **2 passed**; synthetic replay only, not live evidence.
- Codex readiness probes: expected red at `redaction_probe`. That probe asserts the historical
  defect (raw secret survives redaction) and is designed to fail once the defect is fixed; this
  batch did not alter redaction behavior or weaken the probe.
- `uv run python scripts/jira_sync.py` dry run: parsed **28 workstreams / 285 items**, including
  14 WS28 stories, and made no external changes. The attempted `--apply` escalation was rejected
  because it would reconcile the entire 285-item Jira board and D100 settlement did not explicitly
  authorize that broad external mutation. Jira therefore remains unchanged pending operator
  approval; no workaround was attempted.

## Local review

The console is running at `http://localhost:8200`. Sign in, use the **Theme** selector immediately
left of the light/dark control, and compare Default, Skyline, Canvas and Parchment. Theme changes
are immediate and keep the current view. The color-mode toggle remains independent.

## Publish runbook

This batch changes `src/` and `plan/`, both baked into the console image. If the operator chooses
to publish after local acceptance and Claude cross-review, run the normal full console deployment:

```bash
deploy/console/deploy_console.sh
```

Do not use `--skip-build`; it would publish the prior shell and stylesheet. After deploy, smoke
the four selector values, light/dark independence, one experiment Details view, Architecture in
Parchment dark, `/design/axs` precedence, and `/static/themes.css?v=1`. No deployment-map or
Dockerfile change is required because the existing image already copies `src/` and `plan/`.
