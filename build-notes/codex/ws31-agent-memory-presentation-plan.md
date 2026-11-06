# WS31 proposal — Agent Memory presentation mode

## Outcome

Add a console-native presentation mode for the **Agent Memory — customer continuity**
section. A first-time viewer can learn the measured Data 360 Agentic Memory path and then
launch the existing Claude-teaches/OpenAI-recalls experiment without leaving the console.

The tour contains only behavior that is available, observed, and demonstrable in this lab.
Confidential future-roadmap material is explicitly out of scope: no copy, date, concept name,
source reference, asset, route, or hidden browser markup from that material is included in the
console, D116, plan 20, or any served build note. The ignored local research captures are not
copied into the hosted image. A future publication decision requires a
separate batch.

## User experience contract

### Entry and exit

- Add a dedicated **Presentation** action beside the `agent-memory` group header without
  making the expander itself a competing nested control. It is keyboard reachable and launches
  the section-level tour. Retain **Presentation mode** beside Run / Details on both
  customer-memory scenarios as a contextual shortcut.
- Enter a dedicated `memory-presentation` console view. Keep the shared product header and derive
  Control Panel suppression solely from `view.type === "memory-presentation"`; do not snapshot
  panel state or change the persisted `a2a-side-open` preference. Store a stable launcher id—not
  a DOM node detached by sidebar rendering—and restore focus to that id, with the product header
  as the mobile fallback. Leaving the view removes suppression automatically, including across
  auth changes and console Back. Repeated enter/exit cannot accumulate stale state. A live
  handoff uses existing experiment navigation, whose desktop behavior intentionally opens the
  Control Panel.
- Support Previous / Next, direct scene selection, left/right arrow keys, Escape to exit,
  console Back (`goBack()`), and reduced-motion preferences. Browser History integration is out
  of scope because this SPA does not use `pushState`/`popstate`.
- Preserve the selected customer while moving between the tour and either live experiment;
  default to Maya only if no valid selection exists. Never call `setMemoryCustomer()` during
  handoff because it resets chat. Signed-out handoff follows `requireSignIn`; viewer handoff
  follows the existing read-only/Details default rather than forcing Run.

### Verified tour — eight scenes

Every scene names its evidence state and cites the corresponding lab decision/workstream.

1. **Why memory** — cross-session, cross-agent customer continuity; measured Claude →
   OpenAI result and Jordan isolation control. Teach the corrected D115 scope—registration ×
   verified customer owner × category—not universal profile storage (D104/D105/D115, WS27).
2. **Agent configuration** — shared `A2ALab_Memory_Case` registration, required data space,
   extraction enabled, no attached graph on the live registration, and no profile DMO in the
   current readback. The service measurably defaults to `UnifiedIndividual__dlm`; whether the
   missing declaration was caused by an earlier partial PATCH remains open (D115).
3. **Ingest turns** — conversation/session/participant setup; user and agent entries; redaction;
   close triggers extraction; current asynchronous availability boundary.
4. **Extract facts and preferences** — the LLM extraction path and the two current memory
   categories. Do not claim model internals the lab cannot inspect.
5. **Store and isolate** — the server-resolved customer owner within the shared registration,
   facts/preferences categories, and configured data-space boundary. The face's owner mapping is
   the effective isolation boundary measured by the lab: D115 found that the token's customer id
   was not enforced for the internal caller, while the `profile_id` sent by the face selected the
   owner. The mapping isolated Maya from Jordan in the demo, but the tour must not generalize that
   result into a broader governance guarantee. D115 also measured that a second registration
   starts empty for the same customer; that conflicts with the current guide and the exact matrix
   is incomplete, so render it as measured/open. “Verified customer owner” means the server
   resolves an allow-listed customer key behind a verified, non-machine lab JWT; the customer is
   never authenticated by the browser.
6. **Retrieve with GetContext** — show GetContext and explicit facts/preferences Memory API
   reads as separate current operations. The live registration has no attached graph. State the
   D115 result precisely: platform-made graph controls returned their records, proving the
   attached-graph path works. The surviving CRM graph control resolved the memory pipeline's
   self-created Individual/profile, which differs from Maya's CRM-backed AM01 profile, so it
   contributed no CRM record. Do not rely on the earlier arm whose configuration was overwritten
   by a partial PATCH. This is not a caller-governance mismatch. After D107 Start
   fresh, profile rows are quarantined and the saved-profile Memory API merge is skipped; only
   eligible context from the current generation can reach the prompt.
