# Generation Plan

How this requirements set is being produced, in what order, and how it will be
judged finished.

## Objectives

**O1 — A complete requirements suite.** Business case through acceptance
criteria, at the standard a delivery lead would hand to a team. Explicitly
including the sections that get skipped in practice: data residency and privacy,
cost and sizing, build-vs-buy, and verification.

**O2 — A specification a coding agent can build from unattended.** `50-system/`
and `60-cost/` are to be lifted into a fresh empty project as the sole input,
and the resulting build measured against the acceptance criteria written here.
Ambiguity in this set is the variable being tested, so ambiguity must be
deliberate rather than accidental.

**O3 — Three defensible business framings.** The same system, justified for an
enterprise, for a vendor field organisation, and for an individual practitioner.
Not three sales pitches — three honest answers, including where the answer is
"probably not worth it".

## Method

The system is specified **forward** ("the system shall…"), as if being
commissioned. It is *derived* backward, from a working implementation of the
same system: an architecture record, a decision log, measured protocol results,
an interoperability matrix, and observability and cost notes.

That derivation gives this set an advantage a normal requirements document does
not have, and one hazard.

- **The advantage:** every non-functional number can be a *measured* number
  rather than a guess — timeout budgets, cold-start behaviour, protocol version
  incompatibilities, cross-platform log join rates. Where a figure is measured
  it is labelled measured.
- **The hazard:** hindsight leaks. A requirement written after seeing the
  implementation tends to describe the implementation rather than the need, which
  would make O2 meaningless — a coding agent handed a disguised design document
  is not building from requirements. Editorial rule R3 below exists to counter
  this.

## Editorial rules

**R1 — Forward voice.** `50-system/` states obligations, not observations. "The
system shall record the raw request and response bytes of every inter-agent
hop", never "the system records…" or "as built, the system records…".

**R2 — Self-contained.** No requirement's meaning may depend on a document
outside `requirements-docs/`. Constraints that originated elsewhere are restated
in full. Provenance lives only in the severable appendix.

**R3 — Requirement, not design.** Each requirement states *what must be true and
why*, and leaves *how* to the builder wherever the how is genuinely free. Where
the how is genuinely constrained — an external protocol, a regulation, a
platform's actual behaviour — the constraint is stated as a constraint and its
source named. The test: if a competent team could reasonably satisfy the
requirement a different way than the reference implementation did, the
requirement must not foreclose it.

**R4 — Label evidence.** Every quantitative claim carries one of: **measured**
(observed in a real run), **modelled** (derived arithmetically from measured
inputs), or **assumed** (a planning figure with no evidence behind it). No
unlabelled numbers.

**R5 — Publication.** This directory is committed to a public repository. No
cloud account identifiers, project identifiers, subscription or tenant names,
SSO profile names, or organisation identifiers. Regions only, where a region is
material to latency or residency. Cost documents use unit rates and modelled
projections; no actual spend totals.

**R6 — No unattributed real-world entities.** The enterprise in Lens A is
fictional and composite. It is not a real company and is not named after one.

## The subject organisation (Lens A)

Defined once, in `01-conventions.md`, so it can be renamed with a single
find-and-replace: **Meridiaan Group** — a fictional multinational in
professional information and compliance software, dual-headquartered in
Amsterdam and New York, with five divisions of differing technical autonomy.

The heterogeneous agent estate is not a hypothetical in this framing. It is the
*given*: divisions procured their platforms at different times, under different
leadership, some through acquisition. No division will be asked to abandon its
platform. Interoperability is therefore the only available answer, which is what
makes the requirements set worth writing.

## Document inventory

Status: `todo` → `draft` → `written`. Nothing here is `reviewed` until the whole
set is read end to end for contradiction.

