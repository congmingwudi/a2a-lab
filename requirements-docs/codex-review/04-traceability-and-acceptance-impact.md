# Traceability and Acceptance Impact

## 1. How to apply accepted recommendations

This review does not allocate permanent identifiers. When a later editing pass
accepts a change:

1. amend an existing requirement where the obligation already exists but is
   ambiguous or too narrow;
2. allocate a new ID from the relevant thematic block only for a genuinely new
   obligation;
3. add or amend business trace before regenerating the matrix;
4. update use cases/stories and acceptance in the same change;
5. add decision/result provenance only in the severable appendix;
6. add an explicit reference-build verification result without implying that
   implementation evidence is acceptance evidence.

The standards-compliant track and exact-as-built track should never share one
matrix without a profile/status column. They have different subjects.

## 2. Candidate requirement impacts

| Review item | Likely class/block | Existing IDs to amend | Candidate business trace |
|---|---|---|---|
| Reference-build result ledger | Verification/traceability, not a new system requirement | Acceptance governing rule; matrix maintenance | BR-501, BR-502, BR-503 |
| Target/build/live state distinction | Context/conventions | README, plan, provenance | BR-501, BR-502 |
| Experiment identity across modes | FR-6xx / TR-1xx | FR-602, NFR-401, TR-104 | BR-501, BR-502 |
| Concurrency-owner comparison | FR-5xx / OR-3xx | FR-506, FR-507, OR-303 | BR-202, BR-404, BR-501 |
| Async lifecycle matrix | IR-4xx | FR-207, IR-402-IR-404, NFR-107/108 | BR-206, BR-502, BR-503 |
| Long-job owner trigger | FR-2xx / NFR-1xx | FR-207, FR-206, NFR-102 | BR-204, BR-206, BR-501 |
| Persistent worker/idempotency | FR-2xx / NFR-2xx / TR-2xx | FR-208, NFR-205, TR-202/204 | BR-203, BR-206, BR-501 |
| Authoritative store/fallback | TR-3xx / NFR-2xx | TR-301, NFR-205, OR-302 | BR-305, BR-501, BR-503 |
| Typed mutable operator state | TR-3xx / NFR-2xx/7xx | Clarify NFR-205/NFR-701 record scope; otherwise new obligation | BR-305, BR-501, BR-504 |
| Discriminated shared records | TR-3xx / OR-3xx | New obligation; OR-302 remains extraction-specific | BR-305, BR-501, BR-502 |
| Display provenance and empty state | FR-6xx / NFR-6xx | FR-604/605, NFR-602/603 | BR-501, BR-502, BR-503 |
| Hosted fail-closed startup | NFR-4xx / SR-2xx | NFR-402, SR-202 | BR-306, BR-501 |
| Secret relocation/consumption | SR-4xx / NFR-4xx | SR-401/404, NFR-403 | BR-306, BR-501 |
| Issuer/verifier key duties | SR-4xx | SR-401-403 | BR-306 |
| Rotation with controlled restart | SR-4xx | SR-402 | BR-306 |
| Owner/operator separation | SR-6xx | SR-601/602; do not repurpose human-versus-service SR-104 | BR-305, BR-306 |
| Anonymous analytics boundary | DR-1xx/5xx, SR-2xx/5xx, OR-5xx | SR-201, SR-501, NFR-302 | BR-301, BR-305, BR-501 |
| Source-omitted behavioral telemetry | DR-2xx / OR-6xx | Amend DR-201/205 boundary semantics and add a new build-telemetry minimisation obligation; OR-603 only separates domains | BR-301, BR-401, BR-501 |
| One-way delivery projection | NFR-4xx / delivery | NFR-406 | BR-501, BR-504 |
| Running artifact/config identity | NFR-4xx / TR-2xx | NFR-403/404, TR-205 | BR-306, BR-501 |
| Expanded build inventory | Cost/build-vs-buy | CST inventory and M1-M17 | BR-403, BR-404 |
| Per-class identifier schemas | Conventions/plan | Anatomy, allocation and done-criteria rules | Not a new business obligation |
| Authoritative autonomous-build bundle | README/plan/provenance | O2, Q3, self-containment and done criteria | BR-501 |

## 3. Business-requirement effects

### Existing business requirements that remain sufficient

Most late findings trace cleanly to existing business needs:

