# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A2A Interop Lab: cross-platform agent-to-agent experiments between Salesforce Agentforce and Claude (OpenAI later), with each direction runnable over REST, MCP, and the A2A protocol — same scenario, protocols compared side by side with raw wire payloads recorded. `plan/` is the source of truth: decision log (ADRs) in `plan/00-decisions.md`, architecture and protocol mapping rules in `plan/01-architecture.md`, the honest protocol matrix in `plan/02-matrix.md`, runbooks in `plan/04-runbooks.md`.

## Commands

```sh
uv sync                              # install (Python 3.11+, uv-managed)
uv run pytest                        # unit + loopback e2e; live tests deselected by default
uv run pytest tests/unit/test_bridge.py            # one file
uv run pytest tests/unit/test_bridge.py -k name    # one test
uv run pytest -m live                # tests needing real credentials (marker: live)
uv run ruff check . && uv run ruff format .        # lint / format (line-length 100)

scripts/run_local.sh                 # full local stack (Claude servers, shims, bridge, console)
uv run python scripts/matrix.py      # run every runnable protocol cell → appends plan/03-results.md
uv run python scripts/sf_smoke.py    # Agentforce go/no-go (needs SF_* in .env)
uv run python scripts/setup_managed_agent.py       # once: provisions the Managed Agents agent
```

Code under `src/` is imported without a package prefix (`from interop import ...`); tests add `src/` to `sys.path` via conftest, and scripts run with `PYTHONPATH=src` (run_local.sh does this). Config comes from `.env` (see `.env.example`).

## Architecture — the two seams

Everything hangs off two abstractions sharing the canonical `AgentRequest`/`AgentResponse` models (`src/interop/models.py`):

- **Inbound** — `interop.adapter.AgentAdapter`: an agent we host implements `handle(AgentRequest) -> AgentResponse` once; `serve(adapter, protocol, port)` mounts it behind REST (`:8001`), MCP (`:8002`), or A2A (`:8003`) via `src/interop/servers/`.
- **Outbound** — `interop.clients.base.RemoteAgentClient`: `ask(AgentRequest) -> AgentResponse`, one client per protocol (`rest.py`, `mcp.py`, `a2a.py`) plus the platform-native `AgentforceClient`. Clients are resolved by target name through `interop.registry.Registry`, driven by `config/targets.yaml` (each target has an honest `status`: native / via-bridge / via-shim / blocked-beta — keep it honest, it feeds plan/02-matrix.md).

Because both seams share the same models, the loopback e2e suite (`tests/e2e/test_loopback.py`) proves all three client×server pairings against a deterministic EchoAdapter with no external platforms.

A platform = one directory under `src/platforms/<name>/` contributing an `AgentAdapter` and/or a `RemoteAgentClient`, plus entries in `config/targets.yaml`. Nothing in `interop/` or other platforms changes when adding one.

### Key components

- `src/platforms/claude/` — one adapter (`core.py`), two backends selected by `CLAUDE_BACKEND`: `managed_backend.py` (Anthropic Managed Agents beta, the default) and `sdk_backend.py` (self-hosted claude-agent-sdk, the fallback and the AgentCore containerization path). Nothing outside the adapter knows which backend runs. Path B (`ask_agentforce`) is a host-side custom tool under managed, an in-process SDK MCP tool under sdk — Salesforce credentials never enter the managed sandbox.
- `src/platforms/agentforce/` — GA Agent API client (`client.py`) plus MCP (`:8021`) / A2A (`:8023`) shims proxying to the Agent API (Agentforce has no GA MCP/A2A inbound).
- `src/bridge/` (`:8100`) — Path A: Agentforce's outbound is REST-only, so its Apex callout hits the bridge, which fans out to any target/protocol per `config/targets.yaml` — switching protocol needs no Salesforce redeploy.
- `src/console/` (`:8200`) — lab console: groups trace events by trace_id, protocol badges, raw request/response, SSE live tail.

### Trace layer (core requirement)

Every hop appends a `TraceEvent` with the **raw wire bytes** to `traces/YYYY-MM-DD.jsonl` (`src/interop/trace.py`). REST captures at handler level; MCP/A2A use the WireTap ASGI middleware (`src/interop/servers/wiretap.py`) because the JSON-RPC envelopes live inside the frameworks. Trace correlation rides `X-Trace-Id` (REST), a tool argument (MCP), and `metadata.trace_id` (A2A). New code paths must record trace events; tests get an isolated trace dir via the autouse fixture in `tests/conftest.py`.

## Salesforce side

Org metadata (Apex invocable `A2ALabInvokeRemoteAgent` + test, named/external credentials) lives in `salesforce/`, strictly namespaced `A2ALab*` — it deploys to the user's **production org**, so Apex deploys require test runs with ≥75% coverage. Deploys go through the Salesforce DX MCP server registered in `.mcp.json` (use those MCP tools for org auth, metadata deploys, Apex tests); the raw `sf` CLI is the documented fallback in plan/04-runbooks.md.

## Conventions

- Decisions get an ADR entry appended to `plan/00-decisions.md`; measured results go to `plan/03-results.md` (matrix.py appends there), findings to the ledger in `plan/02-matrix.md`.
- Streaming is out of scope for v1 (Apex callouts are buffered); one A2A SSE demo exists as a capability comparison only.
- Timeout budget for Path A is tight (Agentforce action ~60s → Apex 110s → bridge 45s → `CLAUDE_ANSWER_TIMEOUT_S=40`); keep the Claude agent fast (Haiku-tier model, concise prompts, warm servers).
