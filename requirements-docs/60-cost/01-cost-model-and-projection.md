# Cost Model and Projection

## Purpose and standing

This document specifies **how the system's cost is modelled**, and how measured
unit economics are projected to a business volume. It defines the model's
structure and its measurement slots; the slots are populated during evaluation.

Two constraints govern everything below:

- **No actual spend totals appear here.** Per the publication rules, cost content
  is expressed as unit rates, structures and modelled projections. Populating
  this model with an organisation's own figures is done in its own environment,
  not in this document.
- **Every figure carries an evidence label** — `[measured]`, `[modelled]`, or
  `[assumed]`. Any figure appearing in this model without one is a defect.

The companion `02-sizing-framework.md` is deliberately figure-free and is the
document to use for project sizing against a negotiated rate card.

## The two-factor framing

The single most important structural decision in this model, and the one that
most cost analyses get wrong.

Cost decomposes into two independent factors:

```
cost of a business task = consumption per task  ×  price per unit of consumption
                          └──── platform ────┘    └──── commercial ────┘
                               behaviour                  terms
```

They move independently, and conflating them produces the wrong decision:

- **Consumption per task** is a property of the platform's behaviour — reasoning
  depth, tool invocation, how context is carried between turns. It varies
  substantially between platforms for identical work.
- **Price per unit** is a commercial term. It converges under competition and it
  is negotiable.

The consequence, stated plainly: **a platform can be cheaper per unit and more
expensive per answer.** Comparing platforms on published unit rates is therefore
comparing them on the factor that matters less and is easier to change.

Anything presented as a single blended "cost per token" figure has destroyed
this distinction and cannot be used for a platform decision.

---

## CST-1xx — Cost taxonomy

### CST-101 — Three cost classes, tracked separately

| Class | Nature | Behaviour |
|---|---|---|
| **Build** | One-off | Paid before any value; dominated by the fixed mechanisms |
| **Run** | Per interaction | Scales with usage; dominated by model consumption |
| **Operate** | Recurring | Independent of usage; dominated by drift and re-verification |

Tracking them separately matters because they behave differently under scale and
under time. Build is insensitive to volume. Run is proportional to it. Operate is
proportional to *elapsed time and platform count*, not to usage — which is why it
is the class most often omitted and most often decisive.

### CST-102 — Build cost is dominated by fixed mechanisms

The mechanisms enumerated in the build inventory — identity per seam, delegation
bounds, correlation, redaction enforcement, record capture, consumption
accounting — are required whether the estate holds two platforms or five. Their
cost does not scale with platform count.

The implication for scoping: **a narrow evaluation is poor value and a wide one
is good value**, because the expensive part is paid either way. An evaluation
covering two platforms pays nearly the full fixed cost to answer a fraction of
the questions.

### CST-103 — Operate cost scales with platforms and time, not usage

| Driver | Scales with | Note |
|---|---|---|
| Platform drift | Platform count × elapsed time | Independent vendors changing independently |
| Finding re-verification | **Usage of the findings** | Rises with adoption, not with system load |
| Credential rotation | Platform count × elapsed time | Recurring; a hard stop when it lapses |
| Record retention | Retained volume × period | Bounded by retention policy, not by storage |

Re-verification is the counter-intuitive one and it is worth stating explicitly:
it rises with how much the findings are *used*. Budgeting operate cost as a fixed
percentage of build cost gets this exactly backwards and under-funds precisely
the successful case.

---

## CST-2xx — Run cost drivers

### CST-201 — Model consumption dominates

For an evaluation-scale environment, model consumption is expected to dominate
run cost, with compute, storage and network materially smaller. *[assumed —
established by measurement during evaluation]*

This is why the model's precision effort belongs on consumption and why
infrastructure sizing is not the interesting question, notwithstanding that it
receives most scrutiny in review.

### CST-202 — Consumption is metered in separately-priced categories

Providers meter and price several distinct categories, at materially different
rates. The categories must be carried separately through every calculation, and
they cannot be summed and multiplied by a single rate.

| Category | Relative rate | Note |
|---|---|---|
| Uncached input | Baseline | Frequently reported as a **remainder** after other categories, not as total input |
| Cached input read | Substantially below baseline | The economic reason to structure prompts for reuse |
| Cache write | Above baseline | Paid once to save repeatedly; only economic above a reuse threshold |
| Output | Well above baseline | Typically the largest per-unit rate |

*[assumed — relative rates vary by provider and change; obtain current published
rates rather than relying on this ordering]*

**The failure this prevents.** Treating the uncached-input category as though it
were total input understates consumption by a large factor — plausibly more than
an order of magnitude for a cache-heavy workload — while producing figures that
raise no error and look entirely reasonable. The error is arithmetically silent,
which is what makes it dangerous.

### CST-203 — Compute cost follows the hosting model

| Hosting model | Cost shape | Consequence |
|---|---|---|
| Scale-to-zero | Near-zero idle; per-invocation and per-duration when active | Correct for intermittent evaluation workloads; pays in first-invocation latency |
| Always-warm | Continuous, independent of usage | Correct only where latency requirements forbid a cold start |

For an evaluation environment, scale-to-zero is expected to be correct for
nearly every component, making idle cost close to zero and the marginal cost of
a demonstration a handful of model invocations.

A note that matters for asynchronous work: on a suspending runtime the polling
*is* the compute. Cost is incurred while the caller polls, not while the work
sits idle — so an asynchronous design's compute cost is driven by polling
strategy rather than by task duration.

### CST-204 — Storage is driven by retention, not by volume

