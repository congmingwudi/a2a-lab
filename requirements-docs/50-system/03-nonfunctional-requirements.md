# Non-Functional Requirements

Qualities the system must have. Several carry numeric budgets; every such figure
is a **planning value labelled `[assumed]`** until the corresponding measurement
requirement establishes it. That structure is deliberate — the binding
constraints here are imposed by platforms the programme does not control, so
they must be measured early rather than designed around.

## Block allocation

| Block | Theme |
|---|---|
| `NFR-1xx` | Performance, latency and timeout budgets |
| `NFR-2xx` | Availability, resilience and degradation |
| `NFR-3xx` | Capacity and rate limits |
| `NFR-4xx` | Operability and deployability |
| `NFR-5xx` | Evolvability |
| `NFR-6xx` | Usability of the evaluation surface |
| `NFR-7xx` | Qualities of the record |

---

## NFR-1xx — Performance, latency and timeout budgets

### NFR-101 — A response-time envelope is defined and measured per interaction shape

**Statement.** The system SHALL define, measure and publish a response-time
envelope for each interaction shape it supports — single delegation, mediated
delegation, concurrent decomposition, asynchronous completion — stating the
conditions of measurement.

**Rationale.** "How long does this take" has no single answer across shapes, and
a figure quoted without its shape and conditions will be applied to the wrong
one. Business owners need the envelope to judge whether a process can use the
capability at all.

**Priority.** Must
**Verification.** Test — envelopes are produced per shape with conditions
recorded, and re-measured after any change to a route.
**Traces to.** BR-205

---

### NFR-102 — The timeout chain is strictly nested and documented

**Statement.** Where a request traverses several components each with a timeout,
those timeouts SHALL be strictly decreasing from outermost to innermost, and the
whole chain SHALL be documented as a single budget.

**Rationale.** If an inner timeout exceeds an outer one, the outer gives up
first and the inner work continues unobserved — consuming resource, possibly
completing, and reported as a failure. The chain is also unanalysable when its
components are configured independently: each looks reasonable alone.

**Priority.** Must
**Verification.** Inspection and test — the configured chain is verified as
strictly decreasing, and an induced timeout at each level produces the expected
outermost behaviour.
**Traces to.** BR-205, BR-204

---

### NFR-103 — The binding constraint is external and measured before design depends on it

**Statement.** The system SHALL establish by measurement, before committing to a
synchronous interaction design, the maximum time each participating platform
will permit its own outbound action to run.

**Rationale.** The tightest link in the chain is typically a platform's internal
action budget, which is not configurable, frequently undocumented, and often
assumed generously. Designing a synchronous interaction against an assumed
budget and discovering the real one later invalidates the design and every
latency finding taken under it.

**Priority.** Must
**Verification.** Test — the effective budget is measured per platform and
recorded with its date. Planning value: none. This figure SHALL NOT be assumed.
**Traces to.** BR-205, BR-501

---

### NFR-104 — Cold and warm performance are budgeted and reported separately

**Statement.** Latency requirements SHALL be stated separately for cold and warm
paths, and every recorded measurement SHALL carry a warm or cold designation.

**Rationale.** On scale-to-zero hosting the cold path can exceed the entire
interactive budget while the warm path sits comfortably inside it. A blended
figure describes neither and will mislead in both directions.

**Priority.** Must
**Verification.** Test — measurements carry the designation, and cold and warm
figures are reported separately.
**Traces to.** BR-205, BR-501

---

### NFR-105 — Concurrent decomposition approximates its slowest leg

**Statement.** Elapsed time for a concurrently decomposed task SHALL approximate
the slowest contributing leg rather than the sum of legs, and the overhead of
decomposition and recombination SHALL be measured and reported.

**Rationale.** This is the entire performance justification for the shape. If
overhead is large enough to erode the advantage, the finding changes the
recommendation — so it must be measured rather than assumed away.

**Priority.** Must
**Verification.** Test — concurrent and sequential executions of the same task
are compared at two, three and four legs.
**Traces to.** BR-202, BR-205

---

### NFR-106 — Mediation overhead is bounded and measured

**Statement.** The latency added by mediation — outbound, inbound, or generation
translation — SHALL be measured and SHALL be small relative to the interaction
it serves.

**Rationale.** Mediation sits on the estate's highest-volume path, because the
platform needing it holds the data everyone wants. Overhead there is paid by
every cross-divisional interaction, and it is also a build-versus-buy input:
mediation that is expensive as well as burdensome strengthens the case to buy.

**Priority.** Should
**Verification.** Test — mediated and direct paths to comparable agents are
measured and the difference attributed.
**Traces to.** BR-201, BR-404

