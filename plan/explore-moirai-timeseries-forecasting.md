# Exploration — Moirai time-series foundation model + MoiraiAgent over the lab's telemetry

**Status: EXPLORATION, not a workstream.** This is a thinking doc, worked through
before it earns a `WS<n>` and a slot in the numbered plan set. It exists to answer
one question honestly before committing to build anything: *does a time-series
foundation model built for dense operational telemetry (electricity load, retail
sales) tell us anything true about the data this lab collects?* If the answer holds
up, this graduates into a numbered workstream (graduation criteria at the end). If
it doesn't, that negative result is itself a lab finding worth recording.

**Two tracks, and they are NOT equally good fits — say so up front:**

- **Track A — the A2A experiment traces** (`lab.trace_events` etc.). The
  on-theme data, but a *hard* fit: sparse, irregular, episodic. This is where the
  novel MoiraiAgent-over-the-ADR-log idea lives, and also the biggest risk of "not
  enough signal." Covered first below.
- **Track B — the lab's own infrastructure / SRE metrics** (CloudWatch across the
  AWS runtime, the GCP Vertex metrics we already partly pull, Azure Monitor for
  Foundry which we don't pull yet). A *much better* data fit — dense, regular,
  vendor-emitted whether or not an experiment runs — and it is the canonical TSFM
  use case (capacity, anomaly, predictive scaling). Added 2026-08-11; covered in
  its own section.

The load harness the operator asked about (drive volume for two weeks) turns out to
matter for **both** tracks, but for a subtler reason than "more data" — see "The
load-harness question" below.

Tracked on the console's **What's Next** as `moirai-timeseries-forecasting`
(horizon: exploring).

## Reference material (read 2026-08-11; keep regardless of outcome)

- Moirai (original TSFM) — https://www.salesforce.com/blog/moirai/
- Moirai-MoE (mixture of experts) — https://www.salesforce.com/blog/time-series-morai-moe/
- Moirai 2.0 — https://www.salesforce.com/blog/moirai-2-0/
- MoiraiAgent (agentic, context-aware) — https://www.salesforce.com/blog/moiraiagent/
- Code: uni2ts / moirai-agent — https://github.com/SalesforceAIResearch/uni2ts/tree/main/project/moirai-agent
- Model: moirai-2.0-R-small (HF) — https://huggingface.co/Salesforce/moirai-2.0-R-small
- Agent weights (HF) — https://huggingface.co/Salesforce/moirai-agent

