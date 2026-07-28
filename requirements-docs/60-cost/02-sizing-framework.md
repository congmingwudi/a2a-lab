# Sizing Framework

## What this is, and why it contains no figures

A framework for sizing an agent interoperability capability for a **future
project**: you supply projected business volumes and your own negotiated rates,
and it produces a defensible size.

It contains **no numbers at all**, deliberately. Rates change, are negotiated,
and differ per organisation and per contract; a framework carrying figures
becomes obsolete on the next rate change and — worse — its stale figures get
reused because they are conveniently present. Everything here is structure,
inputs and method.

This is the document to use for project planning and commercial alignment.
`01-cost-model-and-projection.md` is its companion and defines how the unit
economics feeding step 3 are measured.

**How to use it:** work sections 1 → 6 in order. Section 6 lists what makes a
sizing wrong, and is the section to read first if you are reviewing someone
else's.

---

## 1. Workload characterisation

Nothing can be sized until the workload is described. These are the inputs that
change the answer materially; anything not listed here is second order.

### 1.1 Interaction inventory

For each distinct interaction the capability will serve:

| Input | Why it matters |
|---|---|
| **Shape** | Single delegation, mediated, concurrently decomposed, or asynchronous. Determines hop count and therefore consumption multiplicity |
| **Participants** | Which platforms, and how many. Consumption is per participant |
| **Volume per period** | The dominant term in every calculation |
| **Volume distribution** | Steady, business-hours, or bursty. Determines concurrency and whether cold starts are routine or rare |
| **Turn count** | Multi-turn interactions accumulate context, shifting the category mix and raising per-task consumption |
| **Payload scale** | Order of magnitude of content exchanged |
| **Latency requirement** | Determines hosting model, which determines cost shape |
| **Residency constraint** | May force a specific region, removing cheaper options |

### 1.2 Estate characterisation

| Input | Why it matters |
|---|---|
| **Platform count** | Drives fixed integration work and, critically, recurring drift cost |
| **Closed platforms** | Each needs mediation, the highest-burden component class |
| **Protocol generations in use** | Divergence forces translation and its ongoing maintenance |
| **Platforms needing asynchronous support** | Changes both design and polling-driven compute |
| **Regulatory constraints per platform** | Drives the estate-specific components, which do not transfer to a bought product |

### 1.3 Programme characterisation

| Input | Why it matters |
|---|---|
| **Duration** | Operate cost is proportional to elapsed time |
| **Expected findings usage** | Re-verification effort scales with use, not with load |
| **Retention requirement** | Drives storage and the data-protection posture behind it |
| **Availability requirement** | Evaluation-grade and production-grade differ by more than a factor |

---

## 2. Component inventory to size

Size each component class present in your estate. Classes absent from your
estate are omitted — the framework does not assume the full inventory.

| Class | Sized by | Cost behaviour |
|---|---|---|
| Protocol surfaces (inbound) | Protocol count | Fixed build; low ongoing |
| Protocol clients (outbound) | Protocol count | Fixed build; low ongoing |
| Canonical model and routing | — | Fixed build; low ongoing |
| Outbound mediation | Constrained platform count | Moderate ongoing; changes with routing |
| Inbound mediation | Closed platform count | **High ongoing** — tracks a vendor interface not designed for it |
| Generation translation | Generation pairs in use | **High ongoing** — bilateral; changes when either side moves |
| Delegation control | — | Fixed build; low ongoing |
| Identity per seam | Seam count | **High ongoing** — rotation across platforms |
| Boundary data rules | Data class count × seam count | High ongoing — regulatory drift |
| Residency-aware routing | Constrained class count | Moderate ongoing |
| Record capture and storage | Hop volume × retention | Moderate ongoing |
| Platform record retrieval | Platform count | **High ongoing** — one integration per platform, each changing independently |
| Consumption accounting | Provider count | Moderate ongoing — repricing and new categories |
| Decomposition orchestration | Shapes supported | Moderate ongoing |
| Evaluation surface | Feature scope | Moderate ongoing |
| Deterministic test path | Protocol count | Fixed build; low ongoing |

**The four marked high-ongoing are the ones to size carefully.** Each tracks
something owned by another party and changing without notice, and together they
tend to dominate operate cost. They are also the entries most likely to be
under-estimated, because build effort for each is modest and visible while the
maintenance is neither.

---

## 3. Rate card

The rates to obtain before sizing. Obtain **your** rates — negotiated where
applicable — rather than published ones, and record the date of each.

| Rate needed | Per | Note |
|---|---|---|
| Consumption rate per billed category | Provider, model, region | One rate per category. A single blended rate is not usable |
| Invocation cost | Hosting platform | For scale-to-zero components |
| Duration cost | Hosting platform | Per unit of active time |
| Always-warm compute | Hosting platform, size | Only for components latency forbids cold-starting |
| Storage rate | Storage class, region | Applied to retained volume |
| Egress rate | Between regions and providers | Frequently overlooked; may be immaterial, should be checked |
| Managed service fees | Per service | Gateways, secret stores, managed databases |
| Engineering day rate | Role | For build and operate effort |

