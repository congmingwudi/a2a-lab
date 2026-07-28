# Functional Requirements

What the system must **do**. Qualities it must have are in
`03-nonfunctional-requirements.md`; structural constraints in
`04-technical-architecture-requirements.md`; protocol specifics in
`05-interoperability-requirements.md`; and the cross-cutting privacy, security
and observability obligations in documents 06 through 08.

Requirements here state capability and leave mechanism to the builder wherever
mechanism is genuinely free. Where a constraint is real — an external protocol,
a regulation, an observed platform behaviour — it is stated as a constraint and
its source named.

## Block allocation

| Block | Theme |
|---|---|
| `FR-1xx` | Hosting agents and exposing them (inbound seam) |
| `FR-2xx` | Invoking remote agents (outbound seam) |
| `FR-3xx` | Mediation for platforms that cannot participate directly |
| `FR-4xx` | Delegation control |
| `FR-5xx` | Orchestration and fan-out |
| `FR-6xx` | Scenario execution and the evaluation surface |
| `FR-7xx` | Findings and reporting |

---

## FR-1xx — Hosting agents and exposing them

### FR-101 — Single implementation contract for hosted agents

**Statement.** The system SHALL define one implementation contract for agents it
hosts, such that an agent satisfies it once and becomes usable through every
supported protocol.

**Rationale.** If an agent must be implemented per protocol, then every
protocol comparison is contaminated by implementation differences and the
results are not attributable to the protocol. A single contract is what makes
the comparison valid.

**Priority.** Must
**Verification.** Test — one agent implementation is served over every supported
protocol and returns semantically equivalent answers to an identical request.
**Traces to.** BR-103

---

### FR-102 — Protocol exposure without agent modification

**Statement.** Exposing a hosted agent over an additional protocol SHALL require
no modification to that agent's implementation.

**Rationale.** Adding a protocol is an operational act, not a development act.
This is also what allows a division to be reached over a new protocol without
that division changing anything.

**Priority.** Must
**Verification.** Test — an agent is exposed over an additional protocol with no
change to its implementation artefacts.
**Traces to.** BR-103, BR-104

---

### FR-103 — Concurrent exposure over multiple protocols

**Statement.** The system SHALL be able to expose the same hosted agent over
several protocols simultaneously.

**Rationale.** Protocols must be compared against the same agent in the same
state. Sequential exposure would require reconfiguration between measurements
and introduce a confound.

**Priority.** Must
**Verification.** Demonstration — the same agent answers concurrently over every
supported protocol.
**Traces to.** BR-103, BR-501

---

### FR-104 — Discovery metadata where the protocol requires it

**Statement.** Where a protocol defines a discovery mechanism, the system SHALL
publish the required description for each exposed agent, generated from that
agent's actual configuration rather than maintained separately.

**Rationale.** A hand-maintained description drifts from what the agent
actually is, and a discovery document that misdescribes its agent is worse than
none — callers make binding decisions on it. Generation from live configuration
is what keeps them consistent.

**Priority.** Must
**Verification.** Test — the published description is retrieved and matches the
agent's configured identity and capabilities; altering configuration alters the
description.
**Traces to.** BR-102

---

### FR-105 — Liveness and readiness surface

**Statement.** Every component the system operates SHALL expose an unauthenticated
liveness indication and a readiness indication that reflects its ability to
serve.

**Rationale.** Operations cannot distinguish a failed component from a slow one
without it, and on scale-to-zero hosting the first request of a period is
routinely mistaken for an outage.

**Priority.** Must
**Verification.** Test — each operated component reports liveness and readiness,
and readiness reflects an induced dependency failure.
**Traces to.** BR-501

---

## FR-2xx — Invoking remote agents

### FR-201 — Uniform invocation contract across protocols

**Statement.** The system SHALL provide one invocation contract for calling
remote agents, with a distinct implementation per protocol, such that calling
code selects a target rather than a protocol.

**Rationale.** The symmetric counterpart to FR-101. It is also what makes
FR-203 possible: if calling code names a protocol, changing the protocol is a
code change.

**Priority.** Must
**Verification.** Test — the same calling code reaches targets on different
protocols with no protocol-specific branching.
**Traces to.** BR-102, BR-103

