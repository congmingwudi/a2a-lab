# Plan → requirements sweep — a long review saved as a Codex skill

**Feature area:** Codex skills, repo-local skill discovery, explicit and
implicit invocation, long-running review workflows, and optional Scheduled
tasks in the ChatGPT desktop app.

## Engineering takeaway

Save the *method* of a recurring judgment-heavy task as a skill, then keep the
*cadence* separate. The skill makes an ad hoc review repeatable today; a
Scheduled task can invoke the same skill later without duplicating its review
logic.

## The problem this solves

The first plan-to-requirements review was not a single diff or lint command. It
read all of `plan/`, all source documents under `requirements-docs/`, and the
implementation evidence behind both. It then separated two different answers:

1. what should change while preserving the reusable, implementation-neutral
   requirements standard; and
2. what should change if the documents were rewritten as an exact as-built
   specification of this lab.

The result lives under `requirements-docs/codex-review/` as five coordinated
documents. Repeating that review from a short prompt is risky unless the prompt
also preserves its evidence rules, coverage guarantees, output contract, and
adversarial verification pass.

Codex calls the reusable authoring format for this kind of task a **skill**. A
skill is a directory containing `SKILL.md`, with optional scripts, references,
assets, and UI metadata. Codex initially sees the skill name and description,
then reads the full instructions when the task explicitly invokes the skill or
matches its description.

## The local configuration

The skill is installed in this checkout at:

```text
.agents/skills/plan-requirements-sweep/
├── SKILL.md
└── agents/
    └── openai.yaml
```

Codex scans `.agents/skills` from the current working directory up to the Git
repository root, so launching Codex anywhere inside this repository makes the
skill available.

### `SKILL.md`

The frontmatter is the matching contract:

```yaml
---
name: plan-requirements-sweep
description: Run a comprehensive, evidence-grounded review of this repository's
  plan/ documents against requirements-docs/ and the implementation, then
  create or refresh the review under requirements-docs/codex-review/.
---
```

The actual description also names trigger phrases such as “periodic
requirements review,” “requirements drift audit,” and “plan/requirements
reconciliation.” That enables both invocation modes:

- **Explicit:** `Run $plan-requirements-sweep for this repository.`
- **Implicit:** `Run the periodic plan-to-requirements review.`

Explicit invocation is preferred for a long, consequential sweep because it
makes the selected procedure visible in the request.

The body encodes six phases:

1. **Establish the baseline** — branch, HEAD, review date, dirty state, prior
   review baseline, complete corpus inventories, and Git changes since the last
   run.
2. **Review all evidence** — every plan and requirements source document,
   corroborated against source, config, deployments, tests, scripts, metadata,
   and committed results.
3. **Separate the two tracks** — standards-compliant recommendations versus an
   exact-as-built rewrite.
4. **Challenge the findings** — try to refute conflicts and nonconformances;
   use `Not verifiable` rather than filling evidence gaps with guesses.
5. **Refresh the durable output** — coherently update the established five-file
   review under `requirements-docs/codex-review/`.
6. **Verify and report** — coverage ledgers, identifiers, local paths, final
   diff, material changes, and live-state limitations.

Every material plan/implementation comparison uses one of five statuses:
`Both`, `Plan only`, `Code only`, `Conflict`, or `Not verifiable`. The skill
also tells Codex not to treat source presence as proof of successful live
operation and not to use one prose document as proof that another is true.

For comprehensive runs, the skill permits bounded parallel subagents when the
surface supports them: plan/implementation, requirements structure,
traceability/acceptance, and adversarial verification. The primary agent still
owns the evidence reconciliation and final output.

### `agents/openai.yaml`

The optional UI metadata makes the skill easy to recognize and supplies a
starter prompt:

```yaml
interface:
  display_name: "Plan → Requirements Sweep"
  short_description: "Audit plans against requirements and implementation"
  default_prompt: "Use $plan-requirements-sweep to run the comprehensive plan-to-requirements review for this repository."
```

There are no bundled scripts. That is deliberate: the difficult part of this
task is evidence-based judgment over an evolving repository, not a fragile
deterministic transformation. Git, search, and the repository itself remain the
inputs; the skill preserves the review method and output contract.

## Current operating mode — ad hoc

No schedule is configured. Run the sweep manually when the plans,
requirements, architecture, implementation, deployment shape, or acceptance
evidence have changed materially:

```text
Run $plan-requirements-sweep for this repository.
```

