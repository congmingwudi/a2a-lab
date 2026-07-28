# Executive Overview — Field Enablement Lens

**Audience.** A vendor field organisation: solution engineering, field
architecture, and the leaders who fund their enablement.
**Purpose.** Whether a working cross-platform interoperability lab is worth
building and maintaining as a field asset.

> **Standing.** This lens is written from the perspective of an individual field
> practitioner. It is not an official position of any organisation, it makes no
> commitment on any vendor's behalf, and every product capability it references
> must be verified against that vendor's current public documentation before
> use with a customer. Product roadmaps move faster than documents.

---

## The problem this lens addresses

Enterprise customers no longer ask whether an agent platform works. They ask
whether it works **with the four other agent platforms they already run**.

That question is asked in nearly every serious architecture conversation, and
the field is poorly equipped to answer it, for reasons that have nothing to do
with competence:

- **Every vendor claims interoperability, in incomparable terms.** Support for a
  protocol is asserted at the level of the protocol's name. Whether two
  implementations of it actually interoperate is a different question, and it is
  not answered anywhere in anyone's documentation.
- **The honest answer is nuanced, and nuance without evidence sounds evasive.**
  "It depends on which generation of the protocol their platform speaks" is
  correct and, delivered without something to show, indistinguishable from
  hedging.
- **The failure modes are non-obvious.** Two platforms both correctly
  implementing a protocol at incompatible generations, with neither able to
  negotiate. Correlation identifiers silently dropped. A response that returns
  success with a section empty. None of these are anticipated in a whiteboard
  conversation; all of them appear in the first integration sprint.
- **Discovery questions go unasked.** A field architect who has not hit these
  problems does not know to ask about them, so they surface after commitment.

The consequence is a slow, expensive failure mode: the architecture conversation
stalls, an interop risk is parked as an open question, and the decision defers.
Nothing is lost visibly. The opportunity simply takes longer, or resolves toward
whoever answered the question more concretely.

## The proposed asset

The same evaluation environment specified in `50-system/`, operated as a field
asset rather than as a customer programme: five agent platforms across four
clouds, wired to one another over multiple inter-agent protocols, with every
exchange recorded at the wire level and a set of measured findings maintained
alongside.

What makes it a field asset rather than a demo is the recorded evidence. A demo
shows a thing working. This shows **what works, what does not, and what it cost
to find out** — including named failures with the wire payloads attached.

## Why the failures are the valuable part

The instinct is to build a lab that demonstrates success. That instinct is
wrong here, and the reasoning is worth stating plainly.

A customer architect has been told by four vendors that everything
interoperates. They do not believe any of them, correctly. The fastest available
route to credibility is being the first person in the process to tell them
something that does not work — with evidence, and without being asked.

That has three effects a success demo does not:

1. **It establishes that the rest of the account is calibrated.** A source that
   reports its own limitations gets believed about its capabilities.
2. **It moves the conversation from claims to constraints**, which is where
   architecture decisions are actually made and where a prepared party has the
   advantage.
3. **It surfaces integration risk before commitment rather than after** — which
   is worth more to the customer than to the vendor, and is precisely why it
   builds trust.

The corollary is a constraint on the asset: **it must never overstate.** A
finding presented as measured that turns out to be a lab artefact costs more
credibility than the asset ever generated. This is why BR-501 through BR-503 —
reproducibility, native-versus-mediated honesty, and explicit statement of what
was not established — matter as much in this lens as in the enterprise one.

## What the field organisation gets

| Capability | What it replaces |
|---|---|
| A concrete, evidenced answer to "does this work with our estate" | An assurance the customer has already heard from four vendors |
| A named inventory of interoperability failure modes | Discovering them during the customer's first integration sprint |
| Discovery questions derived from real failures | A generic questionnaire |
| Measured latency, timeout and cost envelopes | Estimates presented as guidance |
| A build-versus-buy comparison grounded in what building actually took | Vendor-supplied comparison material |
| A reusable technical narrative for architecture conversations | Each architect assembling their own |

The last row is where the leverage is. The asset is built once and used by many;
its value scales with how many conversations it reaches, not with how impressive
any single demonstration is.

## What it is not

Stated because each of these is a way the asset degrades into something worse
than nothing.

- **Not a product commitment.** It demonstrates what protocols and platforms do
  today. It implies no roadmap and creates no obligation.
- **Not a customer deliverable.** It is not deployed into customer environments,
  not supported, and not a reference implementation to be handed over.
- **Not a competitive teardown.** Findings describe protocol and platform
  behaviour reproducibly. A finding that cannot be reproduced by the vendor it
  concerns is not publishable, and one framed as disparagement is not usable.
- **Not a substitute for the product's own documentation.** Where the two
  disagree, the documentation governs and the lab has a bug.

## Principal risks

| Risk | Consequence | Mitigation |
|---|---|---|
| Findings drift out of date as platforms change | A confidently-stated finding is wrong in front of a customer | Every finding carries its observation date and conditions; re-verification before external use is a standing obligation, not a courtesy |
| The lab is mistaken for official guidance | Commitments implied that nobody made | Standing notice on every artefact; no capability claim without a citation to public vendor documentation |
| Over-claiming from a lab artefact | Credibility loss exceeding all value delivered | Native-versus-mediated labelling (BR-502) and explicit not-established reporting (BR-503) |
| It becomes a support burden | Maintenance cost overtakes enablement value | Scoped as an evaluation instrument with an owner and a review point, never as shared infrastructure |
| Single-maintainer dependency | Asset decays when that person moves on | Findings and scenarios are documented artefacts; the asset's value is the record, not the running system |

That last risk is the most likely to materialise and the least likely to be
planned for. It is also the one that shapes the requirement set: **the durable
asset is the recorded evidence, not the running environment.** A lab that has to
be running to be useful has a much shorter useful life than one whose findings
stand on their own.

## The ask

Modest, and mostly time rather than money. The commercial model, the ROI
structure and its sensitivities are in `02-business-case-and-roi.md`; the
running cost shape is in `60-cost/`.
