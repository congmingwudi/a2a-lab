---
name: handoff
description: Use when picking up, reviewing, answering, or handing off a batch of work between coding agents in this repo (Claude Code, Codex, Cursor, Kiro) — "whose move is it", "review the other agent's batch", "answer the verdict", "start the next batch", or when a batch name like ws25-b1 is mentioned
---

# Handoff — the baton between coding agents

The joint loop (D79, `plan/16-joint-agent-workflow.md`) has five stations:
propose → critique → settle → implement → review ⇄ respond → deploy → done.
`scripts/handoff.py` owns the state (`build-notes/handoffs/<batch>.json`) and
refuses a move the caller does not hold. You never paste a handoff between
terminals: you run `status` and it tells you your move and your files.

## Who you are

Pass your own name as `--agent`: `claude` in Claude Code, `codex` in Codex,
`cursor`, `kiro`. Your verdicts and implementer notes go under
`build-notes/<you>/reviews/<batch>.md`. Unknown name → the script lists the
known ones; add a row to `AGENTS` in the script to register a new agent.

## Every time

```sh
python3 scripts/handoff.py status <batch> --agent <you>
```

Read the files it names, do the station's work, then run the verb it prints.
Do not do the other agent's station; do not advance the state by hand.

## Verbs (quick reference)

| When | Run |
|---|---|
| any time | `handoff status <batch> --agent <you>` |
| you wrote a plan | `handoff propose <batch> --agent <you> --title "…" --file <plan.md>` |
| you wrote a critique or verdict | `handoff review <batch> --agent <you> --verdict pass\|request-changes --file <verdict.md>` |
| you finished implementing | `handoff built <batch> --agent <you> --file <notes.md> --commit <sha>` |
| you answered a verdict | `handoff respond <batch> --agent <you> --commit <sha>` |

`handoff` = `python3 scripts/handoff.py`. Commit your file AND
`build-notes/handoffs/<batch>.json` in the same commit — the state is only a
handoff once it is pushed.

## Your own evidence, by agent

| Agent | Rerun before a verdict |
|---|---|
| codex | `python3 -B build-notes/codex/codebase-review/readiness_probes.py` (a probe that asserts an OLD defect goes red when it is fixed — say so, do not "fix" the probe) and `python3 -B -S build-notes/codex/codebase-review/eval-harness/run.py --case 'regression.*'` |
| claude | `uv run pytest -q` plus the honesty skills that fit the batch (`matrix-honesty-sweep`, `insights-audit`, `workstream-honesty`, `architecture-sweep`) |
| cursor / kiro | `uv run pytest -q`; add your own probes under `build-notes/<you>/` and list them here |

## The contracts the script checks

- A verdict file's first line is `**Verdict: PASS**` or
  `**Verdict: REQUEST CHANGES**`; later rounds append `## Round <n>`.
- A response appends a table `finding | fixed / disputed (evidence) / deferred
  to D<n> | what changed` to the implementer's notes file. Every finding gets
  a row; a test is never weakened to keep a report green.
- A review reruns the reviewer's OWN evidence (Codex: its probes and harness;
  Claude: `uv run pytest` and the honesty skills). Reading a diff is an
  opinion; rerunning something is evidence.
- Before implementing, load `CLAUDE.md` (the definition of done) and
  `AGENTS.md`; run `uv sync --all-extras && uv run pytest` for a baseline and
  record its failures in the notes.

## Operator-only verbs

`settle` (scope decided, ADR written) and `deployed` (runbook run, smoke and
scan results recorded) are the operator's; an agent does not run them.
