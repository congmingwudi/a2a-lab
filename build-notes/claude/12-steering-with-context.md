# Steering with context — requirements, docs, and screenshots as inputs

**Feature area:** the input side of the loop — multimodal prompting (screenshots
of config screens and vendor docs), requirements written with their *purpose*
and *architectural direction*, and the model's own habit of refusing to run on
insufficient context.

## Engineering takeaway

The quality of what you put in front of the model decides how few turns it takes
to get a correct plan out. A scoped requirement that carries its *why* and the
architectural calls you've already made, a linked doc, or a raw screenshot of
the actual screen you're looking at — each one collapses a round-trip of
clarifying questions into an immediate, grounded plan. The complement matters as
much: the model that's been given good inputs will also tell you when it *lacks*
one, and decline to guess.

Screenshots:
[`2026-08-06-screenshot-corrects-inferred-design.png`](screenshots/2026-08-06-screenshot-corrects-inferred-design.png),
[`2026-08-10-scoped-requirement-well-scoped.png`](screenshots/2026-08-10-scoped-requirement-well-scoped.png),
[`2026-08-10-refuses-to-guess-metadata.png`](screenshots/2026-08-10-refuses-to-guess-metadata.png),
[`2026-08-08-cites-references-wont-guess-ips.png`](screenshots/2026-08-08-cites-references-wont-guess-ips.png).

## The story

Across a week of build sessions the same pattern kept paying off: the sessions
that moved fastest were the ones that started with a rich input, not a terse
ask.

**A screenshot of a vendor doc corrected a design the model had already
inferred.** Working the Data 360 Identity Match wiring, Claude had *guessed* the
shape of the match rule — a field-equality criterion
(`fieldName: ssot__MatchingRecordId__c`, `matchMethodType: exact`). Four
screenshots of the actual Identity Match documentation, handed in raw, overturned
that guess: IdentityMatch is a "bring your own links" mechanism that selects an
`IdentityMatchType` *value*, not a field-equality match, and it links
Individual↔Individual — which surfaced a real gap on the device side of the data
model. The model's own words: *"These screenshots resolve my one genuine unknown
— and they correct a conceptual error in my earlier design."* No transcription
step, no doc URL to fetch behind auth — the pixels were enough.

