**Verdict: REQUEST CHANGES**

# WS28 B3 critique — `ws28-b3-platform-identity-icons` (Claude, critique station, round 0)

Plan reviewed: `build-notes/codex/platform-identity-icons-plan.md`. Every claim below was checked
against source on branch `ws28-b1-axs-console-theme-local`. The review edited no implementation
files.

## Reviewer evidence (rerun)

- `uv run pytest -q` → **778 passed, 3 skipped, 6 deselected, 105 warnings in 92.21s, exit 0.**
- The baseline includes unrelated uncommitted work. The implementer must keep it out of B3.
- Honesty skills were not run: this batch does not touch matrix cells, insight tiers, or deployment
  topology. `workstream-honesty` fits only after settlement adds item lines.

## Claims verified correct

| Claim | Source | Result |
|---|---|---|
| Account Intelligence Brief is configured without platform names | `config/scenarios.yaml:339` | Correct; its group and tags identify Claude/Agentforce. |
| All three supplier scenarios use ADK, Foundry, and OpenAI units | `config/scenarios.yaml:1478,1671,1819`; `src/orchestration/agents.py:63-107` | Correct. |
| Tags are not a reliable participant contract | `config/scenarios.yaml:1536,1846` | Correct; they also contain modes and products. |
| Cell headings currently escape raw `view.name` | `src/console/static/index.html:10209,10223` | Correct. |
| `withLogos()` escapes before trusted SVG insertion | `src/console/static/index.html:2819-2822` | Correct. |
| Removing the Strands `(AWS)` suffix contradicts D98 | `plan/00-decisions.md` D98; `config/scenarios.yaml:565` | Correct; a new ADR/amendment is needed. |
| `/api/scenarios` passes new YAML keys through | `src/console/app.py:1396-1412` | Correct. |

## Blocking findings

### B1. One field is given two meanings

The plan uses `title_platforms` for Account Brief experiment identity and Supplier Disruption
fan-out participants. A shared “fan-out participants” label would be false for Account Brief:
Claude writes into Salesforce, and the Agentforce agent consumes but is not called during the run
(`config/scenarios.yaml:371-424`). Use role-bearing metadata or separate identity and fan-out
fields. Account Brief accessible text must identify Claude Managed Agents and Salesforce as the
delivery org, not call Salesforce an Agentforce participant.

### B2. The fan-out list needs a drift guard

The runtime list is already `LEG_AGENTS` (`src/orchestration/agents.py:107`) and scenario
`agents[].target` lists also name the units. Keep explicit display configuration, but add a Python
contract test asserting: every configured platform exists in `config/targets.yaml`; every supplier
set equals `{leg.platform for leg in LEG_AGENTS}`; and it equals each scenario's non-orchestrator
agent-target platform set.

### B3. De-duplication semantics are undefined

The ADK orchestrator title already carries a Google Cloud mark, while its Logistics unit also uses
Google Cloud. Never de-duplicate participant marks against title-text marks, or that scenario will
look like a two-unit fan-out. De-duplicate only inside an icon cluster and by mark key. Test the ADK
variant showing its title orchestrator mark plus all three unit marks.

### B4. Existing platform maps disagree

`registryTile` has a local map (`index.html:7883-7885`) and `WARM_LOGO` has another
(`index.html:8870-8871`). Hoist one `PLATFORM_MARK` beside `PLATFORM_LOGOS`, reuse it in those
surfaces, and retain only genuine local exceptions. Explicitly decide whether `guide` is text-only
or AWS-marked and record that in the ADR.

### B5. The platform inventory is incomplete

Targets use nine ids: `agentforce`, `claude`, `openai`, `strands`, `guide`, `langgraph`, `foundry`,
`adk`, and `mulesoft`. The plan omitted `guide` and `mulesoft`. List every id with a mark or an
explicit text-only/null entry; keep MuleSoft text-only unless the operator expands scope. Add a
test requiring every configured platform id to have a map entry.

### B6. Protocol-cell parity is incomplete

The plan decorates only the main heading, while `renderTargets` exposes platform only in a hover
attribute (`index.html:2885-2893`). Confirm that “protocol titles” means target/cell titles, not
protocol badges, and apply the same platform helper to both the Control Panel row and heading.
Resolve only from `t.platform`, never substrings: `agentforce-openai-rest` must receive Salesforce.

### B7. Landing tiles are a third scenario surface

The landing tile calls `withLogos()` at `index.html:9049`. Route cards, landing tiles, and the main
heading through the same scenario-title helper. Either pass display metadata into Agent Registry's
experiment list or explicitly keep that list title-text-only.

### B8. Icon-only marks need an accessible name

All SVGs are `aria-hidden`. Wrap icon-only groups with `role="img"` or `role="group"` and trusted
`aria-label` text describing their role; add a visually-hidden utility if needed and retain a
tooltip for sighted users. Labels must come from a trusted map, not arbitrary YAML. Unknown ids
must produce no output or attribute. Style the group for Default and Skyline.

### B9. Tests need explicit mechanisms

