# Business Case and ROI — Field Enablement Lens

## Why this is a parameterised model, not a number

An ROI figure for an enablement asset is a number assembled from estimates and
then quoted as though it were measured. That is worse than useless in this
context: the asset's entire proposition is that it replaces confident assertion
with evidence, so its own business case cannot be a confident assertion.

So this is a **model with named parameters**, each labelled `[assumed]` until
someone measures it. It produces a break-even condition rather than a return
figure, because the break-even condition is the honest output: *how much would
this have to change, for it to have been worth building?*

Every parameter below is a placeholder to be filled with the reader's own
organisational figures. None are supplied here — invented ones would propagate.

## Cost side

### C1 — Build effort

| Component | Driver | Parameter |
|---|---|---|
| Seam implementation (inbound and outbound, per protocol) | Number of protocols supported | `E_proto` |
| Platform onboarding | Number of platforms; declines with each per H3 | `E_plat(n)` |
| Mediation components (bridge, shim, dialect translation) | Number of constrained platforms in the estate | `E_med` |
| Cross-cutting mechanisms (identity, delegation bounds, correlation, redaction) | Largely fixed; independent of platform count | `E_core` |
| Observability, harvest and consumption accounting | Fixed, plus per-platform harvest work | `E_obs` |
| Scenario library and findings record | Number of scenarios | `E_scen` |

Two structural notes matter more than the individual estimates.

`E_core` is **fixed cost paid once**, and it is the largest single line. Identity
per seam, bounded delegation, correlation propagation and redaction enforcement
are required whether the estate has two platforms or five. This is why a
two-platform lab is poor value and a five-platform lab is good value: the
expensive part is already paid.

`E_plat(n)` is expected to **decline** with each platform (H3). If it does not,
the seam is not actually standard, and that finding should change the plan
rather than be absorbed.

### C2 — Maintenance

The line most often omitted, and the one that determines whether the asset
survives its first year.

| Driver | Parameter | Note |
|---|---|---|
| Platform API and protocol drift | `M_drift` | Recurring. Five vendors changing independently; each break is discovered when a finding fails to reproduce |
| Finding re-verification before external use | `M_verify` | Per use, not per period. Scales with how much the asset is actually used — success increases this cost |
| Scenario upkeep | `M_scen` | Recurring |
| Credential and identity rotation across five platforms | `M_cred` | Recurring, unglamorous, and a hard stop when it lapses |

`M_verify` deserves attention because it is counter-intuitive: **the maintenance
cost rises with adoption.** An asset used in one conversation a quarter costs
almost nothing to keep honest. One used weekly requires findings to be
current weekly. Budgeting maintenance as a fixed percentage of build cost gets
this exactly backwards.

### C3 — Running cost

Model consumption dominates; infrastructure for an evaluation-scale environment
is minor by comparison. Scale-to-zero hosting means idle cost approaches zero
and the marginal cost of a demonstration is a handful of model invocations.

Structure and unit economics are in `60-cost/01-cost-model-and-projection.md`.
Per the publication rules in `01-conventions.md`, no actual spend totals appear
in this set — `60-cost/02-sizing-framework.md` takes the reader's own rates as
inputs.

## Benefit side

Four mechanisms. Each is stated with how it would be *measured*, because a
mechanism without a measurement is a story.

### B1 — Reduced time-to-resolution on interoperability objections

**Mechanism.** An objection currently parked as an open question, resolved
inside the conversation with evidence.

**Measure.** Elapsed time from an interop question being raised to it being
closed, before and after. `T_resolve`

**Attribution risk.** Low. The question is discrete and its closure observable.

### B2 — Fewer opportunities stalled on unresolved interop risk

**Mechanism.** Integration risk surfaced and quantified before commitment rather
than parked.

**Measure.** Proportion of architecture conversations in which interop remains an
open item at close. `P_stall`

**Attribution risk.** **High.** Deals stall for many reasons and the
counterfactual is unobservable. This is the largest potential benefit and the
least defensible one. It should be modelled, watched, and never claimed as
realised.

### B3 — Improved discovery quality

**Mechanism.** Field architects ask better questions earlier, because the
failure modes are known rather than theoretical.

