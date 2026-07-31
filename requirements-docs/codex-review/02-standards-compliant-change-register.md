# Standards-Compliant Change Register

## 1. Editorial position

This register preserves the requirements suite's existing rules:

- forward-looking `SHALL`/`SHOULD` obligations;
- self-contained `50-system/` and `60-cost/`;
- no vendor/product names in system requirements;
- no implementation accident promoted into a requirement;
- measured, modelled and assumed evidence labels;
- permanent IDs allocated only when an accepted change is applied.

The reference build's failures do not lower the target. They become verification
results. A requirement is amended only where the build exposed ambiguity, a
missing general obligation or an obligation whose original form is needlessly
solution-specific.

## 2. System and verification changes

### SC-001 — Add a reference-build verification ledger

| Field | Recommendation |
|---|---|
| Severity | **Critical** |
| Affects | `00-plan.md`, conventions, acceptance/verification, traceability |
| Action | Add a status artefact; do not change requirement statements |
| Problem | The suite defines verification methods but does not state which requirements the reference build passes, fails, partially satisfies or has not run |
| Change | Define statuses `pass`, `fail`, `partial`, `not run`, `not applicable`, and `not verifiable`; require evidence/date/conditions for every result; forbid inferring pass from code presence |
| Evidence | Existing acceptance governing rule; conformance findings in this review |
| Evidence status | **Both** |
| Downstream | Definition of done, acceptance gates, traceability maintenance |

### SC-002 — Distinguish target system, reference build and live deployment

| Field | Recommendation |
|---|---|
| Severity | **Critical** |
| Affects | README, generation plan, context, provenance |
| Action | Amend framing |
| Problem | The suite moves between a fictional target estate, a reference implementation and measured live state without a formal status model |
| Change | Define the three objects once. A target requirement can remain Must while the reference build fails it; a committed implementation can exist while live deployment remains unverified |
| Evidence | Requirements purpose; D48-D63; stale architecture versus current deployment map |
| Evidence status | **Conflict** |
| Downstream | Every claim of “built”, “deployed”, “measured” or “accepted” |

### SC-003 — Narrow configuration-only substitution to semantic independence

| Field | Recommendation |
|---|---|
| Severity | **High** |
| Affects | `FR-203`, `NFR-401`, `AC-103` |
| Action | Amend |
| Problem | The text protects participants from redeployment but leaves config activation, restart and system-owned deployment semantics unstated; repository inspection alone cannot establish failure |
| Change | Preserve the no-participant-change invariant; separately state and test how configuration becomes active and which system-owned lifecycle actions are permitted |
| Evidence | D55 and target remap tests; packaged hosted config |
| Evidence status | **Not verifiable** |
| Downstream | Configuration verification and operations procedures |

### SC-004 — Add experiment-identity invariance across operating modes

| Field | Recommendation |
|---|---|
| Severity | **High** |
| Affects | Functional, technical and acceptance requirements |
| Action | Add requirement in scenario/execution block |
| Problem | A location remap silently changed backend/platform and invalidated a comparison while every request still passed |
| Change | Require deployment/location modes to preserve scenario, participant, backend class and protocol; any intentional change creates a distinct experiment identity |
| Evidence | D55; hosted-mode config tests |
| Evidence status | **Both** |
| Downstream | Scenario schema, recorded run context, comparison criteria |

### SC-005 — Expand orchestration comparison from two placements to concurrency ownership

| Field | Recommendation |
|---|---|
| Severity | **High** |
| Affects | `FR-506`, fan-out use case, acceptance AC-204/AC-603, learning lens |
| Action | Amend |
| Problem | “Both placements” no longer captures host tool, declared graph and serial platform delegating parallel work to a seam |
| Change | Require comparison of every materially different concurrency owner selected for evaluation, on the same task and legs, including where a platform delegates concurrency off-platform |
| Evidence | D41, D61; three orchestrator implementations |
| Evidence status | **Both** |
| Downstream | Orchestrator attribution, partial failure and latency/cost comparison |

### SC-006 — Make asynchronous capability a lifecycle matrix

| Field | Recommendation |
|---|---|
| Severity | **High** |
| Affects | `FR-207`, `IR-402`-`IR-404`, protocol matrix, acceptance |
| Action | Amend/clarify |
| Problem | “Supports A2A” does not establish prompt return, unattended progress, durable task state and readable result |
| Change | Record submission, progress, task durability, polling/read-back and cancellation independently per endpoint; prohibit collapsing submit-only into asynchronous support |
| Evidence | D47; async probe; Agent Engine submit-only finding |
| Evidence status | **Both** |
| Downstream | Protocol matrix schema and live test gates |