---

### FR-202 — Targets resolved by logical name from configuration

**Statement.** Remote agents SHALL be addressed by a logical target name
resolved from configuration that declares the protocol, address, authentication
and timeout for that target.

**Rationale.** Logical naming is what allows a route to be re-pointed,
re-protocoled or disabled without touching either participant. It is also the
enforcement point for per-route policy.

**Priority.** Must
**Verification.** Inspection and test — a target's protocol is changed in
configuration and the next invocation uses the new protocol.
**Traces to.** BR-102, BR-103

---

### FR-203 — Protocol substitution without redeployment

**Statement.** Changing the protocol used for a route SHALL require no
redeployment of software owned by either participating division.

**Rationale.** The evaluation must run the same scenario over each protocol
repeatedly. If each switch requires a deployment by a division, the comparison
will not be run enough times to be credible — and in production it would make
protocol choice effectively permanent.

**Priority.** Must
**Verification.** Test — a scenario is executed over each supported protocol
with only configuration differing, and no participant redeploys.
**Traces to.** BR-103, BR-501

---

### FR-204 — Per-target credentials

**Statement.** Each target SHALL carry its own authentication material, resolved
at invocation time from a secret store rather than embedded in configuration or
source.

**Rationale.** Per-target credentials are what make per-caller identity (BR-306)
achievable and revocation survivable. Resolution at invocation time is what
allows rotation without redeployment.

**Priority.** Must
**Verification.** Inspection and test — no credential value appears in
configuration or source; a rotated credential takes effect without redeployment.
**Traces to.** BR-306

---

### FR-205 — Per-target timeout

**Statement.** Each target SHALL carry its own timeout, and an invocation
exceeding it SHALL fail in a way distinguishable from a remote error.

**Rationale.** Platforms differ by an order of magnitude in response time. A
single global timeout is either too short for the slowest or too long to protect
the caller's own budget. Distinguishability matters because "we gave up" and
"they failed" have different remedies.

**Priority.** Must
**Verification.** Test — an induced slow response produces a timeout outcome
distinguishable from an induced remote error.
**Traces to.** BR-205

---

### FR-206 — Failure classification

**Statement.** Invocation failures SHALL be classified and reported as at least:
unreachable, rejected-by-authentication, protocol-level error, agent-level
failure, and timeout.

**Rationale.** These have different owners and different remedies, and the
underlying protocols conflate them — notably, a task-lifecycle protocol reports
agent failure as a *successful* transport exchange containing a failed task. A
system that reports only success or failure forces every diagnosis to start from
raw payloads.

**Priority.** Must
**Verification.** Test — each failure class is induced and reported distinctly,
including an agent-level failure returned over a successful transport exchange.
**Traces to.** BR-204, BR-501

---

### FR-207 — Asynchronous invocation where supported

**Statement.** Where a protocol and remote platform support submitting work and
retrieving the result later, the system SHALL support that mode, and SHALL
determine and record per target whether it is genuinely available.

**Rationale.** Long-running cross-divisional work cannot complete within
synchronous limits (BR-206). Support is uneven across platforms and cannot be
inferred from protocol conformance — a platform may accept a submission and
return an identifier that no subsequent request shape can read back. Only
measurement distinguishes real support from apparent support.

**Priority.** Must
**Verification.** Test — for each target, submission and later retrieval are
attempted and the outcome recorded as supported, unsupported, or accepted-but-
unretrievable.
**Traces to.** BR-206, BR-502

---

### FR-208 — Delivery of results into an existing business system

**Statement.** The system SHALL be able to deliver a completed result into a
system of work outside the evaluation environment, carrying its attribution and
its correlation identifier, and SHALL retain and retry the delivery if the
destination is unavailable.

**Rationale.** A result that exists only inside an evaluation surface proves the
plumbing but not the value — the business owner who would judge whether the
answer was worth producing never sees it, and asking them to visit an evaluation
tool means they will not. Retention on failure matters because the destination's
availability is not controlled by this system, and a result discarded because its
destination was briefly down represents work already paid for.

