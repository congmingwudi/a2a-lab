# Value Hypotheses and Measures — Meridiaan Group

## Why hypotheses rather than a benefits case

A conventional benefits case asserts what the investment will deliver and then
looks for evidence supporting it. That structure cannot produce a negative
recommendation, which BR-505 requires to remain available.

So the value case is written as **falsifiable hypotheses**. Each states what is
believed, how it will be measured, what result would support it, and — the part
that makes it honest — **what result would disconfirm it**. Disconfirmation is
recorded as a finding of equal standing.

Everything below is currently `[assumed]`. Converting these to `[measured]` is
the programme's actual output.

## Where value could come from

Six channels. Not all will materialise, and two are avoided-cost rather than
benefit, which makes them harder to bank and easier to overstate.

| # | Channel | Type | Confidence before evaluation |
|---|---|---|---|
| V1 | Cycle time on cross-divisional questions | Benefit | Moderate |
| V2 | Avoided duplicate integration effort between division pairs | Avoided cost | Moderate |
| V3 | Fewer decisions made on stale or replicated data | Risk reduction | Low — hard to attribute |
| V4 | Preserved optionality; no forced consolidation | Strategic | Moderate |
| V5 | Compliance established by design rather than remediated later | Avoided cost | Low — counterfactual |
| V6 | A better-informed build-versus-buy decision | Decision quality | High — this one is nearly certain |

V6 is worth noting: it accrues **whichever way the evaluation concludes**. Even a
negative recommendation returns the cost of the evaluation if it prevents an
ill-founded programme.

---

## H1 — Cross-divisional questions are answerable in a fraction of the current time

**Claim.** Questions today requiring a human intermediary between divisions can
be answered by agent delegation within an interactive window.

**Why we believe it.** The delay is coordination, not analysis. The owning
division's data is available; the cost is finding someone to ask.

**Measure.** Elapsed time from question to attributed answer, for a scenario
representative of current practice, compared against the current process
baseline.

**Baseline.** To be established by observation before build, per division pair.
*[assumed: currently measured in hours to days, dominated by human handoff]*

**Supports the hypothesis.** Interactive answers within the stated business
window (BR-205) for at least three division pairs.

**Disconfirms it.** Answers achievable only asynchronously, or the timeout chain
caps synchronous depth below what a useful answer requires. This would not kill
the programme but would move its value from V1 to V6 and reshape the
recommendation toward asynchronous patterns.

---

## H2 — Concurrent decomposition materially beats sequential consultation

**Claim.** Decomposing a business event across divisions concurrently completes
in materially less time than consulting them in sequence, and the gap widens
with the number of divisions involved.

**Why we believe it.** The sub-questions are genuinely independent; nothing
forces ordering.

**Measure.** Elapsed time for the same multi-division scenario executed
concurrently versus sequentially, at two, three and four divisions.

**Supports the hypothesis.** Concurrent elapsed time approximates the slowest
single leg rather than the sum, and the advantage grows with width.

**Disconfirms it.** Coordination overhead, cold starts, or per-leg variance
erode the gain — most plausibly if one leg's cold start dominates, in which case
the finding is about scale-to-zero economics rather than about decomposition.

---

## H3 — Standardising the seam makes onboarding cost sublinear

**Claim.** With a defined seam contract, the effort to add the *n*th division
falls rather than staying constant, and imposes no work on existing
participants.

**Why we believe it.** Per-pair integration is quadratic; a shared seam contract
is linear at worst. The estate is the argument: five divisions is ten pairs.

**Measure.** Effort to onboard each division, in engineer-days, recorded in
order. Plus: changes required from already-onboarded divisions per new
participant — target zero (BR-104).

**Supports the hypothesis.** Onboarding effort declines across the sequence and
no already-onboarded division deploys a change.

**Disconfirms it.** Each division requires comparable bespoke effort — which
would mean the seam is not actually standard, and would substantially weaken V2.

**Note.** The first division is not evidence either way; it pays for the seam
itself.

---

## H4 — Protocol choice affects capability, not merely style

**Claim.** The choice of inter-agent protocol has material consequences for what
is achievable — correlation, session semantics, asynchrony, discovery — rather
than being an interchangeable transport preference.

**Why we believe it.** The protocols model the remote party differently: as an
endpoint, as a callable tool, or as an agent with a task lifecycle. Those are
not the same abstraction.

**Measure.** Per protocol, per platform pair: which of correlation propagation,
session continuity, asynchronous completion, discovery, and structured failure
are achievable without mediation.

**Supports the hypothesis.** Capability differences appear that are attributable
to the protocol rather than to implementation quality.

**Disconfirms it.** All three protocols prove equivalent in practice, making
protocol choice a matter of convenience — which would be a genuinely useful
finding, simplifying every future integration decision.

---

## H5 — Cross-platform observability degrades as the topology widens, and does so silently

**Claim.** As more platforms participate in a single interaction, the proportion
that can demonstrate their participation **from their own execution records**
falls — and nothing signals the loss.

