# Data and Privacy Requirements

Requirements arising from the organisation's data protection obligations,
applied to agent-to-agent interaction specifically.

**The premise this document rests on:** an agent forwarding a request is
**processing**, and sending content to a model endpoint is a **transfer** of
whatever that content holds. Neither is treated as such by default in agent
platform documentation, which describes prompts as input. Almost every
requirement here follows from taking those two statements seriously.

## Block allocation

| Block | Theme |
|---|---|
| `DR-1xx` | Classification and data-flow record |
| `DR-2xx` | Minimisation at the boundary |
| `DR-3xx` | Residency and inference location |
| `DR-4xx` | Confinement of special-category data |
| `DR-5xx` | Retention and subject rights |
| `DR-6xx` | Purpose limitation across delegation |

---

## DR-1xx — Classification and data-flow record

### DR-101 — Every seam has a declared data classification

**Statement.** Each seam SHALL declare which classes of data it is permitted to
carry, in each direction, before it is enabled.

**Rationale.** A seam without a declared classification cannot be assessed, and
by default carries whatever a model chooses to include. Declaring before
enabling is what makes the assessment a gate rather than an audit finding.

**Priority.** Must
**Verification.** Inspection — every enabled seam has a classification;
enablement without one is refused.
**Traces to.** BR-301

---

### DR-102 — The data-flow record derives from traffic, not configuration

**Statement.** The system SHALL be able to report, per seam and period, what
categories of data actually crossed, in which direction, between which parties —
derived from recorded interactions rather than from declared configuration.

**Rationale.** Configuration states intent; only traffic states what happened,
and the gap between them is precisely what a review exists to find. A report
generated from configuration will always show compliance, including when the
system is not compliant.

**Priority.** Must
**Verification.** Test — a report is produced from recorded traffic and reconciled
against a deliberately induced divergence from configuration.
**Traces to.** BR-301, BR-305

---

### DR-103 — Controller and processor roles are determined per seam

**Statement.** Each seam SHALL record which data protection role each party holds
for the processing that seam performs.

**Rationale.** The role determines the lawful basis, the documentation required
and the obligations on each side. It varies by seam — a division consulting
another's system of record is a different arrangement from one division
instructing another's agent to perform work — and assuming one role estate-wide
gets some seams wrong.

**Priority.** Must
**Verification.** Inspection — roles are recorded per seam and reviewed by the
DPO.
**Traces to.** BR-301

---

## DR-2xx — Minimisation at the boundary

### DR-201 — Minimisation is enforced at the outbound boundary

**Statement.** Where a seam's classification excludes a data category, the system
SHALL remove or transform that category from outbound content **before
transmission**, enforced at the seam rather than by the calling agent.

**Rationale.** Minimisation applied after transfer is not minimisation — the
transfer already occurred. Enforcement at the seam is the only form that
survives a reconfigured caller, a rewritten prompt, or a model including more
context than it needed. The calling agent cannot be the control point because
its behaviour is not deterministic.

**Priority.** Must
**Verification.** Test — content containing an excluded category is transmitted
with the category absent, verified from the recorded outbound payload.
**Traces to.** BR-302

---

### DR-202 — Blocked transmission is refusal, not silent removal

**Statement.** Where required content cannot be transmitted under the seam's
classification, the system SHALL refuse the interaction with an explicit reason
rather than transmitting a silently degraded request.

**Rationale.** A silently stripped request produces an answer based on content
the caller believes was sent. That answer is wrong in a way nobody can see, and
it is worse than no answer. The refusal is also a finding: it bounds what the
seam can do.

**Priority.** Must
**Verification.** Test — an interaction requiring excluded content is refused with
a reason and recorded.
**Traces to.** BR-302, BR-503

---

### DR-203 — Pseudonymisation is not treated as anonymisation

**Statement.** Where the system replaces identifiers with tokens, the result
SHALL continue to be treated as personal data for every purpose — classification,
residency, retention and reporting.

**Rationale.** Reversible tokenisation remains personal data. Designs routinely
treat it as though it were not, which produces a residency and retention posture
that is wrong precisely where it was believed to be strongest.

**Priority.** Must
**Verification.** Inspection — pseudonymised content is classified and handled as
personal data throughout.
**Traces to.** BR-302

---

### DR-204 — Re-identification material stays behind the boundary

