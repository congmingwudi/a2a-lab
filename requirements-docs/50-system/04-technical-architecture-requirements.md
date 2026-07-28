# Technical and Architecture Requirements

## Why structural constraints appear in a requirements document

A requirements document that constrains structure is usually overreaching. Each
constraint here earns its place by satisfying one of three tests:

1. **It is the only known way to satisfy a stated business requirement.**
2. **It constrains a property that cannot be added later** without rebuilding —
   testability, enforcement completeness, portability of the record.
3. **It is imposed from outside** by a protocol, a regulation, or an observed
   platform behaviour, and is therefore a constraint rather than a choice.

Constraints failing all three are absent by design. Where a structure is
deliberately left free, this document says so, because silence reads as
under-specification to a builder.

## Block allocation

| Block | Theme |
|---|---|
| `TR-1xx` | Structural decomposition |
| `TR-2xx` | Hosting and deployment |
| `TR-3xx` | State, storage and data placement |
| `TR-4xx` | Interfaces |
| `TR-5xx` | Testability |

---

## TR-1xx — Structural decomposition

### TR-101 — Two seams over one canonical model

**Statement.** The system SHALL be organised around two abstractions — one for
agents it hosts and exposes, one for remote agents it invokes — both expressed
in terms of a single canonical request and response model independent of any
protocol.

**Rationale.** Test 1. Protocol-independent comparison (BR-103) requires an agent
implemented once and reachable over every protocol, and a caller written once
that reaches any target. A shared canonical model is what makes the two sides
symmetrical, and symmetry is what makes the system testable against itself
without any external platform (TR-501).

**Priority.** Must
**Verification.** Inspection — both seams are expressed in the canonical model;
no protocol-specific type appears in agent implementations or calling code.
**Verification note.** Inspection-only by exception class 3 (documentation and artefact-presence): the property is the existence and adequacy of a recorded artefact, which no execution can assert.
**Traces to.** BR-102, BR-103

---

### TR-102 — The canonical model carries no protocol artefacts

**Statement.** The canonical model SHALL NOT contain fields specific to any one
protocol, and protocol-specific concerns SHALL be confined to the components
implementing that protocol.

**Rationale.** Test 2. Once a protocol's concepts leak into the shared model,
every other protocol implementation must accommodate them, and the comparison is
biased toward whichever protocol arrived first — permanently, because unpicking
it later means changing every component.

**Priority.** Must
**Verification.** Inspection — the canonical model is reviewed against each
supported protocol for leaked concepts.
**Verification note.** Inspection-only by exception class 3 (documentation and artefact-presence): the property is the existence and adequacy of a recorded artefact, which no execution can assert.
**Traces to.** BR-103

---

### TR-103 — Platform integrations are self-contained units

**Statement.** Each platform's integration SHALL be a self-contained unit
contributing a hosted-agent implementation, a remote-agent client, or both, plus
its configuration entries — and SHALL require no modification to shared
components or to other platforms' units.

**Rationale.** Tests 1 and 2. This is the structural mechanism behind additive
participation (BR-104) and declining onboarding cost. Shared-component changes
per platform would expose every existing platform to regression on each
onboarding, which is precisely the outcome divisional architects will refuse.

**Priority.** Must
**Verification.** Analysis — the change set for an onboarding is enumerated and shown to touch only that platform's unit and configuration.
**Traces to.** BR-104

---

### TR-104 — Routing authority lives in configuration

**Statement.** The mapping from logical target to protocol, address,
authentication and timeout SHALL reside in configuration read at invocation
time, and SHALL be the single authority for routing decisions.

**Rationale.** Test 1. Protocol substitution without redeployment (BR-103,
FR-203) is impossible if any routing decision is embedded in code. A *single*
authority matters as much as its location: routing split between configuration
and code produces behaviour nobody can predict from either.

**Priority.** Must
**Verification.** Test — every routing dimension is changed via configuration and
takes effect without redeployment.
**Traces to.** BR-103

---

### TR-105 — Cross-cutting controls have exactly one enforcement point each

**Statement.** Delegation control, boundary data rules, credential resolution and
interaction recording SHALL each be enforced at exactly one point that every
relevant path traverses. It SHALL NOT be possible to add a path bypassing them.

**Rationale.** Test 2. A control enforced at four of five paths provides no
guarantee, and the fifth path is typically added later by someone unaware the
control exists. Retrofitting completeness is far more expensive than designing
for it, and until it is complete the system cannot honestly claim the property.

**Priority.** Must
**Verification.** Analysis and test — paths are enumerated and shown to traverse
each control; a constructed bypass fails.
**Traces to.** BR-302, BR-305, BR-306, BR-307

---

### TR-106 — Orchestration is separable from delegation

**Statement.** The mechanism decomposing a task into concurrent legs SHALL be
separable from the mechanism performing a single delegation, such that either can
be exercised without the other.

**Rationale.** Test 1. Both orchestrator placements must be comparable on the
same task (FR-506), and a remote platform's declared orchestration will call the
delegation mechanism without involving the system's own. Entangling them makes
that comparison impossible.

