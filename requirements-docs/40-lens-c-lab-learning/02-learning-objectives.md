# Learning Objectives — Learning Instrument Lens

## How these are written

Each objective states a **capability** — something the practitioner can do
afterwards that they could not do before — rather than a topic they will have
covered. "Understand the A2A task lifecycle" is not an objective; it cannot be
assessed and it is satisfied by reading.

Every objective carries four fields:

- **Objective** — the capability, stated as a demonstrable action.
- **Why building is required** — what specifically is unavailable from reading.
  If this field is weak, the objective does not justify build time and should be
  met by reading instead.
- **Evidence of attainment** — how the practitioner would know, and could show,
  that they have it.
- **Artefact** — what the objective leaves behind for others. An objective
  producing no artefact benefits exactly one person.

Objectives are grouped by theme, and the ordering within each group is roughly
the order in which they become reachable.

---

## Group 1 — Protocol mechanics

### L1 — Compare the three protocol classes on semantics rather than syntax

**Objective.** Explain, for any proposed integration, what each protocol class
makes first-class and what it forces the caller to smuggle — and choose between
them on those grounds.

**Why building is required.** The specifications describe each protocol in its
own terms. The *comparison* exists nowhere, because no document has reason to
place them side by side against one workload.

**Evidence of attainment.** Given an unfamiliar integration requirement, predict
which protocol will force a workaround, and be right for the stated reason.

**Artefact.** A protocol mapping table: how each class expresses invocation,
response, session, correlation and failure.

---

### L2 — Explain agent discovery and what it implies for security

**Objective.** Describe how an agent-to-agent protocol advertises capability,
and articulate why anonymous discovery is a deliberate design decision with a
security consequence that must be handled rather than removed.

**Why building is required.** The requirement that discovery be open reads as a
detail in the specification. Its consequence — an unauthenticated endpoint that
must remain unauthenticated while everything around it is protected — only
becomes concrete when protecting the surface it sits in.

**Evidence of attainment.** Correctly separate anonymous *discovery* from
anonymous *invocation* when reviewing an exposure design, and explain why the
first is acceptable and the second is not.

**Artefact.** The exposure rules for a discoverable agent endpoint.

---

### L3 — Exercise both synchronous and asynchronous invocation, and explain when each helps

**Objective.** Determine whether asynchronous invocation will remove a timeout
ceiling for a given deployment — and recognise when it will not.

**Why building is required.** This is the clearest case in the set. The
asynchronous half of the task lifecycle can be fully implemented and still never
exercised, because a single optional configuration field is left unset and
everything works. Nothing signals it. The behaviour is discovered only by
setting the field and observing the difference.

The second half is subtler and unavailable from any document: on a scale-to-zero
runtime, **the polling is the compute**. Submit work and stay quiet, and it does
not progress, because the runtime is frozen between invocations. Asynchrony
removes a gateway ceiling and does not, by itself, buy unattended progress.

**Evidence of attainment.** Predict, for a given runtime's hosting model,
whether fire-then-poll will deliver unattended progress — and justify it from
the runtime's execution model rather than from the protocol.

**Artefact.** Measured comparison of synchronous versus asynchronous work
envelopes on the same seam, with hosting model stated as a condition.

---

## Group 2 — Interoperation between independent implementations

### L4 — Diagnose and mediate a protocol version wall

**Objective.** Recognise, from observed symptoms, that two implementations are
speaking different generations of the same protocol, and design a compatibility
layer that serves both without either negotiating.

**Why building is required.** Each vendor documents the generation it speaks.
Nobody documents the pair, because nobody owns the pair. The symptom — a method
rejected as unknown, or a description rejected as malformed for missing fields
one generation does not have — reads like a defect until it is recognised as a
generational mismatch.

**Evidence of attainment.** Given a failing exchange, correctly attribute it to
generational mismatch rather than to a defect, and specify the translation
required.

**Artefact.** Documented dialect differences and the translation rules between
them.

---

### L5 — Establish correlation across platforms that do not preserve it

**Objective.** Design correlation for a heterogeneous estate, knowing that
structured identifiers are each dropped by at least one platform.

**Why building is required.** Every platform documents the correlation
mechanism it supports. The empirical fact — that no structured channel survives
every hop, and that the only universally-surviving channel is the message
content itself — is a property of the set, discoverable only by traversing it.

**Evidence of attainment.** Predict which correlation channels will survive a
given path, and design a fallback that survives when they do not.

**Artefact.** Per-platform correlation survival table, and the design of a
channel that survives regardless.

---

### L6 — Enumerate what the protocol class does not provide

**Objective.** State which mechanisms an integrator must supply themselves
because no protocol in the class defines them — delegation depth, loop
prevention, caller identity semantics, purpose propagation.

**Why building is required.** This is the central argument of the whole lens.
Specifications describe what they define; none has a section listing what it
omits. The omissions are discovered by needing them and finding nothing there.

**Evidence of attainment.** Produce the gap list unprompted when reviewing any
new agent protocol, and correctly predict which gaps it will have.

