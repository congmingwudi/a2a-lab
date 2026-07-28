# Business Requirements — Meridiaan Group

What the business needs to be true. These requirements are deliberately free of
technology: each states an outcome and why it matters, and each is satisfied by
one or more system requirements in `50-system/`, linked in
`90-traceability/01-traceability-matrix.md`.

Every requirement carries an **owner** — the stakeholder who can declare it met
or unmet. A business requirement with no owner is a preference.

## Block allocation

| Block | Theme |
|---|---|
| `BR-1xx` | Estate posture and divisional autonomy |
| `BR-2xx` | Business process outcomes |
| `BR-3xx` | Regulatory, privacy and trust |
| `BR-4xx` | Commercial and consumption |
| `BR-5xx` | Evidence quality and the decision itself |

---

## BR-1xx — Estate posture and divisional autonomy

### BR-101 — Divisional platform autonomy is preserved

**Statement.** The solution SHALL NOT require any division to change, replace,
or migrate its agent platform, nor to alter its existing agent implementations.

**Rationale.** Divisional CTOs hold consent, not compliance. Two divisions
arrived by acquisition with board-level technical autonomy retained, and the
health division's platform choice is bound up with its regulatory posture. A
solution requiring consolidation would not be adopted, so specifying one would
waste the programme.

**Priority.** Must

**Success measure.** At the end of the evaluation, no division has made a
platform change attributable to this programme. Divisional participation is
achieved through a seam and a scoped identity only.

**Owner.** Divisional CTOs (collectively).

---

### BR-102 — The seam is standardised, not the platform

**Statement.** The organisation SHALL be able to define one consistent set of
rules for how agents address, authenticate to, and delegate to one another
across divisions, independent of what platform sits behind each seam.

**Rationale.** The only integration standard achievable in this estate is one
that stops at the boundary. Standardising anything inside a division re-opens
BR-101.

**Priority.** Must

**Success measure.** The rules for a new division to join are expressible as a
single documented seam contract, and adding a division exercises no change to
the rules themselves.

**Owner.** Enterprise Architecture.

---

### BR-103 — No single inter-agent protocol is mandated

**Statement.** The solution SHALL support more than one inter-agent protocol
concurrently, and SHALL allow the protocol used for a given route to be changed
without redeploying either participating division's software.

**Rationale.** Two reasons, and the second is the durable one. Platforms
genuinely differ in what they support, so a single mandated protocol would
exclude divisions. More importantly, the programme's purpose is to *compare*
protocols; a system that can only speak one has already assumed the answer.

**Priority.** Must

**Success measure.** The same business scenario is executed over every supported
protocol against the same platform pair, with only configuration differing
between runs.

**Owner.** Enterprise Architecture.

---

### BR-104 — Participation is additive

**Statement.** Onboarding an additional division or platform SHALL NOT require
changes to divisions already participating.

**Rationale.** If each new participant imposes work on every existing one, the
cost of the estate grows faster than its value and adoption stalls at two or
three divisions — precisely the size at which the interesting problems are still
invisible.

**Priority.** Must

**Success measure.** The onboarding of the final division is completed with no
change deployed by any previously-onboarded division.

**Owner.** Enterprise Architecture.

---

### BR-105 — A division can refuse a specific caller without refusing the estate

**Statement.** A division SHALL be able to permit or deny cross-divisional
requests per calling division and per purpose, rather than only at the level of
participating or not participating.

**Rationale.** Divisional architects will not accept an all-or-nothing exposure.
Granular refusal is also what makes purpose limitation (BR-302) enforceable
rather than aspirational.

**Priority.** Must

**Success measure.** A denial is demonstrated for one caller against one
division's agent while other callers continue to be served.

**Owner.** Divisional architects.

---

## BR-2xx — Business process outcomes

### BR-201 — Divisional agents can be grounded in another division's system of record