- **BR-204** covers explicit failure and long-job/partial-result behavior.
- **BR-206/207** cover asynchronous progress and delivery.
- **BR-305** covers durable sign-off, attributable state and reconstruction.
- **BR-306** covers fail-closed identity, secret lifecycle and role separation.
- **BR-401/402** cover build telemetry category and attribution integrity.
- **BR-501/502/503** cover authoritative sources, provenance, honest labels and
  not-established state.
- **BR-404** covers the expanded operational build inventory.

### Candidate new business obligations

Avoid adding a business requirement for every implementation lesson. Two
possible additions survive that test:

1. **Operational truth has one authority.** A decision-grade evaluation must not
   show plausible results or delivery status from an unstated fallback or a
   second editable source.
2. **Human governance acts are durable and attributable.** A sign-off or
   disposition is not complete until durable storage acknowledges it.

These could instead be explicit clarifications under BR-305 and BR-501. That is
preferred unless stakeholders treat them as independently fundable outcomes.

### Business requirements to reclassify in the exact track

BR-301 through BR-304 are target enterprise obligations not implemented by the
lab. In an exact-as-built edition, move them to an enterprise-adoption profile
or mark them failed/not implemented. Do not rewrite them into weak claims about
credential scrubbing or regional deployment.

## 4. Use-case and story impacts

### Amend existing use cases

- **Cross-platform invocation:** add operating-mode resolution and experiment
  identity verification before execution.
- **Fan-out business event:** select concurrency owner/topology, preserve the
  same legs and report both transport and business completeness.
- **Asynchronous work:** separate submit/read lifecycle, worker ownership,
  durable status and destination delivery.
- **Run and inspect scenario:** expose producer/source/bounds and distinguish
  absent, stale, paused and failed evidence.
- **Audit/reconstruction:** include durable human sign-off and typed operator
  state without treating mutable state as an interaction event.

### Add use cases

| Candidate | Primary actor | Success outcome |
|---|---|---|
| Trigger a long-running owned job | Operator | Prompt acknowledgement, durable progress, no duplicate implementation in web request |
| Record a governance act | Reviewer | Durable acknowledged sign-off with attributable actor and visible failure |
| Operate a persistent worker | Platform operations | Restart without duplicate business side effects or lost checkpoint |
| Review monitoring aggregates | Authenticated operator | Minimal anonymous analytics summarized without exposing visitor content/identity |
| Project delivery state outward | Delivery lead | External board regenerated from authority, never read back as truth |
| Rotate a hosted credential | Platform operations | Secret updated, controlled activation completed, old credential revoked, running version verified |

### Add stories

- As an evaluator, I need deployment mode to preserve experiment identity so a
  passing run cannot compare the wrong implementations.
- As an operator, I need stale/fallback data labelled so a plausible dashboard
  cannot be mistaken for the authoritative store.
- As a reviewer, I need failed sign-off persistence to be visible so a green
  approval cannot be lost on restart.
- As platform operations, I need worker checkpoints durable so a restart cannot
  duplicate delivery into a business system.
- As a data-protection reviewer, I need usage telemetry fields and retention
  bounded before an anonymous write surface is accepted.
- As a delivery lead, I need one scope authority so plan and board cannot both
  claim editable truth.

## 5. Acceptance impacts

### Amend existing criteria

| Criterion | Recommended change |
|---|---|
| AC-103 | Prove protocol/backend/scenario identity is unchanged; separately verify config activation without participant code/deploy changes |
| AC-204 | Execute each concurrency owner/topology; require business completeness signal independent of HTTP status |
| AC-301-305 | Re-enumerate paths after bridge fan-out and third orchestrator; include new delegated topology |
| AC-504 | Test hosted missing-auth startup failure plus unauthenticated and wrong-token requests; retain explicit local-dev behavior |
| AC-508 | Verify secret classification, secure relocation, injection and actual runtime consumption—not only absence from source/plain env |
| AC-509 | Resolve the zero-redeploy contradiction; preferred criterion permits controlled restart without rebuild |
| AC-603 | Record orchestrator/topology and platform width alongside join rate |
| AC-701/703/704 | Verify producer, source authority, bounds, evidence state and not-established state at display point; change mechanically checkable completeness/designation assertions from Inspection to Test, with judgement-based reconciliation retained separately |
| AC-706 | Change method to Test and require monetary figures to identify modelled status plus stated, sourced and dated rate basis rather than universally assuming published rates |
| AC-803 | Compare running source/image/config identity with intended deployment, including `--skip-build` cases |
| AC-805 | Add authoritative-store recovery, state sync and worker restart procedures |