### SC-007 — Add a first-class long-job trigger pattern

| Field | Recommendation |
|---|---|
| Severity | **High** |
| Affects | Functional, nonfunctional and technical requirements |
| Action | Add |
| Problem | A web request reimplemented a scheduled job, drifted in platform coverage/credentials and exceeded front-door budgets |
| Change | Require an interactive trigger for long work to invoke the component that owns the job asynchronously, return acknowledgement promptly, and expose durable status/result progress |
| Evidence | D54; harvest Lambda invocation and polling |
| Evidence status | **Both** |
| Downstream | Timeout budgets, operator UI, failure reporting, authorization |

### SC-008 — Add persistent-worker ownership and idempotency state

| Field | Recommendation |
|---|---|
| Severity | **High** |
| Affects | Functional, availability, architecture and operability requirements |
| Action | Add |
| Problem | A long-running poll loop was planned as an event function, and ephemeral serviced-state would duplicate business deliveries after restart. The implemented durable checkpoint is written after the external delivery, so it does not prove exactly-once behavior across a crash window |
| Change | Classify continuous loops as services/workers; require durable replay checkpoints plus destination idempotency, idempotency keys or equivalent crash-safe control; require lost/failed state to fail visibly rather than repeat side effects |
| Evidence | D52 (`plan/00-decisions.md:1699`); delivery-before-checkpoint ordering (`src/briefs/runner.py:343`; `src/briefs/__main__.py:104`); state tests (`tests/unit/test_briefs_watch_state.py:56`) |
| Evidence status | **Not verifiable** |
| Downstream | Async delivery use case, storage, recovery and cost model |

### SC-009 — Add authoritative-store and visible-fallback semantics

| Field | Recommendation |
|---|---|
| Severity | **Critical** |
| Affects | `TR-301`, `NFR-205`, observability and acceptance requirements |
| Action | Amend/add |
| Problem | Multiple selectors with different defaults returned plausible but stale/empty data from the wrong store |
| Change | Name one selection authority per record class; require hosted authority, offline snapshot mode and fallback state to be explicit; prohibit a fallback from masquerading as authoritative data |
| Evidence | D49; unified selector and read-side tests |
| Evidence status | **Both** |
| Downstream | Console status, harvest, analysis and operational recovery |

### SC-010 — Separate immutable interactions from mutable typed operator state

| Field | Recommendation |
|---|---|
| Severity | **High** |
| Affects | `NFR-701`, `TR-301`, findings/sign-off and delivery requirements |
| Action | Clarify/add |
| Problem | Append-only interaction evidence, human sign-offs, worker checkpoints and operational reports have different mutation and failure semantics |
| Change | Define record classes. Interaction evidence remains append-only; governance acts are durable/audited; checkpoints may update atomically; failed governance/checkpoint writes may not soft-fail |
| Evidence | D50, D52; `lab_state` callers |
| Evidence status | **Both** |
| Downstream | Storage interfaces, backup/export and acceptance |

### SC-011 — Require discriminated shared stores and explicit reader filters

| Field | Recommendation |
|---|---|
| Severity | **High** |
| Affects | Functional and observability requirements |
| Action | Add |
| Problem | A reader over a shared table displayed the newest record from the wrong producer and hid a paused analyst |
| Change | Require every shared multi-producer store to carry a producer/type discriminator and every consumer to select its declared type; unfiltered reads must be prohibited or explicitly named. Individual producer schemas may close their own values without making the shared store globally closed |
| Evidence | D56 (`plan/00-decisions.md:1852`); extensible brief writes (`src/observability/pg.py:167`); typed query regression tests |
| Evidence status | **Both** |
| Downstream | UI headings, empty state and producer schedule visibility |

### SC-012 — Make evidence provenance part of every operator display

| Field | Recommendation |
|---|---|
| Severity | **Medium** |
| Affects | `FR-604`, `NFR-602`, findings and operator-interface acceptance |
| Action | Amend/add |
| Problem | Correct numbers and briefs were misleading when their producer, data source, bounds or schedule were absent |
| Change | Require displayed evidence to expose producer, input authority, storage, identity, observation bounds and important caveats; empty states must explain absence versus failure versus not scheduled |
| Evidence | D56, D57, D60, D62 |
| Evidence status | **Both** |
| Downstream | Accessibility/usability demonstration and evidence honesty |

## 3. Security, identity and data changes

### SC-013 — Strengthen hosted fail-closed startup and negative verification

