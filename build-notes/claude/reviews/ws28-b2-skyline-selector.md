**Verdict: REQUEST CHANGES**

# WS28 B2 proposal critique — Skyline theme selector and experiment-heading identity

Station 2 (critique) of the D79 loop, `plan/16-joint-agent-workflow.md`. Claude Code, as the critic,
checked each claim in `build-notes/codex/skyline-theme-selector-plan.md` against the source on
branch `ws28-b1-axs-console-theme-local` (HEAD `111229b`). No implementation code was edited.

The direction is sound. The theme layer is already fully attribute-scoped, `/static/` is already
public, and the `aws` heading badge is not just redundant: its tooltip is wrong on three
of the five scenarios that render it. But three of the plan's factual claims contradict the source.
It also leaves three behaviours undecided that an implementer would otherwise have to invent. And
it promotes a design that D97 says needs a new decision. Under plan/16 "Corrections go back to the
source file", the plan should be amended before settlement. None of the corrections below
changes the scope.

## Required corrections

1. **The promotion needs a superseding ADR, and the existing isolation test changes meaning.**
   D97 decided that "the normal `/` and `/guide` responses remain unchanged"
   (`plan/00-decisions.md#L4305`). It also said that promoting or replacing the released theme
   needs "a later decision", plus a full rebuild (`plan/00-decisions.md#L4311`–`#L4313`). This
   plan does exactly that promotion. Scope 1 loads the stylesheet on every shell response and adds
   a header selector (`skyline-theme-selector-plan.md#L24`–`#L26`). Yet the plan names no ADR. Today's test
   asserts the opposite of the plan's new contract: `/` and `/guide` must NOT carry the stylesheet
   (`tests/unit/test_console.py#L107`–`#L111`). Replacing that assertion is legitimate only if a
   new decision (the next free number is D98; the last entry is D97 at
   `plan/00-decisions.md#L4293`) supersedes D97's "unchanged" clause. Otherwise this is the
   "test weakened to keep a report green" pattern that plan/16 forbids
   (`plan/16-joint-agent-workflow.md#L32`–`#L37`). Amend the plan to name the ADR and to state
   which D97 clauses it supersedes. Keep the half of the test that still holds: the normal route
   carries no `data-design` marker from the server.

2. **"Hosting/runtime remains visible in the scenario's normal metadata chips" is false for the
   main canvas.** The experiment view's tab bar renders only the `<h2>`, the view-only badge, the
   `aws` badge, the mode badge and the tabs (`src/console/static/index.html#L10205`–`#L10218`).
   It has no tag chips. `tagChipsHtml()` is called only by Control Panel cards
   (`index.html#L2711`), landing tiles (`index.html#L9036`) and one registry row
   (`index.html#L8263`). Hosting does stay visible in the heading, but through the title text:
   "Claude (AWS) → Agentforce", "AWS Strands (AWS) → Agentforce", and so on
   (`config/scenarios.yaml#L52`, `#L121`, `#L565`, `#L655`). `LOGO_RE` deliberately leaves the
   bare word "AWS" unmarked (`index.html#L2788`–`#L2792`). Correct the rationale to say that.
   The removal is also better justified than the plan says. The badge's tooltip reads "The Claude
   end runs self-hosted on AWS Bedrock AgentCore (D26)" (`index.html#L10210`–`#L10211`), but it
   fires on every `agentcore` tag. That includes the Strands scenarios (`config/scenarios.yaml#L586`,
   `#L678`, `#L1332`), where no Claude end exists. It also fires inconsistently: "OpenAI (AWS)"
   scenarios carry no `agentcore` tag, so they never got the badge (`config/scenarios.yaml#L429`,
   `#L443`). Cite both facts as the reason for removing the badge.

3. **"The heading identifies the two platforms with their icons" is false for LangGraph.** A
   `langgraph` mark exists (`index.html#L2774`) and the obs tiles use it
   (`index.html#L3874`). But `LOGO_RE`/`LOGO_KEY` have no `LangGraph` entry
   (`index.html#L2792`–`#L2799`). So "LangGraph (Heroku) → Agentforce" and its reverse
   (`config/scenarios.yaml#L746`, `#L858`) would get one mark, not two. That is in the main canvas
   and, already today, in the Control Panel. Either add `LangGraph` to the alternation (an
   identity fix in the same spirit, which applies to every `withLogos` surface) or narrow the
   claim. Make the "non-AWS pair" in the browser evidence (`skyline-theme-selector-plan.md#L49`–`#L50`)
   include a LangGraph scenario so the gap is caught either way.

4. **Two header controls would both be named "Theme".** The existing light/dark group is
   already `role="group" aria-label="Theme"` (`index.html#L1448`). The runtime comment block calls
   it "Theme" too (`index.html#L10426`). A new selector labelled `Theme` placed directly to its
   left (`skyline-theme-selector-plan.md#L20`–`#L21`) gives screen-reader users two identically
   named controls next to each other. Rename the light/dark group (for example "Color mode") in the same
   change, and label the new select "Design" or "Theme" exclusively. Add the rename to the test list.

5. **Decide how the `/design/axs` alias and the saved preference interact.** Today the server
   injects `data-design` on the alias only (`src/console/app.py#L1311`–`#L1317`,
   `#L1332`–`#L1336`). Under the plan, an inline pre-paint script will also set `data-design` from
   local storage on every shell. The plan does not say which one wins, whether visiting the alias
   writes the Skyline preference, or what the selector does on the alias when the user picks
   `Default`: stay on a route that forces Skyline, navigate to `/`, or be disabled. Without a
   rule, a saved `Default` would either override the alias (a broken bookmark) or be overwritten
   by it. State the precedence (recommended: server marker wins, the alias never writes storage,
   and choosing Default on the alias navigates to `/`) and test it.

