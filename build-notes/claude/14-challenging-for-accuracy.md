# Challenging for accuracy — "prove it, don't theorize"

**Feature area:** the output side of the loop — what happens when the model
asserts a confident diagnosis that is *wrong*, the human pushes back, and the
steering tools (a "verify, don't assume" CLAUDE.md convention, live reproduction,
a background researcher, the permission gradient) turn the pushback into ground
truth instead of another guess.

## Engineering takeaway

A coding agent's most dangerous output is a plausible, confidently-stated wrong
answer — and the antidote is not "trust it less," it's *making it cheap and
normal to challenge it, and structuring the project so a challenge forces a
proof.* In this build the model twice explained an async failure with a tidy
theory ("the un-rebuilt Lambda," then "a malformed-URL bug") and stated each as
if settled. A one-line human challenge — *"did something not get pushed in the
build?"* and then simply being right that the theory didn't fit — collapsed both.
What made the correction land was not the model being scolded; it was the
project's own conventions kicking in: *reproduce before concluding*, cite the
`file:line` you're reasoning from, and never assert an irreversible cause you
haven't proven. The model's own words once it stopped: *"Let me stop theorizing
and actually inspect."* Live reproduction then produced a **third, more honest**
answer that contradicted *both* the model's theories *and* the "verified working"
claim it had inherited from a previous session.

Screenshot:
[`2026-08-02-root-cause-overturns-own-assumption.png`](screenshots/2026-08-02-root-cause-overturns-own-assumption.png)
— a separate session, same pattern: the model leading with *"The real root cause
(it was never Hawking, and never the network)"*, overturning a narrative it had
carried for a day.

## The story

**The setup.** Chasing the supplier-disruption fan-out, the console showed the
Google (Vertex AI Agent Engine) leg failing on a poll with a mangled URL —
`…/reasoningEngine` + `asks/{id}`, no `/tasks/` segment — surfacing as a
`MethodNotFoundError`. The user pasted it with one question: *"the async path is
still failing on that google agent? did something not get pushed in the build?"*

**Wrong theory #1 — the un-rebuilt Lambda.** The model had a clean explanation
ready: the header fix (pin `A2A-Version: 1.0`, WS11/D47) lived in
`src/interop/clients/a2a.py` + `config/targets.yaml`, both bundled into the
fan-out MCP Lambda (D41) — and the deploy that turn rebuilt the *console image
only*, not that Lambda. So the failing leg must be running pre-fix code in the
Lambda. It stated this as *"Confirmed root cause."* It was a good story. It was
wrong: the failing run was the **host-side tool** variant, whose legs run **in the
console process**, not the Lambda. The user said so.

**Wrong theory #2 — the malformed URL.** Corrected on the first point, the model
pivoted to the other visible anomaly and asserted it with equal confidence: the
URL `reasoningEngineasks/…` is genuinely mangled, so there's *"a URL-construction
bug in the SDK's get\_task path."* Also plausible. Also wrong.

**The challenge, and the turn.** The human didn't debug it for the model; they
just declined to accept the Lambda theory. That was enough to trip the project's
own discipline. The model's replies change register here — *"You caught a real
error in my reasoning… I was pattern-matching instead of checking,"* then *"Let
me stop theorizing and actually inspect,"* then it ran a **live probe** against
the actual target from `.env`. Ground truth came back different from both
theories:

- **The URL was never malformed.** The real poll URL is well-formed
  (`.../reasoningEngines/<id>/a2a/tasks/{id}`); the `reasoningEngineasks` string
  was a **console display truncation** in the hop narration, not the request on
  the wire. No SDK bug.
- **But the endpoint was genuinely misbehaving** — the submit call *blocked* for
  48s (`ratio 0.98`: the "submit" was the whole job; `return_immediately` was
  ignored), and *then* the returned task 404'd. That contradicted the "verified
  working end-to-end" claim inherited from a prior session.
- **And a real environment fact fell out of the probe:** the hosted console named
  the *default* targets (`google-adk-a2a`, `foundry-a2a`) while local `.env`
  overrode them to different deployments (`adk-logistics-a2a`,
  `foundry-commercial-a2a`) — hosted and local were consulting *different* Google
  and Foundry agents. None of the three theories in play would have surfaced that;
  running the actual code did.

The correction was captured plainly in the wrap-up rather than buried: *"I was
wrong twice (the Lambda theory, the malformed-URL theory) and you were right to
challenge both."* The eventual fix (the grace-window ride-through for transient
post-submit 404s, `poll_not_found_grace_s()` in `src/orchestration/runner.py`)
came from the reproduction, not from either guess.

**The companion (off-repo).** The screenshot shows the identical shape in a
different build: a JWT auth failure the model had spent a day attributing to
network egress and a specific infra ticket. Told to try the fallback path
anyway, it found the actual bug — a single metadata toggle
(`isNamedUserJwtEnabled = false`) — and *led* its summary with the reversal: *"it
was never [the ticket], and never the network."* A background researcher
independently confirmed the field name against the Metadata API docs before it
was asserted — the proof step, delegated. Same discipline, and the same tell: the
honest version of the story is the model **overturning its own earlier
conclusion**, out loud, once it was made to check.

## Why the steering tools work here

The correction wasn't luck or good manners — specific, nameable features made
"you're wrong" productive instead of just deflating:

