# Requirements Traceability Matrix

Generated from the `Traces to` field of every system requirement, then read for
what it reveals. The matrix is an **instrument, not a formality** — it is
constructed to find gaps, and one that finds none has usually been constructed to
agree with itself.

## 1. Coverage summary

| Class | Count | Home document |
|---|---|---|
| `FR-` Functional | 42 | `50-system/01-functional-requirements.md` |
| `NFR-` Non-functional | 32 | `50-system/03-nonfunctional-requirements.md` |
| `TR-` Technical / architecture | 21 | `50-system/04-technical-architecture-requirements.md` |
| `IR-` Interoperability | 21 | `50-system/05-interoperability-requirements.md` |
| `DR-` Data and privacy | 22 | `50-system/06-data-and-privacy-requirements.md` |
| `SR-` Security and identity | 26 | `50-system/07-security-and-identity-requirements.md` |
| `OR-` Observability | 24 | `50-system/08-observability-requirements.md` |
| **Total** | **188** | |

| Priority | Count |
|---|---|
| Must | 175 |
| Should | 13 |

**Upward coverage: 188 of 188.** Every system requirement traces to at least one
business requirement. No orphans.

The absence of *Could* and *Won't* entries is worth noting rather than
celebrating. It means deferrable capability was excluded at the scope boundary
(`10-context/03-scope-and-system-context.md` §4) rather than carried as
low-priority requirements — a legitimate approach, and it means the ten scope
exclusions carry the weight a *Won't* list normally would. A reader looking for
what was deliberately left out should read the exclusions, not this table.

## 2. Business requirement → system requirements

| Business requirement | Downstream | Satisfied by |
|---|---|---|
| BR-101 | 0 | *(none — see §4)* |
| BR-102 | 12 | `FR-104`, `FR-201`, `FR-202`, `FR-302`, `FR-304`, `IR-302`, `IR-501`, `IR-502`, `IR-503`, `TR-101`, `TR-402`, `TR-403` |
| BR-103 | 18 | `FR-101`, `FR-102`, `FR-103`, `FR-201`, `FR-202`, `FR-203`, `FR-301`, `FR-602`, `FR-603`, `IR-101`, `IR-102`, `IR-204`, `IR-401`, `NFR-401`, `NFR-502`, `TR-101`, `TR-102`, `TR-104` |
| BR-104 | 8 | `FR-102`, `FR-304`, `NFR-202`, `NFR-501`, `NFR-502`, `TR-103`, `TR-201`, `TR-401` |
| BR-105 | 5 | `DR-601`, `FR-401`, `SR-301`, `SR-302`, `SR-303` |
| BR-201 | 3 | `FR-301`, `FR-302`, `NFR-106` |
| BR-202 | 5 | `FR-501`, `FR-506`, `NFR-105`, `NFR-301`, `TR-106` |
| BR-203 | 3 | `FR-208`, `FR-503`, `FR-507` |
| BR-204 | 13 | `FR-206`, `FR-502`, `FR-503`, `FR-504`, `FR-505`, `IR-601`, `IR-602`, `IR-603`, `NFR-102`, `NFR-203`, `OR-501`, `OR-502`, `TR-503` |
| BR-205 | 11 | `FR-205`, `FR-501`, `FR-502`, `FR-607`, `NFR-101`, `NFR-102`, `NFR-103`, `NFR-104`, `NFR-105`, `OR-503`, `TR-203` |
| BR-206 | 7 | `FR-207`, `IR-402`, `IR-403`, `IR-404`, `NFR-107`, `NFR-108`, `TR-204` |
| BR-207 | 1 | `FR-208` |
| BR-301 | 15 | `DR-101`, `DR-102`, `DR-103`, `DR-501`, `DR-502`, `DR-503`, `DR-504`, `DR-601`, `DR-602`, `DR-603`, `NFR-302`, `SR-204`, `SR-302`, `SR-603`, `TR-302` |
| BR-302 | 6 | `DR-201`, `DR-202`, `DR-203`, `DR-204`, `DR-205`, `TR-105` |
| BR-303 | 9 | `DR-301`, `DR-302`, `DR-303`, `DR-304`, `DR-403`, `DR-603`, `TR-301`, `TR-302`, `TR-304` |
| BR-304 | 4 | `DR-401`, `DR-402`, `DR-403`, `TR-304` |
| BR-305 | 29 | `DR-102`, `DR-205`, `DR-302`, `DR-504`, `FR-305`, `FR-401`, `FR-402`, `FR-404`, `FR-507`, `FR-604`, `IR-201`, `IR-202`, `IR-203`, `IR-303`, `NFR-205`, `NFR-302`, `NFR-701`, `NFR-702`, `OR-101`, `OR-104`, `OR-201`, `OR-202`, `OR-203`, `OR-301`, `OR-303`, `SR-303`, `SR-601`, `SR-603`, `TR-105` |
| BR-306 | 32 | `FR-204`, `NFR-204`, `NFR-402`, `NFR-404`, `NFR-405`, `NFR-503`, `NFR-703`, `SR-101`, `SR-102`, `SR-103`, `SR-104`, `SR-201`, `SR-202`, `SR-203`, `SR-204`, `SR-301`, `SR-401`, `SR-402`, `SR-403`, `SR-404`, `SR-405`, `SR-501`, `SR-502`, `SR-503`, `SR-504`, `SR-505`, `SR-601`, `SR-602`, `SR-604`, `TR-105`, `TR-205`, `TR-303` |
| BR-307 | 11 | `DR-602`, `FR-305`, `FR-401`, `FR-402`, `FR-403`, `FR-404`, `FR-405`, `IR-203`, `IR-303`, `SR-304`, `TR-105` |
| BR-401 | 5 | `OR-301`, `OR-401`, `OR-402`, `OR-405`, `OR-406` |
| BR-402 | 3 | `FR-605`, `OR-403`, `OR-404` |
| BR-403 | 1 | `OR-406` |
| BR-404 | 7 | `FR-506`, `NFR-106`, `OR-303`, `OR-305`, `OR-602`, `TR-106`, `TR-202` |
| BR-501 | 46 | `DR-502`, `FR-103`, `FR-105`, `FR-203`, `FR-206`, `FR-506`, `FR-601`, `FR-602`, `FR-603`, `FR-604`, `FR-605`, `FR-606`, `FR-607`, `FR-701`, `FR-702`, `FR-704`, `FR-705`, `IR-102`, `IR-103`, `IR-304`, `IR-601`, `IR-603`, `NFR-103`, `NFR-104`, `NFR-205`, `NFR-303`, `NFR-401`, `NFR-403`, `NFR-406`, `NFR-601`, `NFR-603`, `NFR-701`, `NFR-702`, `OR-101`, `OR-102`, `OR-103`, `OR-104`, `OR-201`, `OR-203`, `OR-302`, `OR-405`, `OR-502`, `TR-204`, `TR-501`, `TR-502`, `TR-503` |
| BR-502 | 13 | `FR-207`, `FR-303`, `FR-703`, `IR-202`, `IR-301`, `IR-403`, `IR-404`, `NFR-108`, `NFR-602`, `OR-304`, `OR-305`, `OR-601`, `OR-603` |
| BR-503 | 12 | `DR-202`, `DR-303`, `FR-702`, `FR-703`, `IR-402`, `NFR-201`, `NFR-602`, `OR-303`, `OR-304`, `OR-305`, `OR-601`, `OR-602` |
| BR-504 | 3 | `FR-704`, `FR-705`, `NFR-504` |
| BR-505 | 0 | *(none — see §4)* |

