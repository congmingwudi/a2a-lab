# Build versus Buy — the Capstone Evaluation

> **Verification obligation.** This document specifies *how* to conduct the
> comparison. It deliberately asserts **no capability claim about any commercial
> product**. Every cell in the resulting matrix must be filled from the vendor's
> current public documentation or from a reproducible measurement against a live
> entitlement, cited at the point of use. Product capabilities change faster
> than documents, and a stale capability claim in a comparison matrix is the
> most damaging error this evaluation can make — it discredits the accurate
> cells alongside the wrong one.

## 1. The question, and why this direction is privileged

> Given what operating cross-platform agent interoperability actually requires,
> is it better to build and maintain those mechanisms, or to procure a platform
> that provides them?

Almost everyone answers this from the buy side: read the vendor's material,
compare it against an *estimate* of what building would take, decide. The
estimate is the weak term, and it is almost always wrong in the same direction —
the connective tissue is invisible until you write it.

Approaching from the build side inverts the weak term. The inventory of what
building requires is a **matter of record** rather than judgement, because it
has been built. The comparison becomes a product's documented capabilities
against a known quantity of work, which is a materially better question than two
sets of claims.

The privilege is real but partial, and the bias runs the other way: having built
something creates attachment to it, and an inventory assembled by its author
will tend to look more impressive than it is. Section 7 exists to counter that.

## 2. The build inventory

Derived from the requirement set, independently of any product. This is the
"build" column, and it is the document's primary contribution.

Each entry is classified:

- **Must build** — no protocol in the class defines it, and no general
  off-the-shelf component provides it.
- **Adopt** — a standard implementation exists and was adopted rather than
  written.
- **Build because estate-specific** — could be bought generically, but the
  organisation's constraints make the generic version insufficient.

| # | Mechanism | Class | Ongoing burden |
|---|---|---|---|
| M1 | Protocol server surfaces per protocol class | Adopt (reference implementations exist) | Low — tracks upstream libraries |
| M2 | Protocol client per protocol class | Adopt | Low |
| M3 | Canonical request/response model shared across protocols | Must build | Low once stable |
| M4 | Outbound mediation for platforms with a single call shape (bridge) | Must build | Moderate — changes with routing |
| M5 | Inbound mediation for closed platforms (shim) | Must build | **High** — tracks a vendor API not designed for it |
| M6 | Protocol generation/dialect translation | Must build | **High** — bilateral, changes when either side moves |
| M7 | Correlation propagation surviving every hop | Must build | Moderate |
| M8 | Delegation bounds: depth, cycle prevention, refusal | Must build | Low once stable |
| M9 | Caller identity per seam, scoped and revocable | Build because estate-specific | **High** — rotation across many platforms |
| M10 | Redaction and minimisation enforced at the boundary | Build because estate-specific | High — regulatory drift |
| M11 | Residency-aware routing of inference | Build because estate-specific | Moderate |
| M12 | Wire-level trace capture with credential scrubbing | Must build | Moderate |
| M13 | Platform execution-record harvest and join | Must build | **High** — one integration per platform, each changing independently |
| M14 | Consumption accounting in separately-billed categories | Must build | Moderate — repricing and new categories |
| M15 | Fan-out orchestration with partial-failure semantics | Must build | Moderate |
| M16 | Scenario execution and comparison surface | Must build | Moderate |
| M17 | Loopback verification path independent of external platforms | Must build | Low |

Three observations shape the comparison more than the inventory's length:

**The ongoing burden is concentrated in the integration surfaces** — M5, M6, M9,
M13. All four track things owned by someone else and changing without notice.
Build cost is paid once; these are paid forever, and they are the entries most
likely to be under-weighted by whoever built them.

**Several entries are estate-specific by nature.** M9, M10 and M11 encode
Meridiaan's regulatory obligations. A product may provide the *mechanism* while
leaving the *policy* to be expressed — and if expressing the policy is most of
the work, the mechanism's availability matters less than a feature comparison
suggests.

**M3 and M17 are cheap and disproportionately valuable.** A shared canonical
model and a loopback path that proves every protocol pairing with no external
platform cost little and make everything else testable. Products rarely
advertise an equivalent, and a comparison should ask about it explicitly rather
than assume its absence.

## 3. The convergence hypothesis

**Hypothesis.** A mature managed agent orchestration platform will have
independently productised several mechanisms in section 2 — arriving at
equivalent solutions because the underlying problems are properties of the
protocol class rather than of any particular estate.

**Why it matters more than a feature comparison.** Independent convergence is
evidence that a mechanism is *necessary* rather than incidental. If a commercial
platform and an unaffiliated practitioner both built a compatibility translation
layer, a caller-identity model and a delegation-ordering mechanism, then those
are not implementation preferences — they are what the problem requires. That
conclusion is useful **whichever way the buy decision goes**, and it is the most
transferable output of the whole exercise.

**Candidate convergence points** — to be *tested*, not asserted. Each is a
mechanism from section 2 whose problem is general enough that any platform in
this space is likely to have confronted it:

| Build-side mechanism | Category to look for in a product | Verification status |
|---|---|---|
| M7 — correlation surviving every hop | Any mechanism decorating inter-agent messages to carry context the transport drops | **Unverified** |
| M6 — protocol generation translation | Any transcoding or version-mediation capability | **Unverified** |
| M9 — per-caller scoped identity | Attribute- or policy-based access control on agent and tool invocation | **Unverified** |
| M15 — deterministic ordering and partial-failure handling in fan-out | Declarative orchestration with defined node ordering and failure semantics | **Unverified** |