| # | Document | Status |
|---|---|---|
| — | `README.md` | written |
| — | `00-plan.md` (this file) | written |
| — | `01-conventions.md` | written |
| 10 | `10-context/01-glossary.md` | todo |
| 10 | `10-context/02-stakeholders-and-personas.md` | todo |
| 10 | `10-context/03-scope-and-system-context.md` | todo |
| 20 | `20-lens-a-enterprise/01-executive-overview.md` | todo |
| 20 | `20-lens-a-enterprise/02-business-requirements.md` | todo |
| 20 | `20-lens-a-enterprise/03-value-hypotheses-and-measures.md` | todo |
| 30 | `30-lens-b-salesforce-field/01-executive-overview.md` | todo |
| 30 | `30-lens-b-salesforce-field/02-business-case-and-roi.md` | todo |
| 40 | `40-lens-c-lab-learning/01-executive-overview.md` | todo |
| 40 | `40-lens-c-lab-learning/02-learning-objectives.md` | todo |
| 40 | `40-lens-c-lab-learning/03-build-vs-buy.md` | todo |
| 50 | `50-system/01-functional-requirements.md` | todo |
| 50 | `50-system/02-use-cases-and-stories.md` | todo |
| 50 | `50-system/03-nonfunctional-requirements.md` | todo |
| 50 | `50-system/04-technical-architecture-requirements.md` | todo |
| 50 | `50-system/05-interoperability-requirements.md` | todo |
| 50 | `50-system/06-data-and-privacy-requirements.md` | todo |
| 50 | `50-system/07-security-and-identity-requirements.md` | todo |
| 50 | `50-system/08-observability-requirements.md` | todo |
| 50 | `50-system/09-acceptance-and-verification.md` | todo |
| 60 | `60-cost/01-cost-model-and-projection.md` | todo |
| 60 | `60-cost/02-sizing-framework.md` | todo |
| 70 | `70-delivery/01-delivery-plan.md` | todo |
| 70 | `70-delivery/02-risks-assumptions-dependencies.md` | todo |
| 90 | `90-traceability/01-traceability-matrix.md` | todo |
| 90 | `90-traceability/02-provenance.md` (severable) | todo |

## Sequence

Context before lenses, lenses before system, system before cost, everything
before traceability.

1. **Context** — glossary, stakeholders, scope. The system boundary has to be
   settled before any requirement can be scoped to it.
2. **Lens A** — the enterprise. First, because its regulatory obligations
   generate system requirements the other two lenses never would.
3. **Lenses B and C** — field enablement and learning instrument. Neither adds
   system requirements; both add success measures. Lens C adds build-vs-buy.
4. **System specification** — functional and use cases first, then
   non-functional and technical, then the three cross-cutting sets
   (interoperability, data/privacy, security/identity), then observability.
5. **Cost** — after the system spec, because sizing depends on the component
   inventory the spec settles.
6. **Delivery and RAID** — phasing depends on the full requirement set.
7. **Traceability** — last, mechanically, and it is where contradictions surface.

## Open questions

Carried here rather than guessed at. None currently block progress.

- **Q1 — Renaming.** Is *Meridiaan Group* acceptable, or is a different fictional
  name preferred? Single find-and-replace either way; not worth blocking on.
- **Q2 — Divisional platform mapping.** The five divisions are assigned agent
  platforms to create a genuinely heterogeneous estate. That mapping is a
  modelling choice and is open to revision if a different one is more
  representative of the enterprises worth arguing about.
- **Q3 — Autonomous-build cut line.** O2 lifts `50-system/` and `60-cost/`. Open
  whether `10-context/` should travel too. It makes the spec more readable and
  the build more likely to succeed, which arguably weakens the measurement.

## Done criteria

1. Every document in the inventory is `written`.
2. Every requirement has an ID, a priority, a rationale, and a verification
   method. No orphans in either direction: every system requirement traces up to
   at least one business requirement, and every business requirement traces down
   to at least one system requirement or is explicitly marked *out of scope for
   this release*.
3. Every quantitative claim carries a measured / modelled / assumed label (R4).
4. The set is read end to end and contradictions are resolved — the traceability
   matrix is the instrument, not a formality.
5. `50-system/` + `60-cost/` are checked once for R2 violations by reading them
   in isolation, as a fresh reader would.
