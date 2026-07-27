# How this lab was built with Claude — presentation source notes

The A2A Interop Lab is itself an exhibit twice over: the *product* demonstrates
cross-platform agent interop, and the *build process* demonstrates the breadth of
Claude Code and the Claude API in a real, multi-week engineering project. These
notes document the second exhibit. They are source material for an engineering
presentation: each note separates what happened, the evidence, why the pattern
matters, and the smallest useful slide that can teach it. See also
`config/insights.yaml`, the source of truth for lab findings used in the deck.

Audience: engineers and engineering leaders evaluating coding agents or agent
platforms. Claude familiarity is optional. Product-specific details are paired
with the reusable engineering principle they demonstrate.

## Evidence convention

- **Repository-backed** — supported by current code, an ADR, a recorded run, or
  git history in this repository.
- **Vendor-documented** — supported by current first-party documentation and
  linked near the claim.
- **Observed in this project** — a useful field observation captured in a
  screenshot or session artifact, but not claimed as general vendor behavior.

## Contents

| File | Theme |
|---|---|
| [01-workflows.md](01-workflows.md) | Saved multi-agent workflows ("ultraplan"-style orchestration): the adversarial audit pattern that keeps the matrix and insights honest |
| [02-agent-handoffs.md](02-agent-handoffs.md) | Handing a bounded piece of the build to another coding agent (Codex) via a written contract file — and the pattern for doing it again |
| [03-claude-api-in-the-lab.md](03-claude-api-in-the-lab.md) | The lab's direct Claude API integrations: Managed Agents backend, the Lab Guide's streaming tool-use loop, the hosted observability analyst |
| [04-claude-code-environment.md](04-claude-code-environment.md) | The working environment: CLAUDE.md, the plan/ decision log as shared memory, MCP servers, persistent memory, project skills |
| [05-hooks-notifications.md](05-hooks-notifications.md) | Hooks wired to a custom AWS logging service that routes Stop/Notification events to Slack — walk away from long runs |
| [06-requirements-corpus-hardening.md](06-requirements-corpus-hardening.md) | A colleague's anti-pattern deck as a requirements corpus: evidence-graded audit against the codebase, then a security-hardening plan applied across the stack |
| [07-permissions-guardrails.md](07-permissions-guardrails.md) | The permission gradient in practice: auto-mode classifier holds on production-org and hosted-infra writes, narrow allow rules, and the hard guardrails that don't yield to rules |
| [08-coding-agent-telemetry.md](08-coding-agent-telemetry.md) | Claude Code OTel → CloudWatch, the attribution context teams must add, and the separate validation still required for Codex telemetry |
| [screenshots/](screenshots/README.md) | Terminal screenshots captured at critical decision points — Claude as a thought partner |

## Recommended presentation arc

1. **Make context durable** — project instructions, ADRs, memory, and contract
   files stop each session from rediscovering the project.
2. **Scale judgment without losing evidence** — saved workflows turn a
   subjective claims review into a repeatable, adversarial audit.
3. **Delegate through interfaces** — typed seams and shared tests make
   cross-agent ownership workable.
4. **Choose the right runtime surface** — managed sessions, a self-hosted agent
   SDK, and a direct Messages API loop solve different problems.
5. **Earn autonomy** — hooks make long runs operable; permission layers keep
   unattended work inside an accepted risk envelope.
6. **Measure and harden the system** — an external requirements corpus and
   telemetry expose product debt and build-process blind spots.

## The one-paragraph story

The lab was built conversationally in Claude Code, but with three disciplines
that made it scale past what a chat session normally sustains: (1) **a written
decision log** (`plan/00-decisions.md`, D1–D40) that both human and model treat
as the source of truth, so every session starts grounded instead of re-deriving
context; (2) **saved multi-agent workflows** that fan out dozens of subagents to
adversarially audit the project's published claims before any demo; and (3)
**clean seams for delegation** — to other coding agents (Codex built one backend
against a contract file) and to the Claude API itself (three production
integrations inside the lab). The same architecture idea that powers the lab —
two seams, honest status labels, raw wire evidence — is also how the build
process was run.