**A requirement written with its purpose and pre-answered architecture produced
an immediate, well-scoped plan.** The mobile-responsive console ask arrived as:
the observation that grounded it (*"I opened the a2a lab console from my iPhone
Chrome browser and noticed most of it is not mobile friendly"*), the goal
(navigate, run experiments, read the architecture diagrams from a phone), and the
architectural call already made and offered up (*"if this means hosting under a
separate `/mobile` web context — shouldn't, but just in case — I'm fine with
that"*). Claude's response was not a list of clarifying questions; it was
*"This is a substantial, well-scoped piece of work,"* followed by reading the
full stylesheet and structural DOM to map the layout before writing a line of
CSS. The pre-answered architecture question is the tell — because the human
supplied it, the model didn't have to stop and ask.

**Given good inputs, the model refuses to guess when one is missing.** Two shots
from the same week show the other half of the discipline:

- Chasing why an embedded Tableau Next dashboard wouldn't frame (the D72
  `frame-ancestors` work), Claude had a researcher pulling *authoritative*
  Salesforce docs on what actually controls external framing — and explicitly
  held off touching the org: *"I'll wait for that to land rather than guess at
  another metadata change — the last guess cost a deploy cycle."*
- Correcting a Data Cloud region mismatch (D69/D70), Claude found the two exact
  references (`.env:343`, `.env.example:183`), staged the doc corrections, and
  drew a hard line on the one input it didn't have: *"Holding for the
  researcher's `eu-central-1` /32 list before I touch config, .env, or the prod
  SG — I won't guess IPs into a firewall."*

Both are the same instinct as the permission gradient in
[07-permissions-guardrails.md](07-permissions-guardrails.md): an irreversible,
outward-facing action doesn't get taken on a hunch. Here it's self-imposed and
driven by *missing context* rather than a classifier — which is exactly why good
inputs are leverage: the model will wait for the input that matters.

## Why the inputs work

- **Screenshots are a first-class input, for two distinct jobs.** In
  [06-requirements-corpus-hardening.md](06-requirements-corpus-hardening.md) a
  slide deck was the *corpus to audit*; here a config/doc screenshot is the
  *ground truth that corrects the model's inference*. The 06 case turns prose
  into checkable claims; this case resolves an unknown the model would otherwise
  have filled with a plausible guess. Same modality, opposite direction of
  authority — and worth distinguishing on a slide.
- **A requirement is three things, not one.** *What* you want, *why* you want it,
  and the *architectural direction* you've already decided. The first alone
  yields clarifying questions; all three yield a plan. The `/mobile` aside did
  more work than the feature description.
- **A linked doc or `file:line` reference grounds the plan in reality.** The
  region-fix session named its own sources (`.env:343`) and cited the ADRs it
  would correct (D69, D70) — the same reference discipline the console renders as
  clickable D-chips.

## Teaching points for the deck

- **Put the screen in front of it.** A raw screenshot of the config screen,
  security setting, or vendor doc you're looking at is often faster and more
  accurate than describing it — and it can correct a design the model already
  committed to, not just inform a new one.
- **Write the requirement with its purpose and your architectural calls.** The
  detail you front-load is turn-count you don't spend later. The best signal that
  a requirement was complete is the model *not* asking questions back — it says
  "well-scoped" and starts mapping.
- **The model acknowledges good input — and flags missing input.** It will say a
  screenshot resolved its one unknown, and it will refuse to guess IPs into a
  firewall or metadata into a prod org. Both behaviors are worth showing: the
  payoff of context, and the safety of its absence.
- **Fewer turns is the measurable win.** This is the presales-relevant framing:
  rich, well-formed inputs reduce the clarifying-question round-trips, which is
  where wall-clock and token cost go in a real engineering session.

## Evidence and limits

- **Observed in this project (a2a lab):** the Identity Match doc-screenshot
  correcting the inferred match-rule shape; the mobile-console requirement
  producing a "well-scoped" plan; the D72 "I'll wait rather than guess at another
  metadata change" hold; the D69/D70 region fix citing `.env:343` /
  `.env.example:183` and "won't guess IPs into a firewall." D69, D70, D72 are
  recorded in `plan/00-decisions.md`; the region-topology framing is carried in
  the `datacloud-region-topology-is-demo-artifact` memory.
- **Observed in the author's broader Claude Code work (off this repo):** the
  walk-away deploy summary and the JWT root-cause session in
  [05-hooks-notifications.md](05-hooks-notifications.md) /
  [07-permissions-guardrails.md](07-permissions-guardrails.md). Those two
  screenshots are from a separate presentation-app build; the account id and
  hosted URL in them are **redacted** before commit, per the repo's
  no-account-identifier invariant.
- **Not a vendor claim.** "Screenshots improve accuracy" and "richer requirements
  reduce turns" are field observations from this work, not documented product
  behavior or a measured turn-count study. Present them as observations — they're
  more persuasive that way, and honest.

## Put this in the presentation

**Slide headline:** Steer with context — the input decides the turn count.

- A screenshot of the real screen (config or vendor doc) is a first-class input;
  it can *correct* the model's inferred design, not just inform a new one.
- A requirement carrying its purpose and your architectural calls yields a plan,
  not a round of clarifying questions.
- Given good inputs, the model refuses to guess when one is missing — it waits
  for the doc, and won't push IPs into a firewall on a hunch.

**Visual:** two-panel before/after. Left, the Identity Match shot with the
model's line *"they correct a conceptual error in my earlier design"* called out.
Right, the mobile-console requirement (the gray user-prompt box) paired with the
model's *"well-scoped piece of work"* reply. A footer strip carries the two
"refuse to guess" quotes as the safety complement.

**Do not:** claim a measured reduction in turns or a general vendor accuracy
improvement — these are observations from this build, graded as such.
