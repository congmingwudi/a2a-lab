# Glossary

Terms are defined as this requirements set uses them. Where a term has a looser
meaning in general usage, the narrower meaning here is the binding one.

## Agents and platforms

**Agent** — a software component that accepts a natural-language request,
performs reasoning and optionally calls tools, and returns a natural-language
response. An agent is identified by a name and an address, and is invoked; it is
not a library.

**Agent platform** — the vendor-supplied environment an agent runs in: the model
serving, the execution runtime, the tool-calling mechanism, and whatever
management surface comes with it. A division "has a platform" the way it has a
CRM — chosen once, expensive to change.

**Hosted agent** — an agent this system operates and exposes. The system owns
its front door.

**Remote agent** — an agent operated by someone else, reached over a network.
The system is a client to it and controls nothing about its interior.

**Platform-native surface** — an interface the vendor's platform exposes itself.
Contrast with an interface this system builds *on the platform's behalf*, which
is never native no matter how faithfully it implements the protocol.

**Closed platform** — a platform exposing no general inbound protocol surface
for external agents. Callers must use the vendor's own API. `CTS` is the
estate's closed platform, which is the single most consequential architectural
fact in this set.

## Protocols and interoperability

**Inter-agent protocol** — a wire protocol over which one agent invokes another.
This set treats three classes as first-class and requires the system to support
all three, because comparing them is the point:

- **Direct HTTP** — a plain request/response invocation over HTTP with a JSON
  body. Universally available, no discovery, no session semantics.
- **Tool-invocation protocol** — a protocol in which the remote capability is
  presented as a callable *tool* that a model selects, with a schema and a
  discovery mechanism. Sessions and correlation are not first-class; they are
  smuggled as tool arguments.
- **Agent-to-agent protocol (A2A)** — a protocol in which the remote party is
  addressed as an *agent* rather than a function: it publishes a discoverable
  description of itself, and work is modelled as a task with a lifecycle rather
  than a single call.

**Agent card** — the machine-readable self-description an A2A agent publishes at
a well-known address: identity, capabilities, supported transports and protocol
version. Discovery is anonymous by design, which has a security consequence
recorded in `SR-`.

**Task lifecycle** — the A2A model in which submitted work progresses through
states (submitted, working, completed or failed) and the result is retrieved as
an artefact of the completed task. Failure is a *task state*, not a transport
error — a distinction that matters because a failed task returns HTTP success.

**Synchronous invocation** — the caller holds the connection open until the
answer arrives. Simple, and bounded by every timeout on the path.

**Fire-then-poll (asynchronous invocation)** — the callee acknowledges
immediately with a task identifier and the caller retrieves the result later.
The A2A task lifecycle supports this natively. It is the mechanism that removes
gateway timeout ceilings, and support for it across platforms is uneven enough
that it must be verified per platform rather than assumed.

**Protocol dialect / version wall** — the situation where two implementations
both legitimately claim to speak the same protocol but at incompatible
generations, and **neither negotiates**. This is not an edge case; it is the
normal condition of a multi-vendor estate, and the system is required to handle
it rather than avoid it.

**Bridge** — a component that gives a platform an *outbound* capability it lacks:
it accepts the one call shape that platform can make, and re-issues it over
whatever protocol the destination requires.

**Shim** — a component that gives a closed platform an *inbound* capability it
lacks: it presents a real protocol endpoint on that platform's behalf and
translates each call to the platform's own API underneath.

The two are opposites and are routinely confused. *A bridge lets a platform call
the world; a shim lets the world call a platform.* Both are honest only if
labelled — a shim-served interface must never be reported as platform-native.

## Interaction shapes

**Delegation** — one agent invoking another as part of answering its own
request. The delegating agent remains responsible for the answer.

**Delegation depth** — how many delegation hops deep a request is. Because a
fully-wired estate makes circular delegation possible by construction, and
because no inter-agent protocol in this set defines time-to-live or
max-forwards semantics, depth must be carried and enforced by the system itself.

**Rider** — a delimited, versioned block of text attached to a delegated request
carrying the caller's identity, platform, delegation depth and correlation
identifier. Text rather than structured metadata because it is the only
correlation channel that survives every platform hop — structured fields are
each dropped by at least one platform. It doubles as provenance inside remote
platforms' own logs.

**Fan-out (1:many)** — one request decomposed into several independent
sub-requests dispatched concurrently to different agents, then recombined. The
shape that matches how enterprises actually work, and the shape that exposes
observability gaps a 1:1 call hides.