**Measure.** Presence of estate-specific interop constraints in discovery
documentation, before and after. `Q_disc`

**Attribution risk.** Moderate. Observable in artefacts, though improvement may
have other causes.

### B4 — Enablement leverage

**Mechanism.** One asset informs many practitioners; the marginal cost of the
next conversation is near zero once findings exist.

**Measure.** Number of distinct practitioners and conversations the findings
reach. `N_reach`

**Attribution risk.** Low as a measure of reach. It measures reach, not effect —
and conflating the two is the standard way enablement value gets overstated.

## The model

Total cost over the horizon:

```
Cost = E_core + E_proto + Σ E_plat(n) + E_med + E_obs + E_scen
     + (M_drift + M_scen + M_cred) × periods
     + M_verify × N_reach
     + running_cost
```

Benefit, expressed as value per influenced conversation and reach:

```
Benefit = N_reach × (V_resolve + V_discovery) + (P_stall_reduction × N_opps × V_stall)
```

**Break-even condition** — the form worth quoting, because it asks a question a
field leader can actually answer from experience:

```
N_reach ≥ Cost / (V_resolve + V_discovery + expected stall-avoidance per conversation)
```

Stated plainly: **how many architecture conversations must this asset
meaningfully improve before it pays for itself?** If the answer is a number the
organisation reaches in a quarter, the case is easy. If it needs to influence
more conversations than the field organisation holds in a year, the case fails
regardless of how good the asset is.

## Sensitivity

Which parameters actually decide the outcome — and two of the three are cost
parameters, which is not where attention usually goes.

| Parameter | Sensitivity | Why |
|---|---|---|
| `N_reach` | **Dominant** | Every benefit is per-conversation. An excellent asset used by one person fails the case. This is a distribution problem, not a build problem |
| `M_verify` | **High** | Rises with adoption. Under-modelling it makes success look like a cost overrun |
| `E_core` | High | Fixed, paid up front, largest single line. Determines the height of the bar |
| `P_stall_reduction` | High value, **lowest confidence** | Potentially the largest benefit; least attributable. Include in the model, exclude from any claim |
| `E_plat(n)` | Moderate | Declines with each platform if the seam is genuinely standard |
| `running_cost` | **Low** | Evaluation-scale consumption. Rarely the deciding factor, frequently the one scrutinised most |

The practical conclusion: **the case is won or lost on reach and maintenance,
not on build cost.** An organisation that builds this and does not plan its
distribution has bought an expensive personal learning exercise — which may be
worth it under Lens C, but is not this case.

## What would make this a bad investment

Pre-registered, in the same spirit as BR-505.

1. **Reach stays low.** The asset informs a handful of conversations. Failure of
   distribution, not of the asset — and the most likely failure mode.
2. **Platform drift outruns maintenance.** Findings go stale faster than they can
   be re-verified, and the asset becomes a source of confidently wrong
   statements. Worse than not having it.
3. **The findings are not externally usable.** If most results cannot be shared
   with customers — reproducibility, sensitivity, or framing — the enablement
   benefit evaporates while the cost remains.
4. **Vendor documentation catches up.** If platforms begin publishing
   comparable, verifiable interoperability matrices, the asset's core value is
   commoditised. Good for everyone; bad for this business case.
5. **Single-maintainer dependency, unaddressed.** If the record is not
   independently usable, the asset's life is one person's tenure.

## Measurement plan

If built, these are measured rather than assumed — otherwise this document
becomes exactly the kind of unfalsifiable business case it was written to avoid.

| Parameter | How | When |
|---|---|---|
| `T_resolve` | Timestamped in conversation records, before and after | Baseline first, then per quarter |
| `N_reach` | Count of practitioners and conversations using the findings | Continuous |
| `M_verify` | Actual re-verification effort logged per external use | Continuous |
| `Q_disc` | Sampled review of discovery artefacts for estate-specific interop constraints | Per quarter |
| `P_stall` | Proportion of conversations closing with interop open | Baseline first, then per quarter |
| `E_*` | Actual effort recorded during build | Once, during build |

Baselines for `T_resolve` and `P_stall` must be taken **before** the asset
exists. Neither can be reconstructed afterwards, and without them the two
largest claimed benefits are permanently unmeasurable.
