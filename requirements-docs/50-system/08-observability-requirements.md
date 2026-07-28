# Observability and Operations Requirements

Observability here is not an operational convenience — it is the **product**.
The evaluation's entire output is findings, findings are only as good as the
record behind them, and the record is what survives the environment's
decommissioning.

Two distinct views must be captured and joined:

- **The system's own view** — what crossed the wire at each hop, which the
  system controls completely.
- **Each platform's interior view** — its own execution record of the same work,
  which the system does not control and may not be able to obtain at all.

The relationship between them is itself a measurement, and one of the more
valuable ones.

## Block allocation

| Block | Theme |
|---|---|
| `OR-1xx` | The interaction record |
| `OR-2xx` | Correlation and reconstruction |
| `OR-3xx` | Platform execution records and the join |
| `OR-4xx` | Consumption accounting |
| `OR-5xx` | Operational monitoring |
| `OR-6xx` | Honest reporting of observability itself |

---

## OR-1xx — The interaction record

### OR-101 — Every hop is recorded with its actual payloads

**Statement.** Every inter-agent hop SHALL append a record containing the actual
request and response payloads as transmitted, together with participants,
protocol, timing and status.

**Rationale.** The wire record is the exhibit. A protocol comparison that cannot
show what was exchanged is an opinion, and the characteristic cross-platform
failure — two ends each appearing correct — is diagnosable only from content.

**Priority.** Must
**Verification.** Test — recorded payloads match transmitted bytes for every
protocol and every hop type.
**Traces to.** BR-501, BR-305

---

### OR-102 — Capture works where payloads are held inside framework internals

**Statement.** Payload capture SHALL succeed for protocols whose transport
envelopes are constructed and consumed inside a protocol library, where
application-level handlers never observe the raw bytes.

**Rationale.** For at least one protocol class, handlers receive parsed objects
and never see the envelope. A capture design assuming handler-level access
records a reconstruction rather than the wire — and a reconstruction cannot
settle a dispute about what was actually sent, which is the record's whole
purpose.

**Priority.** Must
**Verification.** Test — captured payloads for a library-mediated protocol are
byte-identical to what traversed the network.
**Traces to.** BR-501

---

### OR-103 — Capture is complete or the gap is reported

**Statement.** Where a hop's payload cannot be captured, the record SHALL contain
an explicit gap marker naming the reason. Silent omission SHALL NOT occur.

**Rationale.** A missing hop makes the whole record untrustworthy rather than
merely incomplete, because a reader cannot distinguish "not captured" from "did
not happen" — and those support opposite conclusions.

**Priority.** Must
**Verification.** Test — an induced capture failure produces an explicit marker.
**Traces to.** BR-501

---

### OR-104 — Recording is on the enforced path

**Statement.** It SHALL NOT be possible to add an interaction path that produces
no record.

**Rationale.** An observability layer covering most paths provides no guarantee,
and the uncovered path is typically the one added last — by someone unaware the
requirement exists. This is the same structural argument as TR-105.

**Priority.** Must
**Verification.** Analysis and test — paths are enumerated and shown to record; a
constructed bypass fails.
**Traces to.** BR-501, BR-305

---

## OR-2xx — Correlation and reconstruction

### OR-201 — An interaction is reconstructable end to end from the record alone

**Statement.** Any completed interaction SHALL be reconstructable in order, with
participants, payloads, timing and outcome, from the retained record without
recourse to any platform.

**Rationale.** Reconstruction from participating platforms' own records is not
achievable — several will be unable to demonstrate they took part. The
seam-side record is therefore the only place reconstruction can be guaranteed,
and it is the basis of both audit (BR-305) and reproducibility (BR-501).

**Priority.** Must
**Verification.** Demonstration — a multi-platform interaction is reconstructed
by someone who did not run it.
**Traces to.** BR-305, BR-501

---

### OR-202 — Platform-native execution identifiers are captured at the time of the hop

**Statement.** Where a platform returns an identifier for its own execution of a
hop, the system SHALL record it against that hop at the time it occurs.

**Rationale.** This identifier is the join key to the platform's interior view.
It is frequently available only in the immediate response and is not
reconstructable later. Capturing it costs nothing at the time and is impossible
afterwards.

**Priority.** Must
**Verification.** Test — recorded hops carry the platform's execution identifier
wherever one is returned.
**Traces to.** BR-305

---

### OR-203 — Platform-initiated legs are correlated into the interaction

**Statement.** Where a platform initiates a call as part of an interaction rather
than being called by the system, that leg SHALL be correlated into the same
interaction record.

**Rationale.** Some of the most interesting behaviour is platform-initiated — an
agent deciding mid-answer to consult another. If those legs are recorded
separately, the interaction appears to have fewer hops than it had, and the
comparison undercounts exactly the delegation the evaluation exists to study.

**Priority.** Must
**Verification.** Test — a platform-initiated leg appears within its parent
interaction.
**Traces to.** BR-305, BR-501

