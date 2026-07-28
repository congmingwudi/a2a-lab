# Stakeholders and Personas

Who has a stake in this system, what each of them needs from it, and — the part
that usually goes unwritten — what each of them would consider a *failure*.

## 1. Stakeholder map

| Stakeholder | Interest | Authority over the programme |
|---|---|---|
| Group CTO | Funds it; owns the group technology strategy | **Decides** — go/no-go, budget, scope |
| Divisional CTOs (×5) | Own their platforms and their engineering capacity | **Consent** — cannot be directed to change platform |
| Data Protection Officer | Group-level regulatory accountability | **Veto** — on any processing that cannot be justified |
| Enterprise Architecture | Owns integration standards and the estate model | **Advises**; owns the target-state reference |
| Divisional business owners | Own the processes the agents would serve | **Consulted** — supply the scenarios and judge the answers |
| Platform Operations | Runs whatever is delivered | **Consulted**; can refuse to accept an unoperable system |
| Information Security | Owns identity, credentials and third-party exposure | **Veto** — on credential handling and inbound exposure |
| Procurement / Vendor Management | Owns platform contracts and commercial terms | **Consulted**; consumes the cost and sizing outputs |
| Internal Audit | Tests whether regulated decisions can be reconstructed | **Consulted**; consumes the audit trail |

Two entries carry a veto, and both vetoes are exercised early rather than at the
end. That is a deliberate shape: the requirements that come from the DPO and
from Information Security are treated as constraints on the design space, not as
review gates applied to a finished design.

The **divisional CTOs hold consent, not compliance**. This is the political fact
that determines the architecture. A requirement whose satisfaction depends on a
division changing platform is not a requirement; it is a wish.

## 2. Personas

### Group CTO — executive sponsor

**Context.** Accountable for group technology spend and for the board's
expectation that the organisation is "doing something about agents". Has five
divisions telling them five different things about what is possible.

**Goals.**
- Know whether agent interoperability is worth investing in before committing to
  a multi-year programme.
- Get an answer grounded in evidence from the actual estate, not a vendor
  reference architecture.
- Avoid a decision that quietly commits the group to one vendor.

**Pains.**
- Every vendor claims interoperability; none of the claims are comparable.
- Cannot distinguish "the protocol supports it" from "our platforms do it".
- Cost of agent workloads is unpredictable and arrives after the fact.

**What they need from this system.** A defensible answer to *should we, and at
what cost*, with the evidence attached. A comparison that names what does not
work as clearly as what does.

**Failure looks like.** A demonstration that works on stage and cannot be
repeated with the estate's real constraints applied. Or a set of findings so
hedged that no decision follows from it.

---

### Divisional architect — per-division technical authority

**Context.** One per division; five of them, with very different starting
positions. `LGR` and `HCE` have their own engineering organisations and
board-level technical autonomy. `CTS` operates the corporate standard and the
system of record everyone else needs.

**Goals.**
- Understand exactly what their division must expose, and what it must not.
- Keep the change confined to a seam rather than spread through their platform.
- Not become a dependency that other divisions' incidents route through.

**Pains.**
- Their platform's inbound and outbound capabilities are asymmetric and neither
  is well documented.
- Cross-divisional requests arrive without context about who is really asking or
  why, which makes them impossible to authorise properly.
- They are measured on their own division's service levels, and a
  cross-divisional agent call is latency they do not control.

**What they need from this system.** A single, well-defined seam per direction;
explicit statements of what the seam carries; and the ability to say no to a
specific caller without saying no to the estate.

**Failure looks like.** An integration that requires changes inside their agent
implementation every time another division adopts a new protocol.

---

### Integration engineer — builds and operates the seams

**Context.** The person who actually makes two platforms talk. Works across
vendor documentation of wildly varying quality, and is the first to discover
that two implementations of the same protocol do not interoperate.

**Goals.**
- See the actual bytes on the wire when something fails.
- Reproduce a failure without needing five platforms' credentials and a
  production incident.
- Change which protocol a route uses without redeploying anything owned by
  another team.

**Pains.**
- Failures surface as a plausible-looking successful response with content
  missing — the hardest class of bug to notice, let alone diagnose.
- Correlation identifiers are dropped by at least one platform on every path.
- Vendor error messages describe the vendor's internal model, not the protocol
  violation.

**What they need from this system.** Raw wire capture at every hop, a local
loopback path that proves the plumbing without any external platform, and
protocol routing that is configuration rather than code.

