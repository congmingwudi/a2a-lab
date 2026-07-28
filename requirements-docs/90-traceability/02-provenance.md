# Provenance Appendix

> **SEVERABLE.** This document is not part of the specification. Delete it and
> `50-system/` and `60-cost/` remain complete and self-contained — that is the
> test rule R2 exists to guarantee, and this file is the proof it was needed.
>
> It exists for one reader: someone asking *where did this requirement actually
> come from, and how much should I trust it?*

## 1. Why the specification does not cite its sources

Ordinarily a requirement that cites its origin is a better requirement. This set
deliberately does the opposite, for a reason specific to its purpose.

`50-system/` and `60-cost/` are to be lifted into a **fresh project** as the sole
input for a build. In that project, a citation to a decision record or a results
document resolves to nothing. Worse, it advertises that an authoritative source
exists somewhere and is unavailable — which invites a builder to either guess at
what it said or to treat the requirement as provisional.

So every constraint is restated in full in the specification, and provenance is
isolated here.

## 2. The underlying work

This requirements set was reverse-engineered from a working implementation: a
cross-platform agent interoperability lab connecting five agent platforms across
four clouds over three protocol classes, with wire-level recording, built over
several months and documented as it went.

That gives the specification an unusual property and an unusual hazard.

- **The property.** Constraints here are not speculative. The failure modes
  described actually occurred, and the non-functional requirements are shaped by
  what was actually measured rather than by what seemed reasonable.
- **The hazard.** A single estate is a sample of one. Requirements generalised
  from it may encode that estate's accidents as though they were properties of
  the problem. Section 5 identifies where this risk is highest.

## 3. Provenance map

Requirement groups against the decisions and findings that produced them.
References are to the originating repository's decision log (`D<n>` in
`plan/00-decisions.md`) and planning documents.

### Structural

| Requirements | Origin |
|---|---|
| TR-101, TR-102 — two seams over a canonical model | The lab's founding architectural decision; `plan/01-architecture.md` |
| TR-103, NFR-501 — platform integrations self-contained | The platform plugin convention; `plan/01-architecture.md` |
| FR-301, FR-302, TR-104 — bridge and shim as opposites | **D8** — bridge for outbound, shims for inbound |
| TR-501, TR-502 — deterministic agent, separable live tests | The loopback suite proving all client×server pairings before any external platform |

### Trace and observability

| Requirements | Origin |
|---|---|
| OR-101 — raw wire bytes at every hop | **D7** — wire visibility as a core requirement, not an add-on |
| OR-102 — capture where envelopes live inside libraries | The ASGI wiretap: two protocols' envelopes are constructed inside frameworks, so handler-level capture records a reconstruction |
| TR-301 — pluggable record storage | **D13**, **D19** — pluggable sinks; local and cloud stores differ |
| OR-302 — deterministic extraction, interpretation above | **D22** — deterministic ETL below, agent analysis above |
| OR-202, OR-203 — platform-native identifiers, platform-initiated legs | `plan/05-observability.md`; the platform reference stamped at emit time |
| OR-303, OR-304 — join rate; structural vs fixable | **WS8** fan-out — the measured result that of four participating platforms only one could be joined from its own logs, with causes differing in kind |

### Delegation

| Requirements | Origin |
|---|---|
| FR-401 through FR-405, BR-307 — the delegation guard | **D27** — standard rider plus depth limit at every seam, because a fully-wired estate makes cycles possible by construction and no protocol defines TTL |
| FR-402, IR-203 — context on a channel measured to survive | **D34** — the text-level rider, adopted after structured channels were each found to be dropped by at least one platform |

### Protocol and interoperability

| Requirements | Origin |
|---|---|
| IR-102 — the concept mapping table | `plan/01-architecture.md` protocol mapping rules; the finding that conversation identity is first-class in exactly one protocol |
| IR-301 through IR-303, TR-403 — generations and translation | The measured version wall: one platform requiring the newer generation and rejecting the older default, another speaking the older dialect and rejecting a pure newer-generation description |
| IR-402 through IR-404, NFR-107, NFR-108 — asynchrony | **D47** — fire-then-poll. The lifecycle had been implemented and driven synchronously for months because one optional field was never set; and on a suspending runtime, polling is the compute |
| FR-303, BR-502 — native versus mediated labelling | `plan/02-matrix.md` — the honest matrix, where every cell is labelled by how the capability is actually achieved |
| IR-603 — success with absent content | Measured twice, a day apart: a platform exceeding its action budget returning success with its delegated section present and empty, and a fan-out returning a short brief with a success code |

### Identity and security

| Requirements | Origin |
|---|---|
| SR-101 through SR-103 — identity per seam, scoped, proven by exercise | **D37** — the shared application's grant was the *union* of four callers' needs; least privilege required splitting the identity, not editing the scope list |
| SR-103 specifically | The finding that an application can authenticate perfectly and still be refused by the interface it needs, presenting as not-found rather than as an authorisation error |
| SR-401, NFR-405 — one human login, everything else a service identity | **D39** |
| SR-203, SR-604 — no credentials in URLs, streaming out of band | **D36** — query-string credentials removed; streaming reworked so no token rides a URL |
| SR-504, SR-505, NFR-503 — no environment identifiers, checked at the serving edge | **D43** — identifiers assembled at runtime from configuration survived a source scrub and were being served anyway. *A repo scrub is not a boundary* |
| SR-404 — inventory secret-bearing files outside the store | **D45** — the credential store covered one file; twenty others needed a different answer |
| NFR-403 — a build step owns shipping its artefact | **D46** — four distinct ways an artefact was built and never deployed, every one reporting success |
| NFR-404, TR-205 — prove the deployment target first | The preflight guard, shipped *with* the identifier scrub rather than after it |

