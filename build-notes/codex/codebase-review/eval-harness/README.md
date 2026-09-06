# A2A lab evidence eval harness

An independent, standard-library-only Python 3.11+ harness for the lab's experiment evidence. It lives outside `tests/`, uses its own JSONL corpus, and neither installs dependencies nor calls models, cloud services, or databases outside temporary SQLite files. Run from any directory; relative CLI filenames resolve from your working directory, while artifact references resolve from the repository root.

## Run

From the repository root:

```sh
python3 -B -S build-notes/codex/codebase-review/eval-harness/run.py \
  --report /tmp/a2a-evals.json --junit /tmp/a2a-evals.xml

python3 -B -S -m unittest discover \
  -s build-notes/codex/codebase-review/eval-harness -p 'test_harness.py' -v
```

`-S` disables site-packages; `-B` suppresses bytecode writes. No `uv`, pytest, YAML parser, API key, `.env`, or virtual environment is needed. Repository adapters explicitly construct local sinks and stores, even if your shell selects Aurora/Postgres. They import and execute the real message, trace and SQLite modules; they do not extract or replace their implementations.

The initial baseline at `a806b6d` is **87 passing cases and 2 failing repository regressions**, plus **28 passing harness integrity tests**. The default eval command deliberately exits **1** because credentials inside serialized JSON and opaque `user_token` fields survive persistence. See [the review](../test-and-eval-review.md). These are positive security assertions, not expected-failure controls. Fixing the application should turn them green without changing the corpus.

Exit codes: **0** all selected cases match their rubrics; **1** at least one rubric mismatch; **2** invalid input, an execution error, an empty selection, or a report-write failure. Unexpected exceptions never satisfy a negative control. Reports separate `repository`, `synthetic-replay`, and `export` modes, include exact violation codes, area counts, negative-control counts, source/corpus/harness hashes, and the Git commit when available. Raw answers and credentials are omitted from reports.

```sh
# Inventory or focus an investigation.
python3 -B -S build-notes/codex/codebase-review/eval-harness/run.py --list
python3 -B -S build-notes/codex/codebase-review/eval-harness/run.py --area protocol
python3 -B -S build-notes/codex/codebase-review/eval-harness/run.py --case 'regression.*'
```

## What the corpus exercises

| Area | Cases | Executed or graded |
|---|---:|---|
| repository | 18 | Real request validation/response serialization; real JSONL and SQLite persistence, redaction, duplicate replay, sink outage; real observability upserts, usage totals and joins. |
| protocol | 16 | REST canonical bodies/header correlation; MCP text/structured/SSE responses, RPC ids and tool failures; A2A v1 and 0.3 task artifacts, session/trace continuity and interrupted states. |
| trace | 16 | Expected edges, shared trace id, raw evidence, errors/pending, elapsed values, duplicate rows, independent process sequence numbers; representative native/provider paths. |
| experiment | 11 | Required facts, forbidden fallback/instruction text, selected runtime, delegated edges, wall-time and cost budgets, business-unit coverage, empty and hidden failed legs. |
| task | 8 | Submit/poll evidence, nonblocking submission, eventual-consistency grace, task/context/trace identity, terminal-state regression, interruption and deadlines. |
| observability | 10 | Platform-qualified native-id and rider joins, fixed required-platform denominator, orphan/duplicate ingestion, stale/blocked harvests, invalid and unknown usage, credential exposure. |
| insight | 10 | Resolving references, recomputed p50/p95, cohort/sample/unit checks, missing artifacts, observed evidence and a hypothesis test plan. |

The 44 `expected_violations` cases are deliberate bad-evidence controls. A PASS for one means the grader detected **exactly** those problems, not that the experiment succeeded. The 71 artifact cases use invented evidence, including the small [measurement artifact](evidence/measurements.jsonl); their latency/cost values are rubric examples, not claims about deployed platforms.

## Add cases