**Statement.** An agent serving one division SHALL be able to obtain
authoritative customer, account and contract facts from the division that owns
them, within a single interaction, without a copy of that data being held
outside the owning division.

**Rationale.** This is the single highest-volume cross-divisional need. Today
the alternatives are a stale replica or a human. Both are worse: the replica
creates a second system of record and a residency problem, and the human is the
latency.

**Priority.** Must

**Success measure.** A scenario in which a division's agent answers a question it
could not answer alone, with the authoritative portion demonstrably sourced live
from the owning division.

**Owner.** Divisional business owners; `CTS`.

---

### BR-202 — A single business event can be decomposed across divisions concurrently

**Statement.** The solution SHALL support decomposing one business task into
independent sub-tasks answered concurrently by agents in different divisions,
recombined into a single result.

**Rationale.** Real events do not respect divisional boundaries. A regulatory
change, a client incident or a supply disruption raises legal, tax, commercial
and clinical questions at once, each owned by a different division running a
different platform. Sequential consultation is too slow to be useful, and
one-to-one integration cannot express the shape at all.

**Priority.** Must

**Success measure.** A realistic multi-division scenario completes concurrently,
with total elapsed time materially below the sum of its parts.

**Owner.** Divisional business owners.

---

### BR-203 — Answers are attributed to their source

**Statement.** Any composed answer SHALL identify which portions derive from an
authoritative system of record, which from another division's agent, and which
from model reasoning.

**Rationale.** A business owner acting on a composed answer needs to know its
standing. Unattributed composition makes an authoritative fact and a plausible
inference look identical, which is how confident errors propagate into
regulated work.

**Priority.** Must

**Success measure.** Every composed answer in the scenario library carries
source attribution, verified by inspection of the delivered output rather than
of the logs.

**Owner.** Divisional business owners; Internal Audit.

---

### BR-204 — A partial result is declared, never silently delivered

**Statement.** Where any contributing agent fails to answer, the result SHALL
state which contributions are missing, and the interaction SHALL NOT report
unqualified success.

**Rationale.** The characteristic failure of these systems is not an error — it
is a well-formed, plausible, *incomplete* answer returned with a success
status. A missing section that nobody notices is more dangerous than an outright
failure, because it is acted upon.

**Priority.** Must

**Success measure.** An induced single-leg failure produces a result that names
the missing contribution and a non-success completion signal, in every supported
interaction shape.

**Owner.** Platform Operations; divisional business owners.

---

### BR-205 — Interactive answers arrive within the business process window

**Statement.** Cross-divisional interactions performed while a user waits SHALL
complete within the time the business process allows, and the achievable
envelope SHALL be measured and published per interaction shape.

**Rationale.** A correct answer after the decision point has no value. The
binding constraint is expected to be a platform's own action budget rather than
anything the programme controls, which makes it a business constraint to be
designed around rather than an engineering target to be optimised toward.

**Priority.** Must

**Success measure.** A measured, published response-time envelope per interaction
shape, and at least one scenario in the library meeting an explicitly stated
business window.

**Owner.** Divisional business owners; Platform Operations.

---

### BR-206 — Work too slow to be synchronous is still supported

**Statement.** The solution SHALL support cross-divisional work whose duration
exceeds interactive limits, delivering results asynchronously without loss of
correlation or attribution.

**Rationale.** The most valuable cross-divisional work — deep research,
multi-source analysis — is precisely the work that cannot finish inside a
synchronous timeout. Restricting the estate to what fits in an interactive
window would exclude the highest-value cases and bias the evaluation toward the
trivial ones.

**Priority.** Must

**Success measure.** A long-running scenario completes and delivers a result
correlated to its originating request, having exceeded the synchronous envelope
established under BR-205.

**Owner.** Divisional business owners.

---

### BR-207 — Results reach the business in the systems people already use

**Statement.** Asynchronously delivered results SHALL be made available in an
existing system of work rather than only within the evaluation environment.