---

## OR-3xx — Platform execution records and the join

### OR-301 — Platform execution records are retrieved where available

**Statement.** For each platform, the system SHALL retrieve that platform's own
execution records for work the system drove, and store them alongside the wire
record.

**Rationale.** The platform's interior view — reasoning steps, tool calls, token
consumption — is not visible on the wire. Without it, consumption cannot be
attributed and a platform's own account of a failure cannot be compared with
what was observed externally.

**Priority.** Must
**Verification.** Test — records are retrieved per platform and associated with
the interactions that produced them.
**Traces to.** BR-401, BR-305

---

### OR-302 — Retrieval is deterministic extraction, separate from interpretation

**Statement.** Retrieval of platform records SHALL be deterministic extraction
with no interpretive step. Any analysis SHALL operate on the stored result as a
separate layer.

**Rationale.** Interpretation folded into extraction cannot be re-run, corrected
or audited, and its output cannot be distinguished from the source data
afterwards. Separation keeps the evidential base intact regardless of how the
analysis above it changes — and analysis will change.

**Priority.** Must
**Verification.** Inspection and test — extraction is re-runnable and produces
identical output; analysis operates only on stored data.
**Traces to.** BR-501

---

### OR-303 — Join rate is measured and reported as a first-class metric

**Statement.** For interactions involving more than one platform, the system
SHALL measure and report the proportion of participating platforms that can be
tied back to the interaction **from their own execution records**, and SHALL do
so at each topology width it supports.

**Rationale.** This is the metric that reveals whether cross-platform
observability actually holds, and it is the finding a one-to-one interaction
cannot produce — two participants are easy to correlate by hand, which hides the
problem entirely. It degrades as topology widens and it degrades *silently*:
every platform returns success and a good answer while losing the ability to say
it took part.

**Priority.** Must
**Verification.** Test — join rate is computed for completed interactions at two,
three and four participants.
**Traces to.** BR-305, BR-404, BR-503

---

### OR-304 — Join failures are classified as structural or fixable

**Statement.** Where a platform cannot be joined, the cause SHALL be classified
as **structural** — no per-execution record exists to join to — or **fixable** —
a record exists and the identifier was not captured or not requested.

**Rationale.** The distinction determines whether anything can be done, and it
is the difference between a platform limitation and a defect in this system.
Reporting an aggregate join rate without it invites both wrong conclusions:
blaming a platform for an omission, or accepting a gap that could have been
closed.

**Priority.** Must
**Verification.** Inspection — each join failure carries a classification
supported by evidence.
**Traces to.** BR-502, BR-503

---

### OR-305 — Observability coverage is reported per platform, including absences

**Statement.** The system SHALL report, per platform, which observability
capabilities are available — execution records, reasoning visibility, tool-call
visibility, consumption reporting, per-execution identifiers — and which are
not.

**Rationale.** What a platform does *not* expose is a finding of equal standing
to what it does, and it bears directly on build-versus-buy. A coverage view
listing only what works reads as complete coverage.

**Priority.** Must
**Verification.** Inspection — the coverage report includes explicit absences.
**Traces to.** BR-502, BR-503, BR-404

---

## OR-4xx — Consumption accounting

### OR-401 — Consumption is recorded in separately-billed categories

**Statement.** Consumption SHALL be recorded and reported in each category the
provider meters and prices separately, and SHALL NOT be presented as a single
aggregate.

**Rationale.** The categories are priced at materially different rates, so they
cannot be summed and multiplied by one rate. Aggregation is an error, not a
simplification — and a uniquely dangerous one, because it produces figures wrong
by more than an order of magnitude that raise no error and look entirely
plausible. In particular, the category commonly labelled as input is frequently
a *remainder* after other categories are counted, so treating it as the total
input understates consumption dramatically.

**Priority.** Must
**Verification.** Test — reports present each billed category separately, and a
report is reconciled against a provider's own figures.
**Traces to.** BR-401

---

### OR-402 — The reason categories are separate travels with the data

**Statement.** Reporting SHALL make the distinction between categories evident at
the point of presentation, not only in accompanying documentation.

**Rationale.** A future reader — or a future maintainer adding a chart — will
re-aggregate the categories unless the reason not to is visible where the numbers
are. The error is easy to reintroduce and hard to notice once made.

**Priority.** Should
**Verification.** Inspection — presentation conveys the distinction.
**Traces to.** BR-401

---

### OR-403 — Consumption is attributable per interaction

**Statement.** Consumption SHALL be attributable to the interaction that caused
it, and where the platform's records permit, to the individual hop.

**Rationale.** Cost per unit of business work (BR-402) is unobtainable without
per-interaction attribution, and it is the only comparable figure across
platforms.

**Priority.** Must
**Verification.** Test — consumption for a known interaction is retrieved and
matches the platform's record within a stated tolerance.
**Traces to.** BR-402

