# 17 — Jev (TypeSafe "System One") in the lab: three experiments

Raised 2026-10-02 (WS26, D82). Jev is TypeSafe AI's first "System One" model,
in limited early access since 2026-09-15. Sources: the vendor docs
(`docs.typesafe.ai` — introduction, concepts/system-one, quickstart, confidence,
patterns) and the Wikipedia article. **Every performance claim below is the
vendor's own**, measured on workflows the vendor built. The experiments exist
to put those claims next to numbers this lab takes itself.

## In TypeSafe's words — and where the lab tests each claim

From TypeSafe's launch card ("Meet Jev — our first (public) System One model",
Diogo, Erik and Sasha; the card is kept at `plan/images/jev.png`). The quotes are
theirs; the right-hand column is what this lab does with each.

> "A new class of AI models optimized for programmatic (inside code) use.
> Think: Smart if-statements."

| TypeSafe says | What the lab does with it |
|---|---|
| **Structured, machine-native outputs.** "Jev doesn't generate text, so you can't chat with it. To ask it questions, you have to define the shape of the answer. Kind of like writing multiple choice questions." | B is literally an if-statement: one `noul` per business unit, and the fan-out branches on the number (`src/orchestration/gate.py`). A asks whether that shape survives each protocol. |
| **Consistent, calibrated, probabilistic results.** "Jev tells you when it's uncertain with a confidence value you can use in code. Each decision is a calibrated probability, so similar inputs give similar outputs." | C measures *calibrated* against the lab's own wire outcomes; B's repeat run measures *consistent* (decisions identical across runs, Brier moved 0.0006). |
| **Trained differently.** RLCD — "Reinforcement Learning for Calibrated Decisions" — "to overcome issues such as mode dropping, hallucinations, and lack of reliability inherent to RLHF." | Not testable from outside; the lab tests the outputs the training is said to produce, and states that no paper or weights are published. |
| **20–200× faster, 40–1,000× cheaper.** | Both experiments record latency and `usage` beside a Haiku-tier baseline asked the same questions (`plan/03-results.md`). Against Haiku — not a frontier model — the gap is what the lab can actually show. |
| **Frontier-level intelligence for System 1 tasks.** "This is the hardest claim to defend, and no one in the field has found a good way to prove it. We hope you experiment with Jev and see for yourself." | That invitation is this plan. The lab's answer is narrow on purpose: two bounded System-1 decisions it already makes, on its own data, beside the obvious alternative. |
| **Limitations, in their words:** "Not good at System 2 tasks", "Not trained on specialized domains", "Not a generative chat model". | Why Jev is a decision service and not a seventh platform (D82), and the list in *Where it does NOT go* below. Supply-chain triage and "did an answer arrive" are deliberately general-domain. |

> "Our claims may sound too good to be true, but the bitterest lesson in AI is
> that optimizing for the right task gets you an unfair advantage."

## What Jev is, and what is new about it

Jev is not a chat model. It does not generate text. You send it a **state** (text)
and a set of named **questions**, and it returns one typed answer per question:

| Primitive | Asks | Returns |
|---|---|---|
| `choice` | pick one option from a list | `choice`, `probabilities` (per option), `confidence` |
| `score` | rate against ordered levels | `score`, `probabilities` (per level), `confidence` |
| `noul` | is this statement true? | `noul` — a probability in 0–1 |

The wire shape is one call: `POST https://api.typesafe.ai/v1/systemone`, bearer
key, body `{state, model: "jev-latest", questions: {name: {type, instructions,
criteria}}}`; the response is `{model, answers: {name: {...}}, usage}`. TypeSafe
publishes no MCP or A2A surface.

