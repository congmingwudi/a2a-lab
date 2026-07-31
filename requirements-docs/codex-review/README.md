# Code-Grounded Requirements Review

## Purpose

This directory reviews the requirements suite against the project that exists in
this repository. It does not amend the requirements. It records what should be
changed if the suite is revised after this review.

The review answers two different questions because the requirements set itself
contains a deliberate tension:

1. **Standards-compliant revision:** what should change while preserving the
   forward voice, self-containment, implementation-neutral language and public
   publication rules in `requirements-docs/00-plan.md`?
2. **Exact-as-built revision:** what would change if the documents were meant to
   describe this particular implementation, its products, components, deployed
   shapes and current limitations?

The first answer remains a reusable requirements suite. The second becomes an
as-built system specification. They are not interchangeable.

## Baseline and evidence

The review was refreshed against `main` at commit `769ac77` on 2026-07-31.
The requirements set's final substantive commit was `bda3df9` on 2026-07-28.
The three-day difference is material: decisions D48 through D63 and their code
landed after, or concurrently with the close of, the requirements pass.

This refresh had no local-HEAD delta from the prior review baseline. The branch
was one commit behind `origin/main` (`0a7df32`), so that remote-only commit is not
part of this baseline. The working tree was dirty: this output directory and
unrelated `.cursor/`, `build-notes/codex/`, `build-notes/cursor/`, and
`scripts/cursor_otel.sh` were untracked. None of those uncommitted implementation
files was used as evidence for a HEAD capability. The review output itself is the
only working-tree material intentionally refreshed here.

Evidence was read from four independent layers:

- all twelve documents under `plan/`, including the decision log, measured
  results, deployment map and operations record;
- all twenty-nine documents in the existing requirements set;
- source, configuration, deployment scripts and Salesforce metadata;
- automated tests that pin intended implementation behavior.

Neither plans nor code silently wins. Each finding is labelled:

| Status | Meaning |
|---|---|
| **Both** | Plan and implementation evidence agree |
| **Plan only** | Planned or claimed, but no implementation evidence was found |
| **Code only** | Implemented behavior is absent from, or materially ahead of, the plans |
| **Conflict** | Plans and implementation describe different behavior or state |
| **Not verifiable** | The repository cannot establish the claim without a live environment or external judgement |

“Code evidence” means a path, configuration item, test or deployed artefact in
the repository. It does not mean a live deployment was re-probed during this
review. Live-state claims remain plan evidence unless a committed result records
the measurement.

### Plan-source coverage

| Plan document | Outcome | Use in this review |
|---|---|---|
| `00-decisions.md` | No change | Decision rationale and late D48-D63 requirement lessons |
| `01-architecture.md` | No change | Original seams/model/trace design and comparison with hosted state |
| `02-matrix.md` | No change | Runnable, mediated, blocked, declined and async-qualified protocol cells |
| `03-results.md` | No change | Committed historical measurements; not current live-state proof |
| `04-runbooks.md` | Change | Stale watcher, AgentCore and observability instructions conflict with current deploy paths |
| `05-observability.md` | No change | Per-platform extraction capability and known absences |
| `06-openai-codex-handoff.md` | Change | Technical contract is corroborated, but branch guidance is stale |
| `07-workstreams.md` | No change | Planned, completed, open and superseded delivery work |
| `08-insights.md` | No change | Published findings and evidence/caveat language |
| `09-deployment-map.md` | Change | Current hosted topology plus stale watcher and protocol-face rows |
| `10-operations.md` | No change | Current rotation, deployment, state and recovery behavior |
| `11-delivery.md` | No change | One-way Jira projection; dated live board counts remain not verifiable |

## Executive findings

### 1. The requirements describe a larger, more governed system than was built

The current suite is not an as-built specification. Its fictional five-division
enterprise, regulatory control plane, corporate human identity provider,
residency-aware routing, traffic-derived data-flow reporting, subject erasure,
retention enforcement and full fault-injection gates are commissioned-system
requirements. The repository implements an interoperability lab with real
platform seams, evidence capture, orchestration, a hosted console and operating
tooling; it does not implement most of that enterprise compliance layer.

This is not a defect in the original exercise: the README says the suite was
written as if the system did not yet exist. It is, however, the dominant change
required by an exact-as-built rewrite.

### 2. Sixteen late decisions add requirements-worthy behavior

D48-D63 add or sharpen behavior around hosted fail-closed startup, one
authoritative observability store, durable operator state, multi-app hosting,
long-running workers, credential relocation, asynchronous job triggering,
experiment identity, typed shared records, UI provenance, one-way delivery
projection, content-off build telemetry, a third orchestrator, anonymous usage
analytics and owner/operator credential separation.

Some are implementation choices and belong only in an as-built document. Others
generalize into requirements and should be added to the reusable suite. The two
change registers separate them.

