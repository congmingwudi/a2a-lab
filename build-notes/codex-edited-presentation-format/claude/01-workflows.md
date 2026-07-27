# Saved multi-agent workflows — the "ultraplan" audit pattern

**Feature area:** Claude Code `Workflow` tool, saved workflow scripts in
`.claude/workflows/`, multi-agent orchestration with structured output.

## Engineering takeaway

Use deterministic code for orchestration and models for bounded judgment. This
turns a difficult review prompt into a repeatable quality gate whose inputs,
outputs, and escalation path are inspectable.

## What exists in this repo

Two saved workflows, registered so they can be invoked by name (also surfaced as
project skills):

- `.claude/workflows/matrix-honesty-sweep.js` — cross-checks every claimed cell
  in `plan/02-matrix.md` against `config/targets.yaml`, recorded results in
  `plan/03-results.md`, and the actual code.
- `.claude/workflows/insights-audit.js` — verifies every `config/insights.yaml`
  entry: measured numbers against recorded runs, refs against the docs they
  cite, and status honesty (`measured` / `observed` / `hypothesis`).

Both are run before demos or before publishing anything from the lab.

## The pattern: Discover → Audit → Verify

Each workflow is a small JavaScript orchestration script (not TypeScript, no
Node APIs) that fans out subagents deterministically:

1. **Discover** — one agent parses the source of truth into a list of atomic,
   checkable claims (one per matrix cell / one per insight), returned as a
   **validated JSON object** via the `schema` option on `agent()`. No fragile
   text parsing — the schema is enforced at the tool-call layer, so the model
   retries until output matches.
2. **Audit** — a `pipeline()` runs **one agent per claim** in parallel, each
   cross-checking its claim against config, recorded runs, and code, returning
   a structured verdict (`consistent` / `discrepancy`) with `file:line`
   evidence refs.
3. **Verify** — every claimed discrepancy gets an **adversarial refutation
   pass**: a fresh agent is prompted to *refute* the finding, and only findings
   that survive ("would this actually mislead a reader?") make the final
   report. This kills plausible-but-wrong findings, which are the main failure
   mode of single-pass LLM audits.

## Why this keeps the project clean

The lab's core value proposition is honesty — the matrix and insights claim
nothing the lab can't back with config, measured runs, or code. Human review
drifts; a repeatable 30-agent sweep doesn't. Concrete outcome: one audit run
produced a correction set of **3 matrix fixes, 10 insight fixes, and 2 missing
records** (commit `aa471c7`), all applied before the material went into the
deck.

## Teaching points for the deck

- **Workflows vs. one big prompt:** control flow (fan-out, loops, phases) is
  deterministic script code; only the *judgment* inside each step is a model.
  You get parallelism, per-claim isolation (no context bleed between cells),
  and a resumable run.
- **Structured output via `schema`** turns subagents into typed functions:
  `agent(prompt, {schema}) → validated object`. The audit can then use ordinary
  collection operations over validated data instead of parsing prose.
- **Adversarial verification** is cheap insurance: a second, skeptical model
  pass per finding costs little and dramatically raises precision.
- **Saving the workflow** (`.claude/workflows/*.js` with a `meta` block) turns
  a one-off orchestration into a named, repeatable project ritual — "run the
  honesty sweep" is now part of the demo-prep checklist, like a lint step for
  claims instead of code.
- Deterministic checks stay deterministic: the actual experiment runner
  (`scripts/matrix.py`) is a plain script that measures and appends results.
  Workflows are used where *judgment at scale* is needed — auditing prose
  claims against evidence — not to replace scripted smoke tests.

## Evidence and limits

- **Repository-backed:** both workflow scripts and their schemas are checked
  in; commit `aa471c7` records the resulting 3 matrix fixes, 10 insight fixes,
  and 2 missing records.
- The workflow improves review coverage and consistency; it does not prove that
  every model verdict is correct. The adversarial pass raises precision, while
  the final report and cited evidence remain reviewable by a human.

## Put this in the presentation

**Slide headline:** Treat model-based review like a build pipeline, not a giant
prompt.

- Deterministic script: discover claims, fan out work, collect typed results.
- Model judgment: one isolated evidence review per claim.
- Quality control: a fresh adversarial pass must uphold each discrepancy.

**Visual:** a three-stage Discover → Audit → Verify flow, with one input claim
splitting into parallel audit nodes and only upheld findings reaching the
report. Use the workflow progress-tree screenshot when available.

<!-- TODO(ryan): drop in the screenshot of the honesty-sweep progress tree
     (the fan-out view) — it's the single best visual for this section. -->