| Field | Recommendation |
|---|---|
| Severity | **Critical** |
| Affects | `NFR-402`, `SR-202`, `AC-504`, deployment verification |
| Action | Amend |
| Problem | Healthy deployment and positive-auth checks passed while the public service accepted every credential |
| Change | Require hosted mode to be explicit; missing required auth exits before serving; deployment acceptance must test unauthenticated and invalid credentials, not only valid credentials |
| Evidence | D48; hosted/local split tests |
| Evidence status | **Both** |
| Downstream | Every externally reachable operated component |

### SC-014 — Pair secret exclusion with relocation and consumption

| Field | Recommendation |
|---|---|
| Severity | **Critical** |
| Affects | `SR-401`, `SR-404`, `NFR-403`, `AC-508` |
| Action | Amend/add |
| Problem | Pattern-based exclusion removed credentials from plain environment without placing them in the runtime secret; another secret was shipped but never loaded |
| Change | Treat detect/exclude, store, grant, inject, load and negative-test as one deployment contract; a newly secret-classified value must fail deployment if it has no secure destination/consumer |
| Evidence | D48, D53 |
| Evidence status | **Both** |
| Downstream | Deploy scripts, environment contract tests and secret inventory |

### SC-015 — State issuer/verifier key responsibilities

| Field | Recommendation |
|---|---|
| Severity | **High** |
| Affects | Security/identity architecture and acceptance |
| Action | Add |
| Problem | A hosted token issuer had only a verification key, generated an ephemeral signing key and issued tokens no configured verifier accepted |
| Change | Require each token participant to declare issuer/verifier role; issuers receive controlled signing material, verifiers only public material; hosted processes may not generate substitute production keys |
| Evidence | D53; identity tests |
| Evidence status | **Both** |
| Downstream | Key rotation, startup and end-to-end login test |

### SC-016 — Correct rotation semantics

| Field | Recommendation |
|---|---|
| Severity | **High** |
| Affects | `SR-402`, `AC-509`, operations |
| Action | Decide and amend |
| Problem | Current Must says rotation needs no redeploy, while container-start loading intentionally requires a controlled redeploy/restart |
| Change | Preferred target: rotation requires no rebuild and no code/config edit; a rolling restart is permitted and documented, with overlap/revocation test. If zero-restart rotation is truly required, the implementation must change instead |
| Evidence | D53; operations record |
| Evidence status | **Conflict** |
| Downstream | Availability, secrets architecture and acceptance wording |

### SC-017 — Add owner credential separation without role-name coupling

| Field | Recommendation |
|---|---|
| Severity | **High** |
| Affects | Personas, `SR-601`/`SR-602`, authorization acceptance |
| Action | Amend/add |
| Problem | A shared operator password also served as the owner's login; code coupled authorization to one role string |
| Change | Require distinct credentials per accountable human class where sharing is allowed; authorize through named capability sets, not equality with one role label; keep reviewer/sign-off as an independent grant |
| Evidence | D63; operator-role helper and tests |
| Evidence status | **Both** |
| Downstream | User configuration, secret inventory, permission matrix |

### SC-018 — Add anonymous analytics as a closed privacy boundary

| Field | Recommendation |
|---|---|
| Severity | **Critical** |
| Affects | Scope, data/privacy, security and observability requirements |
| Action | Add |
| Problem | `/api/track` is a new anonymous write surface and persistent visitor identifier not covered by the old closed endpoint exemption or privacy model |
| Change | Define allowed events/fields as a closed set; prohibit IP, content and client-asserted persona; use server time and verified identity; define visitor-ID purpose/retention; make ingestion non-blocking and write-only; authenticate aggregate reads |
| Evidence | D62; endpoint/store/UI tests |
| Evidence status | **Both** |
| Downstream | `SR-201` exemption list, retention, privacy notice, threat model and acceptance |

### SC-019 — Prefer source omission for behavioral telemetry

| Field | Recommendation |
|---|---|
| Severity | **High** |
| Affects | Data minimisation, observability and build telemetry requirements |
| Action | Add |
| Problem | Behavioral insight does not require prompts, tool arguments, file content or model responses, and downstream masking would transmit/store them first |
| Change | Require content disabled at source where metadata satisfies the purpose; store only derived aggregates; report enabled content flags as configuration evidence |
| Evidence | D59; coding-log reader and tests |
| Evidence status | **Both** for code, live round-trip not re-probed |
| Downstream | Data inventory, telemetry acceptance and operator Details |

## 4. Operations, delivery and commercial changes

### SC-020 — Add one-way projection and authority rules