7. **Evidence and limits** — verified/configured/open-question legend, availability lag,
   fail-open budget, tenant trace hops, D107 generation/isolation state, current Limited GA
   billing boundary, and the current CRM/real-time graph limitation without implying that graphs
   in general fail or that future capability is present.
8. **Claude teaches, OpenAI recalls** — live-demo director that launches the existing Claude
   teaching scenario or OpenAI recall scenario with Maya selected and memory on. Do not
   fabricate a response; the experiment view remains the source of live results and traces.

### Confidential future-material boundary

- No roadmap appendix or roadmap entry point is implemented in WS31.
- No served or deployable file names, describes, paraphrases, dates, or links to the confidential
  concepts or their source material.
- D116 and plan 20 record only the boundary: presentation mode is evidence-backed and excludes
  unavailable or confidential future capabilities.
- Reconsidering that material requires explicit publication approval and a separate decision.

## Implementation shape

### Console state and rendering

- Implement in `src/console/static/index.html`, following the existing single-page render
  model and Canvas tokens.
- Keep the scene and launcher in `view` itself (`type`, `scene`, `launcher`) and add a
  `view.type === "memory-presentation"` branch before the experiment
  renderer. Presentation suppression is a separate CSS/DOM state, not a call to `toggleSide()` or
  `ensureSideOpen()`, so it never writes the user's persisted panel preference.
- Build scenes from a declarative array so another console section can adopt presentation
  mode later without duplicating navigation logic.
- Use semantic HTML buttons, ordered scene navigation, `aria-current`, a live scene-title
  announcement, and visible focus states.
- Use the existing platform marks and CSS tokens. No remote asset, inline SVG, CSS drawing,
  or new frontend dependency is required.

### Live experiment handoff

- `launchMemoryExperiment(name)` uses normal navigation to open the
  selected scenario, preserves the valid customer (Maya only as fallback), sets memory on without
  resetting chat, and loads the scenario's existing seeded prompt for operators. It does not issue
  an API call automatically. Viewer and signed-out behavior remains authoritative in the existing
  experiment navigation.
- Existing experiment controls, authentication, role restrictions, trace evidence, reset
  generation, and customer isolation remain authoritative.

### Responsive behavior

- Desktop: system map / narration split similar to the selected visual target.
- Narrow: narration follows the map; scene rail becomes horizontally scrollable; persistent
  controls remain visible without hiding content.
- Dark and all four design themes use semantic tokens; the prototype must not hard-code a
  light-only palette.

## Test-first acceptance

Add focused assertions to `tests/unit/test_console.py` for:

1. Presentation entry appears at the Agent Memory section and on the two live customer-memory
   scenarios, but nowhere else.
2. Exactly eight verified scenes exist and no ninth scene, alternate presentation route, or
   secondary presentation entry point exists. A separate ignored-local audit supplies the
   restricted-term sweep when its private input is available; tracked tests never embed it.
3. D115 graph truth is present: platform-made controls work, while the surviving CRM/real-time
   graph control returned no CRM record because it resolved a different profile.
4. Live handoff uses the existing scenario state and never auto-sends a turn.
5. Keyboard, ARIA, reduced-motion, and narrow-layout hooks are present.
6. No environment identifiers or profile ids enter browser-visible markup.
7. Superseded scope and governance copy is corrected in `config/scenarios.yaml`,
   `src/console/static/index.html`, and `plan/18-agentic-memory-context.md` to the measured
   registration × server-resolved customer owner × category model, the face-owned isolation
   boundary, and its open boundary.

Interaction evidence must exercise, rather than merely search for, this matrix:

| Surface | Required interaction proof |
|---|---|
| Section entry | Keyboard-focus the separate Agent Memory Presentation action; Enter launches scene 1 |
| Scenario shortcut | Launch from both customer-memory experiments and return focus to the invoking control |
| Scene controller | Previous, Next, direct selection, first/last boundaries, Left/Right arrows |
| Exit paths | Escape, explicit Exit, and console Back restore presentation state exactly once |
| Escape precedence | An open D-chip/popover closes before presentation exits |
| Return path | Console Back from an experiment re-enters the tour without stale sidebar state |
| Auth change | Session expiry clears the user without stale suppression; later sign-in restores the saved panel preference only after leaving presentation; explicit sign-out reloads to landing |
| Re-entry | Repeated enter/exit cycles leave one view, one listener, and no retained suppression |
| Signed out | No presentation entry is available; direct calls are inert, and experiment navigation keeps the existing sign-in gate |
| Accessibility | `aria-current` follows the scene; the changed title is announced through a live region |
| Motion | `prefers-reduced-motion` removes route/step transitions without hiding state |
| Responsive | Desktop and narrow layouts retain reachable navigation and persistent exit controls |
| Viewer role | Verified tour remains readable and live run remains read-only |
| Live handoff | Valid customer preserved (Maya fallback), memory on, generation state visible, seeded prompt loaded for operators, no automatic send; auth/state stays authoritative |