**Statement.** Where pseudonymisation is reversible, the material permitting
reversal SHALL remain within the originating boundary and SHALL NOT accompany
the pseudonymised content.

**Rationale.** Pseudonymisation whose key travels with the data provides no
protection at all while creating the impression of protection.

**Priority.** Must
**Verification.** Test — transmitted content contains no re-identification
material.
**Traces to.** BR-302

---

### DR-205 — Redaction is applied to the record as well as the wire

**Statement.** Content excluded from transmission SHALL also be excluded from the
retained interaction record, and the exclusion SHALL be visible as a redaction
marker rather than as absence.

**Rationale.** The record captures complete payloads by design, which makes it
the place minimised content would otherwise accumulate — turning the
observability layer into the breach. Visible markers matter because silent
absence is indistinguishable from the remote party having omitted it.

**Priority.** Must
**Verification.** Test — recorded payloads contain markers rather than excluded
content.
**Traces to.** BR-302, BR-305

---

## DR-3xx — Residency and inference location

### DR-301 — Inference location is treated as a transfer

**Statement.** The system SHALL treat transmission of content to a model
endpoint as a transfer of that content to the endpoint's jurisdiction, and SHALL
apply the seam's residency rules to it.

**Rationale.** This is the requirement most often missed. Residency analysis
routinely covers storage and omits inference, so a design can satisfy every
storage rule while sending personal data to a model in another jurisdiction on
every request. There is no storage anywhere for an auditor to find; the
transfer is nonetheless real.

**Priority.** Must
**Verification.** Test — an interaction over residency-constrained data is
prevented from reaching an endpoint outside the permitted geography.
**Traces to.** BR-303

---

### DR-302 — Inference location is recorded per interaction

**Statement.** Each recorded interaction SHALL carry the geography in which model
inference occurred for each hop, or an explicit indication that it could not be
determined.

**Rationale.** Demonstrating residency compliance after the fact requires the
location on the record. An explicit *undetermined* is essential: it is the
honest state for platforms that do not expose it, and recording nothing would
present as compliance.

**Priority.** Must
**Verification.** Test — recorded interactions carry inference geography or an
explicit undetermined marker.
**Traces to.** BR-303, BR-305

---

### DR-303 — Undetermined inference location blocks constrained data

**Statement.** Where a platform's inference geography cannot be determined, that
platform SHALL NOT be used for interactions carrying residency-constrained data.

**Rationale.** Undetermined is not permitted-by-default. The alternative — using
it and hoping — produces an unprovable compliance position, which is the same as
non-compliance in front of a regulator. Blocking converts the gap into a
recorded finding that may justify withdrawing the seam.

**Priority.** Must
**Verification.** Test — a platform with undetermined geography is refused for
constrained data.
**Traces to.** BR-303, BR-503

---

### DR-304 — Retained records respect the residency of their content

**Statement.** Interaction records SHALL be stored in a geography permitted for
the most restricted class of data they contain.

**Rationale.** Complete payload capture means records inherit the residency
constraints of what they captured. A globally-placed record store quietly
relocates every constrained payload that passed through it.

**Priority.** Must
**Verification.** Inspection and test — record placement is verified against the
classification of stored content.
**Traces to.** BR-303

---

## DR-4xx — Confinement of special-category data

### DR-401 — Special-category data does not cross its confinement

**Statement.** Special-category data SHALL NOT be transmitted to any agent, model
endpoint or platform outside the confinement declared by its owning division.

**Priority.** Must
**Verification.** Test — an attempt to transmit special-category content beyond
the confinement is prevented at the boundary and recorded.
**Traces to.** BR-304

---

### DR-402 — Collaboration by derived conclusion is supported

**Statement.** The system SHALL support interactions in which a division
contributes a derived, non-identifying conclusion rather than the underlying data
on which it is based.

**Rationale.** This is the mechanism that lets a confined division participate at
all. Most cross-divisional questions need a conclusion rather than the record
behind it — and if the derived conclusion proves too thin to act on, that is a
first-class finding about what collaboration is available to this organisation
(H6), not a defect.

**Priority.** Must
**Verification.** Demonstration — a cross-divisional interaction completes using
a derived conclusion, with no special-category content crossing the boundary.
**Traces to.** BR-304

---

### DR-403 — Derivation happens inside the confinement

**Statement.** The derivation producing a shareable conclusion SHALL be performed
within the confinement, using inference within the permitted geography.

