# Conventions

Everything structural that the rest of the set depends on: how requirements are
identified and written, how priority and verification are expressed, and the
definition of the subject organisation.

## 1. Requirement identifiers

Identifiers are **permanent**. A requirement that is withdrawn keeps its
identifier and is marked withdrawn; identifiers are never reused, because the
traceability matrix and any external build artefact will already refer to them.

| Prefix | Class | Home document |
|---|---|---|
| `BR-` | Business requirement | `20-lens-a-enterprise/02-business-requirements.md` |
| `FR-` | Functional requirement | `50-system/01-functional-requirements.md` |
| `NFR-` | Non-functional requirement | `50-system/03-nonfunctional-requirements.md` |
| `TR-` | Technical / architecture constraint | `50-system/04-technical-architecture-requirements.md` |
| `IR-` | Interoperability / protocol requirement | `50-system/05-interoperability-requirements.md` |
| `DR-` | Data and privacy requirement | `50-system/06-data-and-privacy-requirements.md` |
| `SR-` | Security and identity requirement | `50-system/07-security-and-identity-requirements.md` |
| `OR-` | Observability and operations requirement | `50-system/08-observability-requirements.md` |
| `UC-` | Use case | `50-system/02-use-cases-and-stories.md` |
| `US-` | User story | `50-system/02-use-cases-and-stories.md` |
| `AC-` | Acceptance criterion | `50-system/09-acceptance-and-verification.md` |
| `CST-` | Cost model element | `60-cost/` |
| `RSK-` / `ASM-` / `DEP-` | Risk / assumption / dependency | `70-delivery/02-risks-assumptions-dependencies.md` |

Numbers are allocated in blocks of 100 by theme within each class, so related
requirements stay adjacent as the set grows and late additions do not have to be
squeezed between existing numbers. Each home document opens with its own block
allocation table.

## 2. Anatomy of a requirement

Every requirement is written in this form. Fields are not optional; "none" is a
valid value and an informative one.

```
### FR-101 — Protocol-independent agent invocation

**Statement.** The system SHALL allow any hosted agent to be invoked over any
supported inter-agent protocol without change to that agent's implementation.

**Rationale.** The purpose of the environment is protocol comparison. If
supporting a second protocol requires touching agent code, every comparison
becomes a comparison of two implementations rather than two protocols, and the
results are not attributable to the protocol.

**Priority.** Must

**Verification.** Test — the same agent implementation is exercised over every
supported protocol against an identical request, and the responses are compared
for semantic equivalence.

**Traces to.** BR-102, BR-104

**Notes.** Constrains the agent-hosting seam only. Says nothing about whether
protocols are served in one process or many; that is TR-2xx.
```

### Keywords

**SHALL** — mandatory; the system is non-conformant without it.
**SHOULD** — strong recommendation; deviation must be recorded with a reason.
**MAY** — genuinely optional; a builder choosing not to do it is conformant.

Used in the RFC 2119 sense and capitalised only when carrying that meaning.
Prose elsewhere uses ordinary English.

### Priority (MoSCoW)

| Priority | Meaning |
|---|---|
| **Must** | Release is not usable for its stated purpose without it |
| **Should** | Materially valuable; release is viable without it but diminished |
| **Could** | Worth having if capacity allows; no plan depends on it |
| **Won't (this release)** | Explicitly deferred. Recorded so it is visibly a decision rather than an omission |

*Won't* entries are kept, not deleted. A requirements set that silently drops
things cannot be distinguished from one that forgot them.

### Verification method

| Method | Meaning |
|---|---|
| **Test** | Automated, repeatable, pass/fail without a human judgement |
| **Demonstration** | Executed and observed; outcome is evident but not automatically asserted |
| **Inspection** | Established by reading code, configuration, or output |
| **Analysis** | Established by reasoning over measurements or models rather than direct observation |

Every *Must* requirement needs a verification method stronger than Inspection,
or an explicit note explaining why it cannot have one.

## 3. Evidence labelling

Every quantitative claim carries exactly one label:

- **[measured]** — observed in a real execution. Where it matters, the
  conditions are stated alongside, because a latency without its conditions is
  not a measurement.
- **[modelled]** — derived arithmetically from measured or published inputs. The
  inputs and the arithmetic are shown or referenced.
- **[assumed]** — a planning figure with no evidence behind it. Legitimate, and
  the label is what keeps it from hardening into a fact.

An unlabelled number is a defect.

## 4. Subject organisation — Meridiaan Group

**Fictional and composite.** Not a real company, not named after one, and not a
description of any specific organisation. It exists to give the requirements a
consistent set of obligations to satisfy. Renaming it is a single
find-and-replace across this directory.

**Profile.** A multinational in professional information, compliance software
and analytics. Dual-headquartered in **Amsterdam** and **New York**, operating
across the EU, the UK, North America and APAC. Roughly two decades of growth by
acquisition, which is the reason for everything that follows.

### The five divisions

