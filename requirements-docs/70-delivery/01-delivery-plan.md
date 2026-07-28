# Delivery Plan and Phasing

## Shape of the plan

Eight phases, each ending in a gate with a decision. The programme is
**terminable at every gate** — that is the point of the structure, not a
formality. An evaluation that can only run to completion cannot produce a
negative recommendation (BR-505), because by the time it could, the sunk cost has
made the recommendation unsayable.

Two sequencing principles govern the order:

**Fixed cost first, breadth second.** The mechanisms that do not scale with
platform count — identity, delegation control, correlation, record capture — are
built before the second platform arrives. They are required regardless, and
building them under the pressure of a second integration produces the version
that only works for that integration.

**Cheap questions before expensive ones.** Anything that could terminate the
programme is answered as early as its cost allows. Access, participation and
regulatory feasibility are all cheap to test and all capable of ending it.

Durations are deliberately absent — they depend on team size and organisational
pace, and a duration written here would be quoted as a commitment. Each phase
states its *exit criteria*, which is the property that actually determines
whether it is done.

---

## Phase 0 — Feasibility and access

**Objective.** Establish that the programme can happen at all, before building
anything.

**Work.**
- Confirm participation in principle from all five divisions, and secure the hub
  division explicitly.
- Establish that each division can issue a scoped service identity.
- Obtain entitlement to the commercial comparison platform, in a region
  satisfying residency constraints.
- Obtain DPO agreement in principle on the intended approach to minimisation,
  residency and retention.
- Take baselines for the value hypotheses — current cycle time on cross-divisional
  questions, current interop-open rate. **These cannot be reconstructed later.**

**Exit criteria.** Hub participation secured. Every division has agreed in
principle. DPO has no blocking objection to the approach. Baselines recorded.

**Gate decision.** Proceed, or stop. Two pre-registered stop conditions live
here: the hub declining, and a regulatory position making the estate's regulated
work unreachable.

**Why first.** Every item is cheap, and three of them can end the programme. The
entitlement request in particular may depend on people outside the programme
acting on their own timescale — starting it here and discovering the answer in
Phase 7 is how the build-versus-buy comparison fails to happen.

---

## Phase 1 — Foundation and loopback

**Objective.** A working system with no external platform involved.

**Work.**
- Canonical model and both seams.
- All three protocol classes, inbound and outbound, using reference
  implementations.
- Deterministic agent and the full loopback matrix.
- Record capture at every hop, including for library-mediated protocols.
- Delegation control with depth enforcement, on the enforced path.
- Fault injection for every classified failure.
- Configuration-driven routing.

**Exit criteria.** AC-101 through AC-104, AC-201 through AC-205, AC-301,
AC-302, AC-304, AC-601, AC-605, AC-606 pass — all with no external credentials
present.

**Gate decision.** Proceed to first platform.

**Why this much before any platform.** The loopback matrix is what makes every
later failure interpretable. Without it, the first live integration failure is
ambiguous between the system's plumbing and the platform, and that ambiguity is
resolved by guessing. Fault injection is here for the same reason — partial
failure behaviour built after the fact is built to match whatever the platform
happened to do.

---

## Phase 2 — First platform pair

**Objective.** One real cross-platform interaction, end to end, including the
hub.

**Work.**
- Onboard the hub division, including whichever mediation its constraints
  require.
- Onboard one further division.
- Per-seam service identities, proven by exercise rather than by configuration.
- Boundary data rules for this seam.
- First scenario: grounding one division's answer in the hub's system of record.
- Measure the platform action budget — the binding external constraint (NFR-103).

**Exit criteria.** AC-501 through AC-503, AC-509, AC-401, AC-402, AC-602 pass. A
real cross-divisional question is answered with attribution. The action budget is
measured and the timeout chain is documented against it.

**Gate decision.** Proceed to breadth. Reconsider if the measured budget makes
synchronous cross-divisional work unviable — which would shift the programme's
emphasis toward asynchronous patterns rather than ending it.

**Why the hub is first.** It is the estate's most constrained participant and its
most important one. If mediation for the hub proves unworkable, that is known
before four other integrations have been built around an assumption that it works.

---

## Phase 3 — Estate breadth

**Objective.** All five divisions participating, and the protocol matrix filled.

**Work.**
- Onboard the remaining three divisions.
- Protocol generation translation where generations diverge.
- Correlation survival measured per platform; establish the channel that survives
  every one.
- Asynchronous support classified per platform in three states.
- Record onboarding effort per division, in order — the sublinearity measurement.
- Platform record retrieval per platform.

**Exit criteria.** All five divisions participate. AC-105, AC-303, AC-604 pass.
Protocol matrix populated with honest native-or-mediated designations. Asynchronous
support classified per platform. No already-onboarded division deployed a change.

**Gate decision.** Proceed to fan-out.

**Why breadth before fan-out.** Fan-out findings depend on width. At two or three
platforms the observability degradation that fan-out exists to reveal is still
hidden, because a narrow topology is correlatable by hand.

---

## Phase 4 — Fan-out and the observability findings

**Objective.** The 1:many shape, and the join-rate measurement it exists to
produce.

**Work.**
- Concurrent decomposition with per-leg timeouts and partial-failure semantics.
- Both orchestrator placements, on the same task.
- Coverage statements and attributed recombination.
- Join rate measured at two, three and four participants, failures classified
  structural or fixable.
- Observability coverage reported per platform, including absences.

**Exit criteria.** AC-204, AC-603 pass. Join rate measured at each width. Both
orchestrator placements compared on the same task with their partial-failure
behaviour recorded.

**Gate decision.** Proceed to compliance.

