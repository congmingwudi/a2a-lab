# Build brief: OpenAI Agents SDK backend for the A2A Interop Lab

Audience: an OpenAI Codex agent (or any engineer) implementing the OpenAI
side of this lab. The scaffold, protocol servers, tracing layer, and tests
around your work already exist and are green — your deliverable is **one
backend class plus its tests**. Everything you need to know is in this file;
pointers to the wider repo are context, not prerequisites.

## 1. What this repo is (60 seconds)

A cross-platform agent-interop lab: the same research-assistant scenario
runs between Salesforce Agentforce, Anthropic Claude, and (your part) an
OpenAI-platform agent, over three protocols (REST, MCP, A2A), with the raw
wire payloads of every hop recorded and compared. Two seams make that work,
and both are already built:

- **Inbound**: `interop.adapter.AgentAdapter` — an agent implements
  `handle(AgentRequest) -> AgentResponse` once; `serve(...)` mounts it
  behind REST (:8011), MCP (:8012), or A2A (:8013).
- **Canonical shapes**: `interop.models.AgentRequest` (`message`,
  `session_id`, `trace_id`, `metadata`) and `AgentResponse` (`text`,
  `session_id`, `latency_ms`, `raw`).

The OpenAI adapter (`src/platforms/openai/core.py`) already routes to a
backend selected by `OPENAI_BACKEND`: `stub` (working placeholder) or
`agents-sdk` (**yours**).

## 2. Your deliverable

Implement `AgentsSdkBackend` in `src/platforms/openai/agents_backend.py`:

```python
class AgentsSdkBackend:
    backend_name = "agents-sdk"

    async def answer(self, req: AgentRequest) -> AgentResponse: ...
```

Requirements, in priority order:

1. **One turn of an OpenAI Agents SDK agent** (the `openai-agents`
   package), system prompt = `OPENAI_RESEARCH_SYSTEM_PROMPT` from
   `platforms.openai.core` (import it — do not copy the text). Model from
   env `OPENAI_MODEL` (pick a sensible current default; make it
   env-overridable). API key from env `OPENAI_API_KEY` (already the SDK
   default). `req.message` is the user turn.

2. **The `ask_agentforce` function tool** — the Path C collaboration.
   Declare a tool named `ask_agentforce` (description: ask the Salesforce
   Agentforce agent a question; use for accounts/opportunities/cases/org
   data). Its implementation calls:

   ```python
   from platforms.agentforce.client import AgentforceClient
   resp = await AgentforceClient.from_env().ask(AgentRequest(message=question))
   return resp.text
   ```

   Keep ONE module-level client instance (see the pattern and the comment
   explaining why in `src/platforms/claude/sdk_backend.py` — mirror it).
   Salesforce credentials come from env (`SF_*` via `.env`); they must
   never appear in prompts or tool descriptions.

3. **Tracing — this is the lab's core requirement, not optional.**
   - Wrap the whole agent run in a hop:

     ```python
     from interop.trace import Hop
     with Hop(trace_id, source="openai-researcher", target="openai-platform",
              protocol="internal", transport_detail="agents-sdk run",
              request_payload=req.to_dict()) as hop:
         ... run the agent ...
         hop.platform_ref = <OpenAI response id or trace id>
         hop.response_payload = {"text": ..., "response_id": ...}
     ```

     `trace_id = req.trace_id or interop.models.new_trace_id()`.
   - **`hop.platform_ref` must carry the OpenAI-native id** (the response
     id, or the Agents SDK trace id — whichever your implementation can
     obtain; prefer the one visible in the OpenAI traces dashboard).
     OpenAI exposes no API to read traces back later, so ids captured at
     emit time are the only join key the observability layer will ever
     have. This is a hard requirement (ADR D18/M9 in
     `plan/00-decisions.md`).
   - **Persist every captured id** by appending JSON records to
     `.a2alab/openai_responses.json` (create if absent; list of objects:
     `{"response_id": ..., "trace_id": ..., "session_id": ..., "ts": ...}`).
     Mirror of `.a2alab/cma_sessions.json` used for the Anthropic side.

