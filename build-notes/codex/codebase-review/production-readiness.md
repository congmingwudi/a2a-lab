# Production Readiness Review

**Assessment: not ready for sensitive production data, mutually untrusted users, or unattended consequential writes.** The repository is an interoperability evaluation lab, and [NFR-201](../../../requirements-docs/50-system/03-nonfunctional-requirements.md#L172) explicitly excludes production availability. The findings below identify both existing defects and additional controls needed to change that operating model.

Reviewed **2026-09-05**, commit **`a806b6d`**, including the newly merged LangGraph and MuleSoft paths. Application code and configuration were not changed. P1 means resolve before the production uses above; P2 means resolve before sustained production operation. Findings describe repository behavior, not verified deployed incidents.

## Findings

### F01 - P1: Viewer authorization stops at the console boundary

The console blocks viewers from `/api/run`, but protocol middleware accepts any verified lab JWT without checking its role or permitted destination. The REST handler then invokes the adapter directly. A viewer can therefore bypass the console's experiment restriction by calling a protocol face. The console's path-based operator gate also omits `DELETE /api/traces`; the handler deletes local JSONL files without another role check. This does **not** delete Aurora or SQLite rows, but it allows a viewer to remove the local evidence archive.

**Evidence:** [JWT admission](../../../src/interop/servers/auth.py#L110), [REST invocation](../../../src/interop/servers/rest.py#L32), [operator gate](../../../src/console/app.py#L1147), [trace deletion](../../../src/console/app.py#L2858). Offline probes confirmed viewer admission and the deletion-gate omission; no real records were deleted.

**Required:** authorize principal, action, target, and tool at every entry point; gate destructive methods explicitly. Scope service tokens to their intended audiences/capabilities. Human role-shared passwords in [identity.py](../../../src/interop/identity.py#L187) also cannot establish individual accountability.

### F02 - P1: Conversations are not isolated by authenticated caller

The hosted shim enables `session_reuse=True`. Sessionless requests receive `shim-shared-<platform>`, so unrelated callers using the same platform share an Agentforce conversation. Other session caches accept a caller-supplied session ID without an owner binding. This can mix conversation history, affect answers across users, and permit conversation reuse by anyone who obtains an ID. Agentforce session creation also lacks a per-key lock, allowing concurrent creates to race.

**Evidence:** [hosted setting](../../../deploy/shim/handler.py#L33), [shared key](../../../src/platforms/agentforce/proxy.py#L61), [session cache](../../../src/platforms/agentforce/client.py#L141), [managed cache](../../../src/platforms/claude/managed_backend.py#L107). The probe gave two different user contexts the identical shim session key.

**Required:** bind server-issued conversation IDs to authenticated subject and tenant; serialize turns per conversation; define expiry, cleanup, and durable ownership. Reuse connections and OAuth tokens independently of conversation history.

### F03 - P1: CRM access and write targets are not constrained to the requesting principal

The account-summary action explicitly runs `without sharing`, queries opportunities/cases without user-mode enforcement, and returns their business content. The Agent API client starts sessions with `bypassUser=True`; propagated lab identity does not become an enforced CRM record-access policy in the inspected code. Separately, the brief writer trusts a model-provided account name and chooses the first partial-name match. An empty or ambiguous name can select an unintended account for a real write.

**Evidence:** [Apex queries](../../../salesforce/force-app/main/default/classes/A2ALabGetAccountSummary.cls#L14), [session identity](../../../src/platforms/agentforce/client.py#L115), [model-selected delivery](../../../src/briefs/runner.py#L343), [account lookup](../../../src/briefs/salesforce.py#L97).

**Required:** establish an explicit service-versus-end-user access model, enforce record/field access, and bind delivery to a preauthorized Account ID. Reject ambiguous selections and test unauthorized records. Actual Salesforce grants and data classification still require deployment verification.

### F04 - P1: Raw-wire credential redaction misses ordinary JSON secrets

`redact()` removes sensitive dictionary keys, but string payloads only receive regex replacement. Wiretap passes raw JSON as a string. Consequently, an opaque credential in `{"password":"..."}` or `{"client_secret":"..."}` survives unless its value happens to match a token-specific regex. The offline probe confirmed that the same payload is scrubbed as a dictionary and preserved as serialized JSON. This affects the shared path into trace sinks, whose raw content is viewer-readable.

**Evidence:** [scrubber](../../../src/interop/trace.py#L123), [wire decoding](../../../src/interop/servers/wiretap.py#L90), [trace reads](../../../src/console/app.py#L2871). Existing [redaction tests](../../../tests/unit/test_models_and_trace.py#L162) primarily cover structured keys and recognizable token formats.

**Required:** redact structured credentials inside serialized wire formats before storage, including escaped/nested payloads and truncation boundaries. Add sink-level tests with opaque sentinel credentials; restrict raw evidence access and define business-content redaction separately.

### F05 - P1: Managed research tools and network access exceed the narrow business task

Provisioning enables the entire managed prebuilt toolset and unrestricted networking. The code comment identifies shell, file, and web tools in that set. Research agents also receive tool results containing CRM information or generate content later written to CRM. This creates an exfiltration and workflow-manipulation path if an instruction embedded in retrieved content influences tool use. This is a configured exposure, not a demonstrated prompt-injection exploit. Keeping Salesforce credentials host-side is useful, but does not restrict what returned business data can leave the sandbox.

**Evidence:** [managed tools](../../../scripts/setup_managed_agent.py#L33), [network configuration](../../../scripts/setup_managed_agent.py#L102), [brief provisioning](../../../scripts/setup_brief_agent.py#L47).

**Required:** allow only necessary tools and network destinations; separate external research from protected CRM access; validate write arguments in deterministic code. Exercise adversarial retrieved-content and unauthorized-egress cases. The [self-hosted SDK](../../../src/platforms/claude/sdk_backend.py#L139) already demonstrates a narrower tool configuration.

### F06 - P1: Missing auth configuration can expose hosted invocation

The bridge skips authentication when `BRIDGE_TOKEN` is absent. MCP HTTP similarly treats a missing expected token as authorization success. Bridge startup loads secrets but never asserts its token exists. The fan-out deployment builds its secret by silently omitting absent values, then its handler passes the resulting token into the permissive MCP wrapper. A successful secret fetch therefore does not prove invocation auth is configured. Console/faces startup checks partially mitigate this elsewhere, but are not universal.

**Evidence:** [bridge check](../../../src/bridge/app.py#L58), [bridge startup](../../../src/bridge/app.py#L265), [MCP auth](../../../src/mcp_http/http.py#L23), [secret construction](../../../deploy/fanout/deploy_fanout.sh#L63), [fan-out startup](../../../src/fanout_mcp/lambda_entry.py#L44).

**Required:** fail startup/deployment for missing hosted credentials; make unauthenticated local mode an explicit opt-in. Test missing-key and empty-value configurations for every deployed entry point. Current deployed token values were not inspected.

### F07 - P1: Retries and interruption can duplicate work or permanently lose delivery

The fan-out worker reads state and marks WORKING without an atomic claim. Concurrent deliveries can both call the agent; the probe executed the runner twice for one task. Abrupt worker termination can leave WORKING indefinitely because there is no lease/reaper. Generic A2A tasks remain in memory and can disappear across restart/replicas. Independently, the brief watcher marks a session serviced after catching a processing failure, and Salesforce delivery uses separate brief/Task inserts without an idempotency boundary.

**Evidence:** [worker](../../../src/fanout_mcp/tasks.py#L203), [state update](../../../src/fanout_mcp/tasks.py#L138), [A2A storage](../../../src/interop/servers/a2a.py#L122), [watcher](../../../src/briefs/__main__.py#L113), [delivery](../../../src/briefs/salesforce.py#L97).

**Required:** atomic task claims, expiring leases, recovery/dead-letter handling, durable A2A state, and idempotent CRM delivery keyed by session/tool call/account. Record failed sessions as retryable or explicitly terminal failures, rather than serviced successes. Existing duplicate-task coverage tests sequential completion, not overlap.

### F08 - P1: Async timeout accounting does not enforce the advertised total budget

`_run_leg_async` gives submission the full timeout, starts a fresh deadline afterward, and awaits each poll without a remaining-time bound. A late terminal poll can return success after the deadline. `run_target_async` can then allocate another full timeout to a blocking fallback after an accepted submission becomes unpollable. The original remote work is not cancelled before that second invocation. This can exceed the surrounding Apex/router budget and bill duplicate work.

**Evidence:** [submit/poll loop](../../../src/orchestration/runner.py#L332), [fallback](../../../src/orchestration/runner.py#L421), [bridge budget use](../../../src/bridge/app.py#L143). Probe: a **20 ms** budget returned success after approximately **61 ms** with a slow fake poll; this is a local control-flow result, not a cloud latency measurement.

**Required:** one absolute deadline covering submission, polling, backoff, and fallback; bound every await by remaining time. Do not replay accepted work without cancellation or idempotency. Test slow submission, hung/late polling, transient task disappearance, and client disconnect.

### F09 - P1: Existing green results do not establish business correctness or safety

The matrix marks any non-exception response PASS, including an empty answer; an offline fake-client probe confirmed this. Its default is one run of a generic protocol question, with no business rubric, grounding check, tool-policy assertion, cost ceiling, or latency threshold. Live tests mainly verify nonempty answers and trace participation for Claude/Agentforce and the MuleSoft broker. No versioned adversarial/business evaluation corpus or automated release workflow was found; `.github/` contains CODEOWNERS. External CI configuration was not inspected.

**Evidence:** [matrix success criterion](../../../scripts/matrix.py#L36), [default run count](../../../scripts/matrix.py#L100), [live round trips](../../../tests/e2e/test_live_roundtrip.py#L113), [broker smoke](../../../tests/live/test_mule_broker.py#L22), [test selection](../../../pyproject.toml#L59).

**Required:** gate releases on scenario-level expected facts and provenance, prohibited tool calls, account isolation, prompt injection, partial failure, supported protocol parity, and latency/cost budgets. Preserve model/prompt/configuration versions with results. Existing unit/loopback coverage is a useful base, not evidence that these evaluations passed.

### F10 - P2: Spend controls are retrospective and uneven

The cost sentinel reports usage; it does not admit or reject requests against a budget. No application-level request rate limits, per-principal quotas, global concurrency controls, or spend admission checks were found in inspected source/deploy configuration. Viewers can call the Guide repeatedly, and generic message validation checks type but not size. Wiretap buffers the complete request before clipping its recorded representation. Per-turn limits exist in some backends, but do not bound aggregate spend or memory pressure across requests.

**Evidence:** [cost reporting](../../../scripts/cost_sentinel.py#L1), [Guide endpoint](../../../src/console/app.py#L2046), [Guide round/token limits](../../../src/platforms/guide/core.py#L27), [request validation](../../../src/interop/models.py#L43), [body buffering](../../../src/interop/servers/wiretap.py#L123).

**Required:** enforce body/input limits, concurrency and per-principal quotas, per-run tool/token budgets, and a spend circuit breaker. Reconcile provider usage separately from admission estimates; include failed attempts, retries, research tools, and cloud infrastructure. Verify any externally managed gateway/provider caps before relying on them.

### F11 - P2: Successful observability responses can conceal incomplete evidence

LangSmith harvesting reads one page of 100 runs, ignores pagination, and reports success; larger windows silently lose sessions/spans. Console Guide requests call `answer_stream()` directly, bypassing the `Hop` in `GuideAdapter.handle()`, while the stream loop does not record returned token usage. Wiretap also classifies success by HTTP status, so a JSON-RPC error carried in HTTP 200 can appear as an OK envelope hop. These paths weaken cost attribution, failure rates, and trace-completeness claims.

**Evidence:** [LangSmith query](../../../src/observability/langgraph_source.py#L196), [harvest result](../../../src/observability/langgraph_source.py#L225), [Guide path](../../../src/console/app.py#L2064), [Guide instrumentation](../../../src/platforms/guide/core.py#L234), [wire status](../../../src/interop/servers/wiretap.py#L180).

**Required:** paginate with checkpoints and completeness indicators; instrument every paid entry point with run IDs, principal, usage, outcome, and provider references. Distinguish HTTP, protocol, tool, and business outcomes. Alert on stale harvests and incomplete joins.

### F12 - P2: Trace persistence can both delay requests and silently lose their audit trail

`TraceRecorder.record()` synchronously calls each sink from request/tool paths; the Postgres sink performs blocking database I/O. Database delay therefore adds request latency and can block an async worker's event loop. When a sink fails, the recorder prints a warning and still returns success; there is no durable retry queue or dropped-event counter in that path. Avoiding a logging-induced request failure is reasonable, but it does not establish an auditable production workflow.

**Evidence:** [recorder](../../../src/interop/trace.py#L394), [Postgres sink](../../../src/observability/pg.py#L1165), [REST call site](../../../src/interop/servers/rest.py#L65).

**Required:** bounded asynchronous persistence with durable retry where audit completeness is required, explicit backlog/drop metrics, and an alarmed degraded state. Load-test with a slow/unavailable sink and decide which consequential operations require durable audit acknowledgement.

### F13 - P2: Analyst data permissions and retention exceed a production minimum

The analyst accepts arbitrary SELECT/WITH SQL. Database read-only grants and a 15-second statement timeout are meaningful controls, but `lab_reader` receives SELECT on every current and future table in the schema, including raw traces and task/state data. The 200-row tool limit is applied after query execution. Tools share a bearer and can write arbitrary brief kinds. No scheduled purge for Aurora/SQLite evidence was found; DynamoDB's TTL is a separate, narrower mechanism.

**Evidence:** [SQL tool](../../../src/obs_mcp/tools.py#L90), [brief writes](../../../src/obs_mcp/tools.py#L120), [database grants](../../../src/observability/pg.py#L273), [trace sinks](../../../src/interop/trace.py#L278).

**Required:** expose purpose-specific sanitized views/query operations; separate read and write capabilities per analyst; bound query work before materialization. Define and test retention/deletion for raw content, summaries, tasks, and backups. Actual database grants, backup policies, and external purges remain unverified.

## Release Evidence Needed

| Area | Minimum evidence before production use |
|---|---|
| Data and identity | Cross-user/tenant denial tests over every protocol; deterministic authorized Account IDs; verified CRM field/record policy; opaque secrets absent from persisted wire records. |
| Tool permissions | Retrieved-content injection tests cannot expand allowed tools, destinations, or write targets; missing hosted auth prevents startup. |
| Reliability | Duplicate dispatch, process kill, restart, partial CRM write, and replica-switch tests demonstrate recovery without duplicate effects. |
| Cost and latency | Enforced quotas and aggregate budgets; one measured deadline; cold/warm and concurrent p50/p95/p99 with failures included and provider costs reconciled. Numeric targets require an agreed workload/SLO. |
| Observability | Every paid run attributable; pagination beyond 100 spans; protocol errors visible; sink outages and stale harvests alarmed; deletion/retention verified. |
| Evals | Versioned business and adversarial corpus covering deployed platforms/routes; objective assertions plus calibrated semantic grading; release thresholds and CI enforcement. |

The requirements describe L1-L3 as blocking and live/business evaluations as non-blocking ([acceptance strategy](../../../requirements-docs/50-system/09-acceptance-and-verification.md#L101)). Production release criteria need to make the relevant safety, correctness, and performance checks blocking.

## Evidence And Limits

The review inspected shared protocols/auth, platform tool configurations, CRM access/delivery, orchestration, persistence/harvesters, deployment definitions, and tests. Existing strengths include isolated non-live test fixtures, real protocol loopbacks, signed JWTs, restricted tools in the Claude SDK backend, database read-only controls, explicit partial-leg results, and persisted fan-out state.

Ran [readiness_probes.py](readiness_probes.py) with Python 3.14.7. **Seven offline behavior checks completed**, confirming F01 (two checks), F02, F04, F07's worker overlap, F08's late poll, and F09's empty-answer PASS. The script compiles selected repository definitions and supplies fake authentication/client/store boundaries. It does not prove JWT issuance, a deployed exploit, cloud execution, or end-to-end correctness.

```sh
python3 -B build-notes/codex/codebase-review/readiness_probes.py
```

The full pytest suite was **not run**: this checkout has no virtual environment and the available interpreter lacks pytest and application dependencies. No packages were installed, credentials read, cloud calls made, model evaluations purchased, database queries executed, or deployments changed. IAM grants, external gateway controls, backup/restore, and actual production workload/SLOs remain deployment-level verification items. The earlier module map in this directory describes the pre-pull commit and is not an updated inventory.