Complete payload capture makes the record grow quickly, but retention policy
(DR-501) bounds it. Storage cost is therefore a function of the retention
decision, which is a data-protection decision before it is a cost one.

### CST-205 — Costs excluded from this model

Named so their absence is deliberate:

- Human effort — build and operate labour is modelled as effort in the delivery
  plan, not converted to currency here.
- Divisional platform licensing — the divisions run their platforms regardless;
  the programme does not consolidate them (BR-101) and cannot claim their cost.
- Network egress between the organisation's own environments, expected to be
  immaterial at evaluation scale *[assumed]*.
- Taxes, commitments, credits and negotiated adjustments — these belong to the
  rate card, applied in `02-sizing-framework.md`.

---

## CST-3xx — Unit economics

### CST-301 — The unit is a business task

**Statement.** The model's unit SHALL be one completed business interaction of a
defined shape, not a token, a request, or an hour.

**Rationale.** Only a per-task figure is comparable across platforms, because
platforms differ in how much consumption they expend reaching the same answer.
It is also the only unit a business owner can reason about — the question asked
is always "what does this cost us per enquiry", never "per token".

### CST-302 — Measured per shape and per route

Unit economics are measured for each combination that materially differs:

| Dimension | Why it changes cost |
|---|---|
| Interaction shape | Single delegation, mediated, decomposed, asynchronous — different hop counts and different consumption |
| Route | Which platforms participate |
| Protocol | May change payload size and turn count |
| Warm or cold | Changes compute cost and latency, not consumption |
| Turn count | Multi-turn interactions accumulate context, which changes the category mix |

### CST-303 — What is recorded per unit

For each measured unit, the model records: consumption by category per
participating platform; hop count; elapsed time; warm or cold state; and outcome
classification, since a partial interaction still consumes.

**Partial interactions are counted.** A decomposition that lost a leg consumed
the legs that answered. Excluding partials understates cost in exactly the
condition that occurs most often.

---

## CST-4xx — Projection

### CST-401 — Projection method

From measured unit economics to a business volume, in five steps:

1. **Characterise the workload.** For each interaction shape, the projected
   number of interactions per period.
2. **Apply unit consumption.** Per shape, per platform, per category — kept
   separate throughout.
3. **Apply the rate card.** The organisation's own rates per category per
   provider. Not published rates, where negotiated terms exist.
4. **Add hosting and storage.** From CST-203 and CST-204, driven by invocation
   counts and retention.
5. **Add operate cost.** From CST-103, driven by platform count, elapsed time,
   and expected findings usage.

Every step keeps categories separate. Aggregation happens once, at the end, after
rates have been applied.

### CST-402 — Projection formula

```
run_cost(period) =
    Σ over shapes s:
      volume(s) × Σ over platforms p in route(s):
                    Σ over categories c:
                      consumption(s, p, c) × rate(p, c)
  + Σ over components k: invocations(k) × invocation_cost(k)
                        + duration(k) × duration_cost(k)
  + retained_volume × storage_rate

total(period) = run_cost(period) + operate_cost(period)
                + amortised build_cost
```

The nested category sum is the part that must not be simplified. Collapsing it
to `total_consumption × blended_rate` reintroduces exactly the error CST-202
exists to prevent.

### CST-403 — Sensitivity

Which terms actually decide the outcome:

| Term | Sensitivity | Why |
|---|---|---|
| `volume(s)` | **Dominant** | Run cost is linear in it; every other term is second order at business volume |
| Category mix | **High** | The spread between the cheapest and most expensive category is large. A workload shifting toward output costs materially more per task |
| `consumption(s, p, c)` | High | The platform-behaviour factor; the reason per-task comparison exists |
| `rate(p, c)` | Moderate | Negotiable and converging; the factor most attended to and least decisive |
| Hosting | **Low** at evaluation scale | Near-zero idle on scale-to-zero. Rises in relevance only at production volume |
| Retention | Low, but a data-protection decision first | Cost is a secondary reason to bound it |

### CST-404 — Projection carries its assumptions

Any projection SHALL state its volume assumptions, its rate source and date, the
measurement conditions of its unit economics, and be labelled `[modelled]`.

A projection quoted without its volume assumption is a number with no meaning,
and it will be quoted onward without one.

---

## CST-5xx — Measurement plan

What must be measured to populate this model, and when.

| # | Measurement | When | Populates |
|---|---|---|---|
| CST-501 | Consumption by category, per shape, per platform | Each scenario execution | CST-301, CST-302 |
| CST-502 | Hop count and elapsed time per shape | Each execution | CST-302 |
| CST-503 | Cold and warm compute cost per component | Once per component, re-measured on hosting change | CST-203 |
| CST-504 | Record growth per interaction | Continuous | CST-204 |
| CST-505 | Reconciliation of modelled consumption against a provider's own figures | Per period, per provider | Validates CST-202 |
| CST-506 | Actual build effort per mechanism | During build | CST-102 |
| CST-507 | Actual operate effort, split by driver | Continuous | CST-103 |

**CST-505 is the model's integrity check.** A model never reconciled against a
provider's own account of consumption is unverified, and a category-handling
error will not otherwise surface — the figures look plausible in isolation, which
is the whole problem.

---

## Reporting rules

1. Never present a single blended consumption figure (OR-401).
2. Every monetary figure is labelled modelled at stated rates, never billed cost
   (OR-406).
3. Every projection states its volume assumption and rate date (CST-404).
4. Consumption per task and price per unit are reported separately, never
   pre-multiplied (BR-402).
5. Partial interactions are included in consumption reporting (CST-303).
6. Where a figure could not be measured, report it as unavailable rather than
   estimating silently.
