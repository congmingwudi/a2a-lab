# Codebase Review

Reviewed 2026-09-05 at commit `b7abff0`. Static inspection of source, configuration, deployment definitions, and tests; deployed behavior and test results were not verified. Existing files were left unchanged.

## Likely Risk Areas

Ordered by operational impact. These are code-backed findings and risk hypotheses, not reproduced production incidents.

1. **Brief failures can become permanently skipped.** The watcher catches a session-processing exception, then still adds that session to the serviced set. Delivery also performs separate Salesforce inserts for the brief and Task, so partial success followed by replay can duplicate records. Prioritize failure/restart tests and delivery idempotency. Sources: [watch loop](../../../src/briefs/__main__.py#L92), [delivery](../../../src/briefs/salesforce.py#L97).
2. **Async durability is uneven.** Fan-out tasks persist in Aurora, but `run_task` reads state and marks WORKING without an atomic claim or lease: overlapping workers can both execute, and abrupt termination can leave a task stuck. Generic A2A servers use `InMemoryTaskStore`, making task polling sensitive to restart and replica routing. Sources: [task worker](../../../src/fanout_mcp/tasks.py#L203), [A2A server](../../../src/interop/servers/a2a.py#L113).
3. **CRM access and evidence retention need explicit boundaries.** The account-summary Apex class deliberately runs `without sharing`; its summaries include opportunity amounts and case subjects. Trace redaction targets credentials, preserving business content. End-user visibility therefore cannot be inferred from identity propagation alone. Sources: [Apex action](../../../salesforce/force-app/main/default/classes/A2ALabGetAccountSummary.cls#L14), [redaction](../../../src/interop/trace.py#L73).
4. **Packaging can pass source-tree tests and fail after installation.** Hatch's explicit wheel package list omits `briefs`, `faces`, `fanout_mcp`, and `mcp_http`. Tests inject `src/` into the import path, while containers copy source directly, masking wheel omissions. Some image and Lambda dependencies are also installed outside `uv.lock`. Sources: [packaging](../../../pyproject.toml#L53), [test setup](../../../tests/conftest.py#L4), [console image](../../../deploy/console/Dockerfile), [fan-out bundle](../../../deploy/fanout/build_zip.sh).
5. **Routing and protocol comparisons depend on configuration.** Missing environment substitutions become empty strings; deployment mode remaps target names; A2A includes a 0.3 compatibility layer. Delegation depth depends partly on caller-provided text/metadata, not a tamper-proof hop counter. Validate actual destinations, trace continuity, timeout behavior, and answer content per route. Sources: [registry](../../../src/interop/registry.py), [delegation](../../../src/interop/delegation.py), [compatibility](../../../src/interop/servers/a2a_compat.py).
6. **Console changes have a broad regression surface.** The backend is 4,613 lines and its single HTML/CSS/JS asset is 10,433 lines. API and markup tests exist, but no browser automation configuration or CI workflow was found. Prioritize browser checks for login transitions, background runs, navigation, and streamed updates. Sources: [backend](../../../src/console/app.py), [frontend](../../../src/console/static/index.html), [tests](../../../tests/unit/test_console.py).

## Module Map

Python 3.11+ interoperability lab comparing six agent platforms over REST, MCP, A2A, and platform-native APIs. Imports use top-level packages under `src/`.

| Module / tree | Responsibility |
|---|---|
| `src/interop/` | Canonical `AgentRequest`/`AgentResponse`; adapter interface; protocol clients/servers; registry, auth, identity, delegation, cloud credentials, wire tracing. |
| `src/platforms/` | Claude managed/SDK backends, OpenAI and Strands SDK/stub backends, Agentforce client/shims, ADK agent/tools, Foundry client/configuration. `guide/` provides document-grounded lab Q&A. |
| `src/bridge/`, `src/faces/` | Apex-facing REST gateway to registered targets or fan-out; one ASGI service mounting 14 protocol faces. |
| `src/orchestration/` | Business-unit leg definitions, concurrent dispatch, per-leg outcomes/timeouts, A2A submit/poll, managed-agent orchestration. |
| `src/fanout_mcp/`, `src/mcp_http/` | Remote fan-out tools and durable task workers; shared lightweight MCP HTTP/JSON-RPC transport. |
| `src/briefs/` | Scheduled managed-session watcher, host-side tool execution, Salesforce brief/activity/notification delivery. |
| `src/observability/`, `src/obs_mcp/` | Platform, coding-agent, and infrastructure harvesters; SQLite/Aurora stores; analyst query and brief-writing tools. |
| `src/console/` | FastAPI experiment UI/API, authentication, traces, observability, architecture, insights, and project views. |
| `salesforce/` | Apex actions/tests, Agent Script authoring and published planner metadata, credentials, CRM objects, Lightning components. |
| `config/`, `scripts/`, `deploy/`, `plan/`, `requirements-docs/` | Routing/scenarios, operational tools, cloud packaging/provisioning, decisions and requirements. Some documentation/configuration is runtime console content. |

## Data Flow

1. **Interactive:** console/API request -> scenario and target registry -> protocol/native client -> platform agent. External-agent tools consult Agentforce; Agentforce delegates through Apex -> bridge -> selected target, with a direct Agent Engine action also available. Adapters normalize responses into the shared contract.
2. **Fan-out:** orchestrator -> business-unit legs -> concurrent calls or submit/poll -> per-leg results -> synthesized brief. Durable MCP submission writes Aurora state and dispatches a separate Lambda invocation; later checks read that state.
3. **Scheduled briefs:** managed deployment -> watched session -> `save_account_brief` host tool -> Salesforce Account lookup -> brief record -> completed Task -> best-effort notification. Serviced-session state persists locally or in Aurora.
4. **Evidence:** calls -> `Hop`/wiretap -> credential scrub -> JSONL + SQLite by default, configurable Aurora/DynamoDB sinks. Harvesters independently collect platform interiors; native execution IDs and text riders join them to lab traces. Console and analyst tools consume these stores.

## Test Strategy

- **Default:** `uv run pytest` selects unit and local loopback tests; `live` is excluded by configuration. Fixtures isolate traces and clear hosted auth/mode/database variables.
- **Unit:** request validation, auth/roles, routing, delegation, backend tools, harvesting, store queries, async state, configuration contracts, and account-identifier checks. HTTP/client fakes avoid cloud calls; PostgreSQL read tests replay canned rows rather than execute SQL.
- **Loopback:** real local REST/MCP/A2A servers verify envelopes, responses, trace evidence, concurrent protocols, identity metadata, 0.3 compatibility, and async task lifecycle.
- **Live and Salesforce:** `uv run pytest -m live` exercises Claude/Agentforce round trips with credentials and trace assertions. Apex tests separately cover account summaries and remote/direct callout actions in Salesforce.
- **Limits:** optional ADK tests can skip without extras; mocked tests do not establish cloud IAM, SQL execution, deployment completeness, or browser behavior. No tests, builds, installations, or live calls were run for this review.