**Orchestrator** — the component deciding what to dispatch and recombining the
results. Where the orchestrator *lives* is an architectural choice with real
consequences: in the calling application, the caller owns concurrency, per-leg
timeouts and the partial-failure contract; declared inside an agent framework,
the framework schedules it and nothing calls back.

**Partial failure** — a fan-out in which some legs answer and some do not. It is
the **normal case**, not an exception. A missing leg must be reported, never
silently omitted, and the overall result must carry a coverage statement.

## Observability

**Hop** — a single agent-to-agent invocation across the wire. The unit of
observation.

**Wire payload** — the actual bytes sent and received, not a reconstruction or a
summary. Capturing these is a first-class requirement, because a comparison of
protocols that cannot show the wire is an opinion.

**Trace / correlation identifier** — the identifier that ties every hop of one
logical interaction together. Each protocol carries it differently, and at least
one carries it nowhere structural, which is why the rider exists.

**Platform execution record** — a platform's *own* interior view of a run:
sessions, reasoning steps, tool calls, token consumption. Held by the vendor, in
the vendor's shape, retrievable only through the vendor's API — if at all.

**Harvest** — pulling platform execution records into a local store so they can
be queried alongside the system's own wire traces. Deliberately deterministic
extraction, with any interpretation kept in a separate layer above it.

**Join rate** — of the platforms that participated in an interaction, the
fraction that can be tied back to it **from their own execution logs**. The
metric that reveals whether cross-platform observability actually holds. It
degrades as the topology widens, and it degrades *silently* — every platform
returns success and a good answer while losing the ability to say it took part.

**Scale-to-zero** — a runtime that consumes no resource when idle and must start
on demand. Buys cost, spends latency, and interacts badly with fire-then-poll:
on a frozen runtime the polling *is* the compute, so an unattended task may make
no progress between polls.

**Cold start** — first-invocation latency on a scale-to-zero runtime. A
non-functional requirement in its own right because it lands inside the user's
timeout budget, not outside it.

## Consumption and cost

**Token** — the unit models are metered in. Not a unit of work: the same
business question costs different token counts on different platforms.

**Token buckets** — the distinct categories a model API bills separately, at
materially different rates: uncached input, cached input reads, cache writes,
and output. They **cannot be summed and multiplied by a single rate**. Reporting
one blended token number is a defect, and a costly one — treating uncached
remainder as though it were total input understates consumption by more than an
order of magnitude while looking entirely plausible.

**Unit economics** — consumption per unit of business work, held separately from
the price per unit of consumption. The two-factor framing: a platform can be
cheaper per token and more expensive per answer.

**Modelled at list price** — a cost figure computed client-side from token
counts and published rates. An estimate, never an invoice, and required to be
labelled as such wherever it appears.

## Data protection

**Personal data** — any information relating to an identified or identifiable
natural person, in the GDPR sense.

**Special-category data** — GDPR Article 9 data, including health data. Subject
to a higher bar for lawful processing. In this estate it is `HCE`'s baseline.

**Data residency** — a commitment that data remains within a defined
geography. Critically, residency is a property of **where inference runs**, not
only where data is stored: sending a prompt to a model endpoint in another
region is a transfer, whatever the database is doing.

**Cross-border transfer** — movement of personal data out of its origin
jurisdiction. Between the Amsterdam and New York halves of the business this is
routine for commercial data and constrained for personal data.

**Redaction** — irreversible removal of identifying content before it leaves a
boundary.

**Pseudonymisation** — replacing identifiers with tokens that can be reversed
only with separately-held information. Still personal data under GDPR — a point
this set is explicit about, because designs routinely treat it as if it were not.

**Purpose limitation** — data collected for one purpose may not be freely reused
for another. In an agent estate this bites at delegation: forwarding a request
to a second agent for a second purpose is a distinct processing activity.

**Controller / processor** — the GDPR roles determining who decides the purpose
of processing and who acts on instruction. Each delegation seam is one or the
other, and which one it is has to be determined per seam rather than assumed for
the estate.

## System-of-record terms

**System of record** — the authoritative source for a data domain. In this
estate `CTS` holds customer, account and contract data.

**Grounding** — supplying an agent with authoritative data so its answer is
based on fact rather than model recall. Most cross-divisional agent traffic in
this estate exists to ground one division's agent in another division's data.
