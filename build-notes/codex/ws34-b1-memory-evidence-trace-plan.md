# WS34 batch 1 — honest memory reset evidence and Agentforce trace drill-down

**Proposer:** Codex  
**Implementer:** Codex  
**Critic / cross-reviewer:** Claude Code  
**Requested scope:** the operator's 2026-11-14 customer-memory reset, evidence, and Session Trace requests

## Problem statement

Three presentation contracts currently disagree with the behavior behind them.

1. `POST /api/memory/customer/{customer}` does issue permanent Memory Management deletes, but
   Data 360 can immediately re-materialize profile facts/preferences from retained conversation
   history. D107 deliberately quarantines those rebuilt Profile rows after generation rotation,
   yet the management panel puts them back in the same undifferentiated list and says Start fresh
   deletes the listing. A successful isolation boundary therefore looks like a failed reset.
2. The generic memory adapter supplies the model a bounded context of thousands of characters,
   then returns only the first 360 redacted characters as `context_preview`. The console labels
   that fragment “Data 360 memory supplied to this agent” without a truncation marker. A real
   Customer360/POS record currently stops mid-token at `A2ALab_POS_Order[` even though Claude saw
   the rest.
3. The live Agentforce Session Trace table flattens each OTLP span to name/type/start/status/summary.
   The complete `resourceSpans` document is retained below the table, but connecting a cryptic row
   such as `memory_turn`, `pre_orchestration.guardrail`, or `InstructionAdherence` to its attributes,
   parent, timing, and raw payload requires manually searching the full JSON.

This is evidence integrity and explainability work. It does not change the Agentic Memory storage
model, invoke extra deletion loops, alter the Agentforce agent, or replace the raw OTLP payload.

## Proposed implementation

### 1. Make Start fresh report deletion and quarantine as different outcomes

**Files:** `src/console/app.py`, `src/console/static/index.html`,
`config/scenarios.yaml`, `tests/unit/test_console.py`

- Preserve the complete governed `memories` listing in the server contract so operators can still
  select and delete a re-materialized row.
- Add explicit reset evidence to the management response: the number of semantic rows targeted,
  the number whose original IDs are absent after verification, and the current number of profile
  rows quarantined by the active generation. Do not infer that a new ID is the same entry; call it
  a post-reset profile row, not a failed delete or successful purge.
- When `profile_memory_suppressed` is true, render the listing under a **Quarantined profile rows**
  heading and state that these rows are visible for governance but are not supplied to Claude,
  OpenAI, or Agentforce. Keep selection and `Delete selected` available.
- Rename the action to **Start fresh generation**. Its confirmation says it deletes the currently
  listed profile-memory IDs, creates a new conversation generation, and does not erase retained
  conversation history; Data 360 may rebuild quarantined profile rows later.
- After the mutation, render an operation result that distinguishes “listed IDs deleted” from
  “post-reset rows quarantined.” Never claim the complete management listing will remain empty.
- Update all three customer-memory Details descriptions because the operation affects the shared
  registration/owner/generation regardless of which card opens the manager. Cite D107 and the
  official Memory Management behavior: API writes and automatic extraction share the same scope,
  and the pipeline can run again. State that the documented memory DELETE addresses profile memory
  IDs, not the retained conversation summaries that feed extraction.

### 2. Show the complete redacted context actually supplied to generic agents

**Files:** `src/interop/memory_adapter.py`, `src/console/static/index.html`,
`tests/unit/test_memory_adapter.py`, `tests/unit/test_console.py`

- Add a response field carrying `redact(context)` in full after the existing prompt-side bounds,
  owner-id masking, profile-id masking, and generation filtering. Keep `context_chars` as the
  length of the actual supplied context. Retain the old 360-character preview only as a
  compatibility field during this batch; the console prefers the complete field.
- Label the card **Complete redacted Data 360 context supplied to this agent** and show the supplied
  character count. The full value is already bounded by the same renderer that built the model
  prompt; this does not expose the raw unmasked wire payload.