### 3. Several existing Must requirements are not true of the build

The largest nonconformances are:

- human access uses the lab's password/JWT mechanism rather than a corporate
  identity provider (`SR-104`);
- credential rotation for hosted components normally requires a task or service
  redeploy because secrets are loaded at container start (`SR-402`, `AC-509`);
- the enterprise data/privacy capabilities in DR-1xx through DR-6xx and AC-4 are
  largely specifications, not implemented controls;
- `FR-203` and `AC-103` do not define how packaged configuration becomes active;
  repository inspection does not establish whether every route change preserves
  participant software and redeployment invariants;
- `FR-605` promises run comparison more strongly than the console currently
  provides;
- the full L1-L3 acceptance gate, including every inducible failure, is not
  demonstrated as a standing change gate;
- payload-view attribution (`SR-603`) is not evidenced by a read-audit trail;
- retention enforcement and data-subject erasure are not implemented.

These should not be disguised as “recommended additions.” They are existing
requirements whose verification state is unsatisfied or not established.

### 4. The plan also contains historical views, not one uniformly current truth

`plan/01-architecture.md` still presents local ports and a local console as the
primary topology, while `plan/09-deployment-map.md` records the hosted estate.
The workstream document mixes completed, pending and deliberately declined work.
The matrix records live capability honestly but includes unresolved or scheduled
cells. The review therefore cites decision IDs, statuses and implementation
evidence rather than treating every plan sentence as current state.

### 5. The requirements need a verification-results layer

The suite defines how conformance should be established but has no companion
ledger recording which Must and Should requirements the reference build passes,
fails, partially satisfies or has not tested. A code-grounded revision should
add that ledger without rewriting failed requirements to match defects. This is
the cleanest way to preserve the reusable specification while being honest
about the build.

### 6. This refresh corrected evidence classifications in the prior review

The earlier draft classified several unimplemented requirements as **Code only**.
That reversed the review taxonomy: a required or planned behavior with no
implementation evidence is **Plan only**, while a requirement and implementation
that state incompatible behavior are a **Conflict**. The durable registers now
apply those meanings consistently. No underlying requirement, plan or code was
changed.

### 7. Requirements-suite rules and references still contain internal conflicts

The conventions say every requirement uses one mandatory field schema and every
identifier class uses themed 100-block allocation tables
(`requirements-docs/01-conventions.md:29`). Business requirements, use cases,
acceptance criteria, cost elements and RAID records deliberately use different
schemas and numbering. The conventions must define those per-class shapes rather
than imply that all identifiers are system requirements.

The autonomous-build input is also stated two ways: README, O2, done criteria
and provenance say `50-system/` plus `60-cost/` stand alone, while resolved Q3
says `10-context/` travels with them (`requirements-docs/00-plan.md:151`). One
authoritative input set must replace both claims before the build experiment is
repeatable.

Acceptance also weakens several authoritative verification methods: AC-701,
AC-703, AC-704 and AC-706 use Inspection where their governing requirements call
for Test, including the document's own example that monetary-label completeness
is mechanically testable. Separately, OR-406 says every monetary figure uses
published rates while the cost suite requires negotiated rates, and DEP-10 points
the corporate-IdP dependency at `SR-601` instead of the actual obligation,
`SR-104`. These survived the contradiction challenge and are included in the
standards register.

### 8. Several current operational plan passages are stale enough to be unsafe

The current deployment map both lists the hosted briefs watcher and later calls
it an open laptop dependency; the older runbook still tells an operator to keep a
local watcher running, even though two watchers can duplicate delivery. The same
runbook describes obsolete protocol-specific AgentCore deployments and says an
API-Gateway exposure script creates a Function URL. These are current imperative
instructions, not merely historical rationale, and should be reconciled in a
separate plan-editing pass. This review records the conflicts but does not amend
`plan/`.

## Refresh verification

- All five output files are present and non-empty; all twelve plan documents and
  all twenty-nine requirements-source documents appear in the coverage ledgers.
- The traceability structure was mechanically rechecked: 188 system requirements
  retain valid business traces, the 28 business-requirement rows match the
  authoritative `Traces to` fields, and named UC/US references resolve.
- All 115 named source IDs used by this review resolve. All 96 literal
  `path:line` citations resolve to existing lines.
- Focused unit tests for console, watcher state, observability tools/reads and the
  environment contract produced 111 passes and two failures. Both failures are
  dirty-local-environment parity findings (`.env` missing example keys and an
  unreferenced local variable), not evidence about the committed implementation;
  they were not converted into review conflicts.
- No live cloud, platform, Jira or deployment probe was performed. Current-state
  claims that need those systems remain **Not verifiable**.

## Review outputs