**Rationale.** A result that exists only in a lab surface proves the plumbing but
not the value. Delivery into a system people already use is what allows the
business owner to judge whether the answer was worth producing — which is a
question the evaluation exists to answer.

**Priority.** Should

**Success measure.** At least one asynchronous scenario delivers its result into
an existing business system, visible to its business owner without access to the
evaluation environment.

**Owner.** Divisional business owners.

---

## BR-3xx — Regulatory, privacy and trust

### BR-301 — Every cross-boundary exchange has a demonstrable lawful basis

**Statement.** For each seam, the organisation SHALL be able to state what
categories of data cross it, in which direction, between which legal entities,
and on what lawful basis — and evidence it.

**Rationale.** Agent delegation is processing. The Data Protection Officer holds
a veto and will exercise it against any seam whose data flow cannot be
described. Establishing this per seam during evaluation is cheap; establishing
it retrospectively across a live estate is not.

**Priority.** Must

**Success measure.** A per-seam data-flow record exists, is reviewed by the DPO,
and is derived from what the system actually transmits rather than from design
intent.

**Owner.** Data Protection Officer.

---

### BR-302 — Personal data is minimised before it crosses a boundary

**Statement.** Where a cross-boundary interaction does not require personal
data, that data SHALL be removed or pseudonymised **before transmission**, and
this SHALL be enforced by the solution rather than left to the calling agent.

**Rationale.** Minimisation applied after transfer is not minimisation — the
transfer already happened. Enforcement at the boundary is also the only form
that survives a caller being reconfigured, a prompt being rewritten, or a model
deciding to include more context than it needed. Pseudonymised data remains
personal data; the requirement is not discharged by tokenisation alone.

**Priority.** Must

**Success measure.** An attempt to transmit personal data across a seam
configured to exclude it is prevented at the boundary, evidenced in the retained
record.

**Owner.** Data Protection Officer.

---

### BR-303 — Residency is governed by where inference runs

**Statement.** The organisation SHALL be able to constrain interactions such
that model inference over a given data class occurs only in permitted
geographies, and SHALL be able to evidence where inference occurred.

**Rationale.** Residency analyses routinely cover storage and omit inference.
Sending a prompt to a model endpoint in another region is a transfer of whatever
that prompt contains, regardless of where the database sits. This is the
residency requirement most likely to be missed, and the one most likely to be
found by an auditor rather than by us.

**Priority.** Must

**Success measure.** For every interaction involving residency-constrained data,
the inference location is recorded and demonstrably within the permitted
geography; an attempt to route outside it is prevented.

**Owner.** Data Protection Officer; `HCE` divisional architect.

---

### BR-304 — Special-category data does not leave its confinement

**Statement.** Special-category data SHALL NOT be transmitted to any agent,
model endpoint or platform outside the confinement its owning division defines,
and interactions requiring cross-divisional collaboration over such data SHALL
be satisfiable without transmitting it.

**Rationale.** The health division's baseline is that clinical data does not
leave the EU and does not enter a third-party model sandbox. Rather than
excluding that division from the estate, the requirement is to establish whether
useful collaboration is achievable *within* the constraint — by exchanging
derived, non-identifying conclusions instead of underlying data. If it is not
achievable, that is a finding of real value.

**Priority.** Must

**Success measure.** A cross-divisional scenario involving the health division
completes without special-category data crossing its confinement boundary — or
is documented as not achievable, with the specific obstruction named.

**Owner.** Data Protection Officer; `HCE` divisional architect.

---

### BR-305 — Automated decisions can be reconstructed after the fact

**Statement.** For interactions supporting regulated work, the organisation
SHALL be able to reconstruct which agents participated, what passed between
them, and what authoritative data the outcome relied on.

**Rationale.** Regulated divisions must answer this to auditors and regulators.
Reconstruction from participating platforms' own logs is not achievable —
several will be unable to demonstrate they took part at all — so the record has
to be assembled at the seams, where the organisation controls it.