- **A "prove it, don't assume" convention in CLAUDE.md.** The repo's headless-first
  rule is really a *verification* rule: *"A failed attempt is usually a wrong file
  or a missing field, not a platform limit — investigate the error, don't fall
  back."* The same instinct applies to a diagnosis: a failing leg is usually a
  wrong assumption about which code path ran, not the tidy systemic cause. A
  written convention is what a challenge *invokes* — the human says "no," and the
  model has a standing rule that says "then reproduce it."
- **Live reproduction is a first-class debugging move, not a last resort.** The
  probe run is what produced the truth that no amount of code-reading did — and it
  produced *more* than the answer to the question (the hosted-vs-local target
  divergence). "Read the code and reason" is where the wrong theories came from;
  "run it and watch" is where they died.
- **`file:line` citation makes a theory falsifiable.** Each wrong theory named its
  evidence (`app.py:2300`, `a2a.py`, `config/targets.yaml`). That's what let the
  human — and then the model — check the claim against reality rather than argue
  about vibes. A diagnosis that cites nothing can't be refuted; one that cites
  `app.py:2300` can be, and was.
- **A subagent does the proof you'd otherwise assert.** In the JWT case a
  background researcher confirmed the metadata field name against vendor docs
  before the fix was stated as fact. Delegating the verification is how "I think
  it's this field" becomes "this field, confirmed against the Metadata API docs."
- **The permission gradient is the backstop for a wrong theory that wants to
  act.** The most expensive version of theory #1 would have been *silently
  redeploying the Lambda* on a hunch. The same gradient in
  [07-permissions-guardrails.md](07-permissions-guardrails.md) that holds
  production-org and hosted-infra writes is what keeps a confident-but-unproven
  diagnosis from turning into an irreversible action before it's checked.

This is the complement to [12-steering-with-context.md](12-steering-with-context.md):
that note is about the *input* deciding the turn count; this one is about the
*output* being challengeable, and the project being built so the challenge lands
as a proof.

## Teaching points for the deck

- **Challenge is cheap; make it a habit.** The whole reversal turned on one line —
  *"did something not get pushed?"* — and the human being right that the theory
  didn't fit. You do not have to out-debug the model; you have to decline to
  accept an unproven cause. Budget for it: the confident wrong answer is the
  failure mode to watch for, and a two-word push is the cheapest control you have.
- **"Prove it" belongs in writing.** A CLAUDE.md convention that says *reproduce
  before you conclude, cite the line you're reasoning from, don't fall back to "the
  platform can't"* is what a challenge invokes. Without it, "you're wrong" just
  produces a new guess.
- **Reproduction beats reasoning — and tells you things the question didn't ask.**
  The live probe killed two theories and surfaced a hosted-vs-local target
  mismatch nobody was looking for. Running the code is a superset of reading it.
- **The honest artifact is the model overturning itself.** *"I was wrong twice and
  you were right to challenge both"* and *"it was never the network"* are more
  persuasive to a skeptical audience than a clean success — they show the loop has
  a working error-correction step, which is the thing a buyer actually needs to
  trust.

## Evidence and limits

- **Repository-backed:** the async fire-then-poll correction is recorded in
  `plan/00-decisions.md` (WS11/D47, incl. the 2026-08-11 "the cause was ours —
  a missing `A2A-Version` header" correction) and the grace-window ride-through
  lives in `src/orchestration/runner.py` (`poll_not_found_grace_s`,
  `dispatch_summary`); the two-variant dispatch it hinges on is the comment at
  `src/console/app.py:2300`.
- **Observed in this project (a2a lab):** the two wrong theories, the one-line
  human challenge, the *"stop theorizing and actually inspect"* pivot, and the
  live-probe ground truth (blocking submit, well-formed URL, hosted-vs-local
  target divergence) are from this build's session transcript, not a recorded
  matrix run — present them as a field observation.
- **Observed in the author's broader Claude Code work (off this repo):** the JWT
  root-cause screenshot is from a separate presentation-app build; the account id
  and hosted URL in it are **redacted** before commit, per the repo's
  no-account-identifier invariant. The GCP **project number** the async failure
  exposed in the console UI is likewise never reproduced in these notes.
- **Not a vendor claim.** "Challenging the model improves accuracy" and
  "reproduction beats reasoning" are observations from this work, not measured
  study results or documented product behavior. The *features* that made the
  challenge productive (CLAUDE.md conventions, subagents, plan mode, the
  permission gradient) are real Claude Code capabilities; the claim that they
  *caused* the better outcome here is an observation, graded as such.

## Put this in the presentation

**Slide headline:** Challenge the confident answer — and build the project so the
challenge forces a proof.

- The model twice stated a wrong root cause as settled ("the un-rebuilt Lambda,"
  "a malformed URL"); a one-line human challenge collapsed both.
- What made the correction land was the project's own discipline — *reproduce
  before concluding, cite the line, don't act on a hunch* — not the model being
  told to try harder.
- Live reproduction produced a third, honest answer that beat both theories *and*
  the inherited "verified working" claim — and surfaced a real hosted-vs-local
  mismatch nobody asked about.

**Visual:** the `2026-08-02-root-cause-overturns-own-assumption.png` screenshot,
with the header line *"The real root cause (it was never [the ticket], and never
the network)"* called out — the model overturning its own day-old narrative. A
footer strip carries the async wrap-up quote: *"I was wrong twice… and you were
right to challenge both."*

**Do not:** frame this as "the model is unreliable." Frame it as error-correction
working — the value is that a cheap challenge plus a verification convention turns
a wrong confident answer into ground truth, which is exactly what you want to show
a skeptical buyer.