**Rationale.** Deriving a conclusion outside the confinement requires sending the
underlying data out to do it, which defeats the entire arrangement while
appearing to satisfy it — the shared artefact is non-identifying, but the
transfer already happened.

**Priority.** Must
**Verification.** Test — the derivation's inference occurs within the permitted
geography, verified from the record.
**Traces to.** BR-304, BR-303

---

## DR-5xx — Retention and subject rights

### DR-501 — Retention is defined per content class and enforced

**Statement.** Each class of retained content SHALL have a defined retention
period, and expiry SHALL be enforced automatically rather than by procedure.

**Rationale.** Complete payload capture makes retention a data protection control
rather than a storage concern. Procedural deletion does not happen, and an
evaluation environment accumulates an ever-growing liability by default.

**Priority.** Must
**Verification.** Test — content past its retention period is removed
automatically.
**Traces to.** BR-301

---

### DR-502 — Evidence retention is reconciled with minimisation

**Statement.** Where evidential value requires retaining an interaction beyond
the retention period for its content class, the retained artefact SHALL be
reduced to what the finding requires, rather than the period being extended.

**Rationale.** Reproducibility (BR-501) and minimisation (BR-302) genuinely
conflict, and the conflict must be resolved in the requirements rather than
discovered in operation. Reducing the artefact satisfies both; extending
retention satisfies one at the other's expense and would be decided ad hoc,
under pressure, in favour of whichever party is present.

**Priority.** Must
**Verification.** Inspection — long-retained evidential artefacts are reduced,
and the reduction preserves reproducibility.
**Traces to.** BR-501, BR-301

---

### DR-503 — Records are locatable by data subject

**Statement.** Where retained records contain personal data, the system SHALL be
able to locate all records relating to a given data subject.

**Rationale.** Access and erasure obligations apply to this store as to any
other. A store that cannot be searched by subject cannot satisfy them, and
retrofitting the capability across an accumulated archive is substantially
harder than designing for it.

**Priority.** Must
**Verification.** Test — all records for a subject are located and enumerated.
**Traces to.** BR-301

---

### DR-504 — Erasure is supported without destroying the evidential chain

**Statement.** The system SHALL support erasure of a data subject's personal data
from retained records while preserving the structural record of the interactions
— participants, protocol, timing, status — needed for reconstruction.

**Rationale.** Erasure and auditability both apply. Erasing whole records
destroys the audit trail; retaining whole records defeats erasure. Separating
content from structure is what allows both, and it must be a property of the
record's design rather than an operational workaround.

**Priority.** Must
**Verification.** Test — personal data is erased while the interaction remains
reconstructable in structure.
**Traces to.** BR-301, BR-305

---

## DR-6xx — Purpose limitation across delegation

### DR-601 — Purpose accompanies a delegated request

**Statement.** A delegated request SHALL carry a statement of the purpose for
which it is made.

**Rationale.** The receiving division cannot apply purpose limitation without
knowing the purpose, and it cannot authorise a caller meaningfully without it
either (BR-105). Purpose is not recoverable from the request content once more
than one hop has occurred.

**Priority.** Must
**Verification.** Test — delegated requests carry purpose, recoverable at the
receiving seam.
**Traces to.** BR-301, BR-105

---

### DR-602 — Onward delegation cannot broaden purpose

**Statement.** Where a receiving agent delegates further, the onward request
SHALL NOT assert a purpose broader than the one it received.

**Rationale.** Purpose limitation defeated by an intermediary is purpose
limitation defeated. Without this, a narrow request into an agent that
re-delegates emerges with a wider purpose, and the widening is invisible to both
the originator and the eventual recipient.

**Priority.** Must
**Verification.** Test — an onward delegation asserting a broader purpose is
refused.
**Traces to.** BR-301, BR-307

---

### DR-603 — Data obtained for one interaction is not reused for another

**Statement.** Data obtained from a division during an interaction SHALL be used
only for that interaction, and SHALL NOT be retained for reuse in subsequent
interactions.

**Rationale.** Caching a division's data across interactions creates a
persistent copy outside its owner (TR-302), and reuses data for a purpose it was
not obtained for. The performance temptation is real, which is why the
prohibition is explicit.

**Priority.** Must
**Verification.** Inspection and test — no cache of division data outlives its
interaction.
**Traces to.** BR-301, BR-303
