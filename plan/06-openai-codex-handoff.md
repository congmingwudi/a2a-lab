# Build brief: OpenAI side of the A2A Interop Lab (round 2 — updates)

Audience: an OpenAI Codex agent (or any engineer) updating the OpenAI side
of this lab. Round 1 of this brief (build `AgentsSdkBackend` from scratch)
was delivered, accepted, and is live — the backend now runs containerized
on Bedrock AgentCore (D24/D26). This document is the **standing contract**
for OpenAI-side work: current state, the seams that moved since round 1,
and the rules that have not.

Work from branch **`lab-scaffold-m0-m4`** (the live branch; `main` lags).

## 1. What this repo is (60 seconds)

A cross-platform agent-interop lab: the same research-assistant scenario
runs between Salesforce Agentforce, Anthropic Claude, an OpenAI-platform
agent (your part), and a Google ADK/Gemini agent on Vertex AI Agent
Engine — over REST, MCP, and the A2A protocol, with the raw wire payloads
of every hop recorded and compared. Two seams make that work:

- **Inbound**: `interop.adapter.AgentAdapter` — an agent implements
  `handle(AgentRequest) -> AgentResponse` once; `serve(...)` mounts it
  behind REST (:8011), MCP (:8012), or A2A (:8013).
- **Canonical shapes**: `interop.models.AgentRequest` (`message`,
  `session_id`, `trace_id`, `metadata`) and `AgentResponse` (`text`,
  `session_id`, `latency_ms`, `raw`).

## 2. Current state of your surface

`src/platforms/openai/agents_backend.py` (yours, delivered round 1):

- `AgentsSdkBackend` — one Agents SDK turn per request, system prompt
  imported from `platforms.openai.core`, model via `OPENAI_MODEL`,
  40s self-cap via `OPENAI_ANSWER_TIMEOUT_S`.
- Two function tools, built per-request and closed over the inbound
  delegation depth (D27):
  - `ask_agentforce` — GA Agent API via `AgentforceClient` (one
    module-level client; credentials only from env).
  - `ask_agentforce_a2a` (D28) — same twin over the A2A protocol through
    the lab's AWS-hosted shim, via `interop.af_channel.ask_via_shim`.
    The operator picks the channel per run with a `[A2A-LAB ROUTING]`
    block injected into the prompt; honor it as the tool docstrings say.
- Tracing: the run is wrapped in an `interop.trace.Hop` with
  `platform_ref` = the OpenAI response id (captured at emit time — OpenAI
  has no trace read API, so this id is the only join key; ADR D18). Ids
  are appended to `.a2alab/openai_responses.json`.
- Tests in `tests/unit/test_openai_agents_backend.py` (SDK mocked; one
  `@pytest.mark.live` end-to-end).

## 3. What moved since round 1 — honor these