**Classifiers are not new; this combination is.** A trained classifier gives a
typed label and (if trained well) calibrated probabilities, but needs a training
set per task. An LLM needs no training set, but its output is text to parse, and a
"confidence" it writes is more generated text, not a measurement. Jev claims both
halves at once: zero-shot from instructions, **and a probability distribution over
the options you defined, trained against outcomes** ("RLCD", reinforcement
learning for calibrated decisions), at a claimed 40–1,000× lower cost and 20–200× lower
latency than a frontier LLM. Confidence is not a second model output — it is arithmetic on the
distribution (`confidence = (p_max − 1/n) / (1 − 1/n)` for Choice), and the docs
recommend acting automatically above 0.9, proceeding with caution from 0.5 to 0.9,
and routing to a human or another system below 0.5, with thresholds scaled to the
risk of the action.

So everything rides on one claim: **are the probabilities calibrated?** If a 0.8
is right about 80% of the time, Jev is a cheap decision that knows when it might be
wrong, and code can gate on it. If not, the confidence value is a guess in a
well-formatted field. TypeSafe has published no paper, weights or independent
evaluation. Experiment C measures exactly this.

**What it suits:** high-volume, bounded-answer decisions where knowing when *not*
to trust the answer matters — routing, gating, triage, guardrails. **What it does
not suit:** anything that needs prose, reasoning steps or an explanation. TypeSafe's
own guidance is to pair it with deterministic code and escalate low-confidence
cases to an LLM or a person.

## Where it does NOT go

- **The delegation guard (D27) and bridge routing (`config/targets.yaml`).** They
  are deterministic on purpose. A probabilistic router would make the lab less
  honest.
- **Anything a person reads.** Briefs, the Lab Guide and Details panes stay with
  the LLMs.
- **The production Salesforce org.** None of these experiments write to it.

## Experiment B — a confidence-gated fan-out in Supplier Disruption (BUILT and RUN, WS26 items 1–5; hosting item 6)

**The question.** In `supplier-disruption-cma`, the host-side
`consult_business_units` tool (D41's "tool" topology) runs **every** leg on every
situation: Logistics on ADK, Commercial/Legal on Foundry, Customer operations on
OpenAI. Can a cheap System-One step decide which units a situation actually needs
— the routing work the orchestrating model does in the "mcp" topology — without
worse coverage?

**The shape.** One Jev call per situation, with one `noul` question per business
unit ("this situation requires the Logistics unit to act"). The gate
(`src/orchestration/gate.py`) turns each probability into a decision using the
docs' bands:

| `noul` | Decision | Why |
|---|---|---|
| ≥ `A2ALAB_JEV_GATE_DISPATCH` (0.9) | **dispatch** | confident yes |
| ≤ `A2ALAB_JEV_GATE_SKIP` (0.1) | **skip** | confident no |
| between | **dispatch, marked uncertain** | the lab's own contract |

The middle band dispatches rather than skips because of the runner's rule — *a
missing leg is reported, never omitted* (`src/orchestration/runner.py`). An
uncertain skip would be a silently missing leg. And a **skipped leg is still
reported**: the hand-off to the model carries a `[leg skipped by gate: …]` line
with the probability, so the brief can say which units were not consulted and why.
If the Jev call fails (no key, timeout, HTTP error) the gate **fails open**: every
leg dispatches and the hand-off says the gate was unavailable. An outage can lower
efficiency; it must never lower coverage.

**Where it runs.** Three places, one code path:

1. **The console** — a "Gate: none | Jev" radio on `supplier-disruption-cma`
   (`gate_toggle` in `config/scenarios.yaml`). Only meaningful under the "tool"
   topology, because under "mcp" the model itself is the router.
2. **`scripts/run_fanout.py --gate jev`** — on the direct path and under
   `--orchestrator cma`.
3. **`scripts/jev_gate_eval.py`** — the measurement. It runs the gate alone (no
   legs, cheap) over a labelled set of situations
   (`config/jev_situations.yaml`), and reports per-unit precision and recall,
   how many legs a perfect gate would have saved, and how many it actually saved
   or wrongly skipped. `--compare haiku` runs the same questions through a
   Haiku-tier LLM classifier as the baseline.

**Label provenance, said plainly.** The situations and their expected units are
**author-labelled** — written for this experiment, not drawn from a real
incident log. That makes recall against them a measure of agreement with the
author, not with ground truth, and the eval prints that caveat with the numbers.
The default Rotterdam situation needs all three units, so a gated run of it
should dispatch all three; the set includes situations that need only one or two
units on purpose, because a gate is only interesting when it has something to
skip.

**What to record.** Per-unit recall (a missed unit is the expensive error),
legs saved, gate latency, and Jev's `usage` next to the baseline's tokens. Then
one live gated console run per situation class, with the trace id, in
`plan/03-results.md`.

## Experiment C — is Jev calibrated on this lab's own wire data? (BUILT and RUN, WS26 items 7–9)

**The question.** When Jev says a recorded agent response "contains a substantive
answer" with probability *p*, is it right about *p* of the time?

**Why the lab can answer it.** Every hop the lab has run since 2026-07-10 is in
`trace_events` (`traces/lab.db`, or the JSONL archive) with its **raw wire bytes**
(the trace layer, `src/interop/trace.py`). For a useful slice of those hops, the
wire already says whether an answer arrived — the hop failed, the A2A task state
is `TASK_STATE_FAILED`, the payload carries a JSON-RPC `error`, the answer text is
empty, or a fan-out leg rendered `[leg unavailable: …]`. That is a label nobody has
to write by hand.

**The method** (`scripts/jev_calibration.py`, library in
`src/platforms/jev/calibration.py`):

1. **Extract.** Walk `trace_events`, pull the request and response text out of
   each hop's raw payload (A2A tasks and artifacts, Agent API messages, REST
   `text`, MCP `content`, JSON encoded twice), and assign a **wire label** where
   the bytes decide it: `no` (error status, failed task, JSON-RPC error, empty
   answer, unavailable-leg marker) or `yes` (a completed hop with a non-trivial
   answer and none of those markers). Hops with no answer to judge — `pending`
   polls, submit acknowledgements, internal bookkeeping — are excluded and counted.
