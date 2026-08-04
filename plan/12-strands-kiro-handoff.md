# Build brief: the AWS Strands agent for the A2A Interop Lab (WS5)

Audience: an **Amazon Kiro** coding agent (or any engineer) building the AWS
Strands Agents backend for this lab. This document is the **standing contract**
for that work — the same arrangement the OpenAI side has in
`plan/06-openai-codex-handoff.md` (D24): **this one file is the contract.** The
`strands-sdk` backend and its tests are yours to write; **everything else is
already built and is not yours to touch** (§4, §7).

Your deliverable is small and well-bounded: **one file** —
`src/platforms/strands/strands_backend.py` — plus its unit tests and one
dependency pin. The lab has already built the adapter, the three protocol
faces, a deterministic stub, the Dockerfile, the deploy path, the console
wiring, and this brief. When you finish, the lab owner runs
`deploy/agentcore/deploy.sh strands`, flips the console scenario to `live`, and
the experiment is done.

Work from branch **`lab-scaffold-m0-m4`** (the live branch; `main` lags).

## 1. What this repo is (60 seconds)

A cross-platform agent-interop lab: the same research-assistant scenario runs
between Salesforce Agentforce, Anthropic Claude, an OpenAI-platform agent, a
Google ADK/Gemini agent, a Microsoft Foundry agent — and now **your AWS Strands
agent** — over REST, MCP, and the A2A protocol, with the raw wire payloads of
every hop recorded and compared. Two seams make that work:

- **Inbound**: `interop.adapter.AgentAdapter` — an agent implements
  `handle(AgentRequest) -> AgentResponse` once; `serve(...)` mounts it behind
  REST (:8041), MCP (:8042), or A2A (:8043).
- **Canonical shapes**: `interop.models.AgentRequest` (`message`, `session_id`,
  `trace_id`, `metadata`) and `AgentResponse` (`text`, `session_id`,
  `latency_ms`, `raw`).

**Why Strands, and why it matters that you follow the pattern (WS5).** The lab
already runs the Claude Agent SDK and the OpenAI Agents SDK as containers on the
**identical** Bedrock AgentCore runtime. Strands is the **third framework on
that same runtime**, so the experiment isolates exactly one variable — the agent
SDK — at a constant runtime and a constant model cloud (**Amazon Bedrock**). If
your backend behaves differently in any way the other two don't (different model
cloud, different delegation contract, different trace shape), the comparison
stops being about the framework and the whole point of WS5 is lost. So the
contract below is not bureaucracy: it is what keeps the one-variable comparison
honest.

## 2. Your surface — what to build

Create `src/platforms/strands/strands_backend.py` exporting a class
`StrandsSdkBackend` with:

```python
class StrandsSdkBackend:
    backend_name = "strands-sdk"

    async def answer(self, req: AgentRequest) -> AgentResponse: ...
```

`platforms/strands/core.py` already imports it lazily when
`STRANDS_BACKEND=strands-sdk` (see `make_adapter`). **Model it closely on the
delivered, accepted OpenAI backend `src/platforms/openai/agents_backend.py`** —
that file is your reference implementation; yours is the Strands-SDK translation
of it. Requirements:

1. **One Strands agent turn per request**, using
   `STRANDS_RESEARCH_SYSTEM_PROMPT` from `platforms.strands.core` (already
   written — do not restate the prompt inline) and the model from
   `STRANDS_MODEL_ID` (env). Self-cap the run at `STRANDS_ANSWER_TIMEOUT_S`
   (default 40) — the Path A chain allows ~45s at the bridge (§5).

2. **Model on Amazon Bedrock, via the runtime's IAM role — NOT an API key.**
   This is the WS5 framework-isolation choice and it is fixed:
   - Locally, credentials come from the ambient AWS session (`AWS_PROFILE` in
     `.env`, `aws sso login`).
   - Hosted, the AgentCore runtime's execution role carries
     `bedrock:InvokeModel` (the deploy script grants it — §5). **There is no
     `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` equivalent for you, and you must not
     add one.** If Strands needs a Bedrock model provider configured, configure
     it to use the default credential chain and region `AWS_REGION`
     (us-east-1). Pin the exact Bedrock model id you use in your handback and in
     `STRANDS_MODEL_ID`; a Claude-on-Bedrock inference profile keeps this a
     clean framework-only comparison against the Claude AgentCore twin.

