# Executive Overview — Meridiaan Group

**Audience.** Group CTO and the technology investment committee.
**Purpose.** A funding decision on an agent interoperability evaluation.
**Length of read.** Ten minutes. Everything below this page is detail.

---

## The situation

Five divisions have independently adopted agent platforms. This was not a
governance failure — each choice was defensible when it was made, two arrived
through acquisition, and the two divisions with the strongest regulatory
constraints made the most deliberate choices of all.

The result is nonetheless an estate where **the divisions cannot easily
coordinate the work they already share**. A tax question with a legal dimension,
a clinical enquiry with a contractual one, a customer commitment that touches
three divisions' obligations — these are ordinary Meridiaan business processes,
and each one now spans two or more agent platforms that have no established way
to talk to each other.

Three responses are available. Only one of them is real.

| Option | Why it fails or holds |
|---|---|
| **Consolidate onto one platform** | Not available. Divisional CTOs hold consent, not compliance; two divisions have board-level technical autonomy, and the regulatory posture of the health division makes a shared platform genuinely difficult rather than merely unpopular. Any plan predicated on consolidation is a plan that will not be executed |
| **Do nothing; let divisions integrate ad hoc** | Already happening, and it is the expensive option. Each pair of divisions solves the same problems again — identity, correlation, timeouts, redaction — and none of the solutions compose. The cost is invisible because it is distributed |
| **Standardise the seam, not the platform** | The only option compatible with divisional autonomy. Agree how agents address, authenticate to, and delegate to each other; leave every division's platform choice untouched |

The third option is the one worth evaluating. **This paper does not ask you to
adopt it. It asks you to fund finding out whether it works.**

## What is being proposed

A time-boxed **evaluation environment**: connect the five divisional platforms
to one another over the available inter-agent protocols, run realistic
cross-divisional business scenarios through them, and record every exchange at
the wire level.

It is an instrument, not a service. Its output is evidence and a
recommendation. It is explicitly built to be **decommissioned or promoted on a
decision**, not to accrete into an unplanned integration platform.

Critically, it carries **production-shaped constraints** — real service
identities, real residency obligations, real data-minimisation rules. An
evaluation run without them measures nothing that survives contact with
production, and would produce a confident wrong answer, which is worse than no
answer at all.

## What it will tell you

Five questions, each answerable with evidence rather than vendor assertion:

1. **Which interoperability patterns actually work across our estate** — stated
   per platform pair and per protocol, with failures named as plainly as
   successes. Vendor claims of protocol support are not comparable to each
   other; measurements are.
2. **What a cross-divisional agent interaction costs** — in consumption and in
   latency, at a unit level that scales to a business volume.
3. **Whether it can be made compliant** — with our residency and minimisation
   obligations, and at what cost in capability. This is the question the DPO
   will ask first and it currently has no evidenced answer.
4. **What we would have to build and keep running** to operate this at scale —
   concretely enough to compare against buying a platform that provides it. That
   build-versus-buy comparison is a deliverable, not an afterthought.
5. **What we have not established** — stated as visibly as the rest.

The fifth is deliberate. An evaluation that reports only its successes will be
read as having proved everything, and the decision taken on it will be wrong in
a direction nobody can see.

## What we already expect to find, and why we are measuring anyway

Three expectations are worth stating up front, so that confirming them counts as
a result rather than an anticlimax — and so that disconfirming them is visible.

- **Protocol support will be uneven in ways vendors do not document.** Two
  platforms both claiming the same protocol are likely to be at incompatible
  generations, with neither able to negotiate. *[assumed]*
- **Observability will degrade as the topology widens.** With two platforms,
  correlating an interaction by hand is easy and hides the problem. With four,
  we expect most participating platforms to be unable to demonstrate from their
  own logs that they took part — while every one of them returns success.
  *[assumed]*
- **The constraint on synchronous cross-divisional work will be a timeout chain,
  not model quality.** The slowest link is likely to be a platform's own action
  budget, which we do not control and cannot extend. *[assumed]*

Each is currently an assumption. The value of the exercise is converting them
into measurements — including the ones that turn out to be wrong.

## The shape of the investment

| | |
|---|---|
| **Duration** | Time-boxed. Phased, with a decision gate at the end of each phase *[assumed]* |
| **Team** | Small. Integration engineering, with divisional architect time drawn on per division *[assumed]* |
| **Divisional cost** | Each division supplies a scoped service identity and architect consultation. **No division is asked to change platform, migrate data, or alter its agent implementations** |
| **Running cost** | Model consumption, dominated by evaluation traffic rather than production volume. Reported in the units vendors actually bill, modelled at list price. See `60-cost/` |
| **Exit** | Decommission or promote, on a decision. Not open-ended |

The commercial detail lives in `60-cost/01-cost-model-and-projection.md`; the
sizing framework that takes our negotiated rates as inputs is
`60-cost/02-sizing-framework.md`.

## What we are asking each division for

Stated plainly, because divisional consent is the programme's binding
constraint and an unclear ask is how consent is lost.

| We ask for | We do not ask for |
|---|---|
| One scoped service identity per seam, permitting only what that seam calls | Administrative rights on any divisional platform |
| Architect consultation time | Engineering delivery capacity from the division |
| Permission to run evaluation scenarios against a non-production agent | Access to production business processes |
| Agreement on what data may cross which boundary | Any change to platform, tooling, or agent implementation |

## Principal risks

Full register in `70-delivery/02-risks-assumptions-dependencies.md`. Three
matter at this level:

- **A division declines to participate.** The estate has a hub — the division
  holding the customer system of record. If the hub declines, most
  cross-divisional scenarios become untestable. Mitigation is to secure hub
  participation before the first phase, not during it.
- **The evaluation is not representative.** Softening a constraint to make a
  scenario work — relaxing residency, using a broader identity, testing against
  a mock — produces a result that will not survive production. Mitigation is
  that constraints are requirements in this set, and any relaxation is recorded
  as a finding rather than absorbed silently.
- **The environment outlives its decision.** Evaluation systems that work tend
  to acquire dependents. Mitigation is the explicit exit criterion and the
  standing position that this is not a production integration platform.

## The decision requested

Approve funding for the evaluation, on the basis that it terminates in a
documented recommendation with the evidence attached, and that the recommendation
may legitimately be **"do not proceed"**.

An evaluation that can only conclude in favour of building is not an evaluation.