---

### NFR-107 — Asynchronous submission returns promptly

**Statement.** Where asynchronous invocation is used, submission SHALL be
acknowledged promptly and independently of the work's duration, and the system
SHALL record whether each platform genuinely honours this.

**Rationale.** Prompt acknowledgement is the property that removes gateway
timeout ceilings — it is the whole point of the shape. A platform that accepts a
submission but does not return until the work completes provides none of the
benefit while appearing to support the pattern.

**Priority.** Must
**Verification.** Test — submission acknowledgement time is measured
independently of completion time, per platform.
**Traces to.** BR-206

---

### NFR-108 — Unattended progress is verified, not inferred

**Statement.** Where work is expected to progress without the caller waiting,
the system SHALL verify by measurement that progress actually occurs while the
caller is absent, per hosting model.

**Rationale.** On a runtime that suspends between invocations, the polling *is*
the compute: submit work, stay quiet, and it has not advanced when you return.
Asynchrony removes a ceiling without buying unattended progress, and the
difference is a property of the hosting model rather than of the protocol. It is
invisible to any test that polls continuously.

**Priority.** Must
**Verification.** Test — work is submitted, the caller stays silent for a defined
interval, and progress on return is recorded per platform.
**Traces to.** BR-206, BR-502

---

## NFR-2xx — Availability, resilience and degradation

### NFR-201 — Availability is evaluation-grade, and stated as such

**Statement.** The system SHALL target availability appropriate to an evaluation
environment, and this level SHALL be stated explicitly wherever the system's
readiness is described.

**Rationale.** Production availability is out of scope (X8). Stating the target
prevents two failures at once: operations planning for a level that was never
funded, and findings being read as evidence that a production-grade system was
demonstrated.

**Priority.** Must
**Verification.** Inspection — the target is documented and appears wherever
readiness is described.
**Traces to.** BR-503

---

### NFR-202 — One platform's unavailability does not disable unrelated routes

**Statement.** Failure or unavailability of any single remote platform SHALL NOT
prevent interactions that do not involve it.

**Rationale.** With five independently-operated platforms, something is
routinely unavailable. If any single one can halt the environment, the
evaluation cannot be scheduled and its results cannot be gathered
systematically.

**Priority.** Must
**Verification.** Test — a platform is made unreachable; unrelated routes
continue to complete.
**Traces to.** BR-104

---

### NFR-203 — Degradation is partial and declared, never silent

**Statement.** Under partial failure the system SHALL return the portion it
obtained, marked, rather than either failing entirely or presenting the portion
as complete.

**Rationale.** Total failure discards value that was successfully produced;
silent partial success is acted upon as though complete. The declared middle is
the only safe behaviour, and it is the one that requires deliberate work.

**Priority.** Must
**Verification.** Test — partial conditions produce marked partial results with
a non-success signal.
**Traces to.** BR-204

---

### NFR-204 — Credential expiry fails loudly

**Statement.** An expired, revoked or missing credential SHALL cause an explicit,
attributed failure at the point of use, and SHALL NOT cause a silent fallback to
a different identity or to unauthenticated operation.

**Rationale.** A fallback to a broader identity turns an authentication failure
into a least-privilege violation that works — the most dangerous outcome
available, because nothing reports a problem. Credential rotation across several
platforms is recurring, and lapses are routine rather than exceptional.

**Priority.** Must
**Verification.** Test — each credential is invalidated in turn; each produces an
attributed failure with no fallback.
**Traces to.** BR-306

---

### NFR-205 — The record survives component restart

**Statement.** Recorded interactions SHALL survive restart or failure of any
component, and a component failure SHALL NOT lose records already written.

**Rationale.** The record is the durable asset (BR-504) and the sole basis for
reproducing findings. A record lost to a restart is indistinguishable from an
interaction that never happened.

**Priority.** Must
**Verification.** Test — components are restarted under load and previously
written records are intact.
**Traces to.** BR-501, BR-305

---

## NFR-3xx — Capacity and rate limits

### NFR-301 — Concurrency supports the widest decomposition

**Statement.** The system SHALL sustain concurrent invocation of at least as many
legs as the widest decomposition it supports, without queuing that would erode
the concurrency advantage.

**Priority.** Must
**Verification.** Test — the widest supported decomposition executes with all
legs genuinely concurrent.
**Traces to.** BR-202

---

### NFR-302 — Record growth is bounded by an explicit retention policy

**Statement.** The interaction record SHALL have a defined retention period per
content class, and growth SHALL be bounded by it rather than by available
storage.