3. **Two Agentforce delegation tools, built per request**, closed over the
   inbound delegation depth AND the effective run trace id — identical in
   behaviour to the OpenAI backend's `_build_agentforce_tool` /
   `_build_agentforce_a2a_tool`:
   - `ask_agentforce` — GA Agent API via
     `platforms.agentforce.client.AgentforceClient.from_env()` (one
     module-level/process-lifetime client so the OAuth token survives across
     tool calls; **credentials stay host-side, never in prompts or tool
     schemas**). After building the client, override its `agent_id` with
     `SF_STRANDS_AGENT_ID` when set — this is the **Strands-paired Agentforce
     twin** (D25): the lab gives each cross-platform experiment its own
     Agentforce agent so it stays a closed two-platform system. Fall back to the
     shared `SF_AGENT_ID` when unset.
   - `ask_agentforce_a2a` — the same twin over the A2A protocol through the
     lab's hosted shim, via `interop.af_channel.ask_via_shim(message, meta,
     trace_id=trace_id)`. Used only when the operator's `[A2A-LAB ROUTING]`
     block selects the a2a-shim channel; honor it exactly as the OpenAI tool
     docstrings say.

4. **Every delegation goes through `interop.delegation`** (D27 — the delegation
   guard; §3.1). No exceptions, no hand-rolled riders.

5. **Wrap the run in an `interop.trace.Hop`** so the wire view stays honest:
   `source="strands-researcher"`, `target="strands-platform"`,
   `protocol="internal"`, `request_payload=req.to_dict()`. Set the hop's
   `response_payload` to the final text (+ any join id) on the way out. If the
   Strands/Bedrock response exposes a stable id (a Bedrock request id or a
   Strands run id), set `hop.platform_ref` to it — that is the only join key the
   observability layer will have for the platform-interior leg (mirror the
   OpenAI backend, which uses the OpenAI response id; ADR D18). If no such id is
   available, leave `platform_ref` unset rather than inventing one, and say so
   in your handback.

6. **Return an `AgentResponse`** with `text`, `session_id=req.session_id`,
   `latency_ms`, and a `raw` dict carrying at least
   `{"model": <model id>, "backend": "strands-sdk"}` plus any join id.

7. Tests in `tests/unit/test_strands_backend.py` (Strands SDK mocked — no
   network in the default suite; one `@pytest.mark.live` end-to-end is welcome
   but must be marker-gated so `uv run pytest` stays green offline).

## 3. The rules that are non-negotiable

1. **Delegation guard (D27).** Every outbound delegation goes through
   `interop.delegation`: check `inbound_depth >= delegation.max_depth()` and
   return `delegation.refusal(seam)` if so; otherwise compose with
   `delegation.delegate(question, caller="strands-sdk-agent", platform="strands",
   inbound_depth=..., trace_id=..., user_context=..., user_token=...)`. Read the
   inbound depth and user context from the request with
   `delegation.depth_of(req)` and `delegation.user_of(req)` — exactly as the
   OpenAI backend does. This is what stops circular agent-to-agent chains; a new
   tool path that skips it is a bug.

2. **Trace id threading.** Both per-request tool builders close over the
   effective trace id (`req.trace_id`, or the run's generated fallback via
   `interop.models.new_trace_id()`), alongside `inbound_depth`. Set `trace_id`
   on the direct `AgentforceClient` request AND pass it as the named `trace_id`
   argument to `ask_via_shim`. When threaded, the shim's interior hops land in
   the Aurora trace store under the same trace id and the console merges them
   into one call path. Your unit tests must prove both tools forward that same
   id without changing delegation metadata or routing behaviour.

3. **Shim credential naming.** Inside hosted runtimes the shim token is env
   `AF_SHIM_TOKEN` — `ask_via_shim` reads it first and falls back to
   `A2ALAB_TOKEN` locally. **Never introduce `A2ALAB_TOKEN` into runtime env or
   any code path that runs in the container**: setting it flips on the
   container's own inbound bearer auth, which `invoke_agent_runtime` cannot
   satisfy — every invoke 401s (learned live on the OpenAI side, 2026-07-20).

4. **Shim timeout discipline.** `AF_SHIM_TIMEOUT_S` defaults to 34 in code but
   is deployed as 28 — just under API Gateway's hard 29s ceiling. Don't add
   retries on top of `ask_via_shim` (it retries once internally).

5. **Tool governance.** The synchronous researcher's toolset is exactly the two
   Agentforce tools — nothing that can stall a headless 40s turn waiting on a
   permission prompt or a long fetch. (Lesson from the Claude side: a headless
   agent with built-in tools asked the model to grant a permission instead of
   answering.) Give the Strands agent only the two tools you build; disable any
   Strands built-in tools/toolkits.

6. **No environment identifier is hardcoded, anywhere.** No AWS account id, no
   region literal, no profile name, no model id baked into code — they come from
   env (`AWS_REGION`, `STRANDS_MODEL_ID`, etc.). Not even as a
   `${VAR:-default}` fallback. This is enforced repo-wide
   (`tests/unit/test_no_account_identifiers.py`); a hardcoded id will fail CI.

7. **After ask_agentforce returns, the model gets a synthesis round** (no
   stop-on-first-tool) so it can attribute the CRM portion ("From the CRM (via
   Agentforce): ...") and add its own research — the Path C collaboration
   contract. Budget still fits: cap the tool leg (~34s) inside the 40s run cap.

## 4. Files you may touch

| File | What |
|---|---|
| `src/platforms/strands/strands_backend.py` | **Your backend (create it).** |
| `tests/unit/test_strands_backend.py` | **Your tests (create it).** |
| `pyproject.toml` / `uv.lock` | Add a `strands` optional-dependency extra with the Strands SDK pin. Bump only inside that extra. Run `uv lock` so the lockfile matches. |

**Do not modify anything else.** In particular: `src/platforms/strands/core.py`,
`__main__.py`, `stub_backend.py` (the lab's scaffold — the stub is what keeps
everything runnable before you deliver); `src/interop/**` (shared seams: models,
delegation, af_channel, trace, clients, servers, secret_env);
`src/platforms/{claude,openai,agentforce,adk,foundry,guide}/**`;
`src/console/**`; `src/faces/**`; `config/**`; `deploy/**`; `tests/**` other than
your own file. If a task seems to require touching those, **stop and flag it in
your handback** rather than working around it.

## 5. Working in this environment

```sh
uv sync --extra strands            # after you add the extra (before that: uv sync)
uv run pytest                      # must stay green (live tests deselected by default)
uv run ruff check . && uv run ruff format .   # line-length 100
uv run pytest tests/unit/test_strands_backend.py -k <name>

# local manual run (REST server in front of your backend):
STRANDS_BACKEND=strands-sdk uv run python -m platforms.strands --protocol rest --port 8041
curl -s -X POST http://127.0.0.1:8041/invoke \
  -H 'content-type: application/json' \
  -d '{"message":"In two sentences: what is the difference between the MCP and A2A protocols?"}'
```

- Imports are package-prefix-free (`from interop.models import ...`); tests get
  `src/` on `sys.path` via `tests/conftest.py`.
- **AWS credentials for Bedrock:** local runs use the ambient session
  (`aws sso login`; `AWS_PROFILE` from `.env`). You do NOT provision anything —
  no runtime, no role, no secret. The lab owner runs
  `deploy/agentcore/deploy.sh strands` after your handback; that script builds
  `deploy/agentcore/strands.Dockerfile`, creates the runtime, and grants the
  execution role `bedrock:InvokeModel`. Your job is only that the backend runs
  correctly given working Bedrock credentials.
- `.env` holds `AWS_PROFILE`/`AWS_REGION`, `SF_*`, `AF_SHIM_A2A_URL`,
  `A2ALAB_TOKEN` (local only — see §3.3), `AF_SHIM_TIMEOUT_S`, and the
  `STRANDS_*` knobs (already documented in `.env.example`).
- Trace events land in `traces/YYYY-MM-DD.jsonl` + `traces/lab.db`; verify hops
  with `sqlite3 traces/lab.db "SELECT source,target,platform_ref FROM
  trace_events ORDER BY ts DESC LIMIT 5"`.
- The stub backend answers today with a `[strands-stub]` placeholder, so every
  protocol server, the loopback e2e suite, the matrix, and the console scenario
  already run. Your backend replaces the stub only when
  `STRANDS_BACKEND=strands-sdk`; leaving it unset must keep the stub working.

## 6. Build telemetry — best-effort bonus, not a requirement

The lab collects coding-agent telemetry (WS9) — cost/tokens/model per tool that
builds it — from Claude Code, Codex, and Cursor into CloudWatch. **Kiro is a
research candidate for that same path** (see
`build-notes/kiro/01-kiro-telemetry-research.md`). If Kiro can emit OTel
metrics to the lab's CloudWatch endpoint while you do this build, that build's
cost lands in the lab's telemetry alongside the others — a nice result, since
coding-agent telemetry cannot be backfilled. **This is aspirational and
contingent on the un-run Kiro OTel probes; it must NOT block delivery of the
Strands agent.** If the telemetry path doesn't work, build the agent anyway and
note it in your handback. Do not add telemetry code to the Strands backend for
this — the WS9 signal is about the build tool, not the built agent.

## 7. Explicitly out of scope (all handled on the lab side)

Protocol servers and A2A/MCP wiring (`src/interop/servers`, the faces); the
adapter, stub, and `__main__`; `config/targets.yaml`, `scenarios.yaml`,
`agents.yaml`; the `deploy/agentcore/strands.Dockerfile` and the
`deploy/agentcore/deploy.sh strands` case (including the Bedrock IAM grant and
the Secrets Manager wiring for the Salesforce credentials); console/scenario
surfacing (the `strands-to-agentforce` experiment is built and shows a
coming-soon state); the Agentforce→Strands reverse direction; the deployment map
and ADR. All of that already exists in the repo. **Your PR should contain
exactly: one new backend file, one new test file, and the `strands` extra in
`pyproject.toml`/`uv.lock`.**

## 8. Acceptance checklist (state each in your handback)

- [ ] `uv run pytest` fully green, including your `test_strands_backend.py`
      (the lab's `test_strands_platform.py` already passes and its
      `strands-sdk` case stops skipping once your backend imports)
- [ ] `uv run ruff check .` clean; `uv run ruff format .` applied
- [ ] Manual REST run answers the protocol question in < 40s without calling
      Agentforce; an Omega, Inc. (or other CRM) question triggers
      `ask_agentforce` and attributes the CRM portion
- [ ] Direct and A2A-shim tool tests prove both outbound calls carry the
      effective run trace id (§3.2) without changing delegation metadata
- [ ] The model runs on Bedrock via the default AWS credential chain — no API
      key added anywhere; the exact `STRANDS_MODEL_ID` is stated
- [ ] All delegation paths go through `interop.delegation`; the `[A2A-LAB
      ROUTING]` channel block is honored
- [ ] The run hop records `source=strands-researcher`,
      `target=strands-platform`; `platform_ref` carries a join id if one exists
      (say which, or say none does)
- [ ] No edits outside §4's file list; no new required dependency outside the
      `strands` extra
- [ ] Handback notes: what changed, the Strands SDK + Bedrock model versions
      pinned, whether a platform_ref join id was available, whether the Kiro
      build-telemetry path worked, and anything that didn't fit the contract
      (flagged, not worked around)