Escape handling is ordered: an open D-chip/popover or diagram closes first; only an unconsumed
Escape exits presentation. Arrow navigation ignores events originating in inputs, buttons,
selects, textareas, dialogs, and open popovers.

Exit and Escape both call `goBack()`; there is no second `returnView` source of truth. The
presentation Escape listener runs in capture phase and checks the currently open decision
popover, diagram, and fullscreen surfaces before exiting. The render cache key includes the
scene. The top-bar mode label is a non-interactive status indicator. The visible controls use
**Previous** and **Next**; **Exit presentation** is the only console-level return label.

The selected mock is a composition reference, but measured behavior overrides two pieces of its
small checklist copy: after D107 Start fresh, facts/preferences Memory API reads are intentionally
skipped, and asynchronous extraction is evidenced by a later recall/management read—not by the
teaching trace. Scene 8 renders the checklist conditionally from the current generation state and
viewer role. The configured data space is displayed, never chosen.

Run:

- `uv run pytest tests/unit/test_console.py -q`
- `uv run ruff check src tests scripts`
- `uv run python scripts/jira_sync.py` (dry run)
- the existing standard-library readiness probes and regression harness for cross-review
- `node build-notes/codex/ws31-browser-check.mjs http://127.0.0.1:8200` records reproducible
  browser interaction evidence and 1440×1024 plus narrow Canvas light/dark captures under
  `build-notes/codex/screenshots/ws31/`; its final JSON must report every interaction-matrix case
  and zero uncaught console errors. Screenshots alone do not satisfy the matrix. This harness is
  written and run only after browser-tool approval required by the Product Design workflow.

The full-suite baseline on 2026-11-05 is not green in this shell: 835 passed, 3 skipped,
6 deselected, 50 failed and 10 errored. Most failures are environment contamination from the
configured `socks5h://127.0.0.1:59326` proxy without `socksio`; one existing console test also
fails. Focused regressions must be reported separately and no baseline failure may be hidden.

## Documentation and deployment definition of done

- Add D116 to `plan/00-decisions.md` for the evidence-only presentation boundary and explicit
  exclusion of unavailable/confidential future capabilities.
- Add WS31 item lines to `plan/07-workstreams.md` (the Jira importer needs item lines).
- Add `plan/20-console-presentation-mode.md` as the durable content/extension contract.
- Correct the customer-memory descriptions in `config/scenarios.yaml`, the matching Details copy
  in `src/console/static/index.html`, and the current summary in
  `plan/18-agentic-memory-context.md` to the D115 measured registration × server-resolved customer
  owner × category model and its open boundary.
- Update `CLAUDE.md`'s plan map to include plan 20.
- D116 records a narrow D57 exception: presentation mode is a temporary teaching lens over the
  two existing experiments, whose Run/Details panes remain authoritative; it does not add a
  duplicate Details tab. Scene 7 carries the evidence/limits explanation.
- Presentation maps are semantic HTML application UI, not Mermaid figures; D114's draw.io twin
  rule does not apply. If implementation introduces Mermaid, add its draw.io twin before handoff.
- Console-only deploy: `deploy/console/Dockerfile` already copies `src/`; verify that no new
  package path is introduced. Deploy requires a full console rebuild, not `--skip-build`, because
  `src/console/static/index.html` is baked into the image.
- Record browser evidence and design QA under `build-notes/codex/`; deployment remains an
  operator action.
- Record a separate follow-up for the existing browser-visible memory owner-id field; WS31 does
  not consume or display that field.

## Selected visual truth

The earlier mock contained confidential future-material labels and several inaccurate live-state
assumptions, so it was moved to ignored `tmp-docs/` and is not a design-QA target. The user
approved the revised verified-only mock on 2026-11-06. It shows scene 8 as a launch/readiness
state—no fabricated response, fixed registration control, or unsupported timing—and is preserved
at `build-notes/codex/screenshots/ws31/agent-memory-presentation-target.png` for design QA.