Every row reads **Unverified** by design. They become verified only by an entry
in section 7's log, and the hypothesis is confirmed or disconfirmed by counting
them — not by asserting them.

**Named comparison target.** MuleSoft Agent Fabric is the intended subject, as a
representative of the managed agent orchestration and governance class. No
capability of it is claimed anywhere in this document. If entitlement cannot be
obtained (section 6), the comparison is conducted against publicly documented
capabilities only, and labelled as documentation-based throughout.

## 4. Comparison criteria

A capability checklist alone favours the product, because products are described
in capability terms and built systems are not. These six dimensions are weighted
to prevent that.

| # | Dimension | Question | Why it is decisive |
|---|---|---|---|
| D1 | Capability coverage | Which of M1–M17 does the product provide? | The obvious dimension, and the least discriminating — most products cover the easy middle |
| D2 | Policy expressiveness | Can the estate's actual regulatory constraints be expressed, or only generic ones? | Where M9–M11 are decided. A mechanism that cannot express the policy has not solved the problem |
| D3 | Operational burden transfer | Which of the high-burden entries (M5, M6, M9, M13) genuinely transfer to the vendor? | The recurring cost is the real cost; a capability that still requires estate-side maintenance transfers little |
| D4 | Openness at the edges | What happens for a platform the product does not support? Can it be extended, or is it a hard boundary? | The estate is defined by heterogeneity. A product covering four of five platforms leaves the fifth needing everything anyway |
| D5 | Cost shape | Fixed versus consumption-driven; how it scales with platforms, interactions and volume | A shape mismatched to the workload dominates the total regardless of unit rates |
| D6 | Lock-in and exit | If the product is removed in three years, what remains? Is the estate's interoperability expressed portably? | Buying an interoperability layer to avoid platform lock-in, and acquiring lock-in to the interoperability layer, is a real and common outcome |

D4 and D6 are the dimensions most often omitted, and each can invert the
decision on its own.

## 5. Decision framework

Not a scoring formula. Scores hide the reasoning and can be tuned to any
conclusion.

**Buy is indicated when:** the product covers most of the high-burden entries
(D3), the estate's policies are expressible in its model (D2), unsupported
platforms can be extended rather than excluded (D4), and the cost shape matches
the workload (D5).

**Build is indicated when:** the estate-specific entries (M9–M11) dominate the
work and the product's policy model cannot express them; or coverage stops
precisely at the platforms that make the estate difficult; or the cost shape
assumes a production volume an evaluation will never reach.

**Hybrid is indicated — and is the most likely outcome** when the product covers
the general mechanisms (M6, M7, M15) while the estate retains the policy layer
(M9–M11) and the honest-reporting layer (M12–M14). Hybrid should be evaluated
explicitly rather than emerging as a fallback, because the seam between the two
halves is itself a mechanism somebody has to own.

**Neither is indicated when** the evaluation shows the interoperability problem
is not worth solving at all for this estate — the volume of genuine
cross-divisional agent work does not justify either path. This must remain
available (BR-505), and it is the outcome a build-side author is least likely to
reach unaided.

## 6. Access gating

The comparison depends on obtaining a working entitlement to the commercial
product, in a region satisfying the estate's residency constraints.

**Establish entitlement first, before any comparison work is scheduled.** It may
require action by people outside the programme and on their timescale. Scheduling
the comparison last while leaving the access request until then is the standard
way this evaluation fails to happen — the work is ready and the door is shut.

If entitlement cannot be obtained, the comparison proceeds against public
documentation only and is **labelled documentation-based throughout**. That is a
legitimate and clearly-weaker result; presenting it as though it were measured
would be a BR-502 violation in a different costume.

## 7. Verification protocol

Mandatory before any comparison claim is published. This section exists because
the author built the build side, and that is a bias no amount of good intention
corrects.

1. **Cite at the point of use.** Every product capability claim carries a
   citation to current public documentation or to a reproducible measurement,
   inline. Uncited claims are removed rather than softened.
2. **Date every claim.** Capability claims carry an observation date. An undated
   claim is treated as expired.
3. **Distinguish documented from observed.** "The documentation states" and "the
   product was observed to" are different claims with different strength, and
   must be visibly different in the matrix.
4. **Re-verify before external use.** Findings decay. Re-verification is
   required before any customer-facing or published use, not periodically.
5. **State the build side's weaknesses.** The inventory was authored by the
   person who built it. Where an entry is over-engineered, incomplete, or would
   have been unnecessary under a different design, say so — an inventory with no
   self-criticism is not credible evidence.
6. **Have the comparison read by someone who did not build it.** The single most
   effective control, and the one most easily skipped by an individual
   practitioner working alone.

## 8. What this produces

| Output | Value | Durability |
|---|---|---|
| The build inventory (section 2) | The comparison's foundation; independently useful as a scoping checklist for any similar programme | High |
| Convergence findings (section 3) | Evidence about which mechanisms are *necessary* to the problem class | **Highest** — outlasts every product involved |
| The comparison matrix (section 4) | The decision input | Low — decays with product releases |
| The decision and its reasoning | The programme's terminal output (BR-504) | Moderate |

The ranking is deliberate and slightly counter-intuitive: **the convergence
findings outlast the comparison that produced them.** Products change; which
mechanisms the problem class demands does not. That is the finding worth
publishing, and the one that transfers to platforms that do not exist yet.
