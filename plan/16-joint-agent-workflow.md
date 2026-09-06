# Joint agent workflow — Claude Code and Codex on one codebase (2026-09-06)

How this lab uses two coding agents on the same repository without them
undoing each other: who plans, who builds, who reviews, which model does which
kind of work, and where every artifact of that loop lives. Decided in D79 (the
process) and first applied to WS25 (the Codex codebase-review response, scoped
in D80). This file is the runbook; the ADRs hold the reasoning.

## The loop

Every batch of work goes through five stations. A batch does not skip one.

| Station | Who | Output (checked in) |
|---|---|---|
| 1. Propose | the agent that found the issue writes the plan | `build-notes/<agent>/…-plan.md` — per item: file, fix, test, deploy |
| 2. Critique | the OTHER agent, with repo access, verifies every claim against source | `build-notes/<agent>/…-assessment.md` — corrections with `file#Lnn` cites |
| 3. Settle | the operator decides scope; an ADR records it | `plan/00-decisions.md` D<n> + a `## WS<n>` with `N. ⏳` item lines in `plan/07-workstreams.md` |
| 4. Implement | one agent, one branch per batch, tests first | branch `ws<n>-b<k>-<slug>`; `uv run pytest` + `ruff` green; the CLAUDE.md done-definition met |
| 5. Cross-review | the other agent reviews the diff, reruns its own probes, writes a verdict | `build-notes/<agent>/reviews/ws<n>-b<k>.md`; item line flips `⏳ → ✅` only after the verdict and the deploy |

The baton is code, not memory: `python3 scripts/handoff.py status <batch>
--agent <you>` tells any agent whether it is its move and which files to read,
and the verbs (`propose`, `review`, `built`, `respond`; operator-only `settle`,
`deployed`) refuse a move the caller does not hold. Every agent loads the same
`handoff` skill (see "Where things live").

Two rules make the loop honest:

- **No code before station 3.** The scope split (fix vs accept) is the
  operator's decision, and it is recorded as an ADR before an implementer opens
  a file. Otherwise the implementer inherits whichever plan it read last.
- **Findings are answered, never silenced.** The implementer replies to every
  review finding with one of *fixed*, *disputed (with evidence)*, or *deferred
  to D<n>*. A test is never weakened to keep a report green; a probe that
  asserted the OLD behaviour (Codex's `readiness_probes.py` asserts secrets
  survive) is expected to go red when the defect is fixed, and stays as the
  historical reproduction.

## Who implements, who reviews

