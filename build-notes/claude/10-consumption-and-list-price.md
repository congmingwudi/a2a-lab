# "How much will this cost?" — reporting consumption honestly

The hardest question in a consumption-based deal, and the one where a confident
answer is usually a wrong one. This note is the framing this lab arrived at
while building its own cost telemetry (note 08), generalized past Anthropic to
any metered service — and the specific reporting mistake the lab itself made and
then fixed.

Written for a presales context: the person asking "what will this cost us" is
usually asking two different questions at once, and separating them is most of
the work.

## Engineering takeaway

**Cost has two independent factors, and only one of them is an engineering
property.** Units per unit of work is measurable, reproducible, and improvable.
Price per unit is a contract. Quote their product and you have made a commitment
about something you do not control; quote them separately and both conversations
become answerable.

---

## 1. Why "how much will it cost?" has no honest single answer

Every consumption product has the same two-factor structure:

| Factor | Nature | Who controls it | Knowable at design time |
|---|---|---|---|
| **Units per unit of work** — tokens per resolved case, per PR reviewed, per document processed | Engineering property | The people building it | **Yes** — measurable, and it improves with caching, model choice, effort tuning |
| **Price per unit** | Commercial property | Procurement, contract, timing | **No** — promotions, enterprise agreements, committed spend, tier changes |

The failure mode is quoting the product. The customer hears a dollar figure and
reasonably treats it as a commitment, when the speaker had authority over only
one of its two factors. When the number later moves — because a promotion ended,
or a contract was renegotiated, or list price changed — it reads as an
engineering miss, and it was not one.

The defensible shape of the answer:

> *"This workload runs about 180K tokens per resolved case, roughly 70% of it
> cache reads. Here's the measured distribution across 200 cases. Apply your
> rate card."*

Now the procurement conversation is about the rate card, the engineering
conversation is about the denominator, and neither contaminates the other.

**This also survives model changes, and the dollar figure does not.** Token
counts shift between model generations when the tokenizer changes, so even the
unit count needs re-baselining per model — a fact you can state as a discipline
rather than discover as a surprise.

## 2. What the vendor actually exposes — and the trap inside it

The unit of account on the Anthropic API is tokens, and it is **not one number**.
Every response carries a usage object with four separately-billed categories:

| Field | What it is | Roughly bills at |
|---|---|---|
| `input_tokens` | the **uncached remainder** of the prompt | 1x |
| `cache_creation_input_tokens` | cache writes | 1.25x (5-min TTL) / 2x (1-hour) |
| `cache_read_input_tokens` | cache reads | ~0.1x |
| `output_tokens` | generated tokens | output rate |

The trap is the first row. `input_tokens` is **not the prompt** — it is what was
left after the cache was consulted. The prompt actually processed is
`input + cache_read + cache_creation`. Anyone charting "input tokens" as the
input line is charting the thin slice, and on a long agentic session it is very
thin.

**This lab shipped that exact bug.** The harvest had stored all four buckets
since WS9; the console's build-telemetry endpoint returned two of them. One
harvested day read *120K input tokens* on a workload that had actually processed
*4.42M prompt tokens* — a 36x understatement, on precisely the workload shape
(long agent sessions, heavy caching) the section exists to measure. The fix is
in `src/console/app.py` → `/api/build-telemetry`; the guard against a regression
is `tests/unit/test_console.py` →
`test_build_telemetry_reports_all_four_billed_token_buckets`.

Nothing errored. The number was smaller than reality and looked completely
plausible. That is the same class of failure as note 08's silently-zero
telemetry, one level up: not "did the data arrive" but "does the number mean
what the label says".

**The consequence for reporting:** four buckets billing at ~1x / ~0.1x /
1.25–2x / output rate cannot be summed into one "tokens" figure and multiplied
by a rate. A single-number token report is not a simplification — it is
arithmetic that does not work.

There is a second-order use for the same split. When cost moves and token volume
does not, the explanation is usually a shift in the **mix** — a caching change,
a prompt restructure that broke a prefix, a model switch. That is only visible if
the buckets were kept apart.

## 3. Where the lab's own numbers are soft, and why that is fine

Two honest limits worth stating out loud rather than being caught on:

