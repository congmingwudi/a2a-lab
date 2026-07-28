# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A2A Interop Lab: cross-platform agent-to-agent experiments across Salesforce Agentforce, Claude (Managed Agents + AgentCore sdk), OpenAI (AgentCore), Google ADK (Vertex AI Agent Engine), and Microsoft Foundry, with each direction runnable over REST, MCP, and the A2A protocol — same scenario, protocols compared side by side with raw wire payloads recorded. `plan/` is the source of truth: decision log (ADRs) in `plan/00-decisions.md`, architecture and protocol mapping rules in `plan/01-architecture.md`, the honest protocol matrix in `plan/02-matrix.md`, runbooks in `plan/04-runbooks.md`, the observability plan (M11: cross-platform agent execution logs pulled into the console) in `plan/05-observability.md`, the Codex build brief for the OpenAI agents-sdk backend in `plan/06-openai-codex-handoff.md` (D24 — that one file is the contract; the `agents-sdk` backend and its tests are Codex's to write, everything else OpenAI-related is ours), the multi-platform buildout roadmap in `plan/07-workstreams.md` (WS1–WS12 — the platform pairs, then fan-out orchestration (WS8), build telemetry (WS9), hosted completion (WS7), Agent Fabric (WS10), A2A fire-then-poll (WS11) and the cost sentinel (WS12); the console/exhibit backlog lives at the end of the same file), the operator runbooks in `plan/10-operations.md` (rotating passwords and the JWT keypair, code-vs-config deploys, moving the brief watcher, and what to check when the console looks broken), and the published field insights in `plan/08-insights.md` — generated, don't edit: the sources are `config/insights.yaml` and `config/diagrams.yaml` (mermaid diagrams attached to insight ids — a chip on each insight tile in the console opens the diagram, and the export embeds it as a ```mermaid fence; regenerate with `uv run python scripts/export_insights.py`).

## Commands

```sh
uv sync --all-extras                 # install (Python 3.11+, uv-managed). Plain `uv sync`
                                     # PRUNES extras: the adk tests then skip and the
                                     # foundry/adk harvests fail on missing client libs.
uv run pytest                        # unit + loopback e2e; live tests deselected by default
uv run pytest tests/unit/test_bridge.py            # one file
uv run pytest tests/unit/test_bridge.py -k name    # one test
uv run pytest -m live                # tests needing real credentials (marker: live)
uv run ruff check . && uv run ruff format .        # lint / format (line-length 100)

scripts/run_local.sh                 # full local stack (Claude servers, shims, bridge, console)
uv run python scripts/matrix.py      # run every runnable protocol cell → appends plan/03-results.md
uv run python scripts/sf_smoke.py    # Agentforce go/no-go (needs SF_* in .env)
uv run python scripts/identity_preflight.py  # prove every caller identity can still do its job (D37/F6)
uv run python scripts/obs_harvest.py # pull platform execution logs → traces/lab.db (M11)
uv run python scripts/obs_harvest.py coding  # just the coding-agent telemetry (WS9); same
                                     # pull as the Harvest button in the console's Coding
                                     # Agents Telemetry section. NOT in the unqualified
                                     # sweep — `coding` is not an agent platform.
uv run python scripts/trace_import.py # rebuild lab.db trace tables from the JSONL archive
uv run python scripts/setup_managed_agent.py       # once: provisions the Managed Agents agent
uv run python scripts/obs_analysis.py run          # fire the hosted obs analyst (D23)
uv run python scripts/cost_sentinel.py run         # fire the weekly cost sentinel (WS12/D44);
                                     # setup_cost_sentinel.py provisions it, created PAUSED
uv run python scripts/pg_backfill.py               # copy local lab.db ROWS → hosted Aurora
uv run python scripts/pg_migrate.py                # apply observability.pg DDL as the table
                                     # OWNER (needs A2ALAB_PG_MASTER_SECRET_ARN). The only
                                     # path that can ALTER: lab_writer cannot, and pg_backfill
                                     # used to swallow that as "assuming provisioned" (D46).
deploy/obs/build_zips.sh                           # rebuild the obs Lambda bundles (D23);
                                     # SHIP them with deploy/obs/deploy_harvest.sh [--code]
                                     # and deploy/obs/expose_mcp.sh [--code] — building is
                                     # not deploying, which is how the MCP function ran
                                     # hand-pushed code for three days (D46)
deploy/agentcore/deploy.sh <claude|openai>         # build/push/create-or-update an AgentCore runtime (D26)
deploy/adk/deploy_adk.py                           # deploy/update the ADK agent on Vertex AI Agent Engine (WS2)
uv run python deploy/foundry/provision_foundry.py  # provision/update the Foundry agent + connection + inbound A2A (WS3)
deploy/shim/build_zip.sh && deploy/shim/deploy_shim.sh  # hosted Agentforce A2A shim on Lambda (D28)
uv run python deploy/fanout/provision_gcp_federation.py # once: AWS->GCP workload identity (D41)
deploy/fanout/build_zip.sh && deploy/fanout/deploy_fanout.sh  # the remote MCP fan-out server (D41)
uv run python scripts/run_fanout.py --orchestrator cma-mcp    # model-scheduled fan-out (D41)
uv run python scripts/export_insights.py           # config/insights.yaml + diagrams.yaml → plan/08-insights.md
```

