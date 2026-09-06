# Test estate review and independent eval harness

Reviewed 2026-09-06 against commit `a806b6d`. The existing suite has useful protocol and storage tests, but its passing results would not establish business-experiment quality or the numerical support for published insights. This review adds a separate runnable evidence harness and reproduces two trace-redaction gaps using actual repository code.

Start with [the harness instructions](eval-harness/README.md), [the baseline report](eval-harness/results/baseline.json), or [the complete static test inventory](test-inventory.json). Existing review documents, production code, configuration, and tests were preserved.

## Findings, ordered by impact on trustworthy evaluations

### 1. High: credential-redaction coverage misses serialized JSON and opaque user tokens

**Reproduced using real `TraceRecorder`, `JsonlFileSink`, and `SqliteSink`.** A raw payload string containing `{"client_secret":"synthetic-secret-only"}` is persisted with the value intact. A structured payload with `user_token: synthetic-opaque-token` is also retained. Both copies fail the new persistence assertions.

The current scrubber dispatches strings to token-pattern replacement rather than inspecting JSON object keys; its key set also omits `user_token`. Existing tests exercise structured secrets, bearer/JWT/key-shaped strings, and store redaction, but not these two representations. An opaque token need not resemble a JWT. This is a trace credential-exposure defect, not evidence of actual credentials leaking in a deployed run.