**Priority.** Should
**Verification.** Test — single delegation and decomposition are each exercised
independently.
**Traces to.** BR-202, BR-404

---

## TR-2xx — Hosting and deployment

### TR-201 — Components are independently deployable

**Statement.** Each operated component SHALL be deployable without redeploying
others, and SHALL declare its dependencies explicitly.

**Rationale.** Test 2. Coupled deployment makes every change to any component a
whole-system risk, which in an evaluation environment means changes are batched,
which means failures are hard to attribute.

**Priority.** Must
**Verification.** Test — each component is deployed independently.
**Traces to.** BR-104

---

### TR-202 — Hosting model is chosen per component against its workload

**Statement.** Each component's hosting model SHALL be chosen against that
component's own workload characteristics, and the choice SHALL be recorded with
its reason.

**Rationale.** Test 3. The workloads genuinely differ: an always-warm surface
serving an operator has different economics from a component invoked a few times
a day. Recording the reason is what prevents an unexamined default from
propagating, and what allows the choice to be revisited when the workload
changes.

**Priority.** Must
**Verification.** Inspection — each component's hosting model and reason are
documented.
**Verification note.** Inspection-only by exception class 3 (documentation and artefact-presence): the property is the existence and adequacy of a recorded artefact, which no execution can assert.
**Traces to.** BR-404

---

### TR-203 — Scale-to-zero is permitted, and its consequences are declared

**Statement.** Components MAY use scale-to-zero hosting. Where they do, the
first-invocation latency SHALL be measured, and any interaction depending on
that component SHALL account for it in its timeout budget.

**Rationale.** Test 3. Scale-to-zero is materially cheaper and is the right
choice for most of this estate. Its cost is latency that lands *inside* the
user's budget rather than outside it, and it interacts with unattended
asynchronous progress (NFR-108). Permitted, therefore, but never silently.

**Priority.** Must
**Verification.** Test — cold-start latency is measured per component and
reflected in the documented budget.
**Traces to.** BR-205

---

### TR-204 — No operator workstation on the runtime path

**Statement.** No interaction the system supports SHALL depend on a component
running on an individual's workstation.

**Rationale.** Test 2. A workstation dependency makes scheduled and asynchronous
work impossible, makes availability a function of one person's laptop, and makes
every measurement dependent on a machine nobody else can reproduce. Local
execution remains entirely legitimate for development — the constraint is on the
runtime path.

**Priority.** Must
**Verification.** Analysis — the runtime path is enumerated and contains no
workstation-hosted component.
**Traces to.** BR-206, BR-501

---

### TR-205 — Deployment verifies its target before acting

**Statement.** Automated deployment SHALL confirm the intended target
environment before creating or modifying any resource, and SHALL refuse
otherwise.

**Rationale.** Test 2, and the counterpart to NFR-503. Removing environment
identifiers from source makes a wrong-target deployment easier to attempt, so the
guard ships with that rule rather than after it. Every deployment path must be
covered — one uncovered path is the one that will be used.

**Priority.** Must
**Verification.** Test — an unintended target is refused before resource
creation, on every deployment path.
**Traces to.** BR-306

---

## TR-3xx — State, storage and data placement

### TR-301 — The record's storage is pluggable

**Statement.** The interaction record SHALL be written through an interface
permitting more than one storage implementation, selectable by configuration.

**Rationale.** Test 2. Local development, hosted operation and long-term
retention have different storage needs, and residency obligations may require a
specific placement per data class. Deciding storage once, in code, forecloses
all of that.

**Priority.** Must
**Verification.** Test — the system operates against more than one storage
implementation with only configuration differing.
**Traces to.** BR-303

---

### TR-302 — No division's data is persisted outside its owner

**Statement.** The system SHALL NOT create a persistent store of another
division's business data organised for retrieval as data. Retained interaction
records SHALL be addressable only by interaction — by correlation identifier,
participant, or time — and SHALL NOT be indexed or queryable by the business
entities their content happens to mention.

**Rationale.** Test 1. Copying a division's data would create a second system of
record, a residency problem and an authorisation problem the estate does not
otherwise have (X9).

The distinction needs stating precisely, because interaction records unavoidably
*contain* another division's business data — OR-101 requires recording what was
exchanged, and what was exchanged is frequently a customer record. What
separates a trace archive from a shadow system of record is not its content but
its **access shape**: one is addressable by interaction, the other by business
entity. A well-intentioned index over payload content to make search convenient
converts the first into the second while satisfying every other requirement, and
it is the most likely way this boundary is crossed.

The one exception is DR-503, which requires records to be locatable by data
subject for access and erasure obligations. That capability is a compliance
function, not a query path: it is restricted to that purpose, attributable
(SR-603), and does not make the store generally addressable by business entity.

**Priority.** Must
**Verification.** Analysis and test — the record store's access paths are
enumerated and none permits retrieval by business entity outside the DR-503
compliance function; caches are shown to be bounded to a single interaction's
lifetime.
**Traces to.** BR-301, BR-303

---

### TR-303 — Credentials exist only in the secret store

