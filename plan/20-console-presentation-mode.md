# 20 — Console presentation mode

**Status:** WS31 implementation in progress (D116, 2026-11-06).

## Purpose

Presentation mode is a temporary teaching lens over an existing console section. It explains a
multi-step system path, labels the strength of each claim, and then hands the presenter to the
existing experiment that produces the live result. It does not replace Run, Details, or traces.

The first implementation covers **Agent Memory — customer continuity**. Its ten scenes are:

1. one agent context in Data 360 — the whole path on one figure;
2. why cross-session, cross-agent memory matters;
3. the fixed registration, its Data Space (default-only, AM-API-012), profile DMO, and
   real-time data graph (D117/D118);
4. conversation/session/entry ingest and close;
5. asynchronous fact and preference extraction;
6. registration, server-resolved Contact-id owner, category, and the measured isolation boundary;
7. GetContext with the graph record, explicit category reads, rendering, id masking, and
   generation isolation;
8. an in-store purchase reaching the next turn through the real-time graph (D118);
9. configured, measured, observable, and open boundaries, including the forget lag
   (AM-API-013); and
10. the live Claude-teaches/OpenAI-recalls handoff.

Every scene but the last carries a figure — a `*_DIAGRAM` constant in
`MEMORY_PRESENTATION_FIGURES` with its draw.io twin under `docs/diagrams/memory-*.drawio` (D114)
— above its step cards. Scenes 3, 7 and 8 also show screenshots of the org's own Data 360 setup
(Agent Configuration, Data Spaces, the real-time Data Graph) from
`src/console/static/memory-presentation/`. They are cropped so no record id, user name or
customer row is visible, and the Agent Configuration was opened in Edit and closed without saving.

## Evidence contract

- Scene copy may state only behavior backed by configuration, a trace, a repeatable measured
  result, or an explicitly labeled open question.
- Live responses, actions, timing, and trace hops are never fabricated in the tour. The existing
  experiment view remains authoritative.
- After Start fresh, the tour says that profile rows are quarantined and saved facts/preferences
  reads are skipped. It does not ask a presenter to find hops that should not exist.
- Extraction is asynchronous. Evidence comes from a later recall or management read, never a
  fixed wait promise or the teaching response alone.
- Configuration copy comes from the live registration readback (2026-11-13): Data Space
  `default`, extraction on, profile DMO Unified Individual AM01, and one POS-bearing real-time
  graph attached by a full-configuration v67 PATCH (D117/D118). The faces send the customer's
  CRM Contact id as owner (D117), so the graph record arrives in GetContext beside memory. The
  graph is named generically because the attached graph is the measured copy, not the tracked
  definition (WS32 item 6).
- The Data Space step says `default` is required until the non-default write path is fixed
  and verified on this org (AM-API-012, SR-01 in `build-notes/support-requests.md`). That
  copy changes only after a rerun of the non-default probe passes here.
- Forget is told as measured (AM-API-013): the Memory API list drops a deleted memory within
  a second, while GetContext kept serving it for at least 44 minutes, so a forget demo shows
  the list, not the next agent turn.
- Unavailable or unpublished future capabilities have no scene, alternate route, served copy,
  deployable asset, source reference, or hidden markup. Publishing them later requires a separate
  decision and review.

## Interaction contract

The Agent Memory Control Panel group owns the primary launcher. Both customer-memory experiments
also expose a contextual shortcut. Signed-out visitors keep the existing landing-only boundary;
signed-in viewers can read all ten scenes but still land on read-only experiment Details.

The presentation scene and stable launcher id live in the normal `view` object. Entering pushes
one navigation snapshot. Exit and Escape call console Back, and returning from a launched
experiment restores the exact scene. The Control Panel is hidden only because the current view is
`memory-presentation`; the mode never writes or snapshots `a2a-side-open`.

Scene navigation supports Previous, Next, direct scene selection, and left/right arrows outside
interactive controls. Escape first yields to an open decision popover, diagram, or fullscreen
figure. Leaving restores focus to the invoking control, or the product home control if the
launcher is no longer rendered. Reduced-motion preferences remove presentation transitions.

## Live handoff contract

The handoff validates the shared selected customer and falls back to Maya only when no configured
selection exists. It sets the target experiment's Memory mode on and calls the existing experiment
navigation, which loads the existing seeded prompt. It does not call `setMemoryCustomer()` because
that resets chat, and it never calls the send path.

Scene 10 displays only public-safe readiness state: customer label, fixed registration, configured
data space, a human-readable generation state, Memory on, and no-auto-send. It never displays a
profile id, environment identifier, fabricated reply, action result, or elapsed-time promise.

## Extension rules

A second section may reuse the declarative scene controller when it has:

- a selected and approved visual target;
- a bounded scene list with evidence labels;
- an existing authoritative live surface to hand off to;
- role, auth, keyboard, focus, responsive, and reduced-motion acceptance; and
- a decision explaining any D57 exception.

Presentation maps are semantic application UI. If an implementation introduces Mermaid, D114
requires the corresponding draw.io twin before deployment; register the constant in
`MEMORY_PRESENTATION_FIGURES` and `figKeyForSource`, then
`scripts/arch_drawio.py build <id> && … stamp <id>`.

## Validation and deployment

Focused console tests assert the ten-scene boundary, every figure's registration and twin,
the screenshots' presence, entry points, navigation hooks, evidence
copy, generation-aware guidance, and no-auto-send handoff. Browser evidence must exercise the
interaction matrix at desktop and narrow widths and record console errors; screenshots alone are
insufficient. Product Design QA compares the approved visual with the rendered implementation.

The console Dockerfile already copies `src/`, so there is no deployment-map change. Because the
static shell is baked into the image, deployment requires a full console rebuild; `--skip-build`
cannot ship this change.
