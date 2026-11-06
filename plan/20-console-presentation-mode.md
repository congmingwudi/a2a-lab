# 20 — Console presentation mode

**Status:** WS31 implementation in progress (D116, 2026-11-06).

## Purpose

Presentation mode is a temporary teaching lens over an existing console section. It explains a
multi-step system path, labels the strength of each claim, and then hands the presenter to the
existing experiment that produces the live result. It does not replace Run, Details, or traces.

The first implementation covers **Agent Memory — customer continuity**. Its eight scenes are:

1. why cross-session, cross-agent memory matters;
2. the fixed registration and configured data-space boundary;
3. conversation/session/entry ingest and close;
4. asynchronous fact and preference extraction;
5. registration, server-resolved owner, category, and the measured isolation boundary;
6. GetContext, explicit category reads, generation isolation, and current graph evidence;
7. configured, measured, observable, and open boundaries; and
8. the live Claude-teaches/OpenAI-recalls handoff.

## Evidence contract

- Scene copy may state only behavior backed by configuration, a trace, a repeatable measured
  result, or an explicitly labeled open question.
- Live responses, actions, timing, and trace hops are never fabricated in the tour. The existing
  experiment view remains authoritative.
- After Start fresh, the tour says that profile rows are quarantined and saved facts/preferences
  reads are skipped. It does not ask a presenter to find hops that should not exist.
- Extraction is asynchronous. Evidence comes from a later recall or management read, never a
  fixed wait promise or the teaching response alone.
- Graph language distinguishes the working attached-graph controls from the current CRM-backed
  profile mismatch. It does not generalize one failed CRM lookup into a product-wide failure.
- Unavailable or unpublished future capabilities have no scene, alternate route, served copy,
  deployable asset, source reference, or hidden markup. Publishing them later requires a separate
  decision and review.

## Interaction contract

The Agent Memory Control Panel group owns the primary launcher. Both customer-memory experiments
also expose a contextual shortcut. Signed-out visitors keep the existing landing-only boundary;
signed-in viewers can read all eight scenes but still land on read-only experiment Details.

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

Scene 8 displays only public-safe readiness state: customer label, fixed registration, configured
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
requires the corresponding draw.io twin before deployment.

## Validation and deployment

Focused console tests assert the eight-scene boundary, entry points, navigation hooks, evidence
copy, generation-aware guidance, and no-auto-send handoff. Browser evidence must exercise the
interaction matrix at desktop and narrow widths and record console errors; screenshots alone are
insufficient. Product Design QA compares the approved visual with the rendered implementation.

The console Dockerfile already copies `src/`, so there is no deployment-map change. Because the
static shell is baked into the image, deployment requires a full console rebuild; `--skip-build`
cannot ship this change.