### Candidate new criteria

| Candidate | Criterion | Method |
|---|---|---|
| AC-N01 | Hosted component with a configured hosted-mode signal and missing required credential exits before binding; local mode follows its documented policy | Test |
| AC-N02 | A newly secret-classified value causes deployment failure unless it is stored, granted, injected and loaded through the secure path | Test + Analysis |
| AC-N03 | Token issued by the hosted issuer verifies against every configured verifier; an ephemeral substitute key is refused | Test |
| AC-N04 | Switching local/hosted mode changes address only; participant, backend and protocol identifiers remain equal | Test |
| AC-N05 | Hosted readers use the authoritative store; offline fallback is visibly labelled and cannot be reported as current hosted data | Test |
| AC-N06 | A governance write failure returns failure and does not display a successful sign-off | Test |
| AC-N07 | Restarting the persistent worker does not repeat a previously acknowledged business delivery | Test/Demonstration |
| AC-N08 | Each brief/analysis reader returns only its declared producer type and exposes producer schedule state | Test |
| AC-N09 | Long-running on-demand work returns acknowledgement within the front-door budget and advances through the owning worker's durable state | Test/Demonstration |
| AC-N10 | Unknown analytics events/fields are rejected or dropped; stored accepted events contain no IP, content or client-asserted persona | Test |
| AC-N11 | Monitoring aggregates require authentication while anonymous ingest returns no stored data | Test |
| AC-N12 | Owner and operator credentials are distinct; both reach the same capability set; reviewer remains an independent grant | Test |
| AC-N13 | With all content flags off, harvested behavioral telemetry contains no prompt, response, tool argument, file content or raw body | Test against emitted/harvested schema |
| AC-N14 | Regenerating the external delivery view does not read status back or invent work items from narrative prose | Test |

### Reference-build result table shape

Add a generated or mechanically checked table with these fields:

| Field | Rule |
|---|---|
| Requirement/criterion ID | Must resolve to the authoritative definition |
| Build baseline | Commit or released build identity |
| Status | Pass, fail, partial, not run, not applicable, not verifiable |
| Evidence | Test/result/inspection reference; source presence alone is not pass |
| Conditions | Mode, platform, credentials, region, cold/warm and other material conditions |
| Executed/observed at | Date, required for live third-party behavior |
| Owner | Person/role responsible for rerun or disposition |
| Notes | Failure versus platform finding, accepted deviation or next action |

## 6. Traceability matrix maintenance

After accepted edits:

1. regenerate upward mappings from each requirement's `Traces to` field;
2. check new business obligations for empty downstream coverage;
3. check every new system requirement for an upward business trace;
4. update use-case/story `Satisfied by` references;
5. update acceptance coverage so each new Must has a gate or named exception;
6. keep exact-track product/component evidence out of the reusable matrix;
7. link reference-build results to requirement IDs without counting them as
   downward requirements.
8. validate each identifier against the schema for its own class rather than the
   system-requirement anatomy.
9. test self-containment against the single declared autonomous-build bundle.

The current matrix's contradiction pass should be rerun specifically for:

- anonymous `/api/track` versus the closed `SR-201` exemption set;
- `SR-402`/AC-509 versus container-start secret loading;
- append-only `NFR-701` versus mutable governance/checkpoint state;
- independent deployability versus multi-face service boundaries;
- raw-payload evidence versus content-off coding telemetry (different record
  domains, which must be explicit);
- anonymous visitor identifiers versus retention/subject-rights requirements;
- soft-failing read fallbacks versus authoritative evidence requirements.
- AC-701/703/704/706 Inspection methods versus their governing Test methods;
- OR-406 published-rate wording versus negotiated-rate cost projections;
- DEP-10's `SR-601` reference versus the corporate-IdP obligation in `SR-104`.

## 7. Provenance additions

Add the following to the severable provenance appendix, not to the reusable
requirement statements:

