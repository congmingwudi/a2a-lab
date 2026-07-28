# Risks, Assumptions, Issues and Dependencies

Each entry carries an owner — the person who can act on it, not the person who
noticed it. An entry without an owner is an observation.

Probability and impact are `[assumed]` and should be revised as evidence
accumulates. The ordering within each table is by exposure, highest first.

---

## Risks

### RSK-01 — The hub division declines or cannot participate

**Probability.** Low **Impact.** Critical **Owner.** Group CTO

The division holding the customer system of record is the counterparty in most
cross-divisional scenarios and is also the estate's least open platform. Without
it, the majority of the value is unreachable and most of the scenario library is
untestable.

**Mitigation.** Secured explicitly in Phase 0, before any build. A
pre-registered stop condition rather than something to be worked around — an
evaluation without the hub would measure the easy pairs and generalise from them.

---

### RSK-02 — The evaluation is not representative

**Probability.** **High** **Impact.** Critical **Owner.** Enterprise Architecture

Softening a constraint to make a scenario work — relaxing residency, using a
broader identity, substituting a mock for a platform, testing warm and reporting
without saying so — produces results that will not survive production. The danger
is that each individual softening is reasonable and defensible in the moment.

**Mitigation.** Constraints are Must requirements rather than guidelines. Any
relaxation is recorded as a finding, never absorbed. Native-versus-mediated
labelling (AC-703) and the not-established register (AC-704) are the standing
controls. This risk is why those are Must with named owners rather than
principles.

**Why the probability is high.** Not because anyone intends it — because delivery
pressure makes each individual relaxation locally rational.

---

### RSK-03 — Platform drift outpaces re-verification

**Probability.** **High** **Impact.** High **Owner.** Integration engineering

Five vendors change independently and without notice. Findings decay silently: a
measurement remains in the record looking exactly as authoritative as it did the
day it was taken.

**Mitigation.** Observation dates on every finding. Re-verification before
external use rather than periodically. A finding that fails to reproduce is
itself recorded rather than quietly corrected — the failure to reproduce is
data about platform stability.

---

### RSK-04 — Cross-platform observability proves worse than assumed

**Probability.** **High** **Impact.** Moderate **Owner.** Enterprise Architecture

Most participating platforms may be unable to demonstrate participation from
their own records, making seam-side recording the only viable audit basis.

**Mitigation.** This is hypothesis H5 rather than only a risk — confirming it is
a valuable finding. It is listed here because it changes the architecture
recommendation and strengthens the case to buy. Handled by measurement (AC-603)
rather than avoidance.

---

### RSK-05 — Findings are not externally usable

**Probability.** Moderate **Impact.** High **Owner.** Group CTO

If most results cannot be shared — reproducibility, commercial sensitivity, or
framing that reads as vendor disparagement — the enablement value evaporates
while the cost remains.

**Mitigation.** Findings written reproducibly and neutrally from the start.
Reproducibility by an outside party (AC-702) is the operative test, and it is
much cheaper to satisfy during authoring than retrospectively.

---

### RSK-06 — The environment outlives its decision

**Probability.** **High** **Impact.** Moderate **Owner.** Group CTO

Evaluation environments that work acquire dependents. The result carries neither
production's operational rigour nor the freedom to be torn down.

**Mitigation.** BR-504 makes disposition a requirement with an exit criterion.
The findings record is the durable asset (NFR-504), so decommissioning loses
nothing that matters. Explicitly not a production integration platform (X4).

---

### RSK-07 — A single maintainer becomes the dependency

**Probability.** **High** **Impact.** Moderate **Owner.** Group CTO

Deep cross-platform knowledge concentrates in whoever built it, and the
environment's operability follows them out of the door.

**Mitigation.** Reproducibility from a clean environment with one human
authentication (AC-801). Procedures verified by execution by someone who did not
write them (AC-805). Findings interpretable without the system running (AC-705).
Each of these is a Must partly for this reason.

---

### RSK-08 — Regulatory position blocks the valuable scenarios

**Probability.** Moderate **Impact.** High **Owner.** Data Protection Officer

The derived-conclusion pattern may prove too thin to be useful, or residency
constraints may exclude the platforms the constrained divisions need.

**Mitigation.** Tested in principle at Phase 0 and gated at Phase 5. A negative
outcome is a first-class finding — establishing that this collaboration is not
available under current obligations is worth knowing before it is designed into a
programme, and it is a pre-registered stop condition.

---

### RSK-09 — Commercial entitlement for the comparison is not obtained

**Probability.** Moderate **Impact.** Moderate **Owner.** Procurement

The build-versus-buy capstone depends on access to a commercial platform, in a
region satisfying residency constraints, possibly requiring action by people
outside the programme.

**Mitigation.** Requested in Phase 0 and tracked as a dependency throughout. If
not obtained, the comparison proceeds on public documentation only and is
labelled documentation-based throughout — a legitimate, clearly weaker result.

---

### RSK-10 — Costs at business volume undermine the case

**Probability.** Moderate **Impact.** High **Owner.** Procurement

Per-task cost may exceed the value of the answers at projected volume.

**Mitigation.** Measured at Phase 6 with a pre-registered stop condition. The
two-factor framing separates what is negotiable from what is a platform property,
so a negative result identifies which lever could change it.

---

### RSK-11 — Rate limits become the binding constraint