| # | Division | Code | Centre of gravity | Technical autonomy |
|---|---|---|---|---|
| 1 | Legal & Regulatory | `LGR` | Amsterdam | **High** — acquired, retained its own platform and engineering organisation |
| 2 | Tax & Accounting | `TAX` | New York | Medium — mostly aligned to corporate standards |
| 3 | Health & Clinical Evidence | `HCE` | Amsterdam, strong EU footprint | **High** — regulatory posture forces separation |
| 4 | Financial & Corporate Compliance | `FCC` | New York | Medium |
| 5 | Corporate Technology & Shared Services | `CTS` | Both | Low by design — it *is* the corporate standard |

### Why the estate is heterogeneous

This is the premise, and it is not negotiable within the requirements set.

Divisions selected agent platforms **at different times, under different
leadership, and two of them arrived through acquisition**. LGR and HCE have
board-level autonomy over their technology. No division will be directed to
abandon its platform, and any requirement predicated on consolidation is out of
scope by definition.

The estate is therefore permanently multi-vendor and multi-cloud. The available
lever is interoperability, not standardisation — and the purpose of the system
being specified is to establish, with evidence, whether that lever is worth
pulling.

### Divisional platform assignment

A modelling choice (open question Q2 in `00-plan.md`), chosen to make the estate
genuinely heterogeneous across vendors, clouds and hosting models:

| Division | Agent platform class | Cloud | Hosting model |
|---|---|---|---|
| `LGR` | Managed agent-engine platform, protocol-native | EU region | Fully managed, scale-to-zero |
| `TAX` | Enterprise agent service on the corporate hyperscaler | US + EU regions | Managed, tenant-bound identity |
| `HCE` | Self-hosted agent framework in a container runtime | **EU region only** | Self-operated |
| `FCC` | Vendor-managed agent sandbox | US region | Fully managed, no inbound network surface |
| `CTS` | CRM-embedded agent platform | US + EU regions | SaaS, closed platform |

`CTS` matters disproportionately: it operates the **system of record for
customer, account and contract data**. Every other division's agents need
answers that only `CTS` holds. That makes it the hub of nearly every
cross-divisional interaction, and it is also the least open platform in the
estate — it exposes no general inbound agent protocol surface. That tension
generates a substantial share of the system requirements.

### Regulatory posture

The obligations that turn this from an integration exercise into a governed one:

- **GDPR** applies across EU operations. Lawful basis, purpose limitation and
  data minimisation apply to agent-to-agent exchanges exactly as to any other
  processing — an agent forwarding a prompt is a controller-to-processor or
  controller-to-controller transfer depending on the seam.
- **Special-category data (GDPR Art. 9)** is handled by `HCE`. Its baseline
  posture is that clinical data does not leave the EU and does not enter a
  third-party model sandbox.
- **Data residency** commitments exist per division and per client contract.
  Residency is a property of *where inference runs*, not only where data is
  stored, which is the point most integration designs miss.
- **Cross-border transfer** between the Amsterdam and New York halves of the
  business is routine for commercial data and constrained for personal data.
  Where a cross-border agent call is required, personal data is expected to be
  redacted or pseudonymised **before** the call, not after.
- **Auditability.** Regulated divisions must be able to reconstruct, after the
  fact, what a given automated decision was based on — including which agents
  participated and what passed between them.

### Personas

Named consistently across the set; catalogued in full in
`10-context/02-stakeholders-and-personas.md`.

| Persona | Role | Primary concern |
|---|---|---|
| **Group CTO** | Executive sponsor | Is this worth funding, and what does it commit us to? |
| **Divisional architect** | Per-division technical authority | What does my division have to change, and what does it cost me? |
| **Integration engineer** | Builds and operates the seams | Can I build, test and debug this? |
| **Data protection officer** | Regulatory authority | What crosses which border, on what basis, and can we prove it? |
| **Platform operations** | Runs it in production | What breaks, how do I know, and what do I do about it? |
| **Divisional business owner** | Owns a business process | Did the answer arrive in time and was it right? |

## 5. Publication rules

This directory is committed to a **public** repository.

- No cloud account identifiers, project identifiers, subscription or tenant
  names, organisation identifiers, or credential-store profile names.
- **Regions only**, and only where a region is material to latency or residency.
- Cost documents use **unit rates and modelled projections**. No actual spend
  totals, no invoice figures, no negotiated contract rates.
- No real company is named as the subject organisation, and no vendor is
  characterised beyond what its public documentation or a reproducible
  measurement supports.

## 6. Language and formatting

- Markdown, wrapped near 80 columns.
- Tables for anything enumerable; prose for anything requiring justification.
- Diagrams as Mermaid, inline, kept small enough to read as source.
- Requirement statements are one sentence where possible. If a statement needs
  two sentences it is usually two requirements.
- No vendor product names in `50-system/`. The specification names **platform
  classes and protocol standards**, because naming products in a requirement
  converts a requirement into a procurement decision. Product names belong in
  the lens documents and the build-vs-buy evaluation, where a procurement
  decision is the point.
