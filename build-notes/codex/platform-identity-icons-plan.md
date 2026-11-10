# WS28 B3 — complete platform identity in experiment and protocol headings

## Outcome

Make the main canvas identify every participating platform at a glance without changing
experiment behavior or turning runtime/hosting metadata into decorative chips. This follow-up
extends D98's title-mark rule in three places the first pass could not infer from title text.

## Proposed changes

1. **Declare non-textual scenario identity and fan-out units separately.** Add two optional,
   display-only fields with deliberately different semantics in `config/scenarios.yaml`:

   - `title_identity` on `account-brief-async`, with trusted ids/roles for `claude` as the Managed
     Agents researcher and `agentforce` as the Salesforce delivery org. “Account Intelligence
     Brief (async)” therefore shows both marks without falsely saying an Agentforce agent is called.
   - `fanout_platforms: [adk, foundry, openai]` on all three `supplier-disruption-*` scenarios, the
     three fanned-out business-unit platforms named by their shared runtime definition. Render
     these as a compact “Fan-out units” cluster after the title. The existing named orchestrator
     mark remains independently visible.

   Keep the values aligned with `config/targets.yaml` platform ids rather than introducing a
   second logo vocabulary. Do not derive participation from tags: tags also contain products,
   protocols, dispatch modes, and gates, so they are not a reliable title contract.

2. **Centralize platform-id-to-mark rendering.** In `src/console/static/index.html`, hoist one
   `PLATFORM_MARK` map beside the existing trusted SVGs and replace the divergent local Registry
   and warm-up maps with it. Cover every target-registry id explicitly: `claude → anthropic`,
   `agentforce → salesforce`, `openai → openai`, `adk → gcloud`, `foundry → microsoft`,
   `strands → aws`, `langgraph → langgraph`, `guide → aws` (preserving the Registry's current
   hosting identity), and `mulesoft → null` (deliberately text-only; a new mark is outside scope).
   Keep the warm-up shim override local because it describes a different endpoint. Add helpers that:

   - start from escaped title text through `withLogos()`;
   - render identity and fan-out clusters as separate semantic groups;
   - never de-duplicate a cluster against title-text marks (the ADK orchestrator must still show
     all three fan-out marks), and de-duplicate only within one cluster by resolved mark key;
   - emits no untrusted markup from YAML/target values; and
   - give each icon-only group trusted `role="img"`/`aria-label` and hover text sourced from the
     platform map, never arbitrary YAML. Unknown ids emit no icon or attribute.

   Add minimal base/Skyline cluster and accessibility CSS. Use the scenario helper for Control
   Panel cards, landing tiles, and main-canvas headings. Keep Agent Registry's experiment list
   intentionally title-text-only: it is a relationship list from `/api/agents`, not an experiment
   execution surface, and its payload does not carry scenario display metadata.

3. **Identify protocol-cell titles from target metadata.** Here “protocol title” means the selected
   target/cell title (`claude-rest`, `agentforce-a2a`, etc.), not the REST/MCP/A2A badge. Decorate
   both the Control Panel `renderTargets` row and main-canvas heading with the mark resolved only
   from `t.platform`, then keep the target name escaped verbatim. Never infer from substrings:
   `agentforce-openai-rest` receives Salesforce, not OpenAI. Guide preserves its AWS hosting mark;
   MuleSoft and unknown ids remain text-only. Escape the protocol badge while touching the row.

4. **Remove the redundant Strands location suffix.** Rename only the forward scenario title from
   `AWS Strands (AWS) → Agentforce` to `AWS Strands → Agentforce`. The AWS mark and “AWS Strands”
   already identify the vendor; tags, descriptions, target ids, AgentCore hosting, and the reverse
   title remain untouched because the user's explicit request named only the forward experiment;
   that narrow asymmetry is a scope choice, not a claim that the reverse suffix is nonredundant.
   Settlement may broaden the rule to both directions. Either choice intentionally revises D98's
   statement that configured `(AWS)` text is retained, so it needs a new ADR (or D98 amendment).

## Tests and evidence

- Add Python config-contract tests asserting every target platform id has an explicit mark-map
  entry (including null), every configured identity/fan-out id exists in `config/targets.yaml`, and
  every Supplier Disruption fan-out set equals both `{leg.platform for leg in LEG_AGENTS}` and its
  non-orchestrator `agents[].target` platform set.
- Update the existing static console assertions (including `tests/unit/test_console.py:157`) to
  prove that Control Panel cards, landing tiles, and main headings call the one scenario helper;
  Control Panel target rows and cell headings call the one platform helper; trusted labels and
  escaping remain in place; and the old scenario-vs-cell heading expression is gone.
- Assert the ADK supplier variant retains its title-text orchestrator mark plus a separate complete
  three-mark fan-out cluster, Account Brief carries its two role-labelled identity marks, and the
  requested Strands title omits `(AWS)`.
- Before implementation, run `uv sync --all-extras` and `uv run pytest` as the executable baseline.
- After implementation, run the focused tests, `uv run pytest`, `uv run ruff check src tests
  scripts`, and `git diff --check`.
- Restart the existing local console and capture Default/Skyline screenshots of the Account Brief,
  one Supplier Disruption experiment, the Strands experiment, and representative protocol cells.
  Screenshots are the executed-browser behavioral evidence; confirm narrow wrapping, light/dark
  legibility, tooltip text, and accessible group labels. Adding Node to the project is out of scope.

## Documentation and delivery

- Record the settled title-participant and redundant-suffix rule in a new ADR following D98.
- Add pending WS28 item lines before implementation; flip them only after Claude cross-review and
  the operator's local acceptance/deploy station.
- This is a `config/` + `src/` console-image change. Local validation requires a console restart;
  any later hosted publication requires a full console image rebuild, never `--skip-build`.
- No deployment-map or architecture-diagram change is required because component topology and
  hosting are unchanged.

## Scope boundaries

- No changes to experiment flows, targets, protocols, tags, runtime selection, or observability.
- No download or network loading of brand assets; reuse the existing trusted inline marks.
- No attempt to infer logos from arbitrary prose or target-name substrings.
- The shared `agentcore` tooltip defect remains the already-recorded WS28 item 7.