**Probability.** Low **Impact.** Moderate **Owner.** Platform Operations

Concurrent decomposition makes throttling likeliest during exactly the
interactions the evaluation cares most about.

**Mitigation.** Throttling classified as a distinct condition rather than a
failure (NFR-303), so it does not enter the record as a false finding about
platform reliability.

---

### RSK-12 — A capability is claimed on the strength of being declared

**Probability.** **High** **Impact.** Moderate **Owner.** Enterprise Architecture

A capability advertised in an agent description, or implemented but never
exercised, reads as demonstrated. It appears in a description, it is technically
true, and nothing marks it unused.

**Mitigation.** OR-601 requires declared-but-unexercised marking. The historical
pattern this guards against is specific: a complete implementation of an
asynchronous lifecycle driven synchronously for its entire life because one
optional field was never set, with everything working throughout.

---

## Assumptions

| # | Assumption | If false | Owner | Validated at |
|---|---|---|---|---|
| ASM-01 | Each division will permit a scoped service identity without granting broad platform administration | Least privilege is unachievable; SR-102 cannot be satisfied | Information Security | Phase 0 |
| ASM-02 | The closed platform's vendor interface remains generally available | Inbound mediation is unbuildable; the hub cannot be reached as a peer | Divisional architect (`CTS`) | Phase 0 |
| ASM-03 | At least two platforms expose a genuinely native agent-to-agent surface | Every A2A result measures the system's own code talking to itself; the protocol comparison loses its most important cell | Enterprise Architecture | Phase 3 |
| ASM-04 | Evaluation volumes stay below platform rate limits | RSK-11 materialises; concurrency must be throttled and fan-out findings are affected | Platform Operations | Phase 4 |
| ASM-05 | Regional model endpoints exist in the EU for every platform the confined division needs | DR-303 blocks those platforms for constrained data — a finding, not a workaround | Data Protection Officer | Phase 5 |
| ASM-06 | Model consumption dominates run cost | Cost model precision is misdirected; infrastructure sizing becomes material | Procurement | Phase 6 |
| ASM-07 | Divisions will supply realistic scenarios and judge the answers | The scenario library is synthetic and its findings do not transfer | Divisional business owners | Phase 2 |
| ASM-08 | Onboarding effort declines after the first two divisions | H3 disconfirmed; the seam is not standard, and V2 weakens substantially | Integration engineering | Phase 3 |
| ASM-09 | Published rates are obtainable per billed category per region | The cost model cannot be populated at the required granularity | Procurement | Phase 6 |
| ASM-10 | Baselines can be established before build begins | Two of the largest claimed benefits become permanently unmeasurable | Group CTO | **Phase 0 — not recoverable later** |

ASM-10 is the only assumption with no later validation opportunity. If baselines
are not taken before Phase 1, H1 and the stall-avoidance benefit cannot be
evidenced at any point afterwards.

---

## Dependencies

| # | Dependency | On whom | Needed by | Consequence of delay |
|---|---|---|---|---|
| DEP-01 | Participation agreement from the hub division | Divisional CTO (`CTS`) | Phase 0 gate | Programme cannot start |
| DEP-02 | Scoped service identity per division | Divisional architects | Phase 2 onward, per division | That division cannot be onboarded |
| DEP-03 | Secret store access under one human authentication | Information Security | Phase 1 | Credentials handled unsafely, or work stalls |
| DEP-04 | DPO agreement in principle on the approach | Data Protection Officer | Phase 0 gate | Phase 5 may invalidate work already built |
| DEP-05 | Commercial platform entitlement in a compliant region | Procurement + vendor | Phase 7, **requested Phase 0** | Capstone becomes documentation-based only |
| DEP-06 | Realistic scenarios and answer judgement | Divisional business owners | Phase 2 onward | Findings are synthetic and do not transfer |
| DEP-07 | Baseline measurements of current practice | Divisional business owners | **Phase 0 only** | H1 and stall avoidance permanently unmeasurable |
| DEP-08 | Rate card per provider per region | Procurement | Phase 6 | Cost projections cannot be produced |
| DEP-09 | Independent reviewer for the build-vs-buy comparison | Enterprise Architecture | Phase 7 | The comparison carries the build author's bias unchecked |
| DEP-10 | Corporate identity provider integration for the evaluation surface | Information Security | Phase 1 | Human access unattributable; SR-601 unsatisfied |

**DEP-05 and DEP-07 are the two that fail quietly.** Both are needed at a phase
far from where they are requested, and neither produces a visible blockage until
it is too late to fix — the entitlement because the capstone simply does not
happen, the baselines because their absence only becomes apparent when a benefit
must be evidenced.

---

## Issues

No issues are open at specification time. The register is established here so
that issues arising during delivery are recorded against the same structure
rather than in a separate artefact.

| # | Issue | Raised | Owner | Status |
|---|---|---|---|---|
| — | — | — | — | — |

---

## Review cadence

- **Risks** — reviewed at every phase gate. RSK-02 and RSK-12 reviewed
  continuously, since both materialise gradually through individually reasonable
  decisions rather than as discrete events.
- **Assumptions** — each validated at its stated phase; a falsified assumption
  triggers a gate review rather than being absorbed.
- **Dependencies** — tracked from Phase 0 regardless of when they are needed,
  because the two most consequential ones are requested far earlier than they are
  consumed.
