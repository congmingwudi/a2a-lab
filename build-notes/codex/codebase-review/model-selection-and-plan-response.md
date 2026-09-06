# Model selection and response-plan assessment

Reviewed 2026-09-06 against the current checkout (`a806b6d`), `AGENTS.md`, `CLAUDE.md`, the Codex reviews, and [Claude's response plan](../../claude/codex-review-response-plan.md). This is a recommendation based on task characteristics, repository evidence, and current official model documentation, not a controlled comparison of model performance on this repository. No implementation, deployment, credential inspection, or dependency installation was performed.

My recommendation is to keep Opus 5 as the principal implementer in the existing Claude Code environment, assuming its claimed Salesforce/AWS access works, and use GPT-6 Astra for independent design and diff review before each affected deployment. Use higher effort on auth, idempotency, session ownership, and async state transitions. The advantage of retaining Claude Code here is established workflow context and potential integration access; it is not evidence that OpenAI models are weaker implementers.

## Where I agree and disagree with the take

The defect/accepted-risk/evaluation split is a sensible execution scope for a lab. Do not implement every production-hardening recommendation literally. Decide that scope before implementation and record the accepted-risk rationale early; documentation of shipped behavior should accompany each batch rather than wait for batch 5.

Two corrections matter:

- The separate standard-library harness was explicitly requested by the user, including separation from existing tests. Its existence is not evidence that the model chose to avoid pytest. The original suite was not run, which remains a validation limitation; the new harness and probes were executed.
- The [production review's opening](production-readiness.md#L3) explicitly cites NFR-201 and distinguishes existing defects from controls required to change the operating model. Saying that it ignored the lab boundary entirely overstates the criticism. Its individual "Required" lines could nevertheless have separated lab obligations from conditional production recommendations more clearly.

The new [AGENTS.md](../../../AGENTS.md) is an orientation guide, not a substitute for [CLAUDE.md](../../../CLAUDE.md#L126). It does not reference that file or capture the full ADR/workstream/Details/deployment-map/image workflow. That is a real omission in the guide I wrote. For the next implementation, explicitly require reading both files and the relevant runbooks. No instruction file was modified during this assessment.

## Plan corrections to make before coding

1. **A1's literal regex is insufficient.** Its `"[^\"]*"` value matcher does not handle escaped quotes, and its key matcher misses JSON serialized inside another JSON string. A local standard-library probe of the proposed expression scrubbed an ordinary object but left secret suffixes in both of those cases. Add escape-aware, nested, malformed/truncated, case-variant, and sink-persistence tests before choosing an implementation. Passing the two existing regression cases alone is insufficient.
2. **A2 must enumerate the real invocation surface.** The plan names REST `POST /ask`; the repository serves [`/invoke` and `/invocations`](../../../src/interop/servers/rest.py#L32). A2A also has the v1 `SendMessage` method and the 0.3 compatibility spelling. Auth tests must exercise actual mounted aliases and protocol methods, not merely the example names in the plan.
3. **A4's query-before-insert does not establish concurrent idempotency.** Two executions can both observe no record and insert. Use a uniqueness/idempotency boundary appropriate to the actual Salesforce objects and specify repair of partial brief/Task delivery. The plan also names `A2ALab_Brief__c`; the code writes [`A2ALab_Account_Brief__c`](../../../src/briefs/salesforce.py#L141). These details make batch 2 a reasoning task, not mechanical test porting.
4. **A7 needs an accepted-work replay policy as well as one deadline.** Sharing the remaining timeout bounds total latency, but a blocking fallback can still duplicate work already accepted by the remote platform. Define cancellation, idempotency, or refusal to replay, and test that outcome.
5. **The accepted-risk table has factual premises to fix.** `DEFAULT_TRACE_TTL_DAYS=14` belongs to the [DynamoDB sink](../../../src/interop/trace.py#L286), not a JSONL purge. The claim that PostgreSQL trace writes run off-loop "where it matters" needs call-site proof: [`TraceRecorder.record`](../../../src/interop/trace.py#L394) and [`PostgresSink.emit`](../../../src/observability/pg.py#L1165) are synchronous. Accepting those limitations is a scope decision; recording nonexistent mitigations is incorrect. Treat the dummy-data claim as an operating assumption requiring owner confirmation, not a fact established by source inspection.
6. **The original readiness probes have defect-confirming assertions.** For example, [`redaction_probe`](readiness_probes.py#L46) asserts that raw JSON remains unscrubbed. A correct fix should invalidate that assertion. Preserve the historical reproduction script and add passing assertions for corrected behavior in pytest; do not require the unchanged probe script to stay green. The separate `regression.*` harness cases already assert the desired secure behavior and should turn green.

## Model allocation

| Work | OpenAI choice | Claude choice | How I would use it |
|---|---|---|---|
| A10 packaging, straightforward test ports, resolved documentation edits | GPT-5.6 Sol | Sonnet 5 or existing Opus 5 session | Moderate effort; avoid model switches whose handoff costs exceed the small saving. |
| Batch 1: A1–A3 security boundaries | GPT-6 Astra | Opus 5 | High/xhigh for design and adversarial cases; independent review before deploying. |
| Batch 2: retries, CRM delivery and session isolation | GPT-6 Astra | Opus 5; Fable 5.1 if needed | High/xhigh. Require concurrent execution and partial-delivery tests. |
| Batch 3: atomic claims and absolute deadlines | GPT-6 Astra | Opus 5; Fable 5.1 if needed | High/xhigh. Verify real database semantics, not only a fake honoring the proposed SQL. |
| Batch 4: pagination, instrumentation and evidence grading | GPT-5.6 Sol, escalating to Astra for ambiguous semantics | Opus 5 | High for implementation; strong review of completeness denominators and error semantics. |
| Batch 5: accepted-risk reasoning | GPT-6 Astra | Fable 5.1 or Opus 5 | Settle substantive risk decisions first. Routine ADR/Details formatting does not require a premium model. |

For this session, Opus 5 implementation plus Astra review is a practical default. With equivalent context and tools, Sol for routine implementation plus Astra for the difficult items is a reasonable OpenAI-only alternative. There is no repository-specific evidence here establishing a winner between Astra and Fable, or between Sol and Opus.

I would not prescribe xhigh for every edit. Anthropic recommends starting Opus 5 and Fable 5.1 at high and adjusting with evals; it recommends Fable when higher-effort Opus still falls short on demanding tasks. Fable is documented for long-running coding as well as reasoning, so restricting it to prose/ADR work is an artificial distinction. [Claude model selection](https://platform.claude.com/docs/en/about-claude/models/choosing-a-model), [Fable 5.1 overview](https://platform.claude.com/docs/en/models/fable-5-1/overview).

## Verified model facts and cost caveats

Standard API input/output prices per million tokens currently listed are Astra **$10/$50**, Sol **$4/$20**, Opus 5 **$5/$25**, Fable 5.1 **$10/$50**, and Sonnet 5 **$2/$10**. The comparison that Opus costs half of Fable per uncached input/output token is correct. These are not subscription prices or measured task costs. [Astra](https://developers.openai.com/api/docs/models/gpt-6-astra), [Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol), [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing).

Astra is documented as OpenAI's most capable model for difficult end-to-end reasoning and coding; Sol is a flagship professional-work model. Those product descriptions support candidate selection, not claims that either wins these specific tasks. Their 1.05M context capacity is also not a reason to load all available history. Inputs above 272K tokens incur long-context pricing multipliers. [Astra specifications](https://developers.openai.com/api/docs/models/gpt-6-astra), [Sol specifications](https://developers.openai.com/api/docs/models/gpt-5.6-sol).

Fable 5.1's 30-day retention requirement is documented, with express Anthropic exceptions; it can be enabled at the workspace level rather than necessarily for the entire organization. Its quieter/longer turns are a documented prompting consideration, not a reason to forgo progress updates or staged reviews. [Retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention), [Fable prompting](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1).

## Implementation handoff

Before starting, have the chosen environment establish an executable baseline with `uv sync` and the extras needed by the batch, followed by `uv run pytest`. `--all-extras` is useful for validating the full optional estate when supported; it is not an absolute prerequisite for a packaging or stdlib trace fix. Record baseline failures and skips explicitly. Verify required tool access when the batch needs it; another session's successful AWS/Salesforce setup does not automatically transfer here.

Give the implementer the corrected scope, both instruction files, exact files to change, observable acceptance criteria, and affected deployment/runbook paths. Have the other model review the design before risky edits and the diff plus test output before deployment. Run the appropriate pytest regressions and the independent harness; do not weaken expectations to preserve a green report. Keep decisions, workstream item lines, affected Details/diagrams, and image inputs current with each change. No model's assertion that a batch is done substitutes for these artifacts.