---

### OR-404 — Attribution beyond what platforms supply is configured deliberately

**Statement.** Where attribution dimensions the organisation needs are not
emitted by a platform, the system SHALL supply them by configuration, and SHALL
record that these values are unvalidated.

**Rationale.** Platforms emit what they choose. Dimensions such as which project
or workload consumed the budget are typically absent and must be added — and
because such values are free text, a misconfigured or defaulted value silently
creates a phantom workload. Recording their unvalidated nature is what allows
the read side to correct for it.

**Priority.** Should
**Verification.** Test — configured attribution appears in reporting and is
marked unvalidated.
**Traces to.** BR-402

---

### OR-405 — Corrections are applied on read, not by rewriting history

**Statement.** Where recorded consumption or attribution is later found wrong,
the correction SHALL be applied when reading rather than by altering stored
records.

**Rationale.** Metric stores frequently do not permit deletion or amendment, so
read-side correction is often the only option available. It is also the better
one: the original remains auditable, and the correction is visible as a
correction rather than silently replacing history.

**Priority.** Must
**Verification.** Test — a known bad value is corrected on read while the stored
record is unchanged.
**Traces to.** BR-401, BR-501

---

### OR-406 — Cost figures are labelled as modelled

**Statement.** Any monetary figure the system presents SHALL be labelled as
modelled at published rates, and SHALL NOT be presented as billed cost.

**Rationale.** These are client-side estimates from consumption counts and
published rates. They exclude negotiated terms, commitments, credits and taxes.
Presented unlabelled they will be compared against an invoice and will not match,
which discredits the whole measurement.

**Priority.** Must
**Verification.** Inspection — every monetary figure carries the label.
**Traces to.** BR-401, BR-403

---

## OR-5xx — Operational monitoring

### OR-501 — Interaction outcomes are monitorable in aggregate

**Statement.** The system SHALL expose aggregate outcome counts by
classification — success, each failure class, refusal, partial — over time and
per route.

**Rationale.** Individual records diagnose an incident; aggregates reveal that
one is occurring. Partial completions are the case that matters most, since each
individual one looks like a success.

**Priority.** Should
**Verification.** Demonstration — aggregate outcomes are retrieved by route and
period.
**Traces to.** BR-204

---

### OR-502 — Silent degradation is detectable

**Statement.** The system SHALL be able to detect and report a rise in
structurally-successful interactions whose expected content is absent.

**Rationale.** This is the failure that alerting on errors will never catch,
because there are no errors. It is also the failure this estate actually
exhibits, so it must be a monitored condition rather than something noticed by
chance.

**Priority.** Must
**Verification.** Test — an induced rise in empty-content successes is detected.
**Traces to.** BR-204, BR-501

---

### OR-503 — Cold-start behaviour is distinguishable from failure

**Statement.** Latency reporting SHALL distinguish first-invocation latency on
scale-to-zero components from degraded performance.

**Rationale.** Without it, the first interaction of any quiet period presents as
an incident, and genuine degradation is dismissed as a cold start. Both errors
cost operational credibility.

**Priority.** Should
**Verification.** Test — cold and warm invocations are reported distinguishably.
**Traces to.** BR-205

---

## OR-6xx — Honest reporting of observability itself

### OR-601 — Declared but unexercised capability is marked as such

**Statement.** Where a component or platform advertises a capability the system
has not exercised, that capability SHALL be reported as declared-but-unexercised
rather than as supported.

**Rationale.** An advertised capability is the case most likely to be mistaken
for a proven one — it appears in a description, it is technically true, and
nothing indicates it was never used. A capability advertised in an agent
description and never invoked is exactly this, and it will be read as
demonstrated.

**Priority.** Must
**Verification.** Inspection — each claimed capability carries an exercised or
declared marking.
**Traces to.** BR-503, BR-502

---

### OR-602 — Observability gaps are reported as findings

**Statement.** Gaps in observability — platforms that cannot be joined,
capabilities not exposed, consumption not attributable — SHALL be recorded as
findings and published alongside successful measurements.

**Rationale.** These gaps are among the most valuable outputs, because they
determine what an organisation would have to build itself and therefore feed
build-versus-buy directly. Treated as project shortcomings rather than findings,
they are quietly omitted.

**Priority.** Must
**Verification.** Inspection — the findings record contains observability gaps.
**Traces to.** BR-503, BR-404

---

### OR-603 — The system's own construction is not reported as a platform

**Statement.** Where the system measures the tooling used to build it, that
measurement SHALL be reported separately from the agent platforms it evaluates.

**Rationale.** Build-time tooling is not an agent platform under evaluation.
Presenting it as another column in a platform comparison misrepresents the
comparison's subject, and invites conclusions about platforms from data about
development tooling.

**Priority.** Should
**Verification.** Inspection — build telemetry is presented separately from
platform evaluation.
**Traces to.** BR-502
