# Should the lab add Google Antigravity? — research notes

**Status: research idea, not a decision.** Nothing is built or deployed. These
notes exist so the "is it worth it?" call can be made from evidence rather than
enthusiasm. If the answer is no, this directory is the record of *why* — which
is worth as much as a yes.

Antigravity (Google, announced Nov 2025) is a Gemini-backed **IDE + CLI + SDK**.
The question is narrow: **can it emit the same coding-agent telemetry the lab
already collects for Claude Code, Codex, and Cursor** — sessions, tool
executions, tokens, cost, model — **into the same CloudWatch OTLP metrics
endpoint**, and at what build cost?

These notes complement the existing coding-agent telemetry write-ups:

- [build-notes/claude/08-coding-agent-telemetry.md](../claude/08-coding-agent-telemetry.md) — the Claude Code + Codex native-exporter paths.
- [build-notes/cursor/01-coding-agent-telemetry.md](../cursor/01-coding-agent-telemetry.md) — the no-native-exporter, hooks-bridge path Antigravity most resembles.

## How to read a note

Same convention as the Claude and Cursor build notes:

- **Engineering takeaway** — the one-line thesis.
- **The body** — what the docs actually say, with the two candidate paths.
- **Evidence and limits** — what's proven, what's assumed, and the one probe that decides.
- **In this repo** — the files that would change if we proceed.

## Contents

| File | Theme |
|---|---|
| [01-antigravity-telemetry-research.md](01-antigravity-telemetry-research.md) | Does Antigravity fit the lab's telemetry patterns? Docs findings, the two candidate paths (CLI OTEL wrapper vs hook forwarder), the metric-coverage gap, the deciding probe, and a placeholder for its result |

## Where Antigravity would sit beside the others

| Tool | OTEL mechanism | Cost | Tokens | Model | Sessions/tools |
|---|---|---|---|---|---|
| Claude Code | Native exporter + `otelHeadersHelper` | ✅ (client-side est.) | ✅ | ✅ | ✅ |
| Codex | Native exporter (three exporters) | ❌ | ~ (delta histogram) | ✅ | ✅ |
| Cursor | **None native** — hooks + cursorscope | ❌ | ~ (gen_ai histograms) | mostly `default` | ✅ |
| **Antigravity** | **Unknown** — see 01; hook payload carries no model/token/cost | **TBD** | **TBD** | **TBD** | likely ✅ |

The Antigravity row is deliberately unresolved. `01` explains the one probe that
fills it in.