- Add a regression with a POS-bearing context whose meaningful tail occurs past character 360.
  Assert the model request and browser evidence both contain the tail and that the old preview is
  explicitly identifiable as a preview if it is ever used as fallback.
- This item applies to the generic Claude/OpenAI decorator. Agentforce's Apex→bridge path continues
  to use joined trace evidence and must not falsely claim a returned final-reply context payload.

### 3. Add an Explain drill-down for every Agentforce OTEL span row

**Files:** `src/console/app.py`, `src/console/static/index.html`,
`src/observability/salesforce_otel_source.py`, `tests/unit/test_console.py`,
`tests/unit/test_salesforce_otel_source.py`

- Centralize live-span parsing in the OTel source instead of maintaining a second parser in the
  console route. The parsed row includes span/parent IDs, start/end, duration, status, flattened
  attributes, events, links, and the exact raw span object. The full session document remains in
  `raw` unchanged.
- Add an **inspect** column. Each row gets a keyboard-reachable **Explain** chip keyed by span ID.
  Opening it uses the console's established accessible modal pattern and shows:
  - a concise plain-language interpretation derived from an allow-listed glossary of observed
    Agentforce span names/types;
  - whether it is orchestration, a guardrail/evaluation, the deterministic customer-memory hook,
    or an otherwise-unclassified platform span;
  - parent/child linkage, UTC start/end, duration, status, and useful attributes;
  - the exact raw JSON for that span.
- The glossary must be honest and extensible. Known names such as `memory_turn`,
  `customer_memory`, `pre_orchestration.guardrail`, `InstructionAdherence`,
  `Atlas__GroundednessValidationPrompt`, and `__state_update_action__` receive descriptions.
  Unknown names say “Salesforce emitted this span; no lab interpretation is registered” and still
  expose all structured/raw evidence. A name must never be used to invent success, recall, or
  policy outcome; those claims come only from attributes/status/payload.
- Extend the Session Trace Details pane to explain span/parent structure, orchestration spans,
  deterministic memory-hook spans, evaluation/guardrail spans, the distinction between a span's
  interpretation and its payload, and the continued authority of the raw OTLP document. Cite D73,
  D121, and `plan/05-observability.md`.

## Tests first

1. Console management tests pin the new reset evidence, quarantined rendering/copy, permanent
   selection deletion, and retained-history caveat.
2. Adapter tests reproduce the 360-character cutoff with a tail marker and require the complete
   redacted evidence field to equal the context actually supplied to the inner agent.
3. OTel parser tests pin duration, parent ID, flattened nested attributes, events/links, and exact
   raw-span preservation.
4. Console tests pin the Explain column/chip, known and unknown glossary behavior, accessible
   modal/close controls, escaped attributes/raw JSON, and Details documentation.
5. Run focused suites, then `uv run ruff check src tests scripts` and `uv run pytest -q`.

## Documentation and delivery

- Settle a new D122 and WS34 with item lines for the three implementation items, verification,
  cross-review, full console/faces rebuild, and hosted smoke evidence.
- `src/interop/memory_adapter.py` changes require a full **faces** rebuild. Console Python/static,
  config, and plan changes require a full **console** rebuild. `--skip-build` is invalid for both.
- `plan/09-deployment-map.md` needs no topology diagram change, but its L6 console/faces rows and
  relevant narrative must mention complete redacted evidence and per-span OTel drill-down.
- Hosted smoke: reset Maya; verify the original IDs are reported deleted and any rebuilt rows are
  visibly quarantined; run Teach Claude and confirm the evidence reaches the POS record tail; run
  Agentforce recall, fetch its OTel trace after ingestion, open Explain on `memory_turn`, a
  guardrail/evaluation span, and an unknown/generic span, and confirm the full raw session remains.

## Explicit non-goals

- No whole-registration deletion: that would erase every customer's memory for all three agents.
- No claim that the Agentic Memory API erases retained conversation history or implements a
  right-to-be-forgotten workflow.
- No repeated delete-until-empty loop against an extraction pipeline that can run again.
- No semantic success/recall inference from span names alone.
- No removal, truncation, or replacement of the raw OTLP session document.
