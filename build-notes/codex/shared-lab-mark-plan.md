# WS28 B5 proposal — one shared A2A Interop Lab mark

## D101 settlement

The operator approved the rendered **A2A Exchange Mark** in
`build-notes/codex/screenshots/ws28-b5/shared-lab-mark-review.png` after the Claude Opus
critique. Implementation retains the current Default five-stop gradient exactly and uses fixed
deep-navy `#0b1f33` artwork for 7.2–9.8:1 contrast across those stops. The 20-unit drawing uses
two radius-2.2 endpoint nodes and two opposing open chevrons with 1.5-unit round-capped strokes.
The field stays in base CSS to avoid SVG definition IDs. Theme-specific spacing around the mark
may remain, but no theme may override the mark itself. D101 also includes the required
`themes.css?v=2` cache bust, selector-level regression coverage, ADR/workstream records and
browser evidence before cross-review. These settled details supersede looser geometry and ID
language in the original proposal below.

## Request

Replace the theme-specific artwork beside `A2A Interop Lab` with one lab-owned mark that is
identical in Default, Skyline, Canvas and Parchment. The operator prefers the current Default
mark's compact size and blue/cyan character, and wants the new symbol to communicate
agent-to-agent exchange simply at header scale.

## Proposed identity

Use an original **two agents in exchange** mark: two circular endpoint nodes connected by a
pair of opposing message paths. The two nodes represent independent agents; the two directions
make the relationship a conversation rather than a one-way integration. Keep the geometry
deliberately spare so it remains recognizable at 20 CSS pixels and does not compete with the
wordmark.

The mark will sit in the same softly rounded square silhouette and blue-to-cyan family as the
current Default tile. Its palette and geometry are part of the A2A Lab identity, not theme
tokens: changing theme or light/dark mode must not recolor, resize, reshape or animate it. This
is an original lab symbol, not a platform, protocol or vendor logo.

## Proposed implementation

- Replace the empty decorative `.brandmark` span in `src/console/static/index.html` with a
  single inline SVG. Use a `0 0 20 20` view box, a blue/cyan rounded-square field, two high-
  contrast agent nodes and two compact opposing paths. Keep its rendered width and height at
  the current Default `20px`, with the existing header gap.
- Keep the SVG decorative because the adjacent visible `A2A Interop Lab` heading already names
  the destination: apply `aria-hidden="true"` and `focusable="false"`; do not add a duplicate
  accessible name or tab stop.
- Give gradients and any clip/mask definitions collision-resistant lab-specific IDs. Do not
  load a remote image, font, script or runtime dependency.
- Remove every theme-specific `.brandmark` size, background, shape, shadow, transform and
  pseudo-element override from `src/console/static/themes.css`, including Skyline's narrow-
  viewport resize. Optional themes may continue to style the surrounding header, but not the
  shared identity asset.
- Preserve the current clickable brand/home behavior, header wrapping, theme selector, color
  mode control and all experiment/platform identity marks. This batch changes only the lab mark.

## Accessibility and responsive contracts

- The vector must remain crisp at normal and high-density displays and retain clear internal
  separation at 20px in all four themes and both color modes.
- The fixed blue/cyan field must provide sufficient contrast for its internal paths and nodes;
  it must not depend on surrounding page color for legibility.
- The mark stays 20×20 at narrow widths. Existing responsive rules may hide or wrap adjacent
  header text and controls, but must not substitute a different identity.
- Reduced-motion mode requires no special variant because the mark has no motion.

## Verification

1. Add focused console contracts that assert one inline SVG mark beside the heading, its
   decorative accessibility attributes and 20px dimensions, and the absence of theme-scoped
   `.brandmark` or `.brandmark::after` rules.
2. Add a structural assertion for two endpoint nodes and two directional exchange paths so a
   later cosmetic refactor cannot silently reduce the mark to an unrelated tile.
3. Run the required baseline and final gates: `uv sync --all-extras`, `uv run pytest`, focused
   console tests, Ruff, `git diff --check`, the Codex readiness probes and regression harness.
4. Restart the local console and capture a header matrix for Default, Skyline, Canvas and
   Parchment in light and dark mode, plus one narrow viewport. Confirm computed 20×20 geometry,
   identical SVG markup/style, no layout shift and no browser console error.
5. Request a fresh Claude Opus cross-review after implementation. Any later publication needs
   the existing full console image rebuild because `src/` is baked into that image; this batch
   changes no hosting topology or deployment-map entry.

## Decision requested

Approve this as D101: adopt the compact blue/cyan **two agents in exchange** mark as the single
A2A Interop Lab identity in every console theme and color mode.