**Artefact.** The gap inventory — the primary input to `03-build-vs-buy.md`.

---

## Group 3 — Distributed agent behaviour

### L7 — Reason about delegation as a distributed systems problem

**Objective.** Recognise that a fully-wired agent estate makes circular
delegation possible by construction, and specify bounds that hold without
relying on the good behaviour of participating agents.

**Why building is required.** The failure requires two agents each behaving
correctly. It cannot be found by reviewing either one, and it does not appear at
the scale of a single integration.

**Evidence of attainment.** Identify the cycle risk in a proposed topology
before it is built, and specify enforcement at the seam rather than in the
agents.

**Artefact.** Delegation control design: depth, identity, refusal semantics.

---

### L8 — Design fan-out with partial failure as the normal case

**Objective.** Specify a 1:many interaction in which a missing contribution is
reported rather than omitted, and explain why orchestrator placement changes the
guarantees available.

**Why building is required.** Partial failure is *the* operating condition of
multi-agent work and is nearly absent from introductory material. The placement
consequence — that a host-executed orchestration gives you enforceable rules but
cannot run unattended, while a framework-declared graph runs unattended but
relies on several independent models relaying failure markers faithfully — is
visible only by building both.

**Evidence of attainment.** Given a fan-out requirement, choose an orchestrator
placement and state precisely which guarantee is being given up.

**Artefact.** Comparison of orchestrator placements against the same scenario,
with partial-failure behaviour measured for each.

---

## Group 4 — Operating the estate

### L9 — Assess the limits of cross-platform observability

**Objective.** Determine, for a given estate, what proportion of participating
platforms can demonstrate their participation from their own records — and
recognise that this degrades silently as topology widens.

**Why building is required.** Every platform's observability documentation
describes its interior view. The join problem exists only between platforms, and
its silence is the point: every platform returns success and a good answer while
losing the ability to say it took part.

**Evidence of attainment.** Predict the join rate of a proposed topology, and
identify which losses are structural versus merely unconfigured — a distinction
that determines whether the fix is possible at all.

**Artefact.** Join-rate measurement method, and per-platform results with causes
classified.

---

### L10 — Account for consumption in the units that are actually billed

**Objective.** Report consumption in separately-priced categories, and explain
why a single aggregate figure is not a simplification but an error.

**Why building is required.** The categories are documented by every vendor. The
*consequence* of conflating them is not: the mistake produces figures wrong by
more than an order of magnitude, triggers no error, and looks entirely
plausible. It is learned by making it, or by seeing it made.

**Evidence of attainment.** Identify the error in a consumption report on
inspection, and reconcile a corrected report against a vendor's own figures.

**Artefact.** Consumption accounting model with categories kept separate, and
the two-factor framing that keeps consumption-per-task distinct from
price-per-unit.

---

### L11 — Treat agent seams as regulated data flows

**Objective.** Analyse an agent-to-agent interaction as a data processing
activity: what crosses, on what basis, where inference occurs, and what must be
minimised before transmission.

**Why building is required.** Agent documentation treats prompts as input, not
as transfers. The recognition that **sending a prompt to a model endpoint in
another region is a transfer of whatever that prompt contains** is not stated in
any platform's material, and it is the point most integration designs miss.

**Evidence of attainment.** Review an agent integration design and identify the
transfers its data-flow documentation omitted — inference location first among
them.

**Artefact.** Per-seam data-flow analysis method.

---

## Group 5 — Consolidation

### L12 — Evaluate an unfamiliar agent platform quickly

**Objective.** Assess a platform not previously encountered — its inbound and
outbound surfaces, protocol generations, correlation behaviour, observability
exposure and consumption reporting — in substantially less time than the first
platform took.

**Why building is required.** This capability is the accumulated residue of
L1–L11. It cannot be acquired directly; it is what having done the others
produces.

**Evidence of attainment.** Onboard an additional platform in materially less
time than the first, and correctly anticipate most of its gaps in advance.

**Artefact.** A platform onboarding checklist derived from the estate rather
than from any single vendor.

---

### L13 — Explain all of it to someone who has not built it

**Objective.** Teach the material to a technical audience without requiring them
to build it, using observed behaviour rather than specification quotations.

**Why building is required.** Not required for the explanation — required for
its *credibility*. An explanation grounded in a measurement, with the conditions
attached, is believed and useful. The same explanation from documentation is
indistinguishable from every other summary of the same documentation.

**Evidence of attainment.** A technical audience can predict a behaviour they
have not seen, having only heard the explanation.

**Artefact.** The published findings corpus — the durable output of the entire
lens.

---

## Objectives deliberately excluded

- **Model behaviour, prompting technique, answer quality.** Out of scope (X3)
  and abundantly covered elsewhere.
- **Building an agent framework.** The objective is to understand
  interoperation *between* platforms, not to add another platform to the class.
- **Production operation at scale.** The estate is an evaluation environment
  (X4, X8). Operating one at production scale is a different and much larger
  objective, and pretending otherwise would misrepresent what the findings
  support.