4. **Timeout budget**: wrap the run in
   `asyncio.wait_for(..., float(os.environ.get("OPENAI_ANSWER_TIMEOUT_S", "40")))`.
   The upstream bridge gives up at 45s — your agent must answer inside 40.
   Keep it fast: concise prompt is already given; avoid multi-round
   tool-chatter where one round does.

5. **Return** `AgentResponse(text=final_text, session_id=req.session_id,
   latency_ms=<measured>, raw={"response_id": ..., "model": ...})`.
   If Agentforce delegation fails, still answer (say the CRM lookup
   failed) — never raise for a tool failure.

## 3. Files you may touch

| File | What |
|---|---|
| `src/platforms/openai/agents_backend.py` | Replace the skeleton with your implementation |
| `tests/unit/test_openai_agents_backend.py` | NEW — your unit tests (mock the SDK; no network in default test runs) |
| `pyproject.toml` | Add the dependency ONLY as an extra: `[project.optional-dependencies] openai = ["openai-agents>=<floor you tested>"]` |
| `uv.lock` | Regenerated by `uv sync` |

**Do not modify anything else** — in particular `src/interop/**` (the
shared seams), `src/platforms/claude/**`, `src/platforms/agentforce/**`
(read them, don't edit), `src/console/**`, `config/targets.yaml`, or
existing tests. If something in the contract seems to require touching
those, stop and flag it in your handback notes instead.

## 4. Working in this environment

```sh
uv sync --extra openai            # after you add the extra
uv run pytest                     # must stay green (live tests deselected by default)
uv run ruff check . && uv run ruff format .   # line-length 100
uv run pytest tests/unit/test_openai_agents_backend.py -k <name>

# local manual run: starts a local REST server that calls OpenAI and,
# when the model chooses the tool, the Salesforce Agentforce agent.
OPENAI_BACKEND=agents-sdk uv run python -m platforms.openai --protocol rest --port 8011

curl -s -X POST http://127.0.0.1:8011/invoke \
  -H 'content-type: application/json' \
  -d '{"message":"What is the status of account Omega, Inc.?"}'
```

- Imports are package-prefix-free (`from interop.models import ...`);
  tests get `src/` on `sys.path` via `tests/conftest.py`, scripts run with
  `PYTHONPATH=src`.
- `.env` at the repo root holds `OPENAI_API_KEY` (add it), `SF_*`
  (present), `A2ALAB_TOKEN` (protocol servers enforce it when set — send
  header `x-lab-token` if you curl a running server).
- Tests marked `@pytest.mark.live` may hit real APIs and are deselected by
  default — add ONE live test that runs a real end-to-end answer
  (assert non-empty text and a captured `response_id`), marked live.
- Trace events land in `traces/YYYY-MM-DD.jsonl` + `traces/lab.db` — after
  a manual run, verify your hop appears with `platform_ref` set:
  `sqlite3 traces/lab.db "SELECT source,target,platform_ref FROM trace_events ORDER BY ts DESC LIMIT 5"`.

## 5. Acceptance checklist (your handback must state each)

- [ ] `uv run pytest` fully green, including your new unit tests
- [ ] `uv run ruff check .` clean; `uv run ruff format .` applied
- [ ] Manual REST run answers a research question in < 40s
- [ ] A question like "what's the status of account Omega, Inc.?" triggers
      the `ask_agentforce` tool and the answer attributes the CRM portion
- [ ] The run's trace hop has `platform_ref` = OpenAI response/trace id,
      and the id is appended to `.a2alab/openai_responses.json`
- [ ] No new required dependency outside the `openai` extra; no edits
      outside §3's file list
- [ ] Handback notes: model chosen + why, SDK version pinned, anything
      about the contract that didn't fit (rather than worked around)

## 6. Explicitly out of scope for you

Protocol servers, A2A/MCP wiring, `targets.yaml`, Bedrock AgentCore
deployment (`deploy/agentcore/openai.Dockerfile` exists; the lab owner
deploys), console/scenario surfacing, and the Agentforce→OpenAI reverse
direction. All of that is handled on the lab side after your handback.