| Field | Recommendation |
|---|---|
| Severity | **Medium** |
| Affects | Delivery plan, operability and project evidence |
| Action | Add |
| Problem | Bidirectional plan/board synchronization would create two editable truths; live read-back would make the console disagree with the repo |
| Change | Name the authoritative delivery record, projection direction, supported source shapes, stale-view signal and prohibited reverse sync; do not infer completion from prose |
| Evidence | D58, D60; Jira sync and project endpoint tests |
| Evidence status | **Both** |
| Downstream | Delivery runbook and governance of external work-management tools |

### SC-021 — Add deployed-version and config-activation evidence

| Field | Recommendation |
|---|---|
| Severity | **High** |
| Affects | `NFR-403`, deployment acceptance, operations |
| Action | Amend |
| Problem | `--skip-build` updated task/secret state while old code remained in the image, causing repeated false diagnoses |
| Change | Require every deployment to report source/build identity, config revision and activation action; verification must query the running artifact rather than infer from a successful deploy command |
| Evidence | D53; D46 continuation |
| Evidence status | **Both** |
| Downstream | AC-803 and runbooks |

### SC-022 — Expand the build inventory and cost model

| Field | Recommendation |
|---|---|
| Severity | **High** |
| Affects | build-vs-buy and both cost documents |
| Action | Amend |
| Problem | The cost model expects scale-to-zero for nearly every component, while the reference build has continuous services; the reusable mechanism inventory also omits generalized operating/state/monitoring burdens |
| Change | Add implementation-neutral continuous, scheduled, per-invocation, storage-growth, governance-state, telemetry and external-service cost shapes, plus idle-baseline sensitivity. Keep named console/Jira/analytics components in the exact track rather than M1-M17 |
| Evidence | `60-cost/01-cost-model-and-projection.md:128`; `40-lens-c-lab-learning/03-build-vs-buy.md:35`; D48-D63; `plan/09-deployment-map.md:38` |
| Evidence status | **Both** |
| Downstream | ROI, sizing variables and recurring burden comparison |

### SC-023 — Reconcile actual delivery history with the hypothetical phase plan

| Field | Recommendation |
|---|---|
| Severity | **Medium** |
| Affects | delivery plan and RAID log |
| Action | Add comparison, do not replace dependency plan |
| Problem | The normative eight-phase sequence was not the build's historical sequence and omits late operational workstreams |
| Change | Keep the dependency-derived phase plan; add a severable retrospective mapping actual workstreams/decisions to phases and identify rework caused by sequence deviations |
| Evidence | Workstream chronology, D48-D63, one-way Jira record |
| Evidence status | **Both** |
| Downstream | Learning objectives and future estimation |

## 5. Lens and context changes

### SC-024 — Label the enterprise estate as target context, not build fact

Amend the enterprise lens, stakeholders and context so every regulatory and
divisional claim is visibly a fictional target constraint. Do not imply the
reference build implemented or obtained DPO approval for it. Preserve the lens
because it is the source of legitimate commissioned requirements.

### SC-025 — Update the field-enablement case with operational evidence

Add the hosted estate, three concurrency owners, identity federation, deployment
verification failures, observability join limitations and recurring credential
burden to the field case. These are the evidence that makes customer interop
conversations more credible; UI polish alone is not the value claim.

### SC-026 — Update practitioner learning objectives

Add learning objectives covering:

- protocol support versus asynchronous lifecycle support;
- where concurrency and background compute actually run;
- authoritative versus fallback stores;
- secret exclusion, relocation and runtime consumption;
- durable human governance state;
- metadata-only behavioral telemetry;
- one-way projection from an authoritative delivery record;
- evidence provenance and meaningful empty states.

### SC-027 — Extend risks and assumptions

Add risks for stale mode remaps, fallback stores producing plausible answers,
ephemeral worker state duplicating side effects, shared tables with unfiltered
readers, deployed image/config divergence, anonymous analytics retention,
content telemetry flags being enabled later, and external delivery views being
mistaken for authority. Also add explicit assumptions and dependencies for
authoritative hosted storage, runtime-secret/IAM availability, scheduler/worker
ownership, external delivery APIs and telemetry ingestion. Use the existing RAID
fields for owner, probability/impact, validation point and consequence of delay;
do not reduce the update to a risk-only list.

### SC-028 — Define identifier schemas by class