**Why here.** This phase produces the findings most likely to change the
architecture recommendation, and it needs the estate's full width to produce them
honestly.

---

## Phase 5 — Compliance under real constraints

**Objective.** Establish whether the estate's regulated work is reachable.

**Work.**
- Inference location recorded per interaction; undetermined blocked for
  constrained data.
- Residency-aware routing.
- Special-category confinement, and the derived-conclusion collaboration pattern.
- Retention enforcement, subject location, and erasure preserving structural
  reconstruction.
- Per-seam data-flow reporting derived from traffic.
- DPO review of the produced reports.

**Exit criteria.** AC-403 through AC-407 pass. DPO accepts the data-flow record
as derived from traffic. The health-division scenario either completes within
confinement, or its obstruction is documented specifically.

**Gate decision.** Proceed, or stop. A pre-registered stop condition lives here:
compliance not achievable for the divisions that matter.

**Why not earlier.** Boundary rules require real seams and real traffic to be
meaningful. Enforcing minimisation against loopback traffic verifies the
mechanism and establishes nothing about the obligation.

---

## Phase 6 — Cost and unit economics

**Objective.** Populate the cost model with measured figures.

**Work.**
- Consumption per shape, per platform, per billed category.
- Reconciliation against each provider's own figures.
- Cold and warm compute cost per component.
- Build and operate effort recorded per mechanism.
- Projection to business volumes; sizing framework exercised with a real rate
  card.

**Exit criteria.** Unit economics measured for every scenario in the library.
AC-604, AC-706 pass. At least one reconciliation completed per provider. A
projection produced for a stated business volume with its assumptions recorded.

**Gate decision.** Proceed, or stop. Pre-registered stop condition: per-task cost
at projected volume exceeds the value of the answers.

**Why after breadth and fan-out.** Unit economics require the shapes to exist.
Measuring cost on a single interaction shape produces a figure that will be
generalised to shapes it does not describe.

---

## Phase 7 — Build versus buy

**Objective.** The capstone comparison.

**Work.**
- Component inventory finalised from what was actually built, with operational
  burden per entry.
- Comparison against the commercial platform across the six dimensions.
- Convergence candidates tested and verified against current public
  documentation or live measurement.
- Independent review by someone who did not build the build side.

**Exit criteria.** Comparison complete across all six dimensions with every
product claim cited and dated — or documented as documentation-based only, if
entitlement was not obtained. Independent review completed.

**Gate decision.** Proceed to recommendation. Pre-registered condition: buying
dominates, in which case the recommendation is to buy and the evaluation has
still returned its cost.

---

## Phase 8 — Recommendation and disposition

**Objective.** Terminate in a decision.

**Work.**
- Findings consolidated with conditions and evidence classifications.
- Not-established register finalised and published alongside.
- Value hypotheses resolved against their measures.
- Recommendation issued with evidence attached, addressing each pre-registered
  negative condition explicitly — including where none were met.
- Findings exported and verified readable with the system stopped.
- Disposition executed: decommission or deliberate promotion.

**Exit criteria.** Definition of done (section 4 of
`50-system/09-acceptance-and-verification.md`) satisfied in full. A decision is
recorded. Disposition follows within the agreed period.

---

## Phase summary

| Phase | Produces | Can terminate the programme |
|---|---|---|
| 0 — Feasibility and access | Participation, entitlement, baselines | **Yes** — hub declines; regulatory position |
| 1 — Foundation and loopback | A verifiable system, no platforms | No |
| 2 — First pair | First real interaction; the binding budget | Reshapes rather than terminates |
| 3 — Breadth | Five divisions; the protocol matrix | No |
| 4 — Fan-out | Join rate; orchestration comparison | No |
| 5 — Compliance | Regulatory feasibility | **Yes** — compliance unreachable |
| 6 — Cost | Unit economics; projections | **Yes** — costs do not scale |
| 7 — Build vs buy | The comparison | **Yes** — buying dominates |
| 8 — Recommendation | The decision | Terminates by design |

## Cross-cutting work

Continuous rather than phased. Each is the kind of work that is deferred under
pressure and then never done.

| Work | Cadence | Why it cannot be deferred |
|---|---|---|
| Findings recorded with conditions and evidence class | At the moment of observation | Conditions are not reconstructable afterwards |
| Not-established register maintained | Continuous | Assembled at the end, it will be incomplete — nobody remembers what they did not do |
| Credential rotation | Per platform schedule | A lapse is a hard stop |
| Re-verification of findings before external use | Per use | Findings decay silently |
| Onboarding effort recorded | Per onboarding | The sublinearity measurement is unreconstructable |
| Deployed-version verification | Per deployment | A build that never shipped reports success |

## Sequencing risks

| Risk | Consequence | Handling |
|---|---|---|
| Entitlement deferred to Phase 7 | The comparison never happens; the capstone is lost | Requested in Phase 0, tracked as a dependency from day one |
| Baselines not taken in Phase 0 | Two of the largest claimed benefits become permanently unmeasurable | Explicit Phase 0 exit criterion |
| Fixed mechanisms deferred to fit around the first platform | They are built to serve that platform and rebuilt at the second | Phase 1 gate requires them before any platform |
| Compliance deferred to the end | Discovering the regulated work is unreachable after everything is built | Phase 5 gate; feasibility tested in principle at Phase 0 |
| Fan-out attempted at two platforms | The observability finding is invisible; the wrong conclusion is drawn confidently | Phase 4 gated on Phase 3 breadth |
| Findings written up at the end | Conditions lost; the register incomplete; the durable asset degraded | Cross-cutting, recorded at observation |