2. **Ask.** One Jev `noul` per hop, over **text only** — Jev sees what a caller
   sees, never the status column the label partly came from.
3. **Measure.** Brier score, expected calibration error over 10 bins, a
   reliability table (predicted bin vs observed rate), accuracy at 0.5, and
   coverage vs accuracy at the vendor's bands (act above 0.9 or below 0.1, else
   abstain). `--compare haiku` asks a Haiku-tier model for a probability in the
   same frame, so "is Jev calibrated" sits next to "is the obvious alternative
   calibrated", with tokens for both.
4. **Disagreements are the finding, not noise.** A hop the wire labels `yes`
   that Jev scores low is often a *refusal or apology that arrived with HTTP 200*
   — "I cannot fulfill this request — my capabilities are limited to research
   tasks" (supplier-disruption async run, 2026-08-12) is exactly that shape. That
   is the lab's oldest lesson, "check content, not status" (2026-07-25,
   `plan/03-results.md`). The script writes every disagreement to a review file
   for the operator to adjudicate, and the adjudicated count goes beside the
   calibration numbers.

**Bias to state with the numbers.** The `yes` label is weaker than the `no`
label: "completed with text" cannot see a polite refusal, which is precisely the
case where Jev might be right and the wire wrong. So C reports calibration
against the wire label **and** the list of disagreements, rather than one
headline number.

**What C does not measure: correctness.** The statement is "a substantive answer
*arrived*", not "the answer is *right*". The set contains answers that arrived and
are wrong — one leg explains "A2A" as an Account-to-Account payment method — and a
deterministic `[openai-stub]` placeholder the wire marked `ok`. The first counts as
substantive under the label and is meant to; the second is a disagreement worth
finding. A correctness study would need a human label per hop and is not this one.

