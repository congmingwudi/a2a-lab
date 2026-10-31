# WS28 B3 — complete platform identity in experiment and protocol headings

## Outcome

Make the main canvas identify every participating platform at a glance without changing
experiment behavior or turning runtime/hosting metadata into decorative chips. This follow-up
extends D98's title-mark rule in three places the first pass could not infer from title text.

## Proposed changes

1. **Declare non-textual scenario participants in configuration.** Add an optional, display-only
   `title_platforms` list to the four affected records in `config/scenarios.yaml`:

   - `account-brief-async`: `claude`, `agentforce`, so “Account Intelligence Brief (async)” shows
     both the Anthropic and Salesforce marks even though neither platform name is in the title.
   - all three `supplier-disruption-*` scenarios: `adk`, `foundry`, `openai`, the three fanned-out
     business-unit platforms named by their shared scenario definition. Render these as a compact
     participant-mark cluster after the title, with an accessible label/tooltip explaining that
     they are fan-out participants. The title's existing named orchestrator mark remains in place.

   Keep the values aligned with `config/targets.yaml` platform ids rather than introducing a
   second logo vocabulary. Do not derive participation from tags: tags also contain products,
   protocols, dispatch modes, and gates, so they are not a reliable title contract.

2. **Centralize platform-id-to-mark rendering.** In `src/console/static/index.html`, add an explicit
   platform-id map (`claude`, `agentforce`, `openai`, `adk`, `foundry`, `strands`, `langgraph`) to
   the existing trusted inline SVG keys. Add one helper that:

   - starts from escaped title text through `withLogos()`;
   - appends only configured participant marks not already represented where de-duplication is
     appropriate;
   - emits no untrusted markup from YAML/target values; and
   - gives icon-only marks accessible text and hover context.

   Use the helper for both Control Panel scenario cards and main-canvas scenario headings so the
   two surfaces cannot drift again. Do not change tag chips or runtime metadata.

3. **Identify protocol-cell headings from target metadata.** Protocol titles currently render raw
   `view.name` through `esc()` in the main canvas. Prefix or decorate the heading with the mark
   resolved from the selected target's existing `platform` field, then keep the target name
   escaped verbatim. Unknown/non-platform targets such as the guide remain text-only rather than
   receiving a guessed logo. This covers each relevant REST/MCP/A2A/native protocol title without
   renaming target ids or changing protocol semantics.

4. **Remove the redundant Strands location suffix.** Rename only the forward scenario title from
   `AWS Strands (AWS) → Agentforce` to `AWS Strands → Agentforce`. The AWS mark and “AWS Strands”
   already identify the vendor; tags, descriptions, target ids, AgentCore hosting, and the reverse
   title remain untouched unless the operator explicitly broadens settlement. This intentionally
   revises D98's statement that configured `(AWS)` text is retained, so settlement needs a new ADR
   (or an explicit D98 amendment) rather than treating the edit as cosmetic implementation detail.

## Tests and evidence

- Add focused console regressions for:
  - Account Intelligence Brief carrying exactly the Claude and Salesforce title marks;
  - each Supplier Disruption main heading carrying the Google Cloud, Microsoft, and OpenAI
    fan-out participant marks plus its textual orchestrator identity;
  - the forward Strands title omitting `(AWS)` while preserving `AWS Strands → Agentforce`;
  - protocol cells mapping every known target platform id to the intended mark, escaping the
    target name, and leaving unknown platforms unmarked;
  - no duplicate title marks and no regression to Control Panel/main-canvas parity.
- Before implementation, run `uv sync --all-extras` and `uv run pytest` as the executable baseline.
- After implementation, run the focused tests, `uv run pytest`, `uv run ruff check src tests
  scripts`, and `git diff --check`.
- Restart the existing local console and capture Default/Skyline screenshots of the Account Brief,
  one Supplier Disruption experiment, the Strands experiment, and representative protocol cells.
  Confirm narrow layout wrapping and light/dark legibility.

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