**Priority.** Must

**Success measure.** Internal Audit reconstructs a completed multi-division
interaction end to end from the retained record alone, without vendor
assistance.

**Owner.** Internal Audit; Data Protection Officer.

---

### BR-306 — Each caller has its own identity, scoped and revocable

**Statement.** Every calling seam SHALL present a distinct service identity,
scoped to only what that seam requires, and revocable without affecting other
callers.

**Rationale.** A shared identity across seams makes attribution impossible,
least privilege unachievable, and revocation an outage. It also tends to accrue
the union of every caller's permissions, which then looks like over-granting but
cannot safely be reduced without splitting the identity first.

**Priority.** Must

**Success measure.** Each seam is attributable to its own identity in the target
platform's access records, and one identity is revoked without interrupting the
others.

**Owner.** Information Security.

---

### BR-307 — Delegation between agents is bounded

**Statement.** Agent-to-agent delegation SHALL be limited in depth and SHALL be
prevented from forming cycles, with the limit enforced by the solution rather
than by the good behaviour of participating agents.

**Rationale.** An estate wired in both directions between every pair makes
circular delegation possible by construction. None of the inter-agent protocols
under consideration defines time-to-live or maximum-forwards semantics, so
nothing in the protocol layer will stop it. Unbounded delegation is a cost
incident and an availability incident simultaneously, and it is caused by
correct-looking behaviour on every individual hop.

**Priority.** Must

**Success measure.** A deliberately constructed delegation cycle is refused at
the configured depth, with the refusal recorded.

**Owner.** Information Security; Platform Operations.

---

## BR-4xx — Commercial and consumption

### BR-401 — Consumption is reported in the units vendors bill

**Statement.** Consumption SHALL be reported in the distinct categories vendors
meter and price separately, and SHALL NOT be aggregated into a single
undifferentiated figure.

**Rationale.** The categories are priced at materially different rates and
cannot be summed and multiplied by one rate. Aggregation is not a simplification
but an error, and a plausible-looking one: it produces figures that are wrong by
a large factor while triggering no alarm and appearing entirely reasonable.

**Priority.** Must

**Success measure.** Consumption reporting presents each billed category
separately, and a report is reconciled against a vendor's own figures to within
a stated tolerance.

**Owner.** Procurement / Vendor Management; Platform Operations.

---

### BR-402 — Cost is expressible per unit of business work

**Statement.** The organisation SHALL be able to determine consumption and cost
per business interaction, separately from the price per unit of consumption.

**Rationale.** The two factors move independently, and conflating them produces
the wrong procurement decision. A platform can be cheaper per unit of
consumption and more expensive per answer, because it consumes more units to
reach the same result. Only per-business-task figures are comparable across
platforms.

**Priority.** Must

**Success measure.** The same business scenario is costed across at least two
platforms, with consumption-per-task and price-per-unit reported separately.

**Owner.** Procurement / Vendor Management.

---

### BR-403 — The evaluation produces sizing inputs for commercial planning

**Statement.** The evaluation SHALL produce a sizing model that takes projected
business volumes and the organisation's own negotiated rates as inputs, and is
usable independently of the figures observed during evaluation.

**Rationale.** Evaluation-scale figures do not answer the question Procurement
asks, which is what a business volume would cost under our contracts. A model
whose rates are parameters remains useful after the evaluation ends and after
rates are renegotiated; a spreadsheet of observed totals does not.

**Priority.** Must

**Success measure.** Procurement produces a costed projection for a stated
business volume using the model and its own rate card, without reference to
evaluation totals.

**Owner.** Procurement / Vendor Management.

---

### BR-404 — Build is compared against buy on evidence

**Statement.** The evaluation SHALL produce a comparison between operating this
capability as internally-built components and procuring a platform that provides
it, based on what the evaluation actually had to build.