**Rationale.** The record contains complete payloads, which may contain personal
data. Unbounded retention is both a storage problem and a data-protection
problem, and the second is the serious one — it converts an evaluation artefact
into an accumulating liability.

**Priority.** Must
**Verification.** Inspection and test — retention is configured per class and
enforced.
**Traces to.** BR-301, BR-305

---

### NFR-303 — Platform rate limits are respected

**Statement.** The system SHALL respect each platform's published rate limits and
SHALL treat throttling as a distinct, reported condition rather than as a
failure.

**Rationale.** Throttling misclassified as failure produces false findings about
platform reliability. Concurrent decomposition makes throttling likelier
precisely during the interactions the evaluation cares most about.

**Priority.** Should
**Verification.** Test — an induced throttling response is classified and
reported distinctly.
**Traces to.** BR-501

---

## NFR-4xx — Operability and deployability

### NFR-401 — Behaviour is configured, not coded

**Statement.** Routes, protocols, targets, timeouts, delegation limits and
boundary data rules SHALL be expressed as configuration, changeable without
modifying source.

**Rationale.** These are the dimensions the evaluation varies. If varying them
requires a code change, the comparison matrix will be sparse because filling it
is expensive.

**Priority.** Must
**Verification.** Test — each dimension is varied by configuration alone.
**Traces to.** BR-103, BR-501

---

### NFR-402 — Missing configuration fails immediately and loudly

**Statement.** A required configuration value that is absent SHALL cause an
immediate, explicit failure at startup naming the missing value. The system
SHALL NOT substitute a default for any value identifying an environment,
account, endpoint or credential.

**Rationale.** A default for an environment-identifying value is not a
convenience — it is a hardcoded value that only reveals itself on someone else's
machine, at which point the system is confidently operating against the wrong
target. Failing at startup is recoverable; succeeding against the wrong
environment may not be.

**Priority.** Must
**Verification.** Test — each required value is removed in turn and produces an
immediate named failure. Inspection — no environment-identifying default exists
in source or configuration.
**Traces to.** BR-306

---

### NFR-403 — A build step owns shipping what it produces

**Statement.** Any automated step producing a deployable artefact or a schema
change SHALL also be responsible for deploying it, or SHALL explicitly report
that the artefact is built and not deployed.

**Rationale.** A build that reports success while its output never reaches the
running system produces the worst available failure: local verification passes,
the deployed system runs older code, and nothing indicates a discrepancy. The
same applies to schema changes whose only caller lacks the privilege to apply
them. Building is not deploying, and a step that conflates them will be believed.

**Priority.** Must
**Verification.** Test — for each artefact, the deployed version is verified to
match the built one; a build-without-deploy is reported rather than silent.
**Traces to.** BR-501

---

### NFR-404 — The deployment target is proven before anything is created

**Statement.** Every automated deployment SHALL verify it is operating against
the intended target environment before creating or modifying any resource, and
SHALL refuse otherwise.

**Rationale.** Removing environment identifiers from source (NFR-402) makes a
wrong-target deployment *easier* to attempt, not harder — so the guard must
accompany that rule rather than follow it. A refusal costs a minute; a
wrong-environment deployment can cost far more and may not be noticed.

**Priority.** Must
**Verification.** Test — a deployment attempted against an unintended target is
refused before any resource is created.
**Traces to.** BR-306

---

### NFR-405 — The environment is reproducible from a clean checkout

**Statement.** A newly-provisioned workstation SHALL be able to reach a running
system by a documented sequence requiring one human authentication, with all
other credentials retrieved from the secret store.

**Rationale.** An environment only one person can stand up has a single point of
failure that is a person. It is also the practical form of the one-human-login
principle: everything else is a service identity, retrieved rather than known.

**Priority.** Must
**Verification.** Demonstration — a clean environment reaches a running system by
the documented sequence.
**Traces to.** BR-306

---

### NFR-406 — Operational procedures are documented and current

**Statement.** Starting, stopping, deploying, rotating credentials, and
diagnosing each failure class SHALL be documented, and the documentation SHALL be
verified by execution rather than by review.

**Rationale.** Procedures verified only by reading are wrong at the first step
that changed. Execution is the only review that finds it.

**Priority.** Should
**Verification.** Demonstration — each procedure is executed from its
documentation by someone who did not write it.
**Traces to.** BR-501

---

## NFR-5xx — Evolvability

### NFR-501 — Adding a platform is confined to that platform

**Statement.** Onboarding a platform SHALL require changes only within that
platform's own integration unit and its configuration entries, with no
modification to shared components or to other platforms' units.

