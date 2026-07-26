# How this lab was built with Claude

The A2A Interop Lab is itself an exhibit twice over: the *product* demonstrates
cross-platform agent interop, and the *build process* demonstrates the breadth of
Claude Code and the Claude API in a real, multi-week engineering project. These
notes document the second exhibit. They feed directly into the Claude design
presentation (see also `config/insights.yaml`, the source of truth for lab
findings that go into the deck).

Audience: people learning Claude Code / the Claude API who want concrete,
non-toy examples — every claim in these notes points at a real file in this repo.

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
| [08-coding-agent-telemetry.md](08-coding-agent-telemetry.md) | Claude Code and Codex OTel → CloudWatch, and the per-project / per-repo attribution that is *not* built in — plus two silent-failure modes worth showing a customer |
| [screenshots/](screenshots/README.md) | Terminal screenshots captured at critical decision points — Claude as a thought partner |

## The one-paragraph story

The lab was built conversationally in Claude Code, but with three disciplines
that made it scale past what a chat session normally sustains: (1) **a written
decision log** (`plan/00-decisions.md`, D1–D35) that both human and model treat
as the source of truth, so every session starts grounded instead of re-deriving
context; (2) **saved multi-agent workflows** that fan out dozens of subagents to
adversarially audit the project's published claims before any demo; and (3)
**clean seams for delegation** — to other coding agents (Codex built one backend
against a contract file) and to the Claude API itself (three production
integrations inside the lab). The same architecture idea that powers the lab —
two seams, honest status labels, raw wire evidence — is also how the build
process was run.
