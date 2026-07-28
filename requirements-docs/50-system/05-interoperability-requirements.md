# Interoperability and Protocol Requirements

Requirements arising from the protocols themselves and from the fact that
independent implementations of the same protocol do not reliably interoperate.

Protocol classes are named by what they do, not by product or specification
name, per the conventions. The three in scope are **direct HTTP invocation**, a
**tool-invocation protocol**, and an **agent-to-agent protocol** with a task
lifecycle. Their glossary definitions are binding.

## Block allocation

| Block | Theme |
|---|---|
| `IR-1xx` | Protocol coverage and concept mapping |
| `IR-2xx` | Correlation and session continuity |
| `IR-3xx` | Generations and dialects |
| `IR-4xx` | Task lifecycle and asynchrony |
| `IR-5xx` | Discovery |
| `IR-6xx` | Failure semantics |

---

## IR-1xx — Protocol coverage and concept mapping

### IR-101 — All three protocol classes are supported in both directions

**Statement.** The system SHALL support all three protocol classes for both
hosting agents and invoking remote agents.

**Rationale.** The comparison is the purpose. Supporting a class in one direction
only leaves half the matrix unfillable, and the asymmetries between inbound and
outbound support are themselves among the more useful findings.

**Priority.** Must
**Verification.** Test — every client-by-server pairing completes against the
deterministic agent.
**Traces to.** BR-103

---

### IR-102 — Concept mapping is explicit and documented

**Statement.** The system SHALL define and document how each canonical concept —
invocation, response, conversation identity, correlation, failure — is expressed
in each protocol class, and implementations SHALL conform to that mapping.

**Rationale.** Without an explicit mapping, each protocol implementation invents
its own and the comparison measures the implementations rather than the
protocols. The mapping is also where the interesting differences become visible
rather than remaining folklore.

**Priority.** Must
**Verification.** Inspection and test — each implementation is checked against
the documented mapping.
**Traces to.** BR-103, BR-501

**Required mapping.** Each cell SHALL be populated; a cell with no protocol-level
equivalent SHALL say so explicitly rather than being left blank, because the
absence is the finding.

| Concept | Direct HTTP | Tool-invocation | Agent-to-agent |
|---|---|---|---|
| Invocation | Request body | Tool call with arguments | Message with one text part |
| Response | Response body | Tool result content | Completed task carrying one artefact |
| Conversation identity | No protocol-level equivalent — carried as a field | No protocol-level equivalent — carried as an argument | First-class protocol concept |
| Correlation | Transport header | No protocol-level equivalent — carried as an argument | Message metadata |
| Failure | Transport status | Error flag on the tool result | Task state, over a *successful* transport exchange |
| Capability discovery | No protocol-level equivalent | Tool listing | Published agent description |

Two rows carry disproportionate consequence. **Conversation identity** is
first-class in exactly one class and smuggled in the other two — which means
session behaviour cannot be compared without accounting for the difference.
**Failure** in the agent-to-agent class arrives inside a successful transport
exchange, so any system treating transport status as the outcome will read
failures as successes.

---

### IR-103 — Reference implementations are used where they exist

**Statement.** Where a conformant reference implementation of a protocol exists,
the system SHALL use it rather than implementing the wire format directly.

**Rationale.** A hand-written implementation makes every finding ambiguous
between a protocol property and a local defect. The evaluation's credibility
depends on the protocol side being a known quantity.

**Priority.** Must
**Verification.** Inspection.
**Verification note.** Inspection-only by exception class 3 (documentation and artefact-presence): the property is the existence and adequacy of a recorded artefact, which no execution can assert.
**Traces to.** BR-501

---

## IR-2xx — Correlation and session continuity

### IR-201 — A correlation identifier spans every hop of an interaction

**Statement.** Every hop of one logical interaction SHALL carry the same
correlation identifier, in whatever channel the protocol provides, and it SHALL
be recorded on every hop.

**Priority.** Must
**Verification.** Test — a multi-hop interaction is reconstructed as one
correlated chain.
**Traces to.** BR-305

---

### IR-202 — Correlation survival is measured per platform, not assumed

**Statement.** For each platform, the system SHALL determine by measurement
which correlation channels survive a hop through it, and SHALL record the
result.

**Rationale.** Platforms preserve their own metadata and discard others'. Each
available structured channel is dropped by at least one platform, and no
documentation states which. A correlation design resting on an unmeasured
assumption fails silently and only at the seam where it matters.

