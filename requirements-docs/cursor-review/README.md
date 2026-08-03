# Cursor Project Review

## Purpose

This directory holds a **general improvement review** of the A2A Interop Lab
repository — setup, code, plan, and operations — produced from a Cursor session
exploration. It is not a requirements amendment and does not replace
`requirements-docs/codex-review/`, which reviews the formal requirements suite
against the as-built system.

| Review | Question it answers |
|---|---|
| **codex-review/** | What should change in the requirements documents given what was built? |
| **cursor-review/** | What could improve the lab itself — tooling, code structure, docs, ops? |

## Baseline

Review produced 2026-07-31 against commit `51733d8` on branch `main`.

Evidence: repository structure, `src/` seams, test suite (`403` collected,
`401` passing with `-m 'not live'` — two local env-contract failures),
`plan/` workstream status, deploy scripts, and `requirements-docs/` layout.

## Contents

| File | Theme |
|---|---|
| [01-project-improvement-review.md](01-project-improvement-review.md) | Strengths, gaps, and prioritized improvement suggestions across setup, code, plan, and operations |

## How to use

- Treat findings as **recommendations**, not tracked work items — unless copied
  into `plan/07-workstreams.md` or Jira via `scripts/jira_sync.py`.
- Re-run or extend this review after major architectural changes (new platform,
  console split, CI landing).
- Cross-check against `requirements-docs/codex-review/` when a suggestion
  touches formal REQ-* language or acceptance criteria.