1. Copy a nearby line from `cases/*.jsonl` into a new JSONL file here or elsewhere. Each nonblank line must be one complete JSON object. Comments, duplicate keys/ids, and NaN/Infinity are rejected. A pretty-printed multiline object is not JSONL.
2. Give it a unique, stable lowercase `id`, a supported `area`, and a short `description`. Put the observed or synthetic data in `input`; put the acceptance requirements in `policy`. Keep the rubric independent of the answer being evaluated.
3. For a positive case, omit `expected_violations`. For a synthetic negative control, name the exact sorted-or-unsorted set of diagnostic codes expected. Do not put existing product defects in that list to make a baseline green.
4. Add optional `checks` for exact facts using JSON pointers. A missing pointer fails, including for `not_contains`. Supported operations are `eq`, `contains`, `not_contains`, `ge`, `le`, `length`, and `no_secrets`. Every check has a unique `name`, `path`, and `op`; all except `no_secrets` require `value`. A failed check emits `assert.<name>`.
5. Run the new file, then the complete corpus and harness integrity tests. Retain the report with the sanitized source observation, collection time, model/backend version, prompt version, and configuration revision. Keep cold/warm, model, hosting, and dispatch variants in distinct cohorts.

A complete additional case (one line):

```json
{"id":"my.rest-answer","area":"protocol","description":"REST response preserves the session and required fact","input":{"request":{"message":"Report inventory","trace_id":"eval-1"},"response":{"text":"Inventory is 42","session_id":"session-1"}},"policy":{"protocol":"rest","trace_id":"eval-1","session_id":"session-1","contains":["inventory is 42"]}}
```

```sh
python3 -B -S build-notes/codex/codebase-review/eval-harness/run.py --cases /tmp/my-cases.jsonl
```

### Input and policy contracts

Use the bundled lines as executable templates. Unknown case, policy, and assertion fields fail validation. Extra **observation** fields are permitted so exports can retain their provenance.

| Area | `input` / exported `actual` | `policy` |
|---|---|---|
| repository | `operation`: `request`/`response` with `payload`; `record` with TraceEvent `hops` and optional `broken_sink`; `store` with hops, `sessions`, `events`, `harvest`, `refs`. Store entries use the actual `ObsStore.upsert_*` parameter names. | No policy; requires `checks` on the actual execution result. |
| trace | Nonempty `trace_id`; `hops`: unmodified TraceEvent objects. | `min_hops` (default 1), `routes` as `[source,target,protocol]` triples, `allowed_statuses` (default `ok`), optional `max_hop_ms`. |
| protocol | `request`, `response` as JSON objects or serialized wire bodies; optional `headers` and `http_status`. SSE supports one JSON-RPC response, not a full multi-event streaming transcript. | Required `protocol`: `rest`, `mcp`, `a2a`; optional `tool` (MCP defaults to `ask`), `trace_id`, `session_id`, `contains`. |
| experiment | Trace inputs plus `text`, actual resolved `target`, `elapsed_ms`; optional `cost_usd`, `legs`. Legs have `role`, Boolean `ok`, `text` or `error`, and `trace_id`. | Required `target`; trace policies plus `contains`, `forbidden`, `max_elapsed_ms`, `max_cost_usd`, `roles`, `allow_partial`. A failed leg must appear in a `[leg unavailable: <role> — <reason>]` marker. |
| task | `submit_ms`; chronological `snapshots` with `state`, `at_ms` relative to submission start, `task_id`, `context_id`, `trace_id`, and completion `text`. | Required identity values, `max_submit_ms`, `max_elapsed_ms`; optional `not_found_grace_ms` (default 0), `final_state` (default completed), `require_nonblocking` (default true). Normalizes `TASK_STATE_*` and hyphenated state labels. |
| observability | `trace_id`, `hops`, sessions with `platform`/`native_id`/optional normalized `usage`, events with `platform`/`native_session_id`/`event_id`/`raw_json`, harvest rows with `platform`/`status`/`last_harvest_at`, fixed numeric epoch `as_of`. | Required `platforms` maps every expected platform to its target names, and `max_age_s`; optional `min_join_rate` (default 1). Missing platforms remain in the denominator. Decode exported `usage_json` into `usage`; do not turn missing values into zero. |
| insight | `status`, `refs` (repository file paths, optional `#L123` or simple Markdown heading fragments); measured entries include `measurement` with `path`, `metric`, `cohort`, `unit`, `statistic`, `value`; hypotheses include `test_plan`. | Required expected `status`; measured policies also pin `metric`, `cohort`, `unit`, `statistic`; optional `min_samples` (default 3), `tolerance` in the same unit (default 0). |

