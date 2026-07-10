# A2A Interop Lab

Cross-platform agent-to-agent interoperability experiments: Salesforce
Agentforce ↔ Claude (↔ OpenAI on AWS later), with each direction runnable
over platform-native REST, MCP, and the A2A protocol — same scenario, same
question, protocols compared side by side with the raw wire payloads visible.

- **Plan & decisions:** [plan/](plan/) — decision log, architecture +
  protocol mapping rules, honest protocol matrix, results, runbooks.
- **Claude agent:** `src/platforms/claude/` — one adapter, two backends:
  Anthropic **Managed Agents (beta)** (default) and the self-hosted
  **Claude Agent SDK** (`CLAUDE_BACKEND=sdk`).
- **Agentforce:** `src/platforms/agentforce/` — GA Agent API client + MCP/A2A
  shims. Org metadata (Apex invocable + test, credentials) in `salesforce/`,
  deployed via the Salesforce DX MCP server (`.mcp.json`).
- **Bridge:** `src/bridge/` — Agentforce's REST callout fans out to any
  target/protocol per `config/targets.yaml`; no Salesforce redeploy to switch.
- **Lab console:** `src/console/` (:8200) — per-conversation hop sequence,
  protocol badges, raw request/response, SSE live tail.

## Quick start (local loopback — no external accounts)

```sh
uv sync
uv run pytest                      # unit + loopback e2e (echo agent over rest/mcp/a2a)
```

## With credentials

```sh
cp .env.example .env               # fill in what you have
uv run python scripts/setup_managed_agent.py   # once: provisions the CMA agent
scripts/run_local.sh               # full local stack
uv run python scripts/matrix.py    # run every runnable protocol cell
open http://localhost:8200         # lab console
uv run python scripts/sf_smoke.py  # Agentforce go/no-go (needs SF_* in .env)
```

Milestone status and next steps: see plan/00-decisions.md and plan/02-matrix.md.