Evidence: [string and structured redaction](../../../src/interop/trace.py#L81), [sink serialization boundary](../../../src/interop/trace.py#L165), [current redaction tests](../../../tests/unit/test_models_and_trace.py#L162). Reproducers: `regression.serialized-secret` and `regression.user-token` in [repository cases](eval-harness/cases/repository.jsonl). Both remain failing positive cases; no application fix or expected-failure waiver is included in this review task.

**Next change:** add format-aware scrubbing for serialized payloads and cover every credential-carrying field, then preserve tests against both sinks and the observability store. Keep business content intact under the repository's credential-only policy.

### 2. High: a successful protocol call can still be a failed experiment

**Source-backed gap.** `scripts/matrix.py` sets success from recorded latency and absence of an exception. It does not require a nonempty answer, expected business facts, a delegated contribution, or the intended path. Its default is one run of a generic question. Correct p95 arithmetic cannot make that sample a meaningful tail estimate. Console execution tests use a deterministic fake client, and the live round-trip tests primarily assert nonempty text and participation of expected protocols/actors.

Evidence: [matrix scoring](../../../scripts/matrix.py#L38), [default run count](../../../scripts/matrix.py#L100), [console execution test](../../../tests/unit/test_console.py#L110), [live round trips](../../../tests/e2e/test_live_roundtrip.py). The repository itself documents the HTTP-200-with-missing-contribution problem in [fan-out policy](../../../src/orchestration/runner.py#L1) and the `timeout-chain` insight in [insight configuration](../../../config/insights.yaml).

**Added:** experiment cases require content and intended edges together, detect fallback text, distinguish resolved runtimes, enforce whole-run latency/cost budgets, and require every requested business-unit outcome. Unknown cost fails a configured cost budget. Honest partial failure has an explicit policy and names the unavailable role. These checks grade supplied evidence; they do not purchase or run new agent experiments.

### 3. High: async coverage demonstrates local progress, not distributed recovery

**Source-backed coverage limit.** The slow-adapter loopback test is particularly valuable: it compares submission time against actual work time rather than merely trusting a SUBMITTED state. Orchestration tests cover transient not-found responses, fallback, timeouts, interrupted/failure semantics and resource cleanup. Durable-task tests use a fake SQL client and verify sequential completed-task replay. They do not establish atomic claims under overlapping workers, process death recovery, restart/replica task visibility, or actual cloud persistence.

Evidence: [async timing test](../../../tests/e2e/test_a2a_async.py#L56), [orchestration tests](../../../tests/unit/test_orchestration.py), [task fake and tests](../../../tests/unit/test_fanout_tasks.py). The earlier [production-readiness review](production-readiness.md) contains separate worker-overlap probes; those were not substituted for this harness's results.

**Added:** per-task evidence cases check task/context/trace identity, submit and completion deadlines, bounded eventual visibility, terminal regression, explicit interrupted outcomes, and blocking submission. **Still needed:** actual worker concurrency/restart tests with the real store and deployment topology.

### 4. Medium: telemetry tests need an experiment-level completeness denominator

**Source-backed gap.** The suite tests real local SQLite joins and rollups, and has broad canned-payload coverage for coding, cloud infrastructure and platform sources. PostgreSQL read tests explicitly match SQL fragments and replay canned rows; they do not execute that SQL. Salesforce OTel tests pin session ids and explicitly do not exercise DMO session enumeration. A dashboard rendering correctly can therefore coexist with missing/stale platform evidence.

Native-ref joins also deserve a collision test: local `list_sessions` and `lab_traces_for` compare `platform_ref` with native ids without a platform predicate. This is a potential false-association risk when independent platforms reuse an identifier; this review does not claim that such a collision has occurred in deployed data.

Evidence: [real SQLite join test](../../../tests/unit/test_observability.py#L154), [Postgres fake](../../../tests/unit/test_pg_obs_reads.py#L17), [OTel test scope](../../../tests/unit/test_salesforce_otel_source.py#L1), [local join query](../../../src/observability/store.py#L395), [existing join-rate script](../../../scripts/fanout_join_rate.py).

**Added:** a fixed expected-platform denominator, platform-qualified native-id joins, exact rider boundaries, stale/blocked harvest distinctions, missing usage as unknown, and orphan/duplicate telemetry controls. Direct store cases exercise idempotent totals and both native-id and rider joins. Artifact grading does not fix the application queries or prove remote ingestion completeness.

### 5. Medium: insight rendering and review tests do not verify measured claims

**Source-backed gap.** Existing tests validate diagram links to insights, Markdown export, attachment behavior, and reviewer authorization/content pinning. `test_live_export_carries_the_diagrams` is a local YAML/rendering test despite its name. No test in the inventoried suite recomputes the published latency/cost claims from retained run samples. The configuration describes an artifact-based evidence ladder, but rendering code formats the provided status and refs without verifying their support.

Evidence: [diagram/export checks](../../../tests/unit/test_diagrams.py#L100), [review tests](../../../tests/unit/test_console.py#L936), [insight rendering](../../../src/console/insights.py), [evidence-level convention](../../../config/insights.yaml#L7).

**Added:** separate JSONL insight cases check resolving files/fragments, expected status, cohort/metric/unit/statistic identity, a sample floor, unique sample trace ids, and recomputed p50/p95/mean within an explicit tolerance. Measured claims cannot pass by downgrading themselves to hypotheses. The supplied measurements are labeled synthetic; **the live `config/insights.yaml` estate has not been numerically certified**. Export those claims and genuine supporting samples for that assessment.

### 6. Medium: suite breadth and source-tree execution conceal deployment gaps

**Inventory-backed limit.** There are 54 Python test files and 555 `test_*` function definitions: 536 unit, 18 under e2e, and 1 under live. These are AST counts, not pytest collected/parameterized counts or line/branch coverage. Four functions are in live-marked modules: three Claude/Agentforce round trips and one MuleSoft broker test; `pyproject.toml` excludes `live` by default. Optional platform dependencies can cause skips. Salesforce has three separate Apex test classes. No checked-in CI workflow or browser test framework configuration was found; `.github` contains CODEOWNERS. Externally configured automation was not inspected.

Tests inject `src/` into the import path, so their success cannot prove wheel packaging or installed-layout behavior. Authentication, route/configuration, source adapters, protocol and console tests are useful, but they should not be reported as deployment, browser, cloud IAM or broad provider interoperability certification.

Evidence: [pytest setup](../../../tests/conftest.py), [packaging and pytest settings](../../../pyproject.toml), [live broker](../../../tests/live/test_mule_broker.py), [Salesforce classes](../../../salesforce/force-app/main/default/classes). The [inventory](test-inventory.json) records names and line numbers for every counted function.

## Coverage map

| Surface | Existing coverage inspected | Independent additions | Remaining validation |
|---|---|---|---|
| Experiment configuration and execution | Console fake-client runs, suffix/bridge/channel/mode routing; registry/remap checks; face inventory; backend stubs. | Content + path + target grading; wall/cost limits; explicit unknowns. | Recorded positive cases for every live scenario, mode, model, routing channel and supported direction. |
| REST, MCP, A2A | Real local echo servers; raw trace assertions; 0.3 compatibility; MCP contracts/lightweight HTTP; identity metadata. | Representative canonical/raw/SSE shapes, RPC id/type matching, tool errors, session/trace continuity, empty artifacts. | Full streaming transcripts, discovery drift, cancellation/resubscription, network retry behavior and deployed cross-version pairs. |
| Native platforms and broker | Managed/OpenAI/Strands/LangGraph backend fakes; ADK/Foundry clients; three live Claude/Agentforce tests; live MuleSoft trace attribution. | Representative trace envelopes across native paths and A2A targets. | Real OpenAI/Strands/ADK/Foundry/LangGraph scenario exports and whole-path broker evidence. |
| Fan-out and asynchronous experiments | Orchestration concurrency/timeout/fallback tests; fan-out tool/run-id tests; fake durable task lifecycle. | Complete role outcomes, explicit partial failure, task progression and budget grading. | Worker overlap, restart/replica durability, retries without duplicate side effects, provider clock and timeout measurements. |
| Trace persistence | JSONL/SQLite sinks, fan-out sinks, error/pending status, redaction and attribution tests. | Real sink replay/outage checks; two failing secret regressions; distributed sequence and duplicate-row controls. | Sustained load, sink-loss visibility and remote retention/export parity. |
| Observability and analytics | SQLite rollups; PostgreSQL canned rows; Anthropic/Salesforce/OpenAI/Strands/LangGraph/OTel, coding and infrastructure normalization; analyst tool guards. | Real store upserts/usage/riders; evidence completeness/freshness, platform identity, orphan/duplicate/unknown-value checks. | Real SQL/schema execution, ingestion enumeration/lag, cloud query limits, trace-to-UI consistency and source retention gaps. |
| Insights | Diagram references/export, review permission and content pinning, architecture/doc links. | Measured-number recomputation and evidence-tier/rubric checks on separate JSONL cases. | All current insight claims, reproducible provenance, confidence intervals and actual rendered UI evidence links. |
| Salesforce / delivery / auth | Apex callout/summary classes; bridge/auth/identity tests; brief watch state. | Required/forbidden experiment facts and trace credential checks only. | Salesforce execution, principal/account isolation, idempotent CRM delivery, realistic injection/tool-abuse cases. |

## Validation performed

- Executed the independent corpus under `python3 -B -S`: **89 cases, 87 pass, 2 fail, 0 execution errors**. Of the passing cases, **44 are negative controls** that correctly reject synthetic bad evidence. The 18 repository cases execute actual source modules; the other 71 are synthetic evidence replays. This is not a production pass rate.
- Executed **28 standard-library harness integrity tests**, all passing. They cover changed evidence, malformed/duplicate JSONL, policy mistakes, empty selections, no missing-observation fallback, positive-only external grading, exact negative controls, secret-free reports, input overwrite guards, report exit codes/JUnit, fixed join denominators, measurement contracts, and operation with hosted-store environment variables set.
- Saved [JSON results](eval-harness/results/baseline.json), [JUnit results](eval-harness/results/baseline.xml), and [integrity-test output](eval-harness/results/self-tests.txt). Outputs contain diagnostics/hashes rather than raw credentials or business evidence.
- Inventoried the original suite and inspected the source/tests cited above. **The original pytest suite was not run:** this checkout has no virtual environment and the available interpreter lacks pytest, YAML and FastAPI. No dependency installation was needed for the new harness. No cloud, live model, Salesforce, browser, or production database checks were performed.

## Suggested next test work

1. Resolve the two redaction regressions and require the unchanged cases to pass.
2. Export a warm and cold positive sample set for each runnable scenario and provider path. Pin model, prompt, configuration, deployment and routing context beside the observations. Grade complete business facts, intended tool/route evidence, interrupted/failed outcomes and caller budgets together.
3. Add the distributed worker/store and installed-package checks that offline artifact grading cannot establish. Preserve the real local async timing test.
4. Normalize published insight claims into positive JSONL cases backed by real retained measurements. Keep unsupported costs and stale evidence explicit. Publish a measured claim only when its cohort and artifact can be reproduced.

The harness is additive and independent. Its [case-extension and export instructions](eval-harness/README.md#add-cases) explain how to extend it without editing the original pytest suite or scenario/insight YAML.
