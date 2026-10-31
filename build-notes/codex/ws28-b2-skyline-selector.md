# WS28 B2 — Skyline theme selector and experiment-heading identity

## Scope and decision

- Batch: `ws28-b2-skyline-selector`
- Decision: D98
- Implementer: Codex
- Base: reviewed B1 branch `ws28-b1-axs-console-theme-local`
- Operator settlement: Default remains the released design; Skyline is an optional, independently
  persisted design theme. `/design/axs` remains a compatibility alias.

## Baseline

`uv sync --all-extras` completed successfully.

The first `uv run pytest` inherited `ALL_PROXY=socks5h://127.0.0.1:…` while the optional
`socksio` dependency was absent: 47 failed, 720 passed, 3 skipped, 6 deselected, 10 errors.
This was an invalid environmental run; failures were HTTP-client construction and requests
attempting the dead proxy. With proxy variables removed, the required baseline was clean:

```text
777 passed, 3 skipped, 6 deselected, 105 warnings in 91.62s
```

## Changes

| Area | Change |
|---|---|
| Theme bootstrap | Added versioned `a2a-design-v1` preference, explicit Skyline allow-list and pre-paint marker application independent of `a2a-theme-v2`. |
| Header | Added native Default/Skyline selector left of the light/dark control; renamed the latter's accessible group to `Color mode`; retained responsive wrapping. |
| Skyline layer | Renamed `axs-theme.css` to `skyline-theme.css`, renamed scoped selectors/tokens, added selector styling, and loaded it with `?v=1` on all SPA shells. Default has no design marker, so the scoped CSS is inert. |
| Compatibility | `/design/axs` now emits document-local `data-design="skyline" data-design-forced="skyline"`; it never mutates saved preference, and selecting Default navigates to `/`. The data-free shell remains auth-exempt. |
| Experiment identity | Main scenario headings now use the escaped `withLogos()` helper. LangGraph participates in title matching, and the inconsistent Claude-specific `aws` heading badge was removed. `(AWS)` remains in configured titles. |
| Delivery record | Added D98 and WS28 items 4–6 without changing existing Jira-keyed item text. |

## Test-first evidence

Focused regressions were added before implementation and initially failed in all three intended
areas. After implementation:

- `uv run pytest tests/unit/test_console.py -k 'skyline_theme or theme_controls'` — 3 passed.
- Proxy-clean `uv run pytest tests/unit/test_console.py` — 93 passed.
- Proxy-clean full `uv run pytest` — **778 passed, 3 skipped, 6 deselected**, 105 dependency
  deprecation warnings.
- `.venv/bin/ruff check src tests scripts` — passed after the final JavaScript scoping fix.
- `git diff --check` — passed.

The first Playwright run exposed a real integration defect that static checks did not: the
pre-paint script's global `var forcedDesign` collided with the runtime `const forcedDesign`, so
the main bundle never initialized. The pre-paint logic is now scoped in an IIFE. The rerun passed:

```text
3 passed (8.0s)
```

The browser checks covered Default/Skyline × light/dark persistence, two marks and no `aws`
badge in Claude-on-AWS and LangGraph headings, selector visibility at 390×844, and legacy-alias
precedence without preference mutation.

Screenshots:

- `screenshots/ws28-b2/default-light-claude.png`
- `screenshots/ws28-b2/default-dark-claude.png`
- `screenshots/ws28-b2/skyline-light-langgraph.png`
- `screenshots/ws28-b2/skyline-dark-claude.png`
- `screenshots/ws28-b2/skyline-mobile-langgraph.png`

## Deployment

No hosted deployment was performed. Publishing D98 requires a **full console image rebuild**:

```sh
deploy/console/deploy_console.sh
```

Do not use `--skip-build`: `src/console/app.py`, `src/console/static/index.html`, and the renamed
stylesheet are baked into the image by `COPY src ./src`. The component topology is unchanged, so
`plan/09-deployment-map.md` needs no D98 entry.

## Round 2 response

| Finding | Resolution | What changed |
|---|---|---|
| Choosing Default on `/design/axs` reapplies a saved Skyline preference after navigation | fixed | Added a failing regression first, then made the explicit Default choice persist `default` before navigating to `/`. Loading the alias and choosing Skyline still do not write storage. |
| Browser check was not tracked | disputed (with evidence) | The implementation-time Playwright spec was an ad hoc evidence harness and was removed after producing the checked-in screenshots. Claude independently reran the same behaviors from a clean worktree using a separate CDP probe. The durable unit regression now covers the missing explicit-Default write. |
| `?v=1` had no bump rule | fixed | Added an adjacent source comment requiring the query version to change on every `skyline-theme.css` edit. |
| Claude-specific `agentcore` tooltip remains wrong on Strands cards | deferred to D98 / WS28 item 7 | D98 explicitly retains tag metadata. Recorded as new WS28 follow-up item 7 rather than silently broadening B2. |

Focused response evidence:

- `uv run pytest tests/unit/test_console.py -k theme_controls` — 1 passed.
- Headless Chromium seeded `a2a-design-v1=skyline`, opened `/design/axs`, chose Default, and
  verified `/` had no Skyline marker, the selector read Default, and storage held `default` —
  1 passed in 1.8s. The scratch spec was removed after the run.