**Rationale.** This is the structural expression of additive participation
(BR-104) and the mechanism behind declining onboarding cost. If shared
components change per platform, cost grows rather than falls and every existing
platform is exposed to regression.

**Priority.** Must
**Verification.** Inspection — the change set for the most recent onboarding
touches only that platform's unit and configuration.
**Traces to.** BR-104

---

### NFR-502 — Adding a protocol does not touch agents

**Statement.** Supporting an additional protocol SHALL require no change to any
hosted agent implementation.

**Priority.** Must
**Verification.** Inspection — the change set for an added protocol contains no
agent implementation changes.
**Traces to.** BR-103, BR-104

---

### NFR-503 — No environment identifier appears in source

**Statement.** No account identifier, project identifier, subscription or tenant
name, organisation identifier, or credential-store profile name SHALL appear in
source, configuration, comments, or deployable artefacts. Enforcement SHALL be
automated.

**Rationale.** Identifiers scattered through source cannot be re-pointed, cannot
be published, and are individually invisible in review. Automation is required
because this is exactly the rule that erodes under delivery pressure — and one
reintroduction undoes it.

**Priority.** Must
**Verification.** Test — an automated check fails when an identifier is
introduced.
**Traces to.** BR-306

---

### NFR-504 — Findings outlive the system that produced them

**Statement.** The findings record SHALL be readable and interpretable without
the system running, and SHALL NOT depend on a component of the system for its
meaning.

**Rationale.** The environment is decommissioned or promoted on a decision
(BR-504). Findings that require the environment to interpret are lost exactly
when the decision they informed is reviewed.

**Priority.** Must
**Verification.** Demonstration — the findings record is read and interpreted
with the system stopped.
**Traces to.** BR-504

---

## NFR-6xx — Usability of the evaluation surface

### NFR-601 — Recorded payloads are presented as exchanged

**Statement.** The evaluation surface SHALL present recorded payloads as they
were transmitted, without reformatting that alters content, and SHALL indicate
where content was redacted.

**Rationale.** The wire record is the exhibit. A reconstructed or prettified
payload cannot settle a dispute about what was actually sent, which is the
question it exists to answer. Redaction must be visible so absence is not
mistaken for a platform omission.

**Priority.** Must
**Verification.** Inspection — displayed payloads match recorded bytes, with
redactions marked.
**Traces to.** BR-501

---

### NFR-602 — Honesty labels appear at the point of display

**Statement.** Native-versus-mediated designation and evidence classification
SHALL be visible wherever a capability or finding is displayed, not only in its
detail view.

**Rationale.** A label reachable only by drilling in is a label most readers
never see, and the summary view is where conclusions are formed. This is the
requirement that keeps BR-502 and BR-503 effective in practice rather than
nominally satisfied.

**Priority.** Must
**Verification.** Inspection — every summary presentation carries the labels.
**Traces to.** BR-502, BR-503

---

### NFR-603 — Failures are legible without reading raw payloads

**Statement.** A failed interaction SHALL present its failure classification and
the failing hop directly, with raw payloads available but not required for a
first diagnosis.

**Priority.** Should
**Verification.** Demonstration — an operator identifies the failing hop and
class without opening payloads.
**Traces to.** BR-501

---

## NFR-7xx — Qualities of the record

### NFR-701 — The record is append-only for interactions

**Statement.** Recorded interactions SHALL NOT be modifiable after write.
Corrections SHALL be additional records referencing the original.

**Rationale.** A record that can be edited cannot support audit (BR-305), and a
finding derived from an editable record cannot withstand challenge.

**Priority.** Must
**Verification.** Test — modification of a written record is rejected.
**Traces to.** BR-305, BR-501

---

### NFR-702 — Records carry sufficient context to be interpreted independently

**Statement.** Each recorded interaction SHALL carry its correlation identifier,
participants, protocol, route, timing, status classification, warm or cold state
and evidence-relevant conditions.

**Rationale.** A record interpretable only with knowledge of the run that
produced it fails reproduction by a third party (FR-704), which is the operative
definition of evidence-grade.

**Priority.** Must
**Verification.** Test — a record is interpreted by someone with no knowledge of
the run.
**Traces to.** BR-501, BR-305

---

### NFR-703 — Credentials never enter the record

**Statement.** Authentication material SHALL be removed from payloads before
they are written, and the removal SHALL occur at the point of writing rather
than at the point of display.

**Rationale.** Redaction on display leaves the credential in storage, where it
outlives the display layer and travels with any export. Payload capture is
comprehensive by design, which makes the write path the only safe place for the
control.

**Priority.** Must
**Verification.** Test — payloads containing credential-shaped content are
written with the credentials absent from storage.
**Traces to.** BR-306