**Regional rates differ.** Where residency constrains a workload to a region,
use that region's rate rather than the cheapest available — otherwise the
constraint that shaped the design is absent from its cost.

**Commitments and credits are applied at the end**, to the total, not folded into
unit rates. Folding them in makes the unit economics incomparable across
platforms and hides what the capability costs at the margin.

---

## 4. Method

1. **Characterise the workload** (section 1). If any input is unknown, record it
   as unknown rather than assuming — section 6.1.
2. **Select the component inventory** (section 2) for your estate.
3. **Obtain unit consumption per interaction shape.** From measurement where the
   capability exists; from a pilot where it does not. A pilot measuring the two
   or three highest-volume shapes is sufficient and is substantially better than
   any estimate.
4. **Assemble the rate card** (section 3), dated.
5. **Compute run cost.** Volume × unit consumption × rate, keeping categories
   separate until the final sum. Add hosting and storage.
6. **Compute build effort.** Fixed components once; per-platform components by
   platform count, with a declining factor after the first two if the seam is
   genuinely standard.
7. **Compute operate cost.** Platform count × duration for drift and rotation;
   expected findings usage for re-verification; retained volume for storage.
8. **Apply commitments and credits** to the total.
9. **Run the sensitivity checks** (section 5).
10. **Record assumptions and dates** alongside the result. A sizing without them
    cannot be reviewed or refreshed.

---

## 5. Sensitivity checks

Run all four. A sizing that has not been stressed is a single point estimate
presented as a plan.

| # | Check | Why |
|---|---|---|
| S1 | Volume at ½ and 2× | Run cost is linear in volume. If the decision changes between these, volume must be firmed before committing |
| S2 | Category mix shifted toward the most expensive category | Tests whether the sizing survives a workload producing longer outputs than assumed — a common drift |
| S3 | Platform count +1 | Tests whether operate cost was sized on platforms or treated as fixed. A sizing insensitive to platform count has almost certainly under-modelled drift |
| S4 | Duration ×2 | Operate cost is proportional to elapsed time. Programmes routinely run longer than planned |

If a sizing survives all four without the decision changing, it is robust enough
to commit. If S3 or S4 changes the decision, the operate cost model is the part
to firm up — not the run cost, which receives most of the attention.

---

## 6. What makes a sizing wrong

The failure modes, in descending order of how often they occur.

### 6.1 An unknown treated as an assumption

An unknown volume replaced by a plausible number becomes a fact by the third
document it appears in. Record unknowns as unknowns and size a range.

### 6.2 A single blended consumption rate

Collapsing separately-priced categories into one rate produces a figure that can
be wrong by a large factor and looks entirely reasonable. This is the most
consequential arithmetic error available in this domain, and it raises no alarm.

### 6.3 Operate cost as a percentage of build

Operate cost scales with platform count, elapsed time and findings usage. Build
cost scales with none of those. The percentage convention systematically
under-funds long-running, multi-platform, successful programmes — that is, the
ones worth funding.

### 6.4 Sizing the general components and omitting the estate-specific ones

Boundary data rules, residency routing and per-seam identity are driven by the
organisation's own obligations. They are the components least likely to be
covered by a bought product and most likely to be omitted from a sizing, because
they are invisible in any generic reference architecture.

### 6.5 Published rates instead of negotiated ones

Produces a sizing that will not match the eventual invoice, which discredits the
whole model at exactly the moment it is most needed.

### 6.6 Cheapest-region rates for residency-constrained workloads

Silently removes the constraint that shaped the design.

### 6.7 Excluding partial interactions

An interaction that lost a contribution still consumed the contributions that
answered. Counting only complete interactions understates cost in the condition
that occurs most.

---

## 7. Aligning with commercial terms

For converting a sizing into a commercial position:

- **Size at the margin first.** Establish cost per business task before applying
  commitments. Only marginal cost tells you whether an interaction is worth
  automating; a committed-rate figure conflates the two decisions.
- **Separate the two negotiating levers.** Consumption per task is a technical
  lever — prompt structure, caching, model selection, interaction design. Price
  per unit is commercial. Confusing them wastes negotiation on the factor you
  could have changed yourself.
- **Model the commitment risk in both directions.** A commitment below actual
  usage forfeits the discount; above it, you pay for unused capacity. Size the
  band, not the point.
- **Re-size on any rate change, model change, or platform addition.** Each
  invalidates part of the model, and a sizing carried forward unchanged through
  a model change is one of the more common ways a budget is missed.

---

## 8. Review triggers

Re-run the sizing when any of these occur:

- A provider changes rates, introduces a category, or changes how a category is
  metered.
- A platform is added to or removed from the estate.
- An interaction shape's measured unit consumption moves beyond a stated
  tolerance.
- Actual volume diverges from projected volume beyond a stated tolerance.
- A model version changes on any participating platform.
- A regulatory obligation changes what must be enforced at a boundary.
- The programme's duration extends.

Each trigger invalidates a specific input rather than the whole model, so a
re-run is cheap — provided the assumptions were recorded at step 10, which is
the step most often skipped and the one that makes every later refresh possible.