**Failure looks like.** An observability layer that records that a call happened
and nothing about what was in it.

---

### Data Protection Officer — regulatory authority

**Context.** Group-level accountability across a business with EU
special-category data, US operations and per-client residency commitments. Holds
a veto and is prepared to use it.

**Goals.**
- Know, per seam, what data crosses which border on what lawful basis.
- Ensure personal data is minimised *before* it leaves a boundary, not filtered
  after arrival.
- Be able to demonstrate all of the above to a regulator, after the fact.

**Pains.**
- "The data is encrypted in transit" answers a different question than the one
  being asked.
- Inference location is routinely omitted from data-flow documentation, so
  transfers are missed entirely.
- Pseudonymised data is presented as though it were anonymous.

**What they need from this system.** A per-seam data-flow record; enforcement of
redaction at the boundary rather than by convention; residency treated as a
property of where inference runs; and a retained record sufficient to
reconstruct what a decision was based on.

**Failure looks like.** Discovering, from a trace archive, that prompts
containing personal data were sent to a model endpoint outside the EU because
nobody thought of a prompt as a transfer.

---

### Platform Operations — runs it

**Context.** Inherits whatever is delivered and is measured on availability and
mean time to resolution. Has no authority over any of the five vendor platforms.

**Goals.**
- Know which component failed when an interaction fails, without guessing.
- Have every configured route be one that has been exercised.
- Understand the timeout budget well enough to set alert thresholds that mean
  something.

**Pains.**
- Cross-platform failures have no single owner and no single log.
- Scale-to-zero runtimes make the first request of the day look like an outage.
- Capacity and cost are driven by model consumption, which is invisible in
  ordinary infrastructure monitoring.

**What they need from this system.** Per-hop status and latency, an explicit and
documented timeout chain, health surfaces on everything operated in-house, and
consumption reporting in the units the vendors actually bill.

**Failure looks like.** An alert that fires on total failure only, in a system
whose characteristic failure mode is a successful response with a section
silently empty.

---

### Divisional business owner — owns the process

**Context.** Owns a business process the agents are meant to serve. Does not care
about protocols and should not have to.

**Goals.**
- Get a complete, correct, attributed answer inside the time the process allows.
- Know which parts of an answer came from authoritative data and which from a
  model.
- Not be silently given a partial answer.

**Pains.**
- Answers arrive with no indication of provenance.
- Slow answers are indistinguishable from failed ones.
- A truncated answer reads as a complete one.

**What they need from this system.** Attributed answers, an explicit coverage
statement when a request fanned out, and a response time compatible with the
process.

**Failure looks like.** Acting on an answer that was missing the leg that
mattered, with nothing on screen to indicate it was missing.

---

## 3. What the personas disagree about

Recorded because the requirements have to resolve these, and resolving them
silently is how a requirements set becomes unimplementable.

| Tension | Poles | Where it is resolved |
|---|---|---|
| Trace completeness vs data minimisation | Integration engineer wants full wire payloads retained; DPO wants the minimum retained for the shortest time | `DR-` — retention, scope and redaction of trace content are specified rather than assumed |
| Latency vs grounding | Business owner wants answers inside the process window; grounding in another division's system of record costs a full round trip inside the answer | `NFR-` — timeout budget is allocated explicitly and the depth of synchronous grounding is capped |
| Divisional autonomy vs estate consistency | Enterprise Architecture wants one integration standard; divisional CTOs hold consent and will not re-platform | `TR-` — the estate standardises the *seam*, never the platform |
| Openness vs exposure | Interoperability wants discoverable agents; InfoSec notes A2A discovery is anonymous by design | `SR-` — anonymous discovery is permitted and separated from anonymous invocation, which is not |
| Cost visibility vs measurement cost | Everyone wants per-interaction cost; per-interaction attribution is itself work no vendor does for you | `OR-` / `60-cost/` — attribution is specified as configuration to be added deliberately, with its limits stated |

## 4. Personas explicitly out of scope

Named so their absence is a decision rather than an oversight.

- **End customers of Meridiaan Group.** No externally-facing agent surface is in
  scope for this release. Every interaction is internal or between the
  organisation's own platforms.
- **Model builders / data scientists.** Model selection, tuning and evaluation
  quality are out of scope. The system compares *interoperability*, and treats
  each platform's model as a given.
- **Procurement as a decision-maker.** Procurement consumes the cost and sizing
  outputs but no requirement here targets a commercial negotiation.