**Why we believe it.** Each platform's record is designed for its own interior
view. Correlation identifiers are dropped by at least one platform on every
path. A one-to-one interaction hides this because two participants can be
correlated by hand.

**Measure.** *Join rate* — of the platforms participating in a completed
interaction, the fraction tied back to it from their own logs. Measured at two,
three and four participants.

**Supports the hypothesis.** Join rate falls as participants increase, while
every platform reports success and returns a good answer.

**Disconfirms it.** Join rate holds near complete at four participants.

**This is a risk hypothesis.** Confirmation is bad news, and it is the finding
most likely to change the architecture — it would make seam-side recording
(BR-305) non-negotiable rather than merely prudent, and it bears directly on
build-versus-buy (BR-404).

---

## H6 — Useful collaboration is achievable within the health division's confinement

**Claim.** The health division can participate in cross-divisional work without
special-category data leaving its confinement, by exchanging derived
non-identifying conclusions rather than underlying data.

**Why we believe it.** Most cross-divisional questions need a *conclusion*, not
the record behind it. "Is there a contractual exposure here" does not require
the clinical detail that produced the assessment.

**Measure.** For each health-division scenario: does it complete within
confinement, and is the answer still useful to the requesting division's
business owner?

**Supports the hypothesis.** Scenarios complete within confinement with answers
judged useful by their requesting business owner.

**Disconfirms it.** The derived conclusion is too thin to act on, or the
requesting division cannot verify it without the underlying data. Either
outcome is a **first-class finding**: it would establish that this class of
collaboration is not available to Meridiaan under current obligations, which is
worth knowing before it is designed into a programme.

---

## H7 — Cost per business task varies more across platforms than price per unit does

**Claim.** Platforms differ more in how much consumption they expend reaching an
answer than in what they charge per unit of consumption — so unit price is a
poor basis for platform comparison.

**Why we believe it.** Consumption per task is driven by reasoning depth, tool
use and context handling, which vary substantially. Published unit rates are
converging under competition.

**Measure.** For an identical business scenario across platforms: consumption
per task by billed category, and price per unit. Compare the spread of each.

**Supports the hypothesis.** Spread in consumption-per-task materially exceeds
spread in price-per-unit, and at least one platform is cheaper per unit while
more expensive per answer.

**Disconfirms it.** Consumption per task is comparable across platforms, making
unit price the dominant factor and considerably simplifying procurement.

---

## H8 — The mechanisms required are extensive enough that buying is competitive

**Claim.** Operating this capability at estate scale requires enough
purpose-built machinery — identity per seam, delegation bounds, protocol
translation, correlation, redaction enforcement, consumption accounting — that a
platform providing it is commercially competitive with building and maintaining
it.

**Why we believe it.** The requirement set already implies most of that
inventory before a single scenario has run.

**Measure.** The concrete inventory of components the evaluation had to build,
with operational burden, against the capability set and cost shape of available
platforms.

**Supports the hypothesis.** The built inventory is substantial, its ongoing
operational burden is non-trivial, and available platforms cover most of it.

**Disconfirms it.** The inventory turns out small and stable, or available
platforms cover only the easy parts and leave the estate-specific work — the
regulatory constraints most of all — unaddressed.

**Note.** This hypothesis is the one most exposed to motivated reasoning in
either direction. Its evidence is the component inventory, which is a matter of
record rather than judgement, and that is deliberate.

---

## Pre-registered conditions for a negative recommendation

Required by BR-505, and stated **before** evaluation begins so the standard
cannot be adjusted to fit the result. Any one of these would support
recommending against proceeding:

1. **The hub will not participate.** The division holding the customer system of
   record declines, or its platform cannot be reached within its vendor's
   supported surface. Most cross-divisional value is unreachable without it.
2. **Compliance is not achievable.** H6 is disconfirmed *and* residency
   constraints (BR-303) cannot be met for the divisions that matter — leaving
   the estate's regulated work outside the capability.
3. **Costs do not scale.** H7 resolves such that per-task cost at projected
   business volume exceeds the value of the answers, under the organisation's
   own rate card.
4. **Buying dominates.** H8 is strongly supported and an available platform
   covers the estate's requirements, including the regulatory ones — in which
   case the recommendation is to buy, and the evaluation has still paid for
   itself through V6.
5. **The evidence will not hold.** BR-501 cannot be satisfied — findings are not
   reproducible, or too many results rest on mediated capability misreported as
   native (BR-502) — meaning the evaluation cannot support any recommendation at
   the standard required.

## Benefits explicitly not claimed

Recorded so their absence is visible, and so nobody adds them later without
evidence.

- **Headcount reduction.** No requirement targets it, no scenario measures it,
  and claiming it without measurement would poison divisional consent — which is
  the programme's binding constraint.
- **Improved answer quality from any specific model.** Model selection is out of
  scope (X3). The programme compares interoperability, not intelligence.
- **Reduced platform licensing cost.** The programme explicitly does not
  consolidate platforms (BR-101), so it cannot save their cost. It may inform a
  future decision that does.
- **Faster agent development within divisions.** Plausible, but nothing in scope
  addresses divisional development practice, and it would not be attributable.