## 3. What the distribution reveals

The counts are not evenly spread, and the shape is informative.

**BR-501 (evidence-grade findings) draws 46 requirements — a quarter of the
specification.** That is correct rather than excessive: the programme's only
output is findings, so reproducibility, capture fidelity, honest labelling and
testability all serve it. It does mean BR-501 is the requirement whose failure
would invalidate the most work, and it is worth watching for that reason.

**BR-306 (per-caller scoped identity) and BR-305 (reconstructable decisions) draw
32 and 29.** Both are cross-cutting obligations touching every seam, so wide
fan-out is expected. The concentration confirms that identity and auditability
are architectural concerns rather than features.

**Four business requirements draw three or fewer**, and each was checked
individually rather than assumed thin-but-fine:

| BR | Downstream | Assessment |
|---|---|---|
| BR-207 — results reach existing systems | 1 | **Genuinely thin.** Priority is *Should*; a single requirement is proportionate, but this is the entry most likely to need expansion if the capability is promoted |
| BR-403 — sizing inputs for commercial planning | 1 | Correct. Mostly satisfied by `60-cost/02-sizing-framework.md`, which is a deliverable rather than a system behaviour |
| BR-402 — cost per unit of business work | 3 | Correct. Depends on OR-401's category separation, which traces to BR-401 instead |
| BR-504 — programme terminates in a decision | 3 | Correct. Largely a programme obligation; the system's part is only that findings outlive it |

## 4. Business requirements with no downstream requirement

Two, both examined rather than accepted.

### BR-101 — Divisional platform autonomy is preserved

**Not a gap.** This is a *negative* requirement: it constrains what the
specification may demand, not what the system must do. It is satisfied by the
absence of any requirement obliging a division to change platform, and
structurally reinforced by TR-103 and NFR-501, which confine every platform's
integration to its own unit.

**Verified by inspection of the whole set**, not by any single requirement — the
check is that no requirement anywhere demands a divisional change. That check has
been performed and passes.