6. **Copy the validation from the runtime, not the existing pre-paint line.** The plan requires
   invalid saved values to fall back to Default (`skyline-theme-selector-plan.md#L34`–`#L35`).
   The obvious model to copy does not do this: the existing light/dark pre-paint script sets
   `t || "light"` unvalidated (`index.html#L8`). Only the later runtime path allow-lists
   `"dark"` (`index.html#L10441`–`#L10446`). Specify an explicit allow-list
   (`v === "skyline" ? "skyline" : null`) in the pre-paint script, and make the test feed it a
   garbage value.

## Clarifications (non-blocking, but say them in the plan)

7. **File name and cache.** The plan renames selectors and tokens from `axs` to `skyline` and
   says "do not expose AXS as a product/theme name" (`skyline-theme-selector-plan.md#L28`–`#L29`).
   But it keeps `axs-theme.css` as the file (`#L17`–`#L18`), and that name ends up in every
   shell's `<link>` href. Either rename the file (and update
   `tests/unit/test_console.py#L105`, `#L111`, `#L117`) or say explicitly why it stays. Also,
   `/static/` carries no cache directive of its own (`src/console/app.py#L1300`–`#L1301`,
   `#L1279`), so a stylesheet that every visitor now loads may be heuristically cached across
   deploys. Consider a version query on the href.
8. **Base branch.** The theme stylesheet exists only on the B1 branch. `git cat-file -e
   main:src/console/static/axs-theme.css` fails, and `ws28-b1-axs-console-theme` sits at
   `deploy`, held by the operator. B2 therefore cannot branch from `main` until B1 merges.
   Settlement should record the base. The plan's "currently deployed design as the default"
   (`#L10`–`#L11`) is then accurate only because the selector defaults to Default: the deployed
   console has no Skyline layer at all.
9. **Delivery record.** WS28 items 1–3 are still `⏳` (`plan/07-workstreams.md#L3647`–`#L3652`),
   which is correct because B1 is not deployed. B2 should add new `N. ⏳` item lines (4…)
   under `## WS28` and reword the heading or intro, which still reads "local console design
   study" (`plan/07-workstreams.md#L3640`). Per the jira-sync rule, rewording an existing item's
   text creates a duplicate issue, so append new item lines rather than editing the existing ones.
10. **Exemption comment.** The auth exemption's comment calls `/design/axs` "the AXS-inspired
    design study" (`src/console/app.py#L4868`–`#L4872`). Retitle it as the backward-compatible
    Skyline preview alias. The path itself must stay exempt, or the alias returns 401 when
    `A2ALAB_TOKEN` is set.

## Claim verification (held)

| Plan claim | Source | Holds? |
|---|---|---|
| A light/dark control exists to place the selector beside | `index.html#L1448`–`#L1451`; persisted under the versioned key `a2a-theme-v2`, `#L10432`–`#L10446` | yes |
| The theme CSS is already attribute-scoped and has dark variants | `axs-theme.css#L44`, `#L234`, `#L446`; scoping test `test_console.py#L116`–`#L119` | yes — the rename is mechanical |
| Reduced motion is already handled | `axs-theme.css#L720` | yes |
| A narrow-width header exists to fit into | `index.html#L1372`–`#L1378`; Skyline overrides `axs-theme.css#L684`–`#L693` | yes — a third control adds wrap pressure at ≤560px; screenshot it |
| The stylesheet loads publicly on every shell | `/static/` is an exempt prefix, `src/console/app.py#L4897`–`#L4902` | yes |
| `withLogos()` is a trusted helper, so the `<h2>` swap is safe | escapes first, then adds static SVG: `index.html#L2801`–`#L2806`; scenario titles come from `config/scenarios.yaml` | yes — no new injection surface |
| Control Panel and landing already use `withLogos(title)` | `index.html#L2707`, `#L9033` | yes |
| The heading currently uses `esc(title)`; protocol-cell titles are `view.name` | `index.html#L10193`, `#L10207` | yes |
| `.tabbar h2` styling accommodates inline marks | `.pmark`/`.plogo` are global, `index.html#L161`–`#L167`; Skyline only recolours h2, `axs-theme.css#L628`–`#L633` (no `background-clip: text` that would hide SVGs) | yes |
| No deployment-map change | same console component and route; `COPY src ./src` already ships `static/`, `deploy/console/Dockerfile#L34` | yes — needs a full rebuild, as the plan says |
| Unrelated dirty edits in `index.html` exist and must be preserved | `git diff HEAD -- src/console/static/index.html`: obs-brief copy and diagrams only (Observability Details, `OBS_HARVEST_DIAGRAM`, `INFRA_DIAGRAM`, `COST_SENTINEL_DIAGRAM`) — no overlap with the header, theme bootstrap or tab bar | yes |
| `.badge.aws` CSS can stay | still used by other surfaces (`index.html#L223`, `#L8848`) | yes — do not delete the rule with the branch |

## Evidence rerun

- `uv run pytest -q` on HEAD `111229b` with the working tree as found: **777 passed, 3 skipped,
  6 deselected** (105 deprecation warnings from the a2a/mcp libraries; no failures). This is the
  implementer's baseline.
- **Honesty skills: none apply to this batch.** `matrix-honesty-sweep` (no protocol cell or
  `targets.yaml` status changes), `insights-audit` (no `config/insights.yaml` claim) and
  `architecture-sweep` (no host, identity or dependency change) are out of scope. I checked the
  one claim they would cover, "No deployment-map change", directly: same component, same
  Dockerfile `COPY` and an exempt static prefix. `workstream-honesty` audits all of
  `plan/07` and reads the Jira board, which is broader than this batch. I checked the WS28 lines
  it would touch by hand instead (item 9 above).