1. **`af_channel.ask_via_shim` grew a `trace_id` parameter**:
   `ask_via_shim(message, metadata, trace_id=None)`. When the caller's
   trace id is passed, the shim's interior hops land in the Aurora trace
   store **under the same trace id** and the console merges them into the
   live call path. The ADK and OpenAI backends both thread it end to end
   (tools closed over `(inbound_depth, trace_id)`). Keep the OpenAI
   implementation aligned with this contract:
   - close both per-request tool builders over the effective trace id
     (`req.trace_id`, or the run's generated fallback), alongside
     `inbound_depth`;
   - set `trace_id` on the direct `AgentforceClient` request and pass it
     as the named `trace_id` argument to `ask_via_shim`; and
   - extend the backend unit tests to prove both tools forward that same
     id without changing delegation metadata or routing behavior.
2. **Shim credential naming**: inside hosted runtimes the shim token is
   env `AF_SHIM_TOKEN` — `ask_via_shim` reads it first and falls back to
   `A2ALAB_TOKEN` locally. Never introduce `A2ALAB_TOKEN` into runtime
   env or code paths that run in the container: setting it flips on the
   container's own inbound bearer auth, which `invoke_agent_runtime`
   cannot satisfy — every invoke 401s (learned live, 2026-07-20).
3. **Shim timeout discipline**: `AF_SHIM_TIMEOUT_S` defaults to 34 in
   code but is deployed as **28** — just under API Gateway's hard 29s
   ceiling, so a doomed attempt fails fast and the one retry still fits
   the runtime's interior budget. Don't add retries on top of
   `ask_via_shim` (it retries once internally).
4. **Delegation guard is unchanged and non-negotiable** (D27): every
   outbound delegation goes through `interop.delegation` —
   `delegate()` to compose (rider + metadata), `max_depth()`/`refusal()`
   to stop. Any new tool path must do the same.
5. **Runtime trace sink**: the AgentCore containers write hops to the
   Aurora Postgres store (`A2ALAB_TRACE_SINK=postgres`, writer secret) —
   your `Hop` records become visible in the console's merged view when
   they carry the caller's trace id. Nothing to do beyond passing
   `req.trace_id` (item 1).
6. **Tool-governance lesson from the Claude side** (context, likely
   no-op for you): the Claude Agent SDK ships built-in tools
   permission-gated; a headless agent asked the model to grant WebSearch
   instead of answering, fixed with `tools=[]`. The OpenAI Agents SDK has
   no implicit built-ins, but keep the principle: the sync researcher's
   toolset is exactly the two Agentforce tools — nothing that can stall a
   headless 40s turn waiting on a permission or a long fetch.
7. **Target renames** (display-level, aliases keep old names resolving):
   `adk-a2a` → `google-adk-a2a`, `agentforce-adk-rest` →
   `agentforce-google-adk-rest`. No OpenAI-side impact; mentioned so
   diffs in `config/` don't surprise you.

## 4. Files you may touch

| File | What |
|---|---|
| `src/platforms/openai/agents_backend.py` | Your backend |
| `src/platforms/openai/core.py` | Prompt/adapter tweaks if the task requires (keep the adapter contract) |
| `tests/unit/test_openai_agents_backend.py` | Your tests |
| `tests/unit/test_openai_platform.py` | Only if adapter behavior legitimately changes |
| `pyproject.toml` / `uv.lock` | Dependency floor bumps inside the `openai` extra only |

**Do not modify anything else** — in particular `src/interop/**` (shared
seams: models, delegation, af_channel, trace, clients, servers),
`src/platforms/{claude,agentforce,adk}/**`, `src/console/**`,
`config/targets.yaml`, `deploy/**`, or other tests. If a task seems to
require touching those, stop and flag it in your handback notes instead.

## 5. Working in this environment

```sh
uv sync --extra openai
uv run pytest                     # must stay green (live tests deselected by default)
uv run ruff check . && uv run ruff format .   # line-length 100
uv run pytest tests/unit/test_openai_agents_backend.py -k <name>

# local manual run (REST server in front of your backend):
OPENAI_BACKEND=agents-sdk uv run python -m platforms.openai --protocol rest --port 8011
curl -s -X POST http://127.0.0.1:8011/invoke \
  -H 'content-type: application/json' \
  -d '{"message":"In two sentences: what is the difference between the MCP and A2A protocols?"}'
```

- Imports are package-prefix-free (`from interop.models import ...`);
  tests get `src/` on `sys.path` via `tests/conftest.py`.
- `.env` holds `OPENAI_API_KEY`, `SF_*`, `AF_SHIM_A2A_URL`,
  `A2ALAB_TOKEN` (local only — see §3.2), `AF_SHIM_TIMEOUT_S`.
- Trace events land in `traces/YYYY-MM-DD.jsonl` + `traces/lab.db`;
  verify hops with
  `sqlite3 traces/lab.db "SELECT source,target,platform_ref FROM trace_events ORDER BY ts DESC LIMIT 5"`.
- Deploys are lab-side: after your handback the owner runs
  `deploy/agentcore/deploy.sh openai` to ship the container.

## 6. Acceptance checklist (state each in your handback)

- [ ] `uv run pytest` fully green, including your tests
- [ ] `uv run ruff check .` clean; `uv run ruff format .` applied
- [ ] Manual REST run answers the protocol question in < 40s without
      calling Agentforce; an Omega, Inc. question triggers
      `ask_agentforce` and attributes the CRM portion
- [ ] Direct and A2A-shim tool tests prove both outbound calls carry the
      effective run trace id (§3.1); a traced shim call's interior hops
      and OpenAI run hop therefore appear under one trace in the console's
      merged call path
- [ ] The run hop has `platform_ref` = OpenAI response id, appended to
      `.a2alab/openai_responses.json`
- [ ] All delegation paths go through `interop.delegation`; the
      `[A2A-LAB ROUTING]` channel block is honored
- [ ] No edits outside §4's file list; no new required dependency outside
      the `openai` extra
- [ ] Handback notes: what changed, versions pinned, anything that didn't
      fit the contract (flagged, not worked around)

## 7. Explicitly out of scope

Protocol servers, A2A/MCP wiring, `targets.yaml`, AgentCore deployment,
console/scenario surfacing, the Agentforce→OpenAI reverse direction, and
anything Salesforce/GCP-side. All handled on the lab side after handback.