**The set, as extracted from the current `lab.db`** (2026-10-02): 2,049 hops read;
**652** unique judgeable (question, answer) pairs — 594 `yes` (completed) and 58
`no` (50 hop errors, 6 empty answers, 2 unavailable legs). Excluded and counted:
997 with no question to judge (data calls, polls), 35 too short to call, 27 open
tasks (submit acknowledgements). **115** of the 652 are Agentforce hops — all
labelled `yes`, and among them are clarifying questions and "I don't have access"
replies: the shape the disagreement review exists to surface. The classes are
unbalanced (91% `yes`), so Brier and accuracy are read beside the reliability
table and the status-ok subset, never alone.

## Experiment A — does a typed answer survive each protocol? (RECORDED, not built — WS26 item 10)

Wrap Jev as an `AgentAdapter` under `src/platforms/jev/`, serve it on REST, MCP
and A2A, and trace whether `{choice, confidence, probabilities}` arrives intact.
The code already predicts the answer: MCP carries structured results natively
(`structuredContent` with an `outputSchema`, `src/interop/servers/mcp.py`); the
lab's A2A server only ever emits a text `Part` (`src/interop/servers/a2a.py`) and
its client drops every non-text part (`_texts_from_parts`,
`src/interop/clients/a2a.py`) — the protocol has data parts, the lab never used
them; and Path A ends at an Apex invocable whose contract is a string. Building A
means adding data-part support to the A2A seam, which needs its own ADR, and the
result is a "structured output fidelity" row in `plan/02-matrix.md` for every
platform. Deferred until C says Jev's output is worth carrying.

## First results (2026-10-02, `plan/03-results.md`)

- **B.** On the 14 labelled situations neither Jev nor Haiku made a wrong skip and
  both saved 12% of legs — nearly all on the two out-of-scope situations. Jev's
  probabilities sat closer to the labels (Brier 0.102 vs 0.133) and its decisions
  were identical across two runs. The bands, not the model, are what bind: Jev put
  36 of 42 unit decisions between 0.1 and 0.9, so they dispatched. A live gated run
  showed the cost — a 0.33 Logistics leg was sent and was the one that timed out.
  A wider skip line is the next thing to test, on situations not used to pick it.
- **C.** Against the wire label the two models tie; 16 of 21 disputed hops were
  non-answers that arrived as 200s, so the wire was the one wrong. Against the
  adjudicated label Jev's Brier is 0.019 vs Haiku's 0.033, every confident decision
  from either was right, and Jev's error is underconfidence (0.6–0.9 scores were all
  answers) — the safe direction for a gate.
- **Practical note.** Run these from a plain terminal: a shell that inherits Claude
  Code's own `ANTHROPIC_BASE_URL` sends the Haiku baseline to that gateway and every
  call fails with a connection error.

## Running it

```sh
cd ~/projects/claude-code/rc-a2a
set -a; source .env; set +a      # needs TYPESAFE_API_KEY (and ANTHROPIC_API_KEY for --compare)

# B: the gate alone, over the labelled situations (cheap — no legs run)
uv run python scripts/jev_gate_eval.py
uv run python scripts/jev_gate_eval.py --compare haiku

# B: one real gated fan-out (runs the dispatched legs on their platforms)
uv run python scripts/run_fanout.py --gate jev --task "A customer's CFO disputes the late-delivery penalty on invoice 4471."
uv run python scripts/run_fanout.py --orchestrator cma --gate jev

# C: extract, then calibrate (start small — every hop is one Jev call)
uv run python scripts/jev_calibration.py extract
uv run python scripts/jev_calibration.py run --limit 200
uv run python scripts/jev_calibration.py run --limit 200 --compare haiku
```

`TYPESAFE_API_KEY` belongs in `.env` and AWS Secrets Manager like every other
credential (`scripts/env_sync.py push`, D39). The hosted console needs it in its
task secret before the gate radio works hosted; without it, the gate fails open
and says so.