**Priority.** Must
**Verification.** Test — a request carrying identifiers in every available
channel traverses each platform; surviving channels are recorded per platform.
**Traces to.** BR-305, BR-502

---

### IR-203 — A correlation channel exists that survives every platform

**Statement.** The system SHALL establish and use at least one correlation
channel measured to survive every platform in the estate, even where that
channel is less structurally elegant than a protocol-native alternative.

**Rationale.** Reconstruction (BR-305) and delegation control (BR-307) both
depend on correlation arriving intact. If no structured channel survives every
platform, the surviving channel is the message content itself — carrying context
as delimited text is unattractive and is preferable to a control that silently
does not apply on one seam.

**Priority.** Must
**Verification.** Test — correlation is recovered after a hop through every
platform.
**Traces to.** BR-305, BR-307

---

### IR-204 — Conversation continuity is supported where the protocol allows

**Statement.** Where a protocol supports multi-turn conversation, the system
SHALL maintain conversation identity across turns, and SHALL record how each
protocol expresses it.

**Rationale.** Session semantics differ sharply — first-class in one class,
smuggled in the others — and that difference determines whether a platform pair
can hold a conversation at all. It is only observable across turns.

**Priority.** Must
**Verification.** Test — multi-turn context is retained on every protocol
supporting it.
**Traces to.** BR-103

---

## IR-3xx — Generations and dialects

### IR-301 — Supported generations are declared per participant

**Statement.** The system SHALL record, per participant, which generation of each
protocol it speaks, established by measurement.

**Rationale.** Vendors state protocol support by name and rarely by generation.
The generation is what determines interoperability, and it is discoverable only
by attempting an exchange.

**Priority.** Must
**Verification.** Test — each participant's generation is established and
recorded with its observation date.
**Traces to.** BR-502

---

### IR-302 — Bidirectional translation between supported generations

**Statement.** Where participants speak different generations, the system SHALL
translate requests and responses in both directions, covering operation naming,
message structure and description content, with traffic already at the target
generation passing through unaltered.

**Rationale.** Neither side negotiates. Generational differences are not
cosmetic: an operation may be named differently and rejected as unknown, and
message structure may differ in how parts are discriminated. Pass-through for
matching traffic matters because translation is where fidelity is lost.

**Priority.** Must
**Verification.** Test — participants at each generation complete exchanges with
no change on either side; matching traffic is byte-identical through the path.
**Traces to.** BR-102

---

### IR-303 — Translation preserves correlation and delegation context

**Statement.** Generation translation SHALL preserve correlation identifiers and
delegation context.

**Rationale.** Translation reconstructs the message, which is exactly where
carried context is lost. The loss is invisible — both sides appear well-formed —
and it occurs at the seam serving the estate's most important participant.

**Priority.** Must
**Verification.** Test — context is recovered intact on the far side of every
translation path.
**Traces to.** BR-305, BR-307

---

### IR-304 — Raw inbound payloads are recorded before translation

**Statement.** Where the system translates an inbound request, it SHALL record
the payload **as received**, before translation.

**Rationale.** The untranslated payload is the evidence of what the remote
platform actually sent — the primary artefact for any dialect finding. Recording
only the translated form leaves the system's own interpretation as the sole
record, which cannot settle a dispute about the other party's behaviour.

**Priority.** Must
**Verification.** Test — the recorded inbound payload matches the bytes received.
**Traces to.** BR-501

---

## IR-4xx — Task lifecycle and asynchrony

### IR-401 — The task lifecycle is implemented completely

**Statement.** Where a protocol defines a task lifecycle, the system SHALL
implement its full progression, including terminal failure expressed as a task
state rather than as a transport error.

**Priority.** Must
**Verification.** Test — each lifecycle state is observed, including a failed
task returned over a successful transport exchange.
**Traces to.** BR-103

---

### IR-402 — Asynchronous behaviour is exercised, not merely implemented

**Statement.** Where a protocol permits immediate acknowledgement with later
retrieval, the system SHALL exercise that mode explicitly and record the
outcome per participant.

**Rationale.** This is the requirement most easily satisfied on paper and missed
in practice. A complete lifecycle implementation can be driven entirely
synchronously for its whole life, because the behaviour depends on an optional
configuration field that defaults to off. Nothing signals the omission —
everything works — and a capability the system possesses goes unused and
unmeasured.

**Priority.** Must
**Verification.** Test — asynchronous mode is explicitly requested and the
resulting behaviour recorded per participant.
**Traces to.** BR-206, BR-503

---

### IR-403 — Asynchronous support is classified in three states, not two