A more constrained invocation can add run-specific boundaries without changing
the saved method:

```text
Run $plan-requirements-sweep for this repository. Use repository evidence only;
do not query live cloud state. Refresh requirements-docs/codex-review/ and
summarize only material new or resolved findings.
```

This is the chosen mode for now. It keeps compute-heavy review intentional,
lets the operator decide when the corpus has changed enough to justify a run,
and ensures someone is present to answer questions or approve any exceptional
access.

## How scheduling would work later

Scheduling does not require a second workflow definition. A Codex Scheduled
task can explicitly invoke the same skill with `$plan-requirements-sweep`.

For this local repository, create and manage the task from ChatGPT web or the
desktop app; Codex CLI and the IDE extension do not provide the Scheduled
management interface. The desktop app can attach a task to a local project.
When a scheduled run needs local files, the computer must be powered on and the
desktop app running.

For a periodic independent audit, use a **standalone scheduled task** rather
than scheduling inside a long-lived chat. Each run then starts from the durable
prompt and reports as a separate run. A future setup prompt could be:

```text
Create a standalone scheduled task for the local a2a-lab project.
On the first day of every month at 09:00 America/Denver, run
$plan-requirements-sweep for this repository. Use repository evidence only
unless the prompt explicitly authorizes live checks. Refresh only
requirements-docs/codex-review/, preserve unrelated working-tree changes, and
report material new or resolved findings with the verification performed.
```

Before enabling it:

1. Run the exact prompt manually and review the first output.
2. Choose **local project** mode if the run must see the current uncommitted
   review files. An isolated Git worktree contains committed state and will not
   automatically include untracked prior outputs.
3. Keep sandbox permissions at workspace-write unless the review genuinely
   needs more. Scheduled runs are unattended and cannot pause for ordinary
   interactive approvals.
4. Review the first few scheduled results and tune cadence or scope before
   treating the run as a routine control.

That schedule is an example only. **No Scheduled task has been created for this
project.**

## The portability wrinkle

The repository currently ignores `.agents/` because Salesforce tooling manages
skill bundles there:

```gitignore
# sf CLI-managed skill bundles (installed by Salesforce DX tooling)
.agents/
skills-lock.json
```

Therefore this Codex skill is **local to this checkout**. It is discovered and
usable here, but it is not currently versioned or distributed to another clone.
That matches the present “for this operator, in this project” use case.

If the skill becomes a team control, do not casually remove the broad ignore
rule. Either add a narrow Git exception for this one authored skill or package
it for deliberate distribution, while continuing to exclude generated
Salesforce skill bundles.

## Evidence and limits

- **Locally observed:** the skill exists under
  `.agents/skills/plan-requirements-sweep/`; its metadata and six-phase review
  procedure are readable in this checkout; the official skill validator reports
  it as valid.
- **Measured 2026-07-31:** the skill was invoked end to end, refreshed all five
  documents under `requirements-docs/codex-review/`, ran the coverage and
  reference checks, and the resulting review was committed to `main`. The skill
  definition itself remains local because `.agents/` is excluded by the
  repository's `.gitignore`.
- **Repository-backed:** `.gitignore` deliberately excludes `.agents/` for
  Salesforce-managed skill bundles, which is the reason this authored Codex
  skill does not currently travel with a clone.
- **Vendor-documented:** Codex loads repo skills from `.agents/skills`, supports
  explicit `$skill-name` invocation, and can use skills from Scheduled tasks.
  See [Build skills](https://learn.chatgpt.com/docs/build-skills) and
  [Scheduled tasks](https://learn.chatgpt.com/docs/automations?surface=app).
- **Not established:** no scheduled run has been configured or observed. The
  successful ad hoc run does not establish unattended scheduling behavior or a
  repeatable runtime/finding rate.

## Put this in the presentation

**Slide headline:** Save the review method as a skill; decide the cadence
separately.

- One short `$plan-requirements-sweep` request expands into a six-phase,
  evidence-grounded review with a stable five-document output contract.
- The skill preserves judgment rules and coverage; it does not pretend that a
  long review is a deterministic script.
- The same skill can stay ad hoc or become a Scheduled task later, without
  maintaining two versions of the workflow.

**Visual:** two layers. Top: `SKILL.md` → Baseline → Review → Two tracks →
Refute → Refresh → Verify. Bottom: two triggers pointing to the same skill —
“operator request (current)” in solid lines and “Scheduled task (optional)” in
dashed lines.
