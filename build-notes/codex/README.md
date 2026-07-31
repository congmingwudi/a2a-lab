# How this lab uses Codex — build notes

These notes document Codex-specific engineering in the A2A Interop Lab: local
skills, repeatable review workflows, scheduling, telemetry, permissions, and
other Codex surfaces used while building the project.

Audience: engineers and engineering leaders evaluating coding agents. Each note
pairs the Codex-specific setup with the reusable engineering pattern it
demonstrates.

## How to read a note

Same convention as the Claude build notes:

- **Engineering takeaway** — the one-line thesis.
- **The body** — what is configured here, with concrete paths and prompts.
- **Evidence and limits** — what is repository-backed, locally observed, or
  vendor-documented.
- **Put this in the presentation** — the smallest useful slide that teaches the
  pattern.

## Contents

| File | Theme |
|---|---|
| [01-plan-requirements-sweep.md](01-plan-requirements-sweep.md) | A repo-scoped Codex skill that turns the comprehensive plan-to-requirements review into an ad hoc, repeatable sweep, plus the optional path to a scheduled task |
