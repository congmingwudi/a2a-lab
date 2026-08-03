# How this lab uses Cursor — build notes

These notes document Cursor-specific engineering in the A2A Interop Lab: how the
IDE is wired into the same CloudWatch OTLP path as Claude Code and Codex, and
where the three tools diverge. They complement
[build-notes/claude/08-coding-agent-telemetry.md](../claude/08-coding-agent-telemetry.md),
which covers the Claude Code and Codex paths in depth.

Audience: engineers who already run the lab's coding-agent telemetry and want
the Cursor column to land beside `claude_code.*` and `codex.*` without
re-inventing credentials, endpoints, or attribution.

## How to read a note

Same convention as the Claude build notes:

- **Engineering takeaway** — the one-line thesis.
- **The body** — what actually happened here, with file paths.
- **Evidence and limits** — what's proven, and by what.
- **In this repo** — the files to open first.

## Contents

| File | Theme |
|---|---|
| [01-coding-agent-telemetry.md](01-coding-agent-telemetry.md) | Cursor has no native OTEL — hooks + cursorscope → CloudWatch metrics; project hooks, setup script, attribution, and what's still open |
| [02-cross-tool-cost-comparison.md](02-cross-tool-cost-comparison.md) | Why Claude Code gets dollars and Codex/Cursor get sessions — three telemetry shapes, histogram limits, and what the console renders as `n/a` |

## Relationship to the other coding agents

| Tool | OTEL mechanism | Launch / setup |
|---|---|---|
| Claude Code | Native exporter + `otelHeadersHelper` | `.claude/settings.local.json`; optional `scripts/claude_otel.sh` for behavioural logs |
| Codex | Native exporter (three separate exporters) | `scripts/codex_otel.sh` at every launch |
| Cursor | **None native** — Cursor hooks + [cursorscope](https://github.com/last9/cursorscope) | `scripts/cursor_otel.sh` once; project `.cursor/hooks.json` |

All three share `scripts/otel_headers.sh` for the CloudWatch **metrics** bearer
token and the same managed OTLP endpoint:
`https://monitoring.<region>.amazonaws.com/v1/metrics`.