- `01-as-built-baseline.md` maps the implemented capability and records
  plan/code agreement, conflicts and specification nonconformance.
- `02-standards-compliant-change-register.md` recommends changes that preserve
  the suite's existing editorial rules.
- `03-exact-as-built-change-register.md` describes the alternate rewrite needed
  for a product- and component-specific as-built specification.
- `04-traceability-and-acceptance-impact.md` identifies downstream changes to
  requirement classes, use cases, acceptance, cost, delivery and provenance.

## Requirements-document coverage

Every existing document was reviewed. “Change” means a recommendation appears
in one or both registers; it does not mean this review modified the source.

| Document | Outcome | Principal reason |
|---|---|---|
| `README.md` | Change | Status and purpose no longer cover the code-grounded review or late system state |
| `00-plan.md` | Change | Add an as-built verification pass and evidence ledger to done criteria |
| `01-conventions.md` | Change | Define verification-result and plan/code-evidence statuses; exact track changes product-naming rule |
| `10-context/01-glossary.md` | Change | Add worker, face, authoritative store, operator state, telemetry and delivery-projection terms |
| `10-context/02-stakeholders-and-personas.md` | Change | Distinguish owner, operator, reviewer, viewer and anonymous visitor |
| `10-context/03-scope-and-system-context.md` | Change | Add hosted console/worker/monitoring/delivery surfaces; exact track replaces fictional estate map |
| `20-lens-a-enterprise/01-executive-overview.md` | Change | Clearly label enterprise case as target scenario, not an implemented operating model |
| `20-lens-a-enterprise/02-business-requirements.md` | Change | Add durable governance state and trustworthy operator evidence; exact track narrows fictional obligations |
| `20-lens-a-enterprise/03-value-hypotheses-and-measures.md` | Change | Separate measured lab results from still-hypothetical enterprise outcomes |
| `30-lens-b-salesforce-field/01-executive-overview.md` | Change | Reflect hosted, multi-orchestrator and operational evidence now available |
| `30-lens-b-salesforce-field/02-business-case-and-roi.md` | Change | Include delivery, hosting, monitoring and recurring credential/harvest burden |
| `40-lens-c-lab-learning/01-executive-overview.md` | Change | Add operational, identity, telemetry and topology learnings produced after July 28 |
| `40-lens-c-lab-learning/02-learning-objectives.md` | Change | Add evidence provenance, async job ownership, secret relocation and content-off telemetry objectives |
| `40-lens-c-lab-learning/03-build-vs-buy.md` | Change | Actual build inventory now includes hosted operations, durable state, UI provenance and delivery projection |
| `50-system/01-functional-requirements.md` | Change | Late capabilities and existing build nonconformances |
| `50-system/02-use-cases-and-stories.md` | Change | Add hosted operation, monitoring, sign-off, delivery projection and role-separation flows |
| `50-system/03-nonfunctional-requirements.md` | Change | Startup, deployment verification, authoritative-store and background-job behavior |
| `50-system/04-technical-architecture-requirements.md` | Change | Hosted topology, durable state, single selectors, worker ownership and event triggers |
| `50-system/05-interoperability-requirements.md` | Change | Record async lifecycle states and third-orchestrator/topology comparison |
| `50-system/06-data-and-privacy-requirements.md` | Change | Add anonymous analytics minimisation; record broad as-built nonconformance |
| `50-system/07-security-and-identity-requirements.md` | Change | Hosted fail-closed checks, issuer keys, secret relocation, role semantics and actual rotation behavior |
| `50-system/08-observability-requirements.md` | Change | Authoritative store, typed producers, content-off behavioral telemetry and usage monitoring |
| `50-system/09-acceptance-and-verification.md` | Change | Add late-feature gates and a reference-build result ledger |
| `60-cost/01-cost-model-and-projection.md` | Change | Include hosted services, scheduled functions, state store, telemetry and monitoring paths |
| `60-cost/02-sizing-framework.md` | Change | Add background cadence, analytics events, state growth and multi-orchestrator dimensions |
| `70-delivery/01-delivery-plan.md` | Change | Contrast hypothetical phase plan with actual workstream/Jira projection and deployment verification |
| `70-delivery/02-risks-assumptions-dependencies.md` | Change | Add config drift, wrong-store fallback, ephemeral state, untyped reads and telemetry credential risks |
| `90-traceability/01-traceability-matrix.md` | Change | New and amended requirements require regenerated upward/downward coverage |
| `90-traceability/02-provenance.md` | Change | Add D48-D63 and WS13-WS18 origins; preserve as severable appendix |

## Boundary of this review

This directory is analysis only. Permanent requirement identifiers are not
allocated here, exact replacement requirement prose is not drafted, and no
existing requirement is silently repaired. Those are decisions for a later
requirements-editing pass after the recommendations are accepted.
