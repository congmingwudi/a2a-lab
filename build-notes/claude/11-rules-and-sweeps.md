# Rules and sweeps — stopping documentation drift in a five-platform estate

**Feature area:** `CLAUDE.md` project rules as *prevention*, saved
`.claude/workflows/` multi-agent sweeps as *detection*, and the deliberate
decision to keep them separate from each other and from the existing audits.

## Engineering takeaway

**A rule and a sweep solve the same problem at different moments, and you need
both.** A project rule stops drift while the author still remembers what moved;
a periodic sweep catches what the rule missed. Neither replaces the other — and
a sweep whose evidence base is fuzzy produces findings nobody acts on, so keep
each sweep pointed at one corpus with one kind of proof.

## Why this problem is sharper here than in a normal codebase

This lab spans **five platforms** (Salesforce Agentforce, Anthropic Managed
Agents, OpenAI, Google Vertex AI Agent Engine, Microsoft Foundry) across **three
clouds**, with the lab's own infrastructure — bridge, console, protocol faces,
shims, fan-out, observability — hosted in a fourth place. Two consequences make
documentation drift a first-class engineering risk rather than a tidiness
concern:

1. **Nobody can hold the estate in their head.** `plan/09-deployment-map.md` has
   nine levels and a code→deployment table precisely because the answer to
   "where does this run" stopped being obvious. When the map is the only way to
   know, a wrong map is worse than no map.
2. **The documentation is a published product surface.** The console renders
   `plan/*.md` through `/api/docs`, parses the deployment map into its
   Architecture section, and — since D57 — every canvas carries a **Details**
   pane explaining where its content comes from. A stale Details pane is not
   untidy internal notes. It is *the console asserting something false to a
   visitor*, on a lab whose entire subject is honest reporting about platform
   behaviour.

The lab already refuses to overclaim in `plan/02-matrix.md`. Letting the
architecture diagrams overclaim instead would be the same failure wearing a
different hat.

## What triggered it

WS14 moved the credential-expiry collector off the operator's laptop. It had
shelled out to the `aws`, `az` and `gcloud` CLIs — which read local logins — so
the console's Credentials panel was only as fresh as the last time somebody
remembered to run a script. Rewriting the collectors against SDKs and folding
them into the existing 6-hourly harvest Lambda removed the dependency.

That change invalidated, in one commit:

- a mermaid diagram whose subgraph was literally labelled *"This machine — the
  only place collection can happen"*,
- a console Details pane explaining *why* collection could only happen locally,
- prose in `plan/09-deployment-map.md` and `plan/10-operations.md` describing it as a manual step.

**The operator caught the stale diagram, not the author.** That is the honest
origin of this note: the change was correct and the pictures were not, and
nothing in the process would have noticed.

## The rule (prevention)

Added to `CLAUDE.md`:

> **An architectural change updates its DIAGRAMS AND ITS CONSOLE COPY in the
> same change, not just the plan.** When a component moves host, changes
> identity, gains or loses a dependency, or stops being manual: update the
> mermaid diagram that draws it (`plan/09-deployment-map.md` levels, `config/diagrams.yaml`, the
> `*_DIAGRAM` constants in `index.html`), the console **Details** pane that
> narrates it, and any `plan/*.md` prose describing the old shape. **The test:
> grep the component's name across `plan/`, `config/diagrams.yaml` and
> `index.html` before calling the change done.**

Two properties make it work as a rule rather than an aspiration. It names the
**exact surfaces** to check, so following it is mechanical. And it ends in a
**grep**, so compliance is verifiable in seconds instead of requiring judgement.

## The sweep (detection)

`.claude/workflows/architecture-sweep.js`, same three-phase shape as the two
existing audits (see [01-workflows.md](01-workflows.md)):

1. **Inventory** — one agent collects every *architectural assertion* across
   `plan/`, `config/diagrams.yaml`, `README.md` and the console's `*_DIAGRAM`
   constants and Details panes. One entry per assertion, not per file.