**Priority.** Should
**Verification.** Test — a completed result is delivered into an external system
with attribution and correlation intact; an induced destination failure results
in retry rather than loss.
**Traces to.** BR-207, BR-203

---

## FR-3xx — Mediation

### FR-301 — Outbound mediation for constrained platforms

**Statement.** For a platform whose only outbound capability is a single fixed
call shape, the system SHALL accept that call shape and re-issue the request to
any configured target over any supported protocol.

**Rationale.** Some platforms can originate exactly one kind of outbound call
and cannot be extended. Without mediation such a platform can participate in one
protocol only — and the division holding the customer system of record is one of
them, which would remove most cross-divisional value.

**Priority.** Must
**Verification.** Test — a fixed-shape inbound request is re-issued over each
supported protocol by configuration change alone.
**Traces to.** BR-201, BR-103

---

### FR-302 — Inbound mediation for closed platforms

**Statement.** For a platform exposing no inbound protocol surface of its own,
the system SHALL present a conformant protocol endpoint on that platform's
behalf and satisfy each request through the platform's own interface.

**Rationale.** The closed platform holds the data every other division needs. It
can be called through its vendor interface but cannot be addressed as an agent.
Mediation is what allows it to participate as a peer.

**Priority.** Must
**Verification.** Test — a standard protocol client, with no knowledge of the
platform, completes an exchange and receives the platform's answer.
**Traces to.** BR-201, BR-102

---

### FR-303 — Mediated capability is labelled

**Statement.** Wherever a capability reached through mediation is reported,
listed, or presented, it SHALL be identified as mediated rather than native to
the platform.

**Rationale.** The distinction determines whether a result generalises beyond
this system. Reporting mediated capability as native makes the estate appear
more interoperable than it is, and the error surfaces only during a production
migration that assumed otherwise.

**Priority.** Must
**Verification.** Inspection — every reported capability carries a native or
mediated designation, verified against what the system actually does rather than
against a configuration label.
**Traces to.** BR-502

---

### FR-304 — Protocol generation translation

**Statement.** Where two participants support different generations of the same
protocol and neither negotiates, the system SHALL translate between them in both
directions, and SHALL present descriptions acceptable to each generation.

**Rationale.** This is the normal condition of a multi-vendor estate, not an
edge case. Generations differ in method naming and message structure, and a
description valid for one may be rejected as malformed by the other for lacking
fields it does not define. Neither side will adapt.

**Priority.** Must
**Verification.** Test — participants at each supported generation complete an
exchange through the system with no change on either side.
**Traces to.** BR-102, BR-104

---

### FR-305 — Mediation preserves correlation and delegation context

**Statement.** Mediation SHALL preserve correlation identifiers and delegation
context across the translation, and SHALL NOT introduce a break in the recorded
chain.

**Rationale.** A mediation point is where correlation is most likely to be lost,
because the request is reconstructed rather than forwarded. A break there is
invisible — both halves look complete — and it defeats reconstruction (BR-305)
exactly where the estate's most important participant sits.

**Priority.** Must
**Verification.** Test — an interaction traversing mediation is reconstructed
end to end from the record as a single correlated chain.
**Traces to.** BR-305, BR-307

---

## FR-4xx — Delegation control

### FR-401 — Delegation context on every delegated request

**Statement.** Every request the system issues on behalf of another agent SHALL
carry the calling agent's identity, its platform, the delegation depth, and the
correlation identifier.

**Rationale.** Depth cannot be enforced without being carried, and a receiving
division cannot authorise a request (BR-105) without knowing who is really
asking. The originator is not recoverable from transport metadata once more than
one hop has occurred.

**Priority.** Must
**Verification.** Test — a delegated request is received with all four elements
present and correct at each depth.
**Traces to.** BR-307, BR-105, BR-305

---

### FR-402 — Delegation context survives platforms that discard metadata

**Statement.** Delegation context SHALL be conveyed by a channel that survives
every platform in the estate, determined by measurement rather than assumed from
protocol capability.

**Rationale.** Structured metadata fields are dropped by at least one platform
on every available path — each platform preserves its own and discards others'.
A control that depends on a channel one participant silently strips is not a
control. Which channels survive is an empirical question per estate.