**Statement.** Authentication material SHALL exist only in the secret store and
in the memory of the component using it. It SHALL NOT appear in source,
configuration, deployment descriptors, container images, logs, or the interaction
record.

**Rationale.** Test 3. Each of those locations is copied, exported or shared by a
mechanism unaware it holds a credential. Component configuration should carry a
*reference* resolved at start rather than a value.

**Priority.** Must
**Verification.** Test — automated inspection of source, configuration, images
and records finds no credential material.
**Traces to.** BR-306

---

### TR-304 — Residency-constrained data has a declared placement

**Statement.** Each class of retained data SHALL have a declared permitted
geography, and storage SHALL enforce it.

**Rationale.** Test 3. Retained payloads may contain personal data whose
residency is constrained. A record store placed without reference to residency
turns the observability layer into the compliance breach.

**Priority.** Must
**Verification.** Inspection and test — placement is declared per class and
enforced.
**Traces to.** BR-303, BR-304

---

## TR-4xx — Interfaces

### TR-401 — Externally-reachable interfaces are described and versioned

**Statement.** Every interface the system exposes outside itself SHALL have a
machine-readable description and a version, and a breaking change SHALL be a
version change.

**Rationale.** Test 2. Divisions integrate against these interfaces and cannot be
asked to re-integrate on a silent change (BR-104).

**Priority.** Must
**Verification.** Test — each external interface is checked for a machine-readable description and a version.
**Traces to.** BR-104

---

### TR-402 — Discovery documents are generated from live configuration

**Statement.** Where a protocol requires a published description of an agent,
that description SHALL be generated at runtime from the agent's actual
configuration.

**Rationale.** Test 2. A maintained-by-hand description drifts, and callers make
binding decisions on it — including refusing to proceed when it does not match
their expectations. Generation makes drift structurally impossible rather than a
matter of discipline.

**Priority.** Must
**Verification.** Test — altering configuration alters the published description
without a separate edit.
**Traces to.** BR-102

---

### TR-403 — Descriptions satisfy every supported protocol generation at once

**Statement.** Where participants support different generations of a protocol,
published descriptions SHALL contain what each generation requires, such that no
supported participant rejects them.

**Rationale.** Test 3. Generations differ in required fields, and a description
valid for one may be rejected as malformed by another for lacking fields it does
not define. This fails *before* any request is sent, so it is invisible to
request-level testing and presents as the platform being unreachable.

**Priority.** Must
**Verification.** Test — a client of each supported generation retrieves and
accepts the description.
**Traces to.** BR-102

---

## TR-5xx — Testability

### TR-501 — A deterministic agent exists for every protocol

**Statement.** The system SHALL include a deterministic agent implementation,
exercisable over every supported protocol, whose responses are fully predictable
and which depends on no external platform.

**Rationale.** Test 2. Without it there is no baseline: every failure is
ambiguous between the system's own behaviour and a remote platform's, and no
protocol implementation can be verified before a platform is available. It also
makes the system buildable and verifiable by anyone, removing the dependency on
one person's credentials.

**Priority.** Must
**Verification.** Test — the full protocol matrix passes against the
deterministic agent with no external credentials present.
**Traces to.** BR-501

---

### TR-502 — Tests requiring live platforms are separable

**Statement.** Tests requiring real platform credentials SHALL be identifiable
and excluded by default, and the remaining suite SHALL pass in an environment
holding no external credentials.

**Rationale.** Test 2. A suite that cannot run without five platforms'
credentials will not be run, and a contributor without those credentials cannot
verify their change. Live tests remain necessary — they must simply be opt-in.

**Priority.** Must
**Verification.** Test — the default suite passes with no external credentials;
live tests are selectable.
**Traces to.** BR-501

---

### TR-503 — Every failure class is inducible

**Statement.** It SHALL be possible to deliberately induce each failure class the
system classifies — unreachable, authentication rejection, protocol error,
agent-level failure, timeout, throttling, refusal, and partial decomposition —
without depending on a remote platform misbehaving.

**Rationale.** Test 2. Partial-failure behaviour is a Must requirement (BR-204)
and cannot be verified by waiting for it to occur naturally. Untestable error
handling is unverified error handling, and error paths are where this class of
system actually fails.

**Priority.** Must
**Verification.** Test — each classified failure is induced deterministically and
produces the specified behaviour.
**Traces to.** BR-204, BR-501

---

## Deliberately unconstrained

Stated so a builder does not read silence as omission. The following are free
choices, and this document takes no position:

- **Implementation language and runtime**, provided the protocol libraries used
  are conformant implementations rather than hand-rolled.
- **Process topology** — whether protocol surfaces are served by one process or
  many. TR-101 constrains the abstraction, not its deployment.
- **Storage technology** behind TR-301, subject to TR-304's placement
  requirement.
- **Configuration format.**
- **The evaluation surface's presentation technology**, subject to the usability
  requirements in NFR-6xx.
- **Whether decomposition is expressed imperatively or declaratively**, provided
  both orchestrator placements remain comparable (TR-106).
