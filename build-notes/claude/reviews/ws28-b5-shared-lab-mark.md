**Verdict: REQUEST CHANGES**

## Round 1 — Claude Opus headless critique

The concept is sound. Two agent nodes joined by opposing message paths is a simple, generic,
original symbol for agent-to-agent exchange, and it does not borrow from any vendor identity.
Removing all theme overrides is the right way to get one mark across four themes. But the plan
leaves the parts that decide legibility at 20px unspecified, misses the stylesheet cache-bust
rule, and leaves out two definition-of-done items. Items 1–4 below should be folded into D101
before implementation.

### Blockers

1. **“High-contrast nodes” will not hold if the ink is white on the current gradient.** The
   current Default tile is not just blue/cyan. It runs
   `#09decb → #3fc7d8 → #7db9e8 → #a8a2ec → #c99df0`
   (`src/console/static/index.html:75-76`), ending in lavender and violet. White ink on those
   stops measures **1.7 / 2.0 / 2.1 / 2.3 / 2.2 : 1**. That is below 3:1 everywhere, and at
   the teal stop, where the left node would sit, it is 1.7:1. A dark ink such as `#0b1f33`
   measures **7.2–9.8 : 1** across every stop. D101 should specify the field gradient (reuse
   the existing five stops exactly), dark ink, and a minimum stroke width of 1.5 user units in
   the `0 0 20 20` box with round caps.
2. **The geometry is too loose for 20px.** “Two compact opposing paths” cannot be checked.
   Arrowheads drawn as filled triangles inside a 20px box blur into the nodes. D101 should fix
   the drawing roughly as: nodes `r≈2.5` at about `(5.5,10)` and `(14.5,10)`; one arc above and
   one below the line between them, each ending in one open chevron (two strokes, no more than
   2.5 units long) pointing the opposite way; and at least 1.5 units of clear space between each
   chevron and its node. Render at 20px and 1× and confirm both chevrons remain distinguishable.
3. **`themes.css` cache-bust is missing.** `index.html:9-10` says to bump the version on every
   `themes.css` change, and the link remains `themes.css?v=1`. Removing rules without bumping
   lets deployed browsers keep old theme-specific sizes/shapes over the SVG. Bump to `?v=2` and
   replace the two hard-coded test literals at `tests/unit/test_console.py:123,130` with a
   version-pattern assertion.
4. **The definition of done is incomplete.** D101 should cite D97 (no imitation), D98 and D100
   (theme scoping), and D79 (handoff). Add WS28 item lines 15–17 for the mark and override
   removal, cache bust and tests, and browser evidence and cross-review. Run `jira_sync.py` dry
   and apply under the standing authorization. No deployment-map, Dockerfile `COPY`, or Details-
   pane change is needed; a grep found no prose that narrates the old tile.

### Should fix (non-blocking)

5. **Avoid a gradient ID.** Keep the existing gradient as the SVG element CSS background and
   draw only nodes/paths in SVG. That avoids collisions with the page's generated inline SVG
   IDs. If an in-SVG gradient is retained, prefix it and test that it appears once.
6. **Make the base rule precise.** Use
   `.brandmark { display:block; width:20px; height:20px; flex:none; }` and add `width="20"`
   and `height="20"` attributes to the SVG for correct pre-stylesheet sizing. Do not paint the
   field in both CSS and SVG.
7. **Clarify surrounding spacing.** Theme-specific spacing around the mark, such as Skyline's
   `header .brand` gap/padding, may remain; only the mark itself is invariant.
8. **Cover every override selector.** Assert that no selector anywhere in `themes.css`, including
   inside `@media`, contains `.brandmark`. Checking only the base selector and `::after` misses
   the existing Skyline narrow resize or a future pseudo-element.

### Brittle or unnecessary test requirements

9. Counting `<circle>` and `<path>` elements is brittle. If structural checks remain, use stable
   hooks such as `data-part="agent"` twice and `data-part="message"` twice; otherwise rely on
   browser evidence.
10. For cross-theme identity, compare computed width, height, transform, background/fill and an
    `outerHTML` hash. Do not require screenshot pixel equality because theme header backdrop
    blur changes surrounding pixels.

### Already handled correctly

- `aria-hidden="true"` plus `focusable="false"`, with no duplicate name, is appropriate because
  the visible heading names the brand.
- Removing Skyline's narrow-viewport resize is in scope.
- Theme SVG rules are scoped to architecture figures and do not reach the mark.
- The mark has no motion, so no reduced-motion variant is required.

Outside this batch, the `.brand` home control is a `div` with `onclick` and is not keyboard
reachable. That predates this batch and should be logged separately, not added to B5.

### Files inspected

`build-notes/codex/shared-lab-mark-plan.md`, handoff state, commit `2aa2a16`,
`src/console/static/index.html`, `src/console/static/themes.css`,
`tests/unit/test_console.py`, `AGENTS.md`, `CLAUDE.md`, `plan/16-joint-agent-workflow.md`,
the D98–D100 ADRs, and WS28 items 1–14.

Claude Opus edited no repository files. The review ran headlessly through Claude Code 2.1.316.