### BR-505 — "Do not proceed" is an available conclusion

**Not a gap.** This is a programme governance obligation, discharged by the
pre-registered stop conditions in
`20-lens-a-enterprise/03-value-hypotheses-and-measures.md` and by the phase gates
in `70-delivery/01-delivery-plan.md`. No system behaviour can satisfy it, and a
requirement invented to give it a downstream link would be a fiction manufactured
to make this table look complete.

## 5. Gaps found and closed

Recorded because the value of a traceability exercise is what it catches, and
because a matrix presented without its findings looks like it was generated after
the fact to confirm a conclusion.

| Finding | Resolution |
|---|---|
| **BR-207 had no system requirement.** Delivery of results into an existing business system was described in UC-04 and covered by no requirement — the use case had a step nothing implemented | **FR-208 added**, covering delivery with attribution and correlation, and retention with retry when the destination is unavailable |
| **TR-201, TR-205, SR-204, SR-502 had no upward trace.** Each was a real requirement whose `Traces to` field was omitted | Trace fields added: TR-201 → BR-104; TR-205, SR-204, SR-502 → BR-306 (SR-204 also → BR-301) |

The first is the more instructive. A use-case step with no requirement behind it
is the classic traceability finding, and it was invisible from either document
read alone — the use case looked complete because the step was described, and the
requirement set looked complete because nothing referenced the missing capability.

## 5a. Contradictions found and closed

A separate pass, read across documents rather than within them. Six findings.

| # | Contradiction | Resolution |
|---|---|---|
| 1 | **SR-201 required every exposed endpoint to authenticate**, while FR-105 required an *unauthenticated* liveness surface and SR-503/IR-503 required anonymous discovery. Three requirements directly contradicted one | SR-201 now carries a **closed, enumerated** exempt set of three endpoints, each with its reason and its constraining requirements. Adding a fourth is a change to the requirement, not a configuration decision |
| 2 | **OR-101 required payloads recorded "as transmitted"** and AC-601 required them "byte-identical to what traversed the network", while SR-405, NFR-703 and DR-205 require credential and boundary-excluded content to be removed *before* writing. As written, unsatisfiable | OR-101 now states fidelity **except at redaction markers**, names redaction as the sole permitted divergence, and forbids every other transformation. AC-601 restated; AC-607 added to verify the removals on the same payload |
| 3 | **26 Must requirements were verified by Inspection alone**, against a rule stated in `01-conventions.md` §2 and restated in `09-acceptance-and-verification.md` §1 | 14 upgraded to Test or Analysis where the property is genuinely checkable; 11 retained as Inspection under a newly-defined **exception class 3** (documentation and artefact presence), each carrying a `Verification note`. TR-302 resolved separately under finding 4 |
| 4 | **TR-302 forbade persisting another division's data**, while OR-101 requires recording payloads that routinely *are* that data | TR-302 rewritten to constrain **access shape** rather than content: records addressable by interaction, never indexed or queryable by business entity. The DR-503 subject-location obligation is named as the single scoped exception |
| 5 | **X5 excluded "streaming"** while SR-604 required streaming surfaces to authenticate out of band | X5 narrowed to streaming *between agents*, explicitly not the operator surface's continuous update |
| 6 | **FR-606's "no external credentials"** was readable as exempting loopback from SR-202's fail-closed authentication | Scope note added: no *external platform* credentials; the system's own inbound authentication still applies. There is no authentication-optional mode |

Findings 1, 2 and 4 are the material ones — each was a pair of Must requirements
that could not both be satisfied, and none was visible from reading either
document on its own. Finding 3 is the set contradicting its own stated rule,
which is the failure mode a conventions document exists to prevent and is
therefore the most embarrassing of the six.

## 6. Downward coverage — use cases and stories

Every `UC-` and `US-` entry in `50-system/02-use-cases-and-stories.md` carries a
`Traces to` or `Satisfied by` reference. Several stories reference a requirement
*class* rather than an individual identifier where the whole class applies —
US-301 pointing at `DR-` is an example. That is deliberate: enumerating twenty-two
data requirements against one story adds no information and decays on the first
renumbering.

## 7. Maintenance

This matrix is **generated, not maintained by hand.** It is derived from the
`Traces to` fields in the specification, which are the authority; if this document
and a requirement disagree, the requirement is correct and this document is stale.

Regenerate and re-read whenever a requirement is added, removed, or re-scoped.
Two checks matter more than the table itself:

1. **Orphans** — any system requirement with no upward trace. Either it serves an
   unwritten business need, or it should not exist.
2. **Empty business requirements** — any with no downstream. Either a capability
   is missing, or the requirement is a programme obligation rather than a system
   one and should say so explicitly, as BR-101 and BR-505 now do.
