# Requirements Documentation Set

A standard software-engineering requirements suite for an **agent-to-agent (A2A)
interoperability evaluation environment**, written as if the system did not yet
exist.

This directory is a **side experiment**, deliberately self-contained. It does not
describe the repository it currently sits in, it does not import that
repository's conventions, and nothing outside this directory depends on it.

## Why it exists

Two goals, and they pull in the same direction:

1. **Specification practice.** Produce the requirements set a delivery lead would
   hand a team tasked with building this system — business case through
   acceptance criteria — including the parts that usually get skipped: data
   residency, cost modelling, build-vs-buy, and how anyone would know it worked.

2. **An input for autonomous build.** The `50-system/` specification is written
   to be lifted into a **fresh, empty project** and handed to a coding agent, to
   measure how much of the system can be built unattended from requirements
   alone. That goal sets a hard editorial rule — see *Self-containment* below.

## The three lenses

The same system is justified three different ways, because three different
readers ask for three different things. Business framing is written once per
lens; the **system specification is written once, shared**.

| Lens | Reader | Question it answers |
|---|---|---|
| **A — Enterprise** (`20-lens-a-enterprise/`) | A multinational with five semi-autonomous divisions | Should we invest in agent interoperability, and what would it be worth? |
| **B — Field enablement** (`30-lens-b-salesforce-field/`) | A vendor field organisation | Does having this de-risk customer interop conversations enough to pay for itself? |
| **C — Learning instrument** (`40-lens-c-lab-learning/`) | An individual practitioner | What do these protocols actually do, what does observing them cost to build, and would a platform have done it for me? |

Lens A supplies most of the *system* requirements, because it is the only lens
with regulatory obligations. Lens C supplies the build-vs-buy evaluation. Lens B
supplies the ROI model.

## Layout

```
00-plan.md                  How this set is being generated, and its done criteria
01-conventions.md           Requirement ID scheme, voice, priority, verification
10-context/                 Glossary, stakeholders, scope and system context
20-lens-a-enterprise/       Enterprise executive overview and business requirements
30-lens-b-salesforce-field/ Field enablement business case and ROI
40-lens-c-lab-learning/     Learning objectives and build-vs-buy evaluation
50-system/                  THE BUILDABLE SPEC — functional through acceptance
60-cost/                    Cost model and projection; figure-free sizing framework
70-delivery/                Delivery plan and phasing; risks, assumptions, dependencies
90-traceability/            Requirements traceability; severable provenance appendix
```

## Self-containment

`50-system/` and `60-cost/` **must stand alone**. A reader — or a coding agent —
with nothing but those directories has to be able to build and cost the system.

Concretely: no requirement's meaning may depend on a document outside this
directory. Where a constraint originated elsewhere, the constraint is *restated
in full here*, not referenced. This is a deliberate departure from the host
repository's citation conventions, and it is confined to this directory.

Provenance is not lost, only separated: `90-traceability/02-provenance.md` maps
requirements back to their origins and is marked **severable** — delete it and
the specification is still complete.

## Status

**Complete first pass.** All 28 documents written; see `00-plan.md` for the
inventory.

| | |
|---|---|
| Business requirements | 28, each with a named owner |
| System requirements | 188 across seven classes, 175 Must / 13 Should |
| Use cases / user stories | 8 / 25 |
| Acceptance criteria | 48 across eight capability gates |
| Upward traceability | 188 of 188 — no orphans |

Not yet `reviewed`: the set has been checked for traceability gaps
(`90-traceability/01-traceability-matrix.md` §5 records the two found and
closed) but not yet read end to end for contradiction between documents. That is
the remaining step before it is handed to a build.