2. **Check** — `pipeline()` runs one agent per assertion against the **deploy
   scripts and live cloud state** (`aws ecs describe-services`,
   `aws scheduler list-schedules`, `aws lambda get-function-configuration`),
   returning `accurate | stale | unverifiable`.
3. **Verify** — every claimed staleness faces an **adversarial refutation**
   before it is reported, defaulting to refuted when uncertain.

The refutation stage earns its cost here for a specific reason: the repo
deliberately keeps local fallbacks alive (the `az` CLI path, `run_local.sh`, the
sqlite store). A naive checker would flag every one of them as "stale — this is
hosted now", and sending someone to *delete* an intentional fallback is worse
than saying nothing.

## Why it is a THIRD sweep and not a bigger second one

The obvious move was to extend `matrix-honesty-sweep`. That was rejected:

| | matrix-honesty-sweep | architecture-sweep |
|---|---|---|
| Corpus | `plan/02-matrix.md` cells | diagrams + console Details panes |
| Evidence | `config/targets.yaml`, recorded runs | deploy scripts, live cloud state |
| Failure mode | **overclaim** — asserting support the lab cannot back | **drift** — describing a system that has moved |

One sweep with two evidence bases would have to reason about both at once, and
its findings would arrive without a clear action. Keeping them separate means
each sweep's output maps to one kind of fix: *stop claiming that cell*, versus
*update that picture*.

## The generalizable pattern

**Prevention and detection are different controls; name which one you are
building.**

- A **rule** is cheap, runs at the moment of change, and works only if it names
  concrete artifacts and ends in something checkable.
- A **sweep** is expensive, runs periodically, and is the only thing that finds
  what the rule missed — which is all a sweep should honestly claim.
- **One sweep, one corpus, one kind of evidence.** The temptation to grow an
  existing audit is what turns a sharp tool into a vague one.

The trigger for building either: ask *"if this went wrong, how would anyone find
out?"* — the same question `plan/09-deployment-map.md` L5.7 uses to decide what belongs in the
inventory of scheduled processes. When the answer is "they wouldn't", that is
the gap.

## Evidence and limits

- **Repository-backed** — the rule is in `CLAUDE.md`; the workflow is
  `.claude/workflows/architecture-sweep.js`; the WS14 change that motivated it
  is in `plan/07-workstreams.md` and the commit history for 2026-07-29.
- **Repository-backed** — the drift was real: the credentials diagram asserted
  *"the only place collection can happen"* about a process that had just been
  hosted, and a Details pane explained a constraint that no longer applied.
- **Observed in this project** — the operator, not the author, noticed. That is
  the argument for the control, and it is one instance, not a study.
- **Not yet claimed** — the sweep has been written and syntax-checked but had
  not been run end to end at the time of writing. Its value is asserted from the
  design and from the two sweeps that preceded it, not from a measured catch
  rate. **Do not put a hit-rate number on a slide.**

## Put this in the presentation

**Headline:** *Documentation drift is an engineering control problem, not a
tidiness problem.*

**Bullets:**

- In a five-platform, four-host estate, the architecture diagram is the only way
  anyone knows where something runs — so a wrong diagram is worse than none.
- Pair a **rule** (prevention, at the moment of change, ending in a grep) with a
  **sweep** (detection, periodic, adversarially verified). Different controls,
  different moments.
- Keep each sweep pointed at **one corpus and one kind of evidence**; the
  instinct to extend an existing audit is what makes findings unactionable.

**Visual:** two-column diagram. Left: *change happens* → CLAUDE.md rule → grep
across `plan/` + `diagrams.yaml` + `index.html`. Right: *time passes* →
architecture-sweep → Inventory → Check (vs live cloud state) → adversarial
Verify → findings with a named file and the line to fix. A dotted arrow from
right back to left labelled **"what the rule missed"**.

**Screenshot to capture:** the console's Credentials Expiry **Details** pane
before and after WS14 — the same panel asserting "the only place collection can
happen" and then describing a 6-hourly hosted job. It is the clearest single
image of why console copy is a documentation surface with teeth.