**Statement.** Per participant, asynchronous support SHALL be recorded as
**supported**, **unsupported**, or **accepted-but-unretrievable**.

**Rationale.** The middle case is real and is invisible to a binary
classification: a platform acknowledges the submission and returns an identifier
that no subsequent request shape can read back. Recorded as "supported" it
produces work that never completes; recorded as "unsupported" it hides a
platform that is most of the way there.

**Priority.** Must
**Verification.** Test — submission and retrieval are attempted per participant
and classified into one of the three states.
**Traces to.** BR-206, BR-502

---

### IR-404 — Asynchrony is distinguished from unattended progress

**Statement.** For each participant supporting asynchronous invocation, the
system SHALL determine by measurement whether work progresses while the caller
is absent, and SHALL record that separately from protocol support.

**Rationale.** These are independent properties and are routinely conflated. On a
runtime that suspends between invocations, the polling is the compute: the
protocol behaves correctly, the ceiling is genuinely removed, and the work does
not advance unless someone asks. That is a hosting property, and a design
assuming unattended completion will fail on exactly those platforms.

**Priority.** Must
**Verification.** Test — work is submitted, the caller stays silent for a defined
interval, and progress is measured on return.
**Traces to.** BR-206, BR-502

---

## IR-5xx — Discovery

### IR-501 — Published descriptions are retrievable as the protocol requires

**Statement.** Where a protocol defines a discovery mechanism, the system SHALL
publish a conformant description at the required location, retrievable under the
protocol's own access expectations.

**Rationale.** Discovery is how a caller determines whether to proceed and how.
A description behind an access requirement the protocol does not anticipate is
unreachable by conformant clients, which presents as the agent not existing.

**Priority.** Must
**Verification.** Test — a conformant client retrieves the description as the
protocol specifies.
**Traces to.** BR-102

---

### IR-502 — Descriptions are accepted by every supported generation

**Statement.** A published description SHALL be accepted by clients of every
generation the system supports for that protocol.

**Rationale.** Generations differ in required fields, and a client may reject a
description as malformed for omitting fields its generation requires. This
fails before any request is sent, so request-level testing never sees it and the
symptom is indistinguishable from unreachability.

**Priority.** Must
**Verification.** Test — a client of each supported generation retrieves and
accepts the description.
**Traces to.** BR-102

---

### IR-503 — Discovery is separated from invocation for access purposes

**Statement.** The system SHALL treat retrieval of a published description and
invocation of the agent as distinct operations with independent access
requirements.

**Rationale.** Open discovery is a deliberate design decision in agent
protocols, and conformant clients depend on it. Coupling the two forces a choice
between violating the protocol and exposing invocation — the separation is what
makes both correct simultaneously. The security handling is SR-503.

**Priority.** Must
**Verification.** Test — description retrieval succeeds without credentials while
invocation without credentials is refused.
**Traces to.** BR-102

---

## IR-6xx — Failure semantics

### IR-601 — Protocol-level and agent-level failures are distinguished

**Statement.** The system SHALL distinguish failure of the protocol exchange from
failure of the agent's work, in both directions, and SHALL express each in the
form the protocol defines.

**Rationale.** They have different owners and different remedies. In the
agent-to-agent class an agent failure arrives as a *successful* exchange
containing a failed task — so a system keying on transport status records a
success and reports a good outcome for work that failed.

**Priority.** Must
**Verification.** Test — each failure kind is induced in each direction and
reported distinctly.
**Traces to.** BR-204, BR-501

---

### IR-602 — Failure detail survives translation and mediation

**Statement.** Where a failure crosses a mediation or translation boundary, its
detail SHALL be preserved and expressed in the receiving side's form.

**Rationale.** Mediation is where errors are most often flattened into a generic
transport failure, which removes exactly the information needed to diagnose the
seam that is hardest to diagnose already.

**Priority.** Must
**Verification.** Test — an induced far-side failure is received with detail
intact in the caller's protocol form.
**Traces to.** BR-204

---

### IR-603 — Apparent success with absent content is detected

**Statement.** The system SHALL detect and report responses that are structurally
successful but whose expected content is absent, and SHALL NOT classify them as
successful.

**Rationale.** This is the estate's characteristic failure. A platform exceeding
its own internal budget may return success with the delegated section present
and empty; a decomposition may return a well-formed result missing a
contribution. Both pass every status check. Content must be examined, because
status will not reveal it.

**Priority.** Must
**Verification.** Test — an induced empty-content success is classified as a
failure and reported.
**Traces to.** BR-204, BR-501