### Consumption and cost

| Requirements | Origin |
|---|---|
| OR-401, CST-202 — categories reported separately | **D44** — treating the uncached remainder as total input reported 120K tokens for a day that processed 4.42M: a 36× understatement that raised no error and looked plausible |
| OR-404, OR-405 — configured attribution, corrections on read | The finding that neither of two coding-agent tools emits anything naming project or repository, that the values are unvalidated free text, and that the metric store cannot delete datapoints |
| OR-406 — modelled at list price | The standing labelling rule on every cost figure |
| CST-301 — the unit is a business task | The two-factor framing: consumption per unit of work, held separately from price per unit |

### Performance

| Requirements | Origin |
|---|---|
| NFR-102, NFR-103 — nested timeout chain, external binding constraint | The measured platform action budget, which proved substantially longer than had been assumed for months — and the discovery that the assumption had been load-bearing |
| NFR-104, FR-607, TR-203 — cold and warm measured separately | **D32** — warm-up coverage and the serverless split: what colds and what does not |
| TR-204 — no workstation on the runtime path | The hosting workstream that removed the laptop from the runtime path |

### Scope exclusions

| Exclusion | Origin |
|---|---|
| X5 — streaming | **D11** — scoped as one capability comparison and never built. The servers advertise streaming and the client disables it, which is itself the source of OR-601's declared-but-unexercised requirement |
| X3 — model quality | Consistently out of scope; the lab compares interoperability |

### Lens material

| Document | Origin |
|---|---|
| `40-lens-c-lab-learning/03-build-vs-buy.md` | **WS10** — the managed agent fabric comparison, including its access gating and the convergence hypothesis |
| `20-lens-a-enterprise/03-value-hypotheses-and-measures.md` H5 | The join-rate finding, generalised into a hypothesis |
| `60-cost/` | **WS12** and the consumption framing note |

## 4. Findings that became requirements

The highest-value transfers — each a specific observed failure that became a
general obligation. These are the requirements most worth trusting, because each
was paid for.

| Observed | Requirement it produced |
|---|---|
| A complete asynchronous implementation driven synchronously for months, because one optional field defaulted to off, with everything working throughout | IR-402 — asynchrony must be *exercised*, not merely implemented; OR-601 — declared-but-unexercised marking |
| A 36× consumption understatement that raised no error and looked entirely plausible | OR-401 — categories never aggregated; CST-202 |
| A capability reached through the lab's own translation layer that could have been reported as platform-native | FR-303, BR-502 — mediated labelling wherever reported |
| Identifiers assembled at runtime from configuration, served publicly after being removed from source | SR-505 — check at the serving edge, because a source scrub is not a boundary |
| A permission that existed as a comment rather than as a policy; a migration whose only caller lacked the privilege to run it | NFR-403 — a build step owns shipping what it produces |
| Structured correlation channels each dropped by at least one platform | IR-202, IR-203 — survival measured, not assumed |
| A shared identity whose grant looked bloated but was the union of four callers' genuine needs | SR-102 — least privilege as an identity-modelling problem, not a permission-configuration one |
| Three of four platforms unable to demonstrate participation while all returned success | OR-303, OR-304 — join rate as a first-class metric with classified failures |

## 5. Where generalisation is weakest

Stated plainly, because the specification's forward voice gives every requirement
the same apparent authority and these do not deserve it equally.

| Requirement group | Weakness |
|---|---|
| NFR-1xx budgets | Derived from one estate's platforms. The *shape* — that an external action budget binds, that cold and warm must be separated — generalises. The values do not, which is why NFR-103 forbids assuming them |
| IR-3xx generation translation | Reflects the generations in use at one point in time. The requirement that neither side negotiates is durable; the specific dialect differences are not |
| OR-303 join rate | Measured on four platforms in one estate. That it degrades with width is plausibly general; the rate itself is not transferable |
| CST-202 relative rates | Provider-specific and changing. The requirement to keep categories separate is durable; the ordering is not |
| SR-103 identity prerequisites | The specific prerequisite that motivated it is one platform's behaviour. The general requirement — prove capability by exercise, not by configuration inspection — is sound |
| The five-division estate model | A composite, not an observation. The requirements it generates are plausible rather than measured, and the regulatory ones in particular should be reviewed by a practitioner before being relied upon |

The last row is the most important. Everything derived from the **lab** is
grounded in something that happened. Everything derived from **Meridiaan Group**
is a modelling choice, and its regulatory requirements are written by an
engineer reasoning about obligations, not by a data protection practitioner.
They are structurally sound and should not be treated as legal advice.

## 6. What was deliberately not carried across

| Not carried | Why |
|---|---|
| Component, module and file names | R3 — the specification states requirements, not the reference implementation's design |
| Vendor and product names in `50-system/` | Naming a product in a requirement converts it into a procurement decision |
| Environment identifiers of any kind | Publication rules; and they were never in the source to carry |
| Specific measured values as requirements | R4 — carried as measurement *obligations* with the values to be established, since another estate's numbers will differ |
| The lab's own sequencing and workstream structure | Its order reflected one team's circumstances; the delivery plan is derived from dependency, not from history |
| Local working notes cited in the source material | Not present in the originating repository and not readable by anyone; citing them would send a reader after a file that does not exist |