**Priority.** Must
**Verification.** Test — delegation context is recovered intact after a hop
through every platform in the estate, including any that discard structured
metadata.
**Traces to.** BR-307, BR-305

---

### FR-403 — Depth limit enforcement

**Statement.** The system SHALL refuse to issue a delegated request at or beyond
a configured maximum depth, and the refusal SHALL be enforced by the system
rather than by the requesting agent.

**Rationale.** No protocol in scope defines time-to-live or maximum-forwards
semantics, so nothing in the protocol layer will stop unbounded delegation.
Enforcement inside agents fails because it depends on every agent behaving
correctly, and each individual hop looks correct.

**Priority.** Must
**Verification.** Test — a delegation chain is refused at the configured depth,
and the limit is changed by configuration without code modification.
**Traces to.** BR-307

---

### FR-404 — Refusals are explicit and recorded

**Statement.** A refused delegation SHALL return an explicit refusal identifying
the reason, and SHALL be recorded as a refusal rather than as an error.

**Rationale.** A silent refusal is indistinguishable from a failure and will be
retried. Recording refusals separately is also what makes it possible to tell
whether the limit is set correctly — a limit that never fires and one that fires
constantly are both wrong, and only the record distinguishes them.

**Priority.** Must
**Verification.** Test — a refused delegation returns an identifiable refusal
and appears in the record classified as such.
**Traces to.** BR-307, BR-305

---

### FR-405 — Single enforcement point

**Statement.** Every path by which the system can issue a request on another
agent's behalf SHALL pass through the delegation control, and it SHALL NOT be
possible to add such a path that bypasses it.

**Rationale.** A control enforced at four of five seams provides no guarantee,
and the fifth seam is typically added later by someone unaware the control
exists. This is a structural requirement rather than a procedural one precisely
because procedure does not survive staff turnover.

**Priority.** Must
**Verification.** Analysis and test — every delegation path is enumerated and
shown to traverse the control; a path constructed to bypass it fails.
**Traces to.** BR-307

---

## FR-5xx — Orchestration and fan-out

### FR-501 — Concurrent decomposition

**Statement.** The system SHALL dispatch the independent sub-requests of a
decomposed task concurrently and recombine the results into one response.

**Rationale.** Sequential consultation of several divisions exceeds any
interactive window. Concurrency is what makes the multi-division shape viable at
all.

**Priority.** Must
**Verification.** Test — a multi-division task completes in elapsed time
approximating its slowest leg rather than the sum of its legs.
**Traces to.** BR-202, BR-205

---

### FR-502 — Independent per-leg timeouts

**Statement.** Each leg of a decomposed task SHALL carry its own timeout, and a
leg exceeding it SHALL NOT prevent other legs from completing or the result from
being returned.

**Rationale.** Legs run on platforms with very different response
characteristics. Without independence, the slowest participant sets the
behaviour of the whole interaction and one unavailable division makes the
capability unusable.

**Priority.** Must
**Verification.** Test — one leg is induced to exceed its timeout; remaining legs
complete and a result is returned.
**Traces to.** BR-204, BR-205

---

### FR-503 — Missing contributions are present and marked

**Statement.** Every expected contribution to a composed result SHALL appear in
that result, and a contribution that did not arrive SHALL be present as an
explicit marker naming what is missing and why.

**Rationale.** Omitting a failed contribution produces a well-formed, plausible,
incomplete answer — the failure mode most likely to be acted upon. A marker
converts a silent omission into a visible one. This is deliberately stronger
than logging the failure: the reader of the *answer* must see it, not the reader
of the logs.

**Priority.** Must
**Verification.** Test — an induced leg failure produces a result containing an
explicit marker for the missing contribution.
**Traces to.** BR-204, BR-203

---

### FR-504 — Coverage statement

**Statement.** Every composed result SHALL carry a statement of how many
expected contributions were received.

**Rationale.** A reader should not have to audit sections to discover
completeness. A single explicit statement is checkable by a human at a glance
and by a machine without parsing the content.

**Priority.** Must
**Verification.** Test — composed results carry an accurate coverage statement
under complete and partial conditions.
**Traces to.** BR-204

---

### FR-505 — Partial completion does not report success