> Salesforce AI Research built these. Using a Salesforce research TSFM to analyse a
> lab whose whole point is cross-vendor agent interop — one leg of which is
> Agentforce/Data 360/Tableau — is on-theme, not a detour: it is the *same
> telemetry* (WS19's `lab.trace_events`) read a third way.

## What these are (grounded, from the sources above)

**Moirai** — a pretrained transformer for *universal* time-series forecasting.
Masked-encoder, patch-based; trained on LOTSA (~27B observations, 9 domains). The
pitch is **zero-shot**: one pretrained model forecasts an unseen series with no
per-dataset training. Handles **any-variate** input (flattens multivariate series,
learns time-vs-variable attention biases), **multiple frequencies** (patch-size
projection layers), and emits **probabilistic** forecasts (distributions, so you
get uncertainty bands, not just a point). Sizes 14M/91M/311M.

**Moirai-MoE** — swaps the per-frequency projection layers for a single layer plus
a **sparse mixture-of-experts**, so specialization is learned per-token rather than
bucketed by frequency. Beats the dense model by ~17% on 29 datasets with far fewer
*activated* params (Small ≈ 11M activated). Decoder-only training objective.

**Moirai 2.0** — the redesign: **decoder-only** (autoregressive), **quantile loss**
(not distributional), **multi-token** prediction, data-filtering of non-forecastable
series, missing-value-aware patch embeddings. **#1 by MASE on GIFT-Eval** among
non-leaking models; ~16% better MASE, ~13% better CRPS, ~44% faster, ~96% smaller.
`moirai-2.0-R-small` is **11.4M params, CPU-feasible**, `pip install -e '.[notebook]'`
from uni2ts. **License: CC-BY-NC-4.0 — non-commercial** (see licensing note below).

**MoiraiAgent** — the agentic layer, and the most interesting fit here. A
lightweight **~3B LLM orchestrator** decides which tools to call (forecasting
experts + a Python sandbox), and an **expert-selection** step picks among strong
TSFMs (Chronos-v2, TimesFM-2.5, Tirex) per task using historical values, temporal
features, candidate predictions and CV error — reported to beat any single expert
(MASE 0.689 on GIFT-Eval). Beyond raw forecasting it does three **context-aware**
things driven by *natural-language* context (policy changes, events, operational
shifts): (1) **adaptive lookback** — trim history at a detected phase transition;
(2) **anomaly refinement** — drop non-persistent patterns before forecasting;
(3) **future-event integration** — fold known upcoming events into the forecast.
Inputs: time series *and optionally* NL context. API is roughly
`moirai_agent(time_series_data, context=...)`.

## The lab's data, as time series (what we'd actually feed it)

The obs store (`src/observability/pg.py`, Aurora `lab.*`, the same store WS19
federates) already carries everything below. `ts_at` is a real `timestamptz`, so
every table below resamples to a regular grid cleanly.

| Series (from → column) | Grain | What a forecast/anomaly would mean |
|---|---|---|
| **Hop latency** — `lab.trace_events.latency_ms` by `(target, protocol)` | per-hop → resample hourly/daily | expected latency band per platform×protocol; a hop outside the band = a regression or a cold start, caught without a hand-set threshold |
| **Traffic volume** — `count(*)` over `trace_events` by `protocol` | resample by interval | demand curve → *when* to warm targets (ties straight to `scripts/demo_watch.py` + `/loop`, the pre-demo warm watch) |
| **Error rate** — `trace_events.status='error'` share | resample | per-platform reliability trend; a rising band before it crosses any alarm |
| **Trace wall-clock / depth** — `lab.trace_rollup_zc.wall_ms`, `total_latency_ms`, `max_depth` | per-trace | end-to-end latency drift across the full delegation chain |
| **Token / cost burn** — `obs_events.usage_json`, WS9 ADK `est_cost_usd`/tokens, the cost-sentinel inputs | daily | forecast spend → extend the WS12 cost sentinel from *threshold* to *trajectory* ("at this rate you cross budget in N days") |
| **Cold-start latency** — `warmups.jsonl` durations per target | per warm | predict when a target goes cold / how long the next warm costs |
| **Console usage** — `lab.usage_events.occurred_at` by `event`/`section` | resample | visitor/section demand; least interesting, but real and regular |

## What answers this could open up

Framed as questions the lab can't currently answer, ranked by how novel the answer
would be:

1. **Anomaly detection that isn't a hand-tuned threshold.** Today "is this slow?"
   is a constant in `.env`/config (the Path-A budget, the sentinel's dollar line).
   A probabilistic forecast gives a *learned* expected band per series, so
   "AgentCore hop 4× its own forecast at 2am" surfaces without anyone picking a
   number — and adapts as the baseline shifts.
2. **Demand forecasting → smarter warm-ups.** The warm watch currently fires on a
   fixed `/loop` interval. A volume forecast could say *which* targets a coming
   demo window needs hot and when — the difference between a watch and a schedule
   the lab already draws (build-notes/claude/13-recurring-tasks.md).
3. **Cost trajectory, not cost threshold.** The cost sentinel (WS12/D44) alarms on
   a level. A forecast alarms on a *slope* — the more useful signal, and it
   composes with the sentinel's existing refuse-if-you-can't-back-it discipline.
4. **Cross-platform drift over time.** Per-series trend per platform answers "is
   Foundry getting slower relative to Claude over the last month?" — a longitudinal
   read the point-in-time matrix (`plan/02-matrix.md`) can't give.
5. **The meta-finding (the honest one).** *Does a TSFM pretrained on electricity
   and retail generalize to agent-infra telemetry?* The lab's whole method is
   testing generalization claims across boundaries and reporting the result
   honestly — a strong or weak zero-shot MASE here is a finding in exactly that
   spirit, whichever way it lands.

## The novel angle: the ADR log as MoiraiAgent's context channel

MoiraiAgent's differentiator is folding **natural-language context** — "a policy
change", "a scheduled event", "an operational shift" — into a numeric forecast.
**This lab already keeps that channel, dated:** `plan/00-decisions.md` records
*when* the Claude backend swapped, when AgentCore came online, when the Data Cloud
region was repinned (D70), when a face moved host. Those are precisely the phase
transitions and operational shifts the agent is built to reason over — a hop-latency
series with "on 2026-07-28 three deploys shipped stale images" as context is a
textbook adaptive-lookback / anomaly-refinement case. Most operational telemetry
has no such curated, timestamped narrative; this lab does. **That is the most
lab-specific thing to pressure-test** — not raw forecasting (many models do that),
but context-conditioned forecasting where the context is our own decision log.

## The honest tensions (why this is exploration, not a plan yet)

- **Our data is sparse, irregular, and episodic.** Experiments run when the
  operator runs them, not on a cadence. ~960 traces / ~3,021 hops total (WS19
  verified 2026-08-09) is *thin* for forecasting, and gaps are the norm. TSFMs want
  reasonably dense, regular series. Resampling to daily helps but can't manufacture
  history that isn't there. **This is the central risk**: the technique may simply
  not have enough signal, and we should be ready to say so.
- **Non-commercial license.** `moirai-2.0` weights are **CC-BY-NC-4.0**. A research
  lab that publishes findings is fine; anything that looked like a product would
  not be. State it wherever the model is used, same as any provenance claim.
- **MoiraiAgent is heavier than the model.** The model is 11.4M params, CPU-fine.
  The *agent* needs a ~3B LLM orchestrator + a Python sandbox + multiple expert
  TSFMs — a real hosting cost. Cleanly separate "run the small model as a forecast
  job" (cheap, near-term) from "stand up the full agent" (a later, bigger bet).
- **Don't reinvent what the sentinel/analyst already are.** The obs analyst (D23)
  and cost sentinel (WS12) are already scheduled analysis jobs over `lab_reader`
  that write briefs. A forecast is most likely **another brief kind**
  (`obs_briefs.kind='forecast'`), not new infrastructure — reuse the seam.

## The load-harness question (raised 2026-08-11)

*"What if we drove a decent volume across all experiments for ~2 weeks via a
scheduled Lambda — basically warm + Run All — would that give Moirai meaningful
data?"* The mechanism is trivial (we already have warm-up + Run All + `/loop` +
the scheduled-Lambda pattern). The catch is what kind of data it produces.

**Volume is not the blocker; structure is.** Moirai is zero-shot — it needs no
training history from us. Forecasting *evaluation* needs a series with structure a
naive baseline would miss (seasonality, trend, regime change). A flat daily "Run
All at 03:00" produces the *least* useful possible dataset:

- Hop **count** becomes a single spike in one hourly bucket, zero elsewhere — a
  spiky, mostly-zero series.
- **Latency** per `(target, protocol)` becomes stationary noise around a per-platform
  mean, and a forecaster nails stationary noise trivially — last-value/naive is
  already near-optimal, so "Moirai beat naive by 2%" is a null result dressed up.

You'd manufacture ~3k more rows and learn nothing the current distribution doesn't
already tell you. **Denser ≠ more-forecastable-in-an-interesting-way.**

**Two real sources of signal, different in kind:**

1. **Structure you inject (you control the load).** A *shaped* demand profile —
   heavier in simulated business hours, a weekend dip, occasional bursts — gives the
   aggregate series genuine diurnal/weekly seasonality, so the honest test becomes
   "can Moirai beat **seasonal-naive**?" (a strong baseline; beating it means
   something). A metronome defeats this; a single daily batch defeats it harder.
2. **The platforms' response you do NOT control (the real prize).** You manufacture
   the *load*; you do not manufacture the *latency and error rate under it*. Cold
   starts at quiet hours, provider-side variance, throttling under burst, a
   model-version rollout mid-window — genuine, exogenous, and exactly what anomaly
   detection and MoiraiAgent's context channel exist for. **The strongest framing of
   the whole experiment: shape a plausible demand curve, then ask whether the
   platforms' RESPONSE to it is forecastable.** Load is synthetic; the response is
   real behaviour.

**So: yes, two weeks helps — but only a *shaped, jittered, time-spread* harness, not
a daily batch.** Design constraints if it's built:

| Decision | Meaningful choice | Why |
|---|---|---|
| Grain | aggregate **hourly** per `(target, protocol)` | per-hop is a point process, not a series; daily = only 14 points. Hourly × 14 days = 336 points/series + a 24–48h holdout to score |
| Series | ~a dozen busy pairs; targets: hop count, p50/p95 latency, error rate, token/cost | a dozen independently-scored series is a real multi-series eval, not one lucky curve |
| Drive pattern | **jittered across the day on a diurnal/weekly profile**, ~10–30 traces/hour in active hours | spreads runs so percentiles are stable AND injects the seasonality worth testing against |
| Window | 2 weeks workable; **4 weeks materially better** (672 hourly points) | more holdout; better odds of catching an organic regime change |

**Caveats to name honestly:**
- **Cost.** Driving real calls across five clouds for weeks is real spend — the
  reason WS12 (cost sentinel) exists. A load harness is itself a cost generator;
  budget it. (Silver lining: it produces the cost-burn series that is one forecast
  target.)
- **Validity subtlety.** If you shape demand with a *known* profile, "can Moirai
  forecast it?" partly reduces to "can it recover the profile we injected?" — a
  weaker claim than forecasting organic demand. Keep the load realistic-but-incidental
  and make the *response* (latency/error) the real forecast target, since we don't
  author that.
- **Short window, few events.** Two weeks may catch zero regime changes, leaving the
  ADR-context angle untested for lack of a discontinuity. Four weeks, or timing the
  window around a *planned* change (a deploy, a model swap), improves the odds.

**Recommendation: do NOT run the two-week harness first.** Run the gating experiment
(Q1 below) on data we already have — resample the 2–3 busiest existing
`(target, protocol)` latency series to hourly and check there's even enough non-gap
history for a zero-shot forecast to beat naive. That's an afternoon, not two weeks of
five-cloud spend. If it shows *any* signal, the shaped harness is justified as the
next step. **Note also that Track B (infra metrics, below) needs no harness at all** —
CloudWatch/GCP/Azure emit dense regular series whether or not experiments run, so it
is the faster path to a real TSFM result.

## Track B — infrastructure / SRE metrics (CloudWatch, GCP, Azure) — added 2026-08-11

This is the **better data fit for a TSFM, and the more honest SRE story** — worth
saying plainly: Moirai was pretrained on exactly this shape of data (dense, regular,
machine-emitted operational metrics), so it is far more likely to produce a real
result here than on the sparse experiment traces of Track A. It also sidesteps
Track A's central risk (not enough signal) because these metrics exist on a fixed
emission cadence independent of whether anyone runs an experiment.

**What the lab runs, and what each cloud already emits.** The estate is in
`plan/09-deployment-map.md`; the point is that every hosted piece emits
vendor-native metrics on a regular grid we are mostly *not* looking at yet:

| Cloud | Runtime pieces (plan/09) | Metric source | Pulling today? |
|---|---|---|---|
| **AWS** | Fargate faces, the bridge, AgentCore runtimes (Claude/OpenAI), the obs/MCP/fan-out/shim Lambdas, Aurora | **CloudWatch** — ECS CPU/mem/task count, Lambda invocations/errors/duration/concurrency/throttles, Aurora ACU/connections/read-IO, API GW/ALB latency & 5xx | **No** — we harvest agent *execution* logs (M11), not the runtime's own CloudWatch metrics |
| **GCP** | ADK on Vertex AI Agent Engine | **Cloud Monitoring** — `reasoning_engine/request_count`, `cpu/allocation_time`, `memory/allocation_time`, token counts | **Partly** — `adk_source.py` already pulls these for the WS9 cost column; not as *forecastable series* |
| **Azure** | Foundry agent (gpt-5-mini) | **Azure Monitor** — request/latency/throttle metrics for the Foundry endpoint | **No** — not harvested at all |

So Track B is *also* a coverage gap the lab has independently: **AWS runtime metrics
and Azure Monitor are not harvested at all today.** Even before any forecasting, a
harvester that lands these three clouds' runtime metrics as regular series in the obs
store is a standalone lab improvement (and a natural M11 sibling: M11 harvests
platform *agent* logs; this harvests the *infrastructure* underneath them).

**The SRE use cases (what a TSFM actually buys here):**

1. **Predictive capacity / scale-to-zero timing.** Aurora is scale-to-zero (ACUs);
   Fargate and Lambda scale on demand. Forecast the load curve → know *before* a demo
   window whether the cluster will be cold or under-provisioned. This is the textbook
   TSFM win and it ties straight to the warm-watch work (build-notes/claude/13).
2. **Learned anomaly bands per resource.** "Aurora connections 3× their forecast",
   "Lambda p95 duration drifting up", "ECS CPU climbing without a matching request
   rise (a leak)" — surfaced without hand-set CloudWatch alarm thresholds, the same
   learned-band argument as Track A but on data dense enough to actually support it.
3. **Cross-cloud reliability comparison, honestly.** The lab's whole premise is
   cross-vendor comparison. Forecast-vs-actual error on *the same resource class*
   across AWS/GCP/Azure is a genuinely novel, on-brand reading: whose runtime is the
   most *predictable*, not just the fastest — predictability is an SRE virtue the
   point-in-time matrix can't express.
4. **Correlate infra with the A2A traces (the bridge between tracks).** A latency
   spike in `lab.trace_events` (Track A) lined up against an Aurora ACU cold-start or
   an ECS CPU saturation (Track B) turns "that hop was slow" into "that hop was slow
   *because the runtime under it was cold*" — root cause the trace alone can't give.
   This multivariate, cross-source forecast is exactly Moirai's **any-variate**
   strength.

**Why this is arguably the one to graduate first.** It has no data-density risk, no
load-harness dependency, fills a real observability gap (AWS/Azure runtime metrics
unharvested), and it is the canonical thing a TSFM is *for*. The MoiraiAgent context
angle still applies — the ADR log dates the infra changes (a face moved host, a
runtime added) that explain the regime shifts in these series. The honest caution: it
is the **least differentiated** use — "forecast CloudWatch with a TSFM" is a known
pattern many teams do — so its lab-specific value has to come from the *cross-cloud
comparison* (#3) and the *infra⋈trace correlation* (#4), not from forecasting one
cloud's metrics in isolation, which is table stakes.

**SRE framing note (ties to build-notes/claude/13-recurring-tasks.md).** A forecast
does not make this a *monitor* — a forecast job is another scheduled analysis (the
analyst/sentinel shape), not a live pager. The "watch vs monitor" line still holds:
predictive analysis over harvested metrics is a scheduled job that writes a brief; it
does not replace real alerting, and the doc should not oversell it as SRE monitoring.

## Likely deployment shape (if it graduates)

Nothing to build yet, but the shape that fits: a forecast job runs `moirai-2.0-R-small`
via uni2ts (CPU is fine at 11.4M params) against read-only `lab_reader`, resampling
the series above, and writes a **forecast brief** (`obs_briefs.kind='forecast'`,
reusing the WS12 discriminator) plus optional structured rows for the console to
plot bands over actuals. This mirrors the analyst/sentinel exactly: scheduled or
on-demand, read-only identity, output-as-brief, empty-state that explains itself.
The MoiraiAgent (context channel = the ADR log) is a **second, later** phase with
its own hosting decision. A console surface would be a tab under the Observability
section (the same rollup the trace viewer and Tableau already read — a *fourth*
reading of the one truth).

**Track B adds one upstream step — and that step is now BUILT (2026-08-11).** The
metrics harvester (an M11 sibling) lands CloudWatch / Cloud Monitoring / Azure
Monitor runtime series into `lab.infra_metrics` (regular grid, `lab_reader`-visible),
reusing `obs_harvest.py`'s shape and, for GCP, the metric queries `adk_source.py`
already issues. Concretely:

- `src/observability/infra_source.py` — three sources (`AwsInfraSource`,
  `GcpInfraSource`, `AzureInfraSource`) reading `config/infra_metrics.yaml` (series
  per cloud, `${VAR}`-expanded via the registry's own expander so no environment
  identifier is hardcoded). Each pulls its series as an evenly-spaced grid and writes
  one row per point through `store.upsert_metrics`; each degrades honestly (a series
  whose id is unset is skipped, a whole cloud with no ids is `blocked`).
- `lab.infra_metrics` on both stores (sqlite `store.py` + Aurora `pg.py`, duck-typed
  parity per D49), keyed `(cloud, resource, metric, ts_at)` so a re-harvest of an
  overlapping window is idempotent.
- Wired into `scripts/obs_harvest.py` and the hosted harvest Lambda under an `infra`
  group, kept OUT of the five-platform coverage sweep (the same treatment as the
  coding-agent telemetry — these are the infrastructure *under* the platforms, not a
  sixth platform column). Run with `uv run python scripts/obs_harvest.py infra`.

The forecast job (not yet built) then reads that table exactly like Track A reads the
traces. Same brief-kind output, same read-only identity, same console tab. This is the
plumbing Q5 needs; the gating experiment can now run on real harvested series rather
than waiting on any new collection.

## Open questions to pressure-test next (no build)

**Track A (traces):**
1. Resample `lab.trace_events` to an hourly latency series for the 2–3 busiest
   `(target, protocol)` pairs — **is there enough non-gap history to forecast at
   all?** This is the Track-A gating experiment; run it before any harness.
2. Run `moirai-2.0-R-small` zero-shot on that series and eyeball MASE/CRPS vs a
   naive baseline. Weak-but-honest is an acceptable answer.
3. For MoiraiAgent: can the ADR dates be turned into the NL-context format the
   agent expects, and does context actually change the forecast at a known
   discontinuity (e.g. the D70 region repin)?

**Track B (infra/SRE):**
4. ✅ **Done (2026-08-11).** The metric surfaces ARE pullable headlessly as series and
   the harvester is built (CloudWatch `GetMetricData`, Cloud Monitoring
   `timeSeries.list` reusing `adk_source.py`'s auth, Azure Monitor via
   `azure-monitor-query`). It lands them in `lab.infra_metrics` on a 5-minute grid by
   default (`config/infra_metrics.yaml` `period_s`, tunable per series). Remaining:
   run it against the live estate and confirm the finest grid each cloud actually
   returns at that resolution.
5. Pick one dense series (Aurora ACU or a Fargate face's CPU) from the now-harvested
   `lab.infra_metrics` and run the zero-shot-vs-seasonal-naive eval. Track B *should*
   clear the bar Track A might not — if it doesn't, that reframes the whole
   exploration. **This is now unblocked** — the harvester feeds it.
6. Is the infra⋈trace correlation (#4 in Track B) expressible as an any-variate
   Moirai input, and does it actually attribute a trace latency spike to an infra
   cause on one known incident?

**Both:**
7. Does the license (CC-BY-NC) constrain anything we'd want to publish or host?
8. Which single use case is worth a workstream first? Current lean: **Track B
   predictive capacity + cross-cloud predictability**, because it has no
   data-density risk and needs no load harness — with Track A's ADR-context angle
   held as the *differentiated* follow-on once Track B proves the plumbing.

## Graduation criteria → becomes a `WS<n>`

Promote to a numbered workstream when: (a) a gating experiment (Q1 for Track A, Q5
for Track B) shows a zero-shot forecast beats a naive/seasonal-naive baseline on at
least one real series; (b) one concrete use case is chosen as the first deliverable
(current lean: Track B predictive capacity / cross-cloud predictability); and (c) the
deployment shape is confirmed as a reuse of the analyst/sentinel brief seam (plus, for
Track B, one metrics-harvester as an M11 sibling) rather than net-new infrastructure.
At that point: add `WS<n>` to `plan/07-workstreams.md` with item lines, an ADR for the
non-commercial-model decision and the metric-harvest access model, and flip the What's
Next chip to `planned`.

**Partial graduation — Track B plumbing + surface is now `WS22` (2026-08-11).** The
*infrastructure-metrics half* of criterion (c) has landed ahead of the forecasting
gate: the metrics-harvester (M11 sibling), the `lab.infra_metrics` store tables on both
backends, and the console **Infrastructure Metrics** section are built and recorded as
`WS22` items 1–6 in `plan/07-workstreams.md`. This does **not** mean the exploration
graduated — the *forecast* still waits on criterion (a): `WS22` items 7–9 (the live-grid
confirmation, the Moirai forecast runner, and the CC-BY-NC ADR) are explicitly held
**gated on Q5**, so promoting the harvester did not pre-empt the honest gating
experiment. The What's Next chip stays `exploring` until a real forecast beats naive on
a harvested series.
