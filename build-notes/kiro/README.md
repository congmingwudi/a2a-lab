# Should the lab add Amazon Kiro? — research notes

**Status: research idea, not a decision.** Nothing is built or deployed. These
notes exist so the "is it worth it?" call can be made from evidence rather than
enthusiasm. If the answer is no, this directory is the record of *why*.

Kiro (Amazon, GA 2025) is an **agentic IDE + CLI + web + mobile**, built around
specs, steering, and hooks. The question is the same one asked of Antigravity:
**can it emit the coding-agent telemetry the lab already collects for Claude
Code, Codex, and Cursor** — sessions, tool executions, tokens, cost, model —
**into the same CloudWatch OTLP metrics endpoint**, and at what build cost?

Kiro carries one twist the others don't: it is an **AWS product**, so a
first-party CloudWatch path is at least conceivable in a way it never was for a
Google or Anthropic tool. That possibility is a probe, not an assumption — see
`01`.

These notes complement the existing coding-agent telemetry write-ups and the
sibling Antigravity research:

- [build-notes/claude/08-coding-agent-telemetry.md](../claude/08-coding-agent-telemetry.md) — the Claude Code + Codex native-exporter paths.
- [build-notes/cursor/01-coding-agent-telemetry.md](../cursor/01-coding-agent-telemetry.md) — the no-native-exporter, hooks-bridge path Kiro most resembles.
- [build-notes/antigravity/01-antigravity-telemetry-research.md](../antigravity/01-antigravity-telemetry-research.md) — the same research question for Google Antigravity.

## How to read a note

Same convention as the Claude, Cursor, and Antigravity build notes:

- **Engineering takeaway** — the one-line thesis.
- **The body** — what the docs actually say, with the candidate paths.
- **Evidence and limits** — what's proven, what's assumed, and the probes that decide.
- **In this repo** — the files that would change if we proceed.

## Contents

| File | Theme |
|---|---|
| [01-kiro-telemetry-research.md](01-kiro-telemetry-research.md) | Does Kiro fit the lab's telemetry patterns? Docs findings, three candidate paths (native AWS/CloudWatch, CLI OTEL wrapper, hook forwarder), the exit-code hook contract, the metric-coverage gap, the deciding probes, and a placeholder for their results |

## Where Kiro would sit beside the others

| Tool | OTEL mechanism | Cost | Tokens | Model | Sessions/tools |
|---|---|---|---|---|---|
| Claude Code | Native exporter + `otelHeadersHelper` | ✅ (client-side est.) | ✅ | ✅ | ✅ |
| Codex | Native exporter (three exporters) | ❌ | ~ (delta histogram) | ✅ | ✅ |
| Cursor | **None native** — hooks + cursorscope | ❌ | ~ (gen_ai histograms) | mostly `default` | ✅ |
| Antigravity | **Unknown** — hook payload carries no model/token/cost | TBD | TBD | TBD | likely ✅ |
| **Kiro** | **Unknown** — hooks exist; native AWS/CloudWatch path plausible but undocumented | **TBD** | **TBD** | **TBD** | likely ✅ |

The Kiro row is deliberately unresolved. `01` explains the probes that fill it in.