| Origin | Requirement lesson |
|---|---|
| D48 (`plan/00-decisions.md:1479`) | Hosted fail-closed startup, negative auth verification, secure-variable classification |
| D49 (`plan/00-decisions.md:1539`) | Single authoritative store selector and visible fallback mode |
| D50 (`plan/00-decisions.md:1595`) | Durable acknowledged governance writes |
| D51 (`plan/00-decisions.md:1623`) | Hosted/local semantic twins and mounted-app lifecycle ownership |
| D52 (`plan/00-decisions.md:1666`) | Persistent-worker shape and durable replay checkpoint; exactly-once delivery remains not verified across the delivery-before-checkpoint crash window |
| D53 (`plan/00-decisions.md:1711`) | Secret relocation, issuer key custody and running-image verification |
| D54 (`plan/00-decisions.md:1769`) | Long job invoked through its owning worker rather than web reimplementation |
| D55 (`plan/00-decisions.md:1811`) | Experiment identity invariant across deployment modes |
| D56 (`plan/00-decisions.md:1852`) | Shared-store discriminator and mandatory explicit reader filters; the store remains extensible to new producer types |
| D57 (`plan/00-decisions.md:1897`) | Producer/source/bounds and meaningful empty state at the display point |
| D58 (`plan/00-decisions.md:1957`) | One-way projection from authoritative delivery scope |
| D59 (`plan/00-decisions.md:2007`) | Source-side content omission and aggregate-only behavioral telemetry |
| D60 (`plan/00-decisions.md:2082`) | Operator project view reads the repo authority, not external projection |
| D61 (`plan/00-decisions.md:2114`) | Concurrency ownership as the orchestration comparison axis |
| D62 (`plan/00-decisions.md:2168`) | Minimal anonymous server-mediated analytics and authenticated aggregate reads |
| D63 (`plan/00-decisions.md:2219`) | Owner credential separation and capability-set authorization |

Include WS13-WS18 as delivery origins where they add implementation context, but
use decision IDs for the durable rationale.

## 8. Cost and delivery impacts

### New cost/sizing dimensions

- always-on vCPU/memory hours for bridge/console/faces/watcher services;
- scheduled function invocations and duration for harvest/credential jobs;
- PostgreSQL compute, Data API requests and growth by record class;
- usage-event rate, visitor cardinality and retention window;
- telemetry ingestion/read-back volume for metrics versus logs;
- managed-agent scheduled/on-demand sessions and tool servicing;
- external logging/notification forwarding;
- build/deploy/rotation/operator effort per component and credential seam;
- orchestrator topology cost per completed business task, including failed legs.

### New delivery risks

| Risk | Required treatment |
|---|---|
| Workaround remains after capability lands | Review compensating remaps/config whenever hosting/platform capability changes |
| Fallback produces plausible stale answer | Label authority/fallback and alert on hosted fallback |
| Secret excluded but not relocated | Validate end-to-end secret contract at deployment |
| New image/config not active | Query running identity after every deployment |
| Ephemeral worker state repeats side effect | Durable idempotency state and restart test |
| Shared table returns wrong producer | Closed discriminator and mandatory reader filter |
| Anonymous analytics grows into tracking | Closed fields/events, retention, purpose and privacy review |
| Delivery projection becomes second authority | One-way sync and no read-back |
| Telemetry content flags drift on | Configuration test and schema-level negative assertions |

### New assumptions, dependencies and issues

| Kind | Candidate impact | Required treatment |
|---|---|---|
| Assumption | Hosted authoritative storage remains reachable and correctly selected | Name validation point, owner and fallback consequence; plausible local data must not silently substitute |
| Assumption | Runtime secret assembly and IAM grants deliver every classified credential to its consumer | Validate end to end at deployment; record blast radius and rotation owner |
| Dependency | Scheduler/worker ownership remains singular for harvest and brief delivery | Name owning service, checkpoint store and duplicate-execution consequence |
| Dependency | External platform, Jira/logging and telemetry APIs remain available under scoped identities | Record owner, degradation mode, review trigger and consequence of delay |
| Issue | Current plan runbooks/deployment map disagree with deployed watcher, face and API-Gateway shapes | Correct current imperative instructions before the next operator execution; retain dated history separately |

Allocate permanent RAID IDs only in the later editing pass. Preserve the source
schema in `70-delivery/02-risks-assumptions-dependencies.md:3` and ensure the
update covers assumptions, issues and dependencies as well as risks.

## 9. Completion criteria for a later requirements update

A later edit implementing recommendations is complete only when:

- the standards-compliant and exact-as-built profiles are visibly separate;
- every accepted change has requirement, use-case, acceptance and trace impacts
  updated together;
- the reference build has an explicit conformance status for every Must;
- existing nonconformances remain visible rather than rewritten into passes;
- D48-D63 provenance is present and severable;
- traceability and contradiction checks are rerun;
- no environment identifier, credential or private deployment detail enters the
  public requirement set.