`A2ALAB_MODE=hosted` in .env remaps `claude-rest`/`openai-rest` to the AgentCore runtimes wherever clients are resolved (bridge, tools, console runs; matrix.py is exempt) — the local↔hosted dev switch (D26). Restart the stack after flipping.

Code under `src/` is imported without a package prefix (`from interop import ...`); tests add `src/` to `sys.path` via conftest, and scripts run with `PYTHONPATH=src` (run_local.sh does this). Config comes from `.env` (see `.env.example`).

## Architecture — the two seams

Everything hangs off two abstractions sharing the canonical `AgentRequest`/`AgentResponse` models (`src/interop/models.py`):

- **Inbound** — `interop.adapter.AgentAdapter`: an agent we host implements `handle(AgentRequest) -> AgentResponse` once; `serve(adapter, protocol, port)` mounts it behind REST (`:8001`), MCP (`:8002`), or A2A (`:8003`) via `src/interop/servers/`.
- **Outbound** — `interop.clients.base.RemoteAgentClient`: `ask(AgentRequest) -> AgentResponse`, one client per protocol (`rest.py`, `mcp.py`, `a2a.py`) plus the platform-native `AgentforceClient`. Clients are resolved by target name through `interop.registry.Registry`, driven by `config/targets.yaml` (each target has an honest `status`: native / via-bridge / via-shim / blocked-beta — keep it honest, it feeds plan/02-matrix.md).

Because both seams share the same models, the loopback e2e suite (`tests/e2e/test_loopback.py`) proves all three client×server pairings against a deterministic EchoAdapter with no external platforms.

A platform = one directory under `src/platforms/<name>/` contributing an `AgentAdapter` and/or a `RemoteAgentClient`, plus entries in `config/targets.yaml`. Nothing in `interop/` or other platforms changes when adding one.

### Key components

- `src/platforms/claude/` — one adapter (`core.py`), two backends selected by `CLAUDE_BACKEND`: `managed_backend.py` (Anthropic Managed Agents beta, the default) and `sdk_backend.py` (self-hosted claude-agent-sdk, the fallback and the AgentCore containerization path). Nothing outside the adapter knows which backend runs. Path B (`ask_agentforce`) is a host-side custom tool under managed, an in-process SDK MCP tool under sdk — Salesforce credentials never enter the managed sandbox.
- `src/platforms/agentforce/` — GA Agent API client (`client.py`) plus MCP (`:8021`) / A2A (`:8023`) shims proxying to the Agent API (Agentforce has no GA MCP/A2A inbound). The AWS-hosted shim (D28, Lambda) additionally captures raw inbound A2A envelopes (wiretap) and translates the 0.3 dialect (`interop/servers/a2a_compat.py` — Foundry speaks 0.3, Agent Engine requires 1.0).
- `src/platforms/foundry/` — Microsoft Foundry prompt agent (gpt-5-mini): platform-side Agentforce consult via Foundry's A2A tool → hosted shim; incoming A2A enabled (second platform-native A2A endpoint, Entra-only). `core.py` instructions are pushed by the provision script.
- `src/bridge/` (`:8100`) — Path A: Agentforce's outbound is REST-only, so its Apex callout hits the bridge, which fans out to any target/protocol per `config/targets.yaml` — switching protocol needs no Salesforce redeploy.
- `src/console/` (`:8200`) — lab console: groups trace events by trace_id, protocol badges, raw request/response, SSE live tail.

