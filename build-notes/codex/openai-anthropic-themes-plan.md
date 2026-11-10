# WS28 B4 proposal — OpenAI- and Anthropic-inspired console themes

## D100 settlement

The operator settled D100 after the Claude Opus critique with two original public names:
**Canvas** for the OpenAI-site-inspired direction and **Parchment** for the
Anthropic-site-inspired direction. Implementation also includes the reproduced Skyline
light-token specificity correction, one versioned `themes.css` asset, explicit non-semantic
primary colors, the generalized four-theme forced-alias behavior, measurable contrast, and
the complete repository delivery record. This settlement supersedes the vendor-name and
two-stylesheet details in the original proposal below.

## Request

Add two more choices to the console's existing Theme selector, one inspired by the current
OpenAI site and one inspired by the current Anthropic site. Default and Skyline must continue
to behave exactly as they do now, and light/dark must remain an independent appearance axis.

## Live-reference findings (2026-10-31)

The official public homepages were inspected in a temporary headless-browser session at a
1440×1000 viewport. The captures are research evidence only and will not be committed or shipped.

- **OpenAI:** white ground, black ink, compact sans typography, unusually generous negative
  space, fine gray outlines, large softly rounded interaction surfaces, restrained pill controls,
  and very low-contrast shadows. The design's character comes from proportion and reduction,
  not from a signature accent color.
- **Anthropic:** warm `#faf9f5` paper ground and `#141413` ink (also delivered as the page's
  theme color), warm-stone panels, fine outlines, small capsule controls, and an editorial mix
  of sturdy sans labels with large serif display text.

The console themes will use these high-level visual principles only. They will not copy logos,
wordmarks, illustrations, photos, prose, proprietary fonts, or exact page components. The A2A
Lab brand and information architecture remain intact.

## Proposed implementation

### Theme model and selector

- Keep the persisted key `a2a-design-v1` and extend the strict allow-list to `default`,
  `skyline`, `openai`, and `anthropic` in both the pre-paint bootstrap and runtime setter.
- Add selector choices **OpenAI** and **Anthropic** after Skyline. These labels identify the
  requested inspiration clearly; shipped CSS comments will state that they are independent
  A2A Lab interpretations.
- Keep `/design/axs` as the only forced compatibility alias. Its Skyline marker continues to
  win for that document, and switching away from Skyline there follows the existing exit-to-`/`
  contract rather than inventing more preview routes.
- Invalid or unavailable stored values continue to select Default with no `data-design` marker.
- Keep light/dark mode independent and preserve the current view while switching themes.

### Stylesheet isolation

- Add versioned `openai-theme.css` and `anthropic-theme.css` links to every shared shell.
- Scope every rule to `html[data-design="openai"]` or
  `html[data-design="anthropic"]`; no unscoped selectors or global resets.
- Continue to own layout and semantic platform/protocol colors in the base console. The new
  stylesheets may redefine neutral surface, type, border, radius, spacing, focus, and motion
  tokens, but must not redefine `--rest`, protocol tokens, or platform chip tokens.
- Use only local/system font stacks. No external font, image, script, or runtime dependency.

### OpenAI-inspired direction

- Light: pure/near-white canvas, black ink, quiet gray secondary surfaces, single-pixel borders,
  sparse chrome, large rounded cards and input surfaces, nearly flat shadows, compact pill
  controls, and generous canvas/card breathing room.
- Dark: near-black canvas with charcoal surfaces and off-white ink, retaining the same restrained
  outline hierarchy rather than adding a colored glow.
- Typography: clean system sans throughout, tighter display tracking, strong but not heavy titles.
- Motion: subtle opacity/border/one-pixel lift transitions only; disabled under reduced motion.

### Anthropic-inspired direction

- Light: warm paper canvas, near-black brown ink, warm-stone cards and borders, mostly flat
  surfaces, capsule controls, and editorial section hierarchy.
- Dark: warm charcoal canvas, softened parchment text, and brown/stone elevation steps rather
  than a cool neutral inversion.
- Typography: system sans for controls/body and Georgia/Charter-style system serif for major
  hero, detail, and section headings. Dense telemetry, code, tables, and small labels retain
  their existing UI/mono families for legibility.
- Motion: gentle background/border transitions and one-pixel lift only; disabled under reduced
  motion.

### Responsive and accessibility contracts

- Preserve existing mobile drawer, header wrapping, scrollable tabs/tables, and side-closed grid.
- Theme selector labels and the separate `Color mode` group retain their current accessible names.
- Provide theme-specific visible `:focus-visible` treatments and preserve text contrast in both
  color modes.
- Do not convert icon-only platform identity into color-only meaning; D99 labels and semantic
  icon/chip colors remain unchanged.

## Verification

1. Extend unit contracts for all four options, both bootstrap/runtime allow-lists, invalid-value
   fallback, independent color mode, stylesheet loading/versioning, full selector scoping, no
   semantic-token overrides, and reduced-motion support.
2. Run the required baseline and final gates: `uv sync --all-extras`, `uv run pytest`, focused
   console tests, Ruff, `git diff --check`, the Codex readiness probes, and regression harness.
3. Restart the local console and exercise all four themes without reload or view loss.
4. Capture owned browser evidence for OpenAI and Anthropic in light/dark at desktop width plus
   narrow responsive views. Verify selector state, computed document marker, persisted reload,
   invalid-storage fallback, focus visibility, and no page overflow.
5. Request a fresh Claude Opus cross-review after implementation. Publication, if later chosen,
   requires the existing full console image rebuild because `src/` is baked into that image.

## Decision requested

Approve this as D100: extend the optional console appearance system with two original,
site-inspired themes while preserving Default, Skyline, semantic identity colors, the independent
light/dark axis, and the `/design/axs` compatibility contract.
