**Verdict: REQUEST CHANGES**

## Round 1 — Claude Opus headless critique

Claude Opus ran headlessly as the D79 critic for `ws28-b4-openai-anthropic-themes`
(proposal commit `b9eb242`). It edited no repository files. A throwaway page under `/tmp`
was used to reproduce the CSS cascade in headless Chromium.

The direction is sound: the proposal keeps the persisted key, uses a strict allow-list,
scopes rules to theme markers, uses local fonts, preserves semantic color ownership, and adds
no preview routes. The following findings must be resolved before implementation.

### Blocking findings

1. **Theme light-token selectors lose to the base light palette.** The proposed
   `html[data-design="openai"]` has lower specificity than the existing
   `:root[data-theme="light"]`. This was reproduced against Skyline: light-mode `--bg`,
   `--card`, and `--primary` compute to Default values. Use explicit
   `html[data-design="X"][data-theme="light|dark"]` token blocks, fix Skyline in this batch,
   test selector specificity, and capture computed root-token evidence for Skyline, OpenAI,
   and Anthropic in both modes.
2. **Vendor names are ambiguous beside platform identity.** OpenAI and Anthropic are already
   platform identities in this public console, so selector labels could read as filters or
   endorsements. Prefer original theme names with inspiration documented in code/ADR, or have
   D100 explicitly approve vendor names. Do not ship exact Anthropic palette or coral brand
   values; deliberately shift them and record that separation.
3. **Theme accents must not collide with platform identity colors.** State `--primary` and
   `--bar` for each mode. Anthropic chrome must not read as Claude coral, and OpenAI chrome must
   not collapse into the OpenAI chip. Verify primary controls beside semantic chips.
4. **The forced `/design/axs` contract must handle all four choices.** Use an explicit shared
   design allow-list and a generic `applyDesignTheme`. The forced marker wins and the selector
   displays Skyline on the alias; choosing any non-Skyline choice saves it and navigates to `/`.
   Test option/bootstrap/runtime parity and all forced cases.
5. **Complete the repository definition of done.** D100 must cite D97–D99; WS28 needs pending
   items for both themes, the Skyline specificity correction, and evidence; Jira sync must run
   dry then apply; implementation notes must be written; version comments must cover each CSS
   link; publication still requires a full rebuild.

### Suggestions to incorporate

6. Make contrast measurable: 4.5:1 for foreground/muted text on primary surfaces and 3:1 for
   focus/input boundaries.
7. Define each theme's treatment of the base hero and wordmark gradient.
8. Make selector-scope validation inspect selectors inside at-rules.
9. Verify Mermaid legibility on Anthropic dark surfaces.
10. Account for the cost of multiple render-blocking CSS files; consider one versioned
    `themes.css` asset instead.

### Files inspected

`build-notes/codex/openai-anthropic-themes-plan.md`, handoff state,
`src/console/static/index.html`, `src/console/static/skyline-theme.css`,
`src/console/app.py`, `tests/unit/test_console.py`, `AGENTS.md`, `CLAUDE.md`,
`plan/16-joint-agent-workflow.md`, `plan/07-workstreams.md`, `plan/00-decisions.md`,
`deploy/console/Dockerfile`, and prior WS28 review files.