Measurement artifacts are separate JSONL files with `trace_id`, `metric`, numeric nonnegative `value`, `unit`, and `cohort`. The grader selects the cohort and metric, requires unique trace ids, checks units, and recomputes median, nearest-rank p95, or mean. Reports include the artifact hash. This validates numerical support, not statistical significance or the authenticity of an exported measurement. Resolve shorthand ADRs such as `D28` to actual file/anchor paths before grading; the harness does not parse YAML or invent link resolution rules.

Repository operations return their results for pointer assertions: request/response return `accepted` plus `result` or exception class; recording returns `archive`, `sqlite`, `sink_warning`; storage additionally returns `summary`, `sessions`, `callers`, `lab_traces`, `joins`. Read `repository.py` for this small adapter boundary.

## Grade exported runs

Use a **positive** case with a rubric for the real run, not a synthetic negative control. An observations JSONL file replaces fixture inputs explicitly:

```json
{"id":"my.rest-answer","actual":{"request":{"message":"Report inventory","trace_id":"eval-1"},"response":{"text":"Inventory is 42","session_id":"session-1"},"http_status":200}}
```

```sh
python3 -B -S build-notes/codex/codebase-review/eval-harness/run.py \
  --cases /tmp/my-cases.jsonl --case my.rest-answer \
  --observations /tmp/observations.jsonl --report /tmp/actual-eval.json
```

Observation ids must **exactly match** the selected cases. Missing, extra, duplicate or negative-control ids are errors; the runner never falls back to a synthetic fixture. Repository cases cannot be overridden with claimed outputs.

For traces and experiments, `prepare.py` joins an already-collected trace JSONL archive to a manifest. A minimal trace manifest is:

```json
{"id":"my.trace-check","actual":{"trace_id":"the-real-trace-id"}}
```

```sh
python3 -B -S build-notes/codex/codebase-review/eval-harness/prepare.py \
  --manifest /tmp/run-manifest.jsonl --traces /tmp/exported-traces.jsonl \
  --output /tmp/observations.jsonl
```

Create a positive `trace` case with id `my.trace-check` and the required route policy, then grade it with `--observations`. For experiments add the final answer, resolved target, and measured wall-time to manifest `actual`. `/api/run` supplies `text` and `trace_id`, but its `latency_ms` can be adapter latency and its response does not include the resolved target: preserve the request/registry context and measure the whole caller interval as `elapsed_ms`. For async runs collect the final poll and the complete snapshot timeline. Normalize fan-out failure markers to role names when the application renders platform names. Do not infer unreported spend.

The preparer preserves duplicates and hop ordering, partitions by exact trace id, and leaves missing hops empty so they fail grading. It performs no redaction: use sanitized exports. It does not read `.a2alab`, `.env`, local production archives, or cloud credentials automatically.

## Boundaries

- Protocol grading accepts the lab's documented envelope variants; it does not execute HTTP servers or prove full REST/MCP/A2A SDK conformance, discovery, streaming, cancellation, identity authorization, or cloud IAM. Keep the existing loopback/live tests.
- Artifact grading does not execute agents, orchestration workers, harvesters, the browser, Salesforce Apex, PostgreSQL, or insight rendering. The repository adapter executes only the dependency-free seams listed above. This is evidence evaluation alongside the original suite.
- Required terms and forbidden phrases are deterministic checks, not a semantic/factual model judge. Add independently sourced facts and prohibited tool/route assertions for each business scenario. Prompt-injection resistance and account isolation need richer recorded cases and live boundary tests.
- Native ids and trace ids establish joins inside supplied evidence; they do not cryptographically attest origin or tenant ownership. Missing cost stays unknown. Credential scanning covers representative structured/serialized keys and token patterns; it is not a complete secret or PII scanner.
- JSONL insight cases are deliberately separate from `config/insights.yaml`. A passing example does not audit every published insight. Export actual claims and their artifacts to positive cases before drawing that conclusion.