Existing tests are static assertions and `tests/unit/test_console.py:157` pins the exact current
heading expression. Specify Python config/contract tests, static source assertions that all
surfaces call the single helper, and screenshots as behavioral evidence. Update rather than delete
the existing assertion. Executed-JS testing would be a separate toolchain decision.

### B10. Forward-only Strands rename is inconsistent

The redundancy argument applies equally to `Agentforce → AWS Strands (AWS)`
(`config/scenarios.yaml:655`). Cross-cloud Strands titles already omit the suffix. The ADR should
drop `(AWS)` from both Strands pair titles while retaining host qualifiers where the platform name
does not identify that host, such as `Claude (AWS)` and `OpenAI (AWS)`. If forward-only is truly
desired, the ADR must explain the deliberate asymmetry.

## Suggestions (non-blocking)

- Escape `t.protocol` in `renderTargets` while that line is touched.
- Keep the shared `agentcore` tooltip defect scoped to existing WS28 item 7 and mention it in visual
  evidence so it is not mistaken for a B3 regression.
- The Account Brief group heading already provides identity in the Control Panel; the main heading
  is where the new marks add the most information.
- A config contract test may import `src/orchestration/agents.py`; no runtime Dockerfile change is
  needed.

## Required corrections before settlement

1. Make icon-cluster semantics role-bearing and describe Account Brief accurately.
2. Add fan-out and target-platform drift contract tests.
3. Define cluster-only mark-key de-duplication.
4. Consolidate one complete platform-to-mark map and decide Guide behavior.
5. Cover all nine platform ids, with MuleSoft explicitly text-only.
6. Confirm target/cell title meaning and decorate Control Panel plus main heading.
7. Include landing tiles and decide Agent Registry behavior.
8. Add accessible labels for icon-only marks.
9. Name each test mechanism and preserve the existing heading assertion's intent.
10. Settle the suffix rule for both Strands directions.

## Round 1

**Verdict: REQUEST CHANGES**

Cross-review of implementation commit `d64b911` against parent `e061abd`; `299c9b3` only moves
handoff state. The review graded the committed diff and edited no repository files.

### Reviewer evidence

| Command | Tree | Result |
|---|---|---|
| proxy-free `uv run pytest -q` | main checkout | **779 passed, 3 skipped, 6 deselected, 105 warnings in 90.00s** |
| same command with `--no-sync` | clean detached `d64b911` worktree | **777 passed, 5 skipped, 6 deselected**; the two additional skips are gitignored `.env` checks |
| `pytest -q tests/unit/test_console.py` | clean `d64b911` | **94 passed** |
| `uv run ruff check src tests scripts` | committed diff | **All checks passed** |
| `git diff d64b911^ d64b911 --check` | committed diff | clean |

The optional `workstream-honesty` skill is not installed in `.claude/skills`; D99 and WS28 items
8–10 were audited manually. The matrix, insights and architecture honesty passes do not apply.

### Blocking finding R1-B1 — scenario spacing is stripped by the target-heading reset

`src/console/static/index.html:179` resets margin, padding and border for
`.tabbar h2 > .title-icons:first-child`. It intends to target protocol-cell headings, where
`targetTitleHtml` puts the icon before the name. CSS `:first-child` ignores text nodes, however, so
the generic Account Brief title's identity group is also its first element child. In Default the
cluster loses its 8px gap/separator; in Skyline the more-specific reset beats the theme pill rule
and removes left padding/border. The committed Account Brief screenshot shows the Anthropic mark
touching `)` and the missing pill edge.

Required fix: give the leading target group an explicit modifier class from `targetTitleHtml` and
scope the reset to that modifier. Recapture Account Brief in Default and Skyline.

### Verified correct

- Config drift tests pin Account Brief roles, all three runtime `LEG_AGENTS`, scenario agent-card
  platforms and all nine target platform ids.
- Target names/protocols are escaped; label text comes only from trusted JS maps, YAML roles are
  lookup keys, and `hasOwnProperty` rejects prototype keys.
- Icon-only groups have correct `role="img"`, `aria-label` and title semantics.
- Control Panel cards, landing tiles and main headings share `scenarioTitleHtml`; target rows and
  cell headings share `targetTitleHtml` based only on `t.platform`.
- Registry and warm-up rows reuse the complete map; Guide remains AWS and MuleSoft is text-only.
- Account Brief roles are accurate, ADK keeps its orchestrator mark plus all three fan-out marks,
  and the forward-only Strands rename matches D99 exactly.
- The implementation commit contains only B3 `index.html` hunks; unrelated dirty work remains
  outside it. WS28 items 8–10 honestly remain pending.

### Non-blocking suggestions

1. Preserve the old `view.name` heading fallback when the experiment lookup is temporarily absent.
2. Guard trusted attribute-label literals against future quote characters.
3. Anchor platform-map tests to the actual map block and assert Registry/warm-up reuse it.
4. Capture Skyline target rows/cards while recapturing the Account Brief evidence.
5. Preserve a plain-text platform hover for text-only MuleSoft targets.