**Rationale.** The build-versus-buy question is the real decision, and it is
usually answered from vendor material before anyone knows what building
entails. Having built the mechanisms, the organisation can compare against a
product with a concrete inventory rather than an estimate — including mechanisms
a vendor has productised that we had to construct by hand.

**Priority.** Must

**Success measure.** A comparison matrix covering capability, operational
burden, cost shape and lock-in, in which every "build" entry corresponds to
something the evaluation actually built.

**Owner.** Group CTO; Enterprise Architecture.

---

## BR-5xx — Evidence quality and the decision

### BR-501 — Findings are evidence-grade and reproducible

**Statement.** Every published finding SHALL be reproducible from a recorded
interaction, including the exchanged content, and SHALL state the conditions
under which it was obtained.

**Rationale.** The programme's only output is its findings. A finding that
cannot be reproduced is an anecdote, and a measurement without its conditions —
warm or cold, which model, which region, what payload size — is not a
measurement. These findings will be used to justify or refuse a multi-year
investment and must withstand challenge from the vendors they concern.

**Priority.** Must

**Success measure.** Any published finding can be re-derived from the retained
record by someone who did not run it.

**Owner.** Enterprise Architecture; Group CTO.

---

### BR-502 — Capability is reported honestly, including what is mediated

**Statement.** Where a capability is achieved by the solution acting on a
platform's behalf rather than by the platform natively, this SHALL be stated
wherever that capability is reported.

**Rationale.** The distinction determines whether a result generalises. "This
platform speaks the protocol" and "we built something that speaks the protocol
on its behalf" have opposite implications for lock-in, for effort, and for what
happens when the vendor changes. Blurring them makes the estate look more
interoperable than it is, and the error is only discovered during production
migration.

**Priority.** Must

**Success measure.** Every reported capability carries an explicit native /
mediated designation, and the designations are verified against what the system
actually does rather than against configuration labels.

**Owner.** Enterprise Architecture.

---

### BR-503 — What was not established is reported as prominently as what was

**Statement.** The evaluation's outputs SHALL state explicitly which questions
were not answered, which capabilities were declared but not exercised, and which
results rest on assumption rather than measurement.

**Rationale.** Selective reporting of successes will be read as completeness,
and the resulting decision will be wrong in a direction nobody can see. This is
the single requirement most likely to be quietly dropped under delivery
pressure, which is why it is a Must with a named owner rather than a principle.

**Priority.** Must

**Success measure.** Published outputs contain an explicit not-established
section, and each claimed capability is marked as exercised or merely declared.

**Owner.** Group CTO.

---

### BR-504 — The programme terminates in a decision

**Statement.** The evaluation SHALL conclude with a documented recommendation,
and the environment SHALL be decommissioned or deliberately promoted on that
decision rather than continuing by default.

**Rationale.** Evaluation environments that work acquire dependents, and an
instrument that has quietly become production infrastructure is the worst of
both — carrying neither production's operational rigour nor the freedom to be
torn down. The exit is a requirement precisely because it is the one nobody
enforces.

**Priority.** Must

**Success measure.** A recommendation is issued, a decision is recorded against
it, and the environment's disposition follows the decision within an agreed
period.

**Owner.** Group CTO.

---

### BR-505 — "Do not proceed" is an available conclusion

**Statement.** The evaluation SHALL be structured such that a recommendation
against proceeding is a legitimate outcome, supported by the same evidence
standard as a recommendation to proceed.

**Rationale.** An evaluation that can only conclude in favour of building is not
an evaluation, and everyone reading its output knows it. Establishing this at
the outset is what makes the eventual recommendation credible — including to the
divisions being asked to consent.

**Priority.** Must

**Success measure.** The success criteria for the evaluation are defined
independently of the answer, and at least one pre-registered condition under
which the recommendation would be negative is stated before evaluation begins.

**Owner.** Group CTO.