### Delegation guard (D27)

Every delegation seam (the three `ask_agentforce` tool paths and the bridge) stamps outbound requests with the standard rider + `metadata["delegation"]` from `src/interop/delegation.py`, and refuses onward delegation at depth ≥ `A2ALAB_MAX_DELEGATION_DEPTH` (default 1) — this is what stops circular agent-to-agent chains. New delegation paths (new platforms' tools, new outbound seams) must go through `delegation.delegate()` / `delegation.allowed()`.

### Trace layer (core requirement)

Every hop appends a `TraceEvent` with the **raw wire bytes** to `traces/YYYY-MM-DD.jsonl` (`src/interop/trace.py`). REST captures at handler level; MCP/A2A use the WireTap ASGI middleware (`src/interop/servers/wiretap.py`) because the JSON-RPC envelopes live inside the frameworks. Trace correlation rides `X-Trace-Id` (REST), a tool argument (MCP), and `metadata.trace_id` (A2A). New code paths must record trace events; tests get an isolated trace dir via the autouse fixture in `tests/conftest.py`.

## Salesforce side

Org metadata (Apex invocable `A2ALabInvokeRemoteAgent` + test, named/external credentials) lives in `salesforce/`, strictly namespaced `A2ALab*` — it deploys to the user's **production org**, so Apex deploys require test runs with ≥75% coverage. Deploys go through the Salesforce DX MCP server registered in `.mcp.json` (use those MCP tools for org auth, metadata deploys, Apex tests); the raw `sf` CLI is the documented fallback in plan/04-runbooks.md.

## Conventions

- Decisions get an ADR entry appended to `plan/00-decisions.md`; measured results go to `plan/03-results.md` (matrix.py appends there), findings to the ledger in `plan/02-matrix.md`.
- **No environment identifier is hardcoded anywhere in this repo — they all live in `.env`.** That means AWS account ids, GCP project ids, Azure subscription/resource names, Salesforce org ids, and the SSO profile name. Not as a literal, not as a `${VAR:-default}` fallback (a fallback is a hardcode that only shows up on someone else's machine), not in a comment, and not in deployable Salesforce metadata — `A2ALab_GCP.externalCredential-meta.xml` is gitignored for exactly this reason. Use `${VAR:?set VAR in .env}` in shell and `os.environ[...]`/`--flag` defaults in Python, so a missing value fails loudly at the top instead of silently targeting the wrong cloud. `tests/unit/test_no_account_identifiers.py` enforces the AWS half. Values are synced with `uv run python scripts/env_sync.py pull|push|diff` (AWS Secrets Manager, D39). The secret-bearing files that Secrets Manager does *not* cover — `.a2alab/lab_jwt_private.pem`, the Cloudflare origin key, `f6-eca-wiring.md`, and the two `*_mcp.json` files that carry bearer tokens despite their names — are age-encrypted into a private chezmoi dotfiles repo (D45). **A new secret-shaped file under `.a2alab/` is not picked up automatically**: the daily sync only re-adds files already tracked, so it needs a one-time `chezmoi add --encrypt` (drop the path in `~/projects/chezmoi-dotfiles-setup/chezmoi-tracking/to-track.md`).
- **Nothing in this repo names a cloud ACCOUNT.** Docs, code, comments and the console say `AWS — us-east-1`; never an account id, never the SSO profile name, never company-vs-personal labels on GCP/Azure. The region is what a reader needs for latency and residency; the account identifies whose cloud it is and who is paying. Concretely: write `aws sso login` (no `--profile`), take the profile from `AWS_PROFILE` in `.env`, and build ARNs from `$AWS_ACCOUNT` — which `deploy/aws_preflight.sh` sets from `aws sts get-caller-identity` and checks against `A2ALAB_AWS_ACCOUNT_ID`. **Every AWS deploy script sources that preflight**, so a wrong-account deploy fails before it creates anything; a new deploy script must source it too. The account mapping lives in the gitignored `.a2alab/accounts.md`.
- **`tmp-docs/` is never surfaced anywhere.** It is gitignored scratch space for the author's preliminary thinking before it becomes a workstream — so it must stay out of the `/api/docs` whitelist, out of the Lab Guide corpus (`src/platforms/guide/corpus.py`), and out of the doc-chip pattern. Citing a `tmp-docs/` path in a checked-in doc is fine as provenance, but say it is a local note so a reader is not hunting for a file the repo does not contain.
- **When you write UI copy or a doc, CITE the ADRs and docs it depends on by their real `D<n>` / `plan/*.md` form** — the chip mechanism below only renders references that exist, so an explanation that mentions no sources produces no chips. This is the authoring half of the rule and the half that gets skipped: the mechanism is automatic, writing the references is not.
- **Markdown surfaced in the console is explorable, never a dead end.** Any doc the UI renders (Lab Guide answers, experiment details, insight refs, the Architecture section) turns explicit references into clickable chips: `D<n>` opens the ADR, and a whitelisted doc path (`plan/*.md`, `docs/*.md`, `build-notes/**/*.md`, `README.md`) opens that file rendered, via `/api/docs`. This is automatic — `linkifyDecisions` runs under a MutationObserver over every render path — so new UI that renders markdown gets it for free, but two things break it: a ref written in a form the regex doesn't match (keep `DOC_REF_RE`/`REF_SPLIT_RE`/`DOC_PATH_SRC` in step), and a path the `/api/docs` whitelist doesn't serve (a chip that 404s is worse than plain text). Adding a new doc tree means updating both.
- **`--skip-build` ships a new task definition against the OLD image.** Every Fargate deploy script takes it, and it only skips the image — the task definition, its env and the secret are all rewritten either way. So a **config-only** change (an env var, a rotated credential) is safe with `--skip-build`; anything touching `src/`, `config/` or a Dockerfile needs a **full rebuild**, because `COPY src ./src` and `COPY config ./config` bake those into the image. The failure mode is silent and reads as a code bug: the env is right and the code that reads it is stale. It cost three separate debugging rounds on 2026-07-28 — a renamed env var (`A2ALAB_FACES_BASE`), a new `config/targets.yaml`, and a new `identity.PRIVATE_KEY_ENV` — each shipped as "deployed" while the container ran yesterday's code. When a fix does not take effect, check the image before re-reading the code.
- **Deploying anything new, anywhere, means updating `plan/09-deployment-map.md` in the same change** — it is the answer to "what is deployed, where, and why there", and it is the doc that rots fastest because a deploy script is edited without it. Concretely, a new or changed hosted component needs: its box in the L0 estate diagram and the relevant L1–L5 level, a row in the **L6 code → deployment table** (repo path → deploy command → what it creates → which cloud), and — when the hosting shape differs from its neighbours — a line in "Why not, in one place" saying what its work costs in seconds and why that ruled the shape. The console's Architecture section parses this file, so the UI follows for free. Applies to a new platform, a new Lambda/task/runtime, a new federation, or a component moving hosts.
- Streaming is out of scope for v1 (Apex callouts are buffered). D11 scoped one A2A SSE demo as a capability comparison; it has **not** been built — the servers advertise `AgentCapabilities(streaming=True)` and the client hard-codes `ClientConfig(streaming=False)`, so don't describe streaming as exercised.
- Timeout budget for Path A is tight (Agentforce action ~85–90s **measured** 2026-07-25, plan/03-results.md — not the ~60s long assumed → Apex 110s → bridge 45s → `CLAUDE_ANSWER_TIMEOUT_S=100`); keep the Claude agent fast (Haiku-tier model, concise prompts, warm servers). When the action budget IS blown, Agentforce returns 200 with the delegated section present but empty — check content, not status.
