# WS28 B6 proposal — place the Exchange Mark on the landing title

## Request

Place the D101 A2A Exchange Mark beside the `A2A Interop Lab` title in the public landing-page
hero. Keep the persistent header mark and every theme behavior unchanged.

## Proposed implementation

- Add a small `.landing-title` flex wrapper around the generated hero wordmark in
  `landingHtml()` and render the same decorative, ID-free Exchange Mark immediately before the
  title.
- Keep the approved mark at 20×20 with the same base `.brandmark` gradient, navy geometry,
  `aria-hidden="true"` and `focusable="false"`. The hero use may add a placement-only class but
  must not redefine its dimensions, colors, geometry or transform.
- Preserve the wordmark's existing responsive font size, gradient, margin and semantics: the
  landing title remains the single `<h1>` in the hero and the decorative SVG contributes no
  duplicate accessible name.
- Keep theme-specific spacing outside the mark. The wrapper may control only alignment, gap and
  the wordmark's existing bottom margin. No `themes.css` edit or version bump is needed.
- Avoid SVG `<defs>`, external assets and runtime dependencies. The two instances must retain the
  same stable `data-part="agent"` and `data-part="message"` structure.

## Verification

1. Extend console contracts to require one header mark and one landing-title mark, identical
   dimensions/accessibility/part counts, with one landing hero `<h1>`.
2. Run the focused console suite, full pytest, Ruff, diff checks, Codex probes and regression
   replay.
3. Capture Default/Skyline/Canvas/Parchment landing heroes at desktop width plus one narrow view;
   confirm the mark remains 20×20, aligns with the title and does not wrap or overflow.
4. Request Claude Opus cross-review. Publication remains a full console image rebuild; no
   Details, Dockerfile, diagram, deployment-map or runtime change is involved.

## Decision requested

Approve this as D102: reuse the D101 A2A Exchange Mark beside the public landing-page title.