| Field | Recommendation |
|---|---|
| Severity | **High** |
| Affects | `01-conventions.md`, `00-plan.md`, all identifier-bearing documents |
| Action | Amend conventions; do not renumber permanent IDs |
| Problem | The conventions require one field anatomy and themed 100-block tables for every requirement class, while BR, UC/US, AC, CST and RAID records intentionally use different schemas and numbering (`01-conventions.md:29`; `20-lens-a-enterprise/02-business-requirements.md:25`; `50-system/02-use-cases-and-stories.md:14`; `70-delivery/02-risks-assumptions-dependencies.md:11`) |
| Change | Define mandatory fields, verification/trace rules and numbering separately for system requirements, business requirements, use cases/stories, acceptance, cost and RAID; retain all allocated IDs |
| Evidence status | **Conflict** |
| Downstream | Done criteria, mechanical validation, traceability generation and authoring guidance |

### SC-029 — Make the autonomous-build input set unambiguous

| Field | Recommendation |
|---|---|
| Severity | **High** |
| Affects | README self-containment, O2, resolved Q3, done criteria and provenance |
| Action | Reconcile |
| Problem | Several current statements say `50-system/` plus `60-cost/` are the sole standalone input (`README.md:56`; `00-plan.md:13`; `90-traceability/02-provenance.md:15`), while resolved Q3 says `10-context/` travels with them (`00-plan.md:151`) |
| Change | Declare one authoritative autonomous-build bundle and use it consistently in objectives, self-containment tests, done criteria and provenance; keep severable origin material outside that bundle |
| Evidence status | **Conflict** |
| Downstream | Autonomous-build experiment setup, self-containment verification and reproducibility |

### SC-030 — Align acceptance methods with governing requirements

| Field | Recommendation |
|---|---|
| Severity | **High** |
| Affects | AC-701, AC-703, AC-704, AC-706 and their governing requirements |
| Action | Correct methods |
| Problem | Acceptance marks these criteria Inspection even though FR-701, FR-703, FR-303 and OR-406 require mechanically checkable Tests; the acceptance document itself says monetary-label completeness is a Test (`50-system/09-acceptance-and-verification.md:40`; `50-system/09-acceptance-and-verification.md:200`) |
| Change | Use Test for required-field/label/completeness assertions; add Inspection or Analysis only where evidence adequacy or behavior reconciliation needs judgement |
| Evidence | `50-system/01-functional-requirements.md:301`, `50-system/01-functional-requirements.md:703`, `50-system/01-functional-requirements.md:736`, `50-system/08-observability-requirements.md:359` |
| Evidence status | **Conflict** |
| Downstream | Test strategy, exception classes and reference-build result ledger |

### SC-031 — Reconcile published and negotiated rate semantics

| Field | Recommendation |
|---|---|
| Severity | **High** |
| Affects | `OR-406`, `AC-706`, both cost documents and reporting rules |
| Action | Amend |
| Problem | `OR-406` applies published-rate labelling to any monetary figure (`50-system/08-observability-requirements.md:359`), while projections mandate the organisation's negotiated rate card (`60-cost/01-cost-model-and-projection.md:203`; `60-cost/02-sizing-framework.md:98`) |
| Change | Require “modelled at stated, sourced and dated rates”; distinguish public/list-rate UI estimates from private negotiated projections without weakening the modelled label |
| Evidence status | **Conflict** |
| Downstream | AC-706, cost reconciliation, publication and confidentiality rules |

### SC-032 — Correct DEP-10's identity trace

| Field | Recommendation |
|---|---|
| Severity | **Medium** |
| Affects | `DEP-10`, `SR-104`, `SR-601`, RAID trace references |
| Action | Correct reference |
| Problem | DEP-10 says missing corporate IdP makes `SR-601` unsatisfied (`70-delivery/02-risks-assumptions-dependencies.md:209`), but corporate IdP is the explicit `SR-104` obligation (`50-system/07-security-and-identity-requirements.md:83`); `SR-601` requires human authentication, which the lab mechanism may satisfy |
| Change | Point the corporate-IdP consequence to `SR-104`; assess `SR-601` independently on human authentication and attribution |
| Evidence status | **Conflict** |
| Downstream | RAID validation, identity conformance results and trace reference checks |

## 6. Explicit no-change recommendations

The review recommends retaining these principles despite reference-build gaps:

- the fictional enterprise and its regulatory obligations in the reusable
  standards track;
- implementation-neutral protocol and platform classes in `50-system/`;
- evidence labels and native/mediated honesty;
- permanent IDs and severable provenance;
- the not-established register and negative recommendation option;
- the distinction between deterministic extraction and optional agent analysis;
- live tests being separable from no-credential test levels.

Weakening these to make the reference build appear conformant would defeat the
original autonomous-build and evidence-quality experiment.