**Default: Claude Code implements, Codex reviews.** Not because one model is the
better coder — there is no repo-specific evidence either way — but because of
harness reach: this Claude Code session holds the Salesforce DX MCP server
(`.mcp.json`), the AWS SSO session the deploy preflight needs, and CLAUDE.md
loaded automatically. Codex's review of the same repo could not run `uv run
pytest` (no environment), which is why its harness is stdlib-only. The
implementer must be the agent that can run the suite and the deploys.

**Swap when the reach swaps.** A Codex environment with `uv sync`, `aws sso
login` and the DX CLI working is an equally valid implementer, and the roles
invert: Codex builds, Claude Code reviews. The station table does not change.

**The reviewer always has its own evidence.** Codex reviews with
`readiness_probes.py` and the eval harness under
`build-notes/codex/codebase-review/eval-harness/`; Claude reviews with the pytest
suite plus the four honesty skills (`matrix-honesty-sweep`, `insights-audit`,
`workstream-honesty`, `architecture-sweep`). A review that only reads the diff
is an opinion; a review that reruns something is evidence.

## Which model for which work

Prices are first-party API rates per million tokens, input/output, as documented
on 2026-09-06 by both vendors: Opus 5 $5/$25, Fable 5.1 $10/$50, Sonnet 5
$2/$10; GPT-6 Astra $10/$50, GPT-5.6 Sol $4/$20. Fable 5.1 needs 30-day data
retention on the workspace. Effort starts at `high` and is raised only where the
item is a judgment call.

| Work | Claude | OpenAI | Effort | Why |
|---|---|---|---|---|
| Mechanical: packaging lists, test ports, resolved doc edits | Sonnet 5, or the running Opus 5 session | GPT-5.6 Sol | medium/high | A model switch costs a context handoff; do not switch for a small saving |
| Security seams: auth, redaction, fail-closed config (WS25 batch 1) | Opus 5 | GPT-6 Astra | xhigh | Adversarial cases matter more than volume; the OTHER agent reviews the design before the edit |
| CRM writes, idempotency, session ownership (WS25 batch 2) | Opus 5; Fable 5.1 if Opus at xhigh still misses | GPT-6 Astra | xhigh | Concurrency and partial-delivery reasoning; a production org is on the other end |
| Atomic claims, absolute deadlines (WS25 batch 3) | Opus 5; Fable 5.1 as above | GPT-6 Astra | xhigh | Real database semantics must be verified, not a fake that honours the proposed SQL |
| Observability completeness, evidence grading (WS25 batch 4) | Opus 5 | GPT-5.6 Sol, Astra for ambiguous semantics | high | Denominators and error semantics need a strong review more than a strong writer |
| Accepted-risk reasoning, ADR argument (WS25 batch 5) | Fable 5.1 or Opus 5 | GPT-6 Astra | high | The substantive risk decisions are settled FIRST (station 3); formatting them does not need a premium model |

Fable 5.1 is not reserved for prose: it is documented for long-running coding as
well. The rule is escalation — reach for it when Opus 5 at `xhigh` still falls
short on an item — not a fixed split by content type.

## Context each agent must load

- **Both instruction files.** `CLAUDE.md` carries the done-definition (ADR, WS
  item lines, Details panes, deployment map, Dockerfile `COPY`, full-rebuild vs
  `--skip-build`); `AGENTS.md` is the orientation guide and points at it. An
  implementer that has read only one produces code that passes tests and still
  fails the repo's definition of done.
- **The operating model.** NFR-201 / X8 in `requirements-docs/50-system/` exclude
  production; the lab's org serves demo data (an operating assumption the
  operator owns, not a fact source inspection can establish — say so when citing
  it). A review that grades against production readiness is still useful; the
  scope split is the operator's, made at station 3.
- **The ADRs the item touches.** `grep -n "D<n>"` before proposing a change that
  contradicts one; D27 (delegation guard), D36/D37 (roles, identity, redaction
  policy), D46 (who can ALTER Aurora), D57 (console canvas) recur.
- **An executable baseline.** `uv sync --all-extras` then `uv run pytest`,
  recording failures and skips, before the first edit. Another session's
  working AWS or Salesforce access does not transfer.

## Where things live

| Artifact | Path | Why there |
|---|---|---|
| The process (this file) | `plan/16-joint-agent-workflow.md` | `plan/` is the source of truth; the console serves it via `/api/docs` |
| The decisions | `plan/00-decisions.md` D79 (process), D80 (WS25 scope) | ADRs are where the lab records *why* |
| The delivery record | `plan/07-workstreams.md` `## WS25` | item lines feed `jira_sync.py` and the Project page (D58/D60); prose alone imports as a childless epic |
| Claude's plan and reviews | `build-notes/claude/codex-review-response-plan.md`, `build-notes/claude/reviews/` | build-notes are per agent, by convention |
| Codex's reviews, probes, harness, assessment | `build-notes/codex/codebase-review/` | same; the harness stays stdlib-only and outside `tests/` (see D80) |
| The baton | `build-notes/handoffs/<batch>.json`, written only by `scripts/handoff.py` | whose move, which station, which round, which files — a fact in git, not a prompt pasted between terminals |
| The skill every agent loads | `skills/handoff/SKILL.md`, symlinked from `.claude/skills/handoff` and `.agents/skills/handoff` | one source; each harness finds it where it expects to. Register a new agent with one symlink and one `AGENTS` row in the script |

There is deliberately no `build-notes/joint/` directory. Each agent's artifacts
stay under its own name so provenance is visible, and the joint state is the
WS25 item lines plus the ADRs, which are model-neutral.

## Iterating

- **One batch at a time, one branch each.** Merge to `main` after the
  cross-review verdict and the hosted smoke of the changed route. A batch that
  cannot be deployed is not merged.
- **Corrections go back to the source file.** When a critique corrects a plan
  (Codex's six corrections to Claude's WS25 plan), the plan is edited and the
  correction cited — a reader must not have to diff two documents to learn which
  is right.
- **Rotate the proposer per workstream.** Claude proposed WS25; the next joint
  workstream is Codex's to propose and Claude's to critique. Alternating keeps
  either agent from grading its own homework across two cycles.
- **Measure the loop itself.** WS9/WS16 already export both agents' coding
  telemetry (cost, edit-acceptance, tool mix). After WS25, read the DevOps →
  Coding Agents Telemetry tabs for the batch window and record per-batch cost
  and edit-acceptance in `plan/03-results.md`. That is the evidence a future
  "which model for which work" revision should use instead of vendor pages.