**Statement.** An interaction that produced fewer than all expected contributions
SHALL NOT report unqualified success to its caller or to any automated consumer.

**Rationale.** A scheduled or automated consumer will otherwise treat a partial
result as complete and act on it. This is the same defect as an interactive
platform returning a success status with its content silently absent, and it is
harder to notice because no human sees it.

**Priority.** Must
**Verification.** Test — a partial run yields a non-success completion signal
observable by an automated consumer.
**Traces to.** BR-204

---

### FR-506 — Both orchestrator placements are supported

**Statement.** The system SHALL support decomposition both where the system
executes the concurrency itself and where it is declared to a remote platform's
own orchestration mechanism, and SHALL make the two comparable on the same task.

**Rationale.** The placement determines which guarantees are available.
System-executed orchestration allows rules to be enforced in code that a model
cannot be talked out of, but cannot run unattended. Platform-declared
orchestration runs unattended and guarantees ordering, but failure markers
survive only if several independent models relay them faithfully. Neither offers
both, and the trade is a finding the evaluation exists to produce (BR-404).

**Priority.** Should
**Verification.** Demonstration — the same task is executed under both
placements and their partial-failure behaviour compared.
**Traces to.** BR-202, BR-404, BR-501

---

### FR-507 — Contributions are attributed in the composed result

**Statement.** A composed result SHALL identify, for each contribution, which
agent and division produced it and whether it derives from an authoritative
system of record or from model reasoning.

**Rationale.** Composition destroys provenance unless provenance is carried
deliberately. A business owner acting on a composed answer needs to know which
parts are authoritative, and an auditor needs it to reconstruct the basis of a
decision.

**Priority.** Must
**Verification.** Test — composed results carry per-contribution attribution
matching the recorded interaction.
**Traces to.** BR-203, BR-305

---

## FR-6xx — Scenario execution and the evaluation surface

### FR-601 — Scenarios are declared, not coded

**Statement.** Evaluation scenarios SHALL be defined as data — participants,
route, protocol, prompts and expected shape — and adding a scenario SHALL NOT
require code changes.

**Rationale.** The scenario library is the evaluation's instrument and will grow
throughout. If each scenario is code, the library grows slowly and inconsistently,
and scenarios stop being comparable to one another.

**Priority.** Must
**Verification.** Test — a scenario is added and executed with no code change.
**Traces to.** BR-501

---

### FR-602 — Execute a scenario over a selected route and protocol

**Statement.** An operator SHALL be able to execute any scenario over any route
and protocol combination its participants support, and SHALL be prevented from
selecting a combination known to be unsupported.

**Rationale.** This is the evaluation's primary action. Prevention matters
because an unsupported combination produces a failure that looks like a finding,
and the library will accumulate false findings if it is easy to run.

**Priority.** Must
**Verification.** Demonstration — a scenario executes over each supported
combination; an unsupported combination is refused with a reason.
**Traces to.** BR-501, BR-103

---

### FR-603 — Multi-turn interaction

**Statement.** The system SHALL support multi-turn conversation with a scenario's
entry agent, preserving conversational context across turns within a session.

**Rationale.** Single-shot invocation does not exercise session semantics, and
session handling differs sharply between protocols — one carries conversation
identity as a first-class protocol concept while others smuggle it as an
argument. That difference is a finding that only multi-turn use reveals.

**Priority.** Must
**Verification.** Test — a multi-turn exchange demonstrates retained context, on
every protocol supporting it.
**Traces to.** BR-501, BR-103

---

### FR-604 — Inspect the exchange, including raw payloads

**Statement.** For any executed interaction, an operator SHALL be able to inspect
every hop in sequence, including the actual request and response payloads
exchanged.

**Rationale.** Wire-level inspection is the reason the environment exists. It is
also the only way to diagnose a failure whose two ends each appear correct, which
is the characteristic cross-platform failure.

**Priority.** Must
**Verification.** Demonstration — an executed interaction is inspected hop by
hop with actual payloads visible.
**Traces to.** BR-501, BR-305

---

### FR-605 — Compare runs

**Statement.** An operator SHALL be able to compare executions of the same
scenario across protocols, routes and platforms, on response content, latency,
hop count and consumption.