**The dollar column is a client-side estimate at list price.**
`claude_code.cost.usage` is computed by the agent from token counts against list
pricing. It is not an invoice, and on a subscription plan it is not money that
changed hands. The lab renders that caveat next to every rendering of the number
(`BUILD_TELEMETRY_COST_NOTE` in `src/console/app.py`), and the cost sentinel
(WS12) is instructed to lead with it.

**Consumption spanning two accounts cannot be totalled.** This lab's work runs
across two different Claude accounts depending on what is being built, so its
own dollar total is structurally approximate. Saying so is cheaper than being
asked.

Neither limit damages the actual result, because **the deliverable was never the
dollar figure** — it is the attribution method. Per-repo, per-project,
per-model, per-tool consumption, derived from OTel resource attributes rather
than from any vendor's billing export. Every vendor will hand you *their*
consumption; nobody hands you a comparable denominator across vendors. That is
the part a customer cannot buy, and it is unaffected by which account paid.

## 4. What to do with the framing in a room

- **Separate the two questions before answering either.** "Do you want the unit
  economics or the rate card? I can answer the first precisely and I shouldn't
  answer the second."
- **Bring a denominator, not a total.** Tokens per case, per PR, per document —
  with the distribution, not just the mean. A p90 is more useful than an average
  for anyone sizing a budget.
- **Show the mix, not just the volume.** The cache-read share is the single most
  actionable number in an agentic workload, and it is invisible in a two-bucket
  report.
- **Name the softness yourself.** Estimate vs invoice; list price vs contracted;
  subscription vs metered. Stating it costs one sentence and buys the rest of
  the numbers credibility.
- **Watch the ratio, not the total.** A dollar total moving might just be a
  promotion ending. Tokens-per-unit-of-work moving is a real engineering signal
  — which is why the cost sentinel (WS12) is built to explain movement rather
  than report a figure.

## Evidence and limits

- **Repository-backed** — the four-bucket under-reporting bug and its fix
  (`src/console/app.py`, `tests/unit/test_console.py`); the four buckets stored
  by the harvest since WS9 (`src/observability/coding_source.py`); the
  list-price caveat shipped with every rendering of the number
  (`BUILD_TELEMETRY_COST_NOTE`); the cost sentinel's instruction to lead with it
  (`scripts/setup_cost_sentinel.py`).
- **Vendor-documented** — the four usage fields and their relative billing
  multiples come from the Anthropic prompt-caching and pricing documentation.
  **Check current rates before quoting a multiple on a slide**; they are
  versioned and this note is not the source of truth for them.
- **Not claimed** — how Anthropic's commercial organization prefers to *present*
  consumption to enterprise customers (credit burn-down against committed spend,
  invoice reconciliation, Console usage dashboards). That is a contract surface,
  not an API surface, and this lab has no grounded evidence about it. The
  two-factor framing above is a general argument about metered services, not a
  description of anyone's pricing policy.

## Put this in the presentation

**Slide headline:** You can quote the units or the price. Quoting their product
is a commitment you don't control.

- Units per unit of work is an engineering property — measurable, improvable,
  and it survives a price change.
- Price per unit is a contract — promotions, agreements, timing. Not yours.
- Bring a denominator and a distribution, not a total.

**Visual:** two columns, "what we measured" and "what you pay", with an
explicit multiplication sign and a caption naming who owns each side.

**Second slide — the one with the number on it:** the four token buckets as a
stacked bar for one real harvested day, with the *input* slice labelled and the
36x understatement called out. Headline: **`input_tokens` is not the input.**
Three bars beside it — ~1x, ~0.1x, 1.25–2x — landing the point that these cannot
be summed and priced. It's a specific, checkable, slightly embarrassing finding
from this repo, which is what makes it land.

---

**In this repo:** `src/console/app.py` → `/api/build-telemetry` (the four
buckets and `token_note`), `src/console/static/index.html` (the four tiles and
the composition bar), `src/observability/coding_source.py` (the harvest that
stored them all along), `scripts/setup_cost_sentinel.py` (WS12 — the agent that
explains movement rather than reporting a total), `plan/07-workstreams.md` →
WS12. Related: note 08 for how the telemetry is attributed in the first place.