**Rationale.** Comparison is the analytical act the evaluation exists to perform.
Requiring it to be assembled by hand from individual runs means it will be done
rarely and inconsistently.

**Priority.** Should
**Verification.** Demonstration — two executions of one scenario are compared on
all four dimensions.
**Traces to.** BR-501, BR-402

---

### FR-606 — Loopback verification without external platforms

**Statement.** The system SHALL provide a deterministic agent and a set of
scenarios exercising every protocol pairing without any external platform, and
these SHALL be runnable in an environment holding no external credentials.

**Rationale.** Without it, every failure is ambiguous between the system's own
plumbing and a remote platform, and there is no way to establish a baseline. It
also allows the system to be developed and verified by anyone, which is what
prevents it from depending on one person's credentials.

**Priority.** Must
**Verification.** Test — the full loopback suite passes with no external
credentials present.
**Traces to.** BR-501

---

### FR-607 — Warm-up control

**Statement.** An operator SHALL be able to explicitly warm the scale-to-zero
components of a route before executing a measured scenario, and measurements
SHALL record whether the route was warm or cold.

**Rationale.** Cold-start latency can exceed the interaction's entire budget, so
an unrecorded warm/cold distinction makes latency measurements incomparable and
occasionally meaningless. Both states are worth measuring; conflating them is
what must be prevented.

**Priority.** Must
**Verification.** Test — a route is warmed on demand, and recorded measurements
carry a warm or cold designation.
**Traces to.** BR-205, BR-501

---

## FR-7xx — Findings and reporting

### FR-701 — Findings are recorded with their conditions

**Statement.** Each finding SHALL be recorded with the conditions under which it
was obtained — participants, protocol, route, warm or cold state, date — and a
reference to the interaction record supporting it.

**Rationale.** A measurement without its conditions is not a measurement, and a
finding without a supporting record is an anecdote. These findings will be used
to justify or refuse investment and must withstand challenge from the vendors
they concern.

**Priority.** Must
**Verification.** Inspection — every recorded finding carries conditions and a
resolvable reference to its supporting interaction.
**Traces to.** BR-501

---

### FR-702 — Evidence class on every claim

**Statement.** Every finding SHALL be classified as measured, observed, or
hypothesised, and SHALL NOT be presentable without a classification.

**Rationale.** The three carry very different weight and are indistinguishable
once written as prose. Enforcing the classification at the point of recording is
what stops a hypothesis being read a year later as a measurement.

**Priority.** Must
**Verification.** Test — a finding cannot be recorded or published without a
classification.
**Traces to.** BR-501, BR-503

---

### FR-703 — Not-established register

**Statement.** The system SHALL maintain and publish a register of questions not
answered, capabilities declared but not exercised, and results resting on
assumption — presented alongside the findings rather than separately.

**Rationale.** Selective reporting of successes reads as completeness. A
capability that is advertised by a component but never exercised is the specific
case most likely to be mistaken for a proven one. Placement alongside the
findings is part of the requirement: a register nobody encounters does not
discharge it.

**Priority.** Must
**Verification.** Inspection — published outputs contain the register, and each
claimed capability is marked exercised or merely declared.
**Traces to.** BR-503, BR-502

---

### FR-704 — Findings are reproducible from the record

**Statement.** It SHALL be possible to re-derive any published finding from the
retained interaction record, by someone who did not execute the original run.

**Rationale.** This is the operational definition of evidence-grade. It is also
the property that makes the record — rather than the running environment — the
durable asset, which matters because the environment will eventually be
decommissioned (BR-504).

**Priority.** Must
**Verification.** Demonstration — a person who did not run it re-derives a
published finding from the record alone.
**Traces to.** BR-501, BR-504

---

### FR-705 — Findings are exportable independently of the system

**Statement.** Findings and their supporting evidence SHALL be exportable to a
durable format readable without the system running.

**Rationale.** The evaluation terminates in a decision and the environment is
then decommissioned or promoted. Findings locked inside a decommissioned system
are lost precisely when the decision they informed comes up for review.

**Priority.** Must
**Verification.** Test — findings export and are read with the system stopped.
**Traces to.** BR-504, BR-501
