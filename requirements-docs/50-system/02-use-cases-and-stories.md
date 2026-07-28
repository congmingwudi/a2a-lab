# Use Cases and User Stories

Use cases describe complete interactions from a primary actor's point of view.
They exist to check the functional requirements for gaps: a step with no
requirement behind it is a missing requirement, and a requirement appearing in no
use case is either unnecessary or serving an unwritten scenario.

Extensions are as important as main flows here. In this system the interesting
behaviour — partial results, dialect mismatch, refusal, timeout — lives almost
entirely in the extensions.

---

## UC-01 — Ground a divisional answer in another division's system of record

**Primary actor.** Divisional business owner (`TAX`)
**Stakeholders.** `CTS` as data owner; DPO for what crosses
**Preconditions.** Both divisions onboarded; a route configured between them; the
caller holds a scoped identity accepted by `CTS`
**Trigger.** The business owner asks their divisional agent a question requiring
authoritative customer facts

**Main success scenario.**

1. The business owner puts the question to their own division's agent, through
   that division's normal interface.
2. The agent determines it cannot answer from its own knowledge and requires
   authoritative customer data.
3. The agent delegates to the system, which stamps the request with caller
   identity, platform, delegation depth and correlation identifier.
4. The system checks the delegation is within the permitted depth.
5. The system applies the boundary data rules for this seam, removing or
   pseudonymising anything the interaction does not require.
6. The system invokes the `CTS` agent over the route's configured protocol,
   presenting the caller's own scoped identity.
7. `CTS` answers from its system of record.
8. The system returns the answer to the delegating agent, recording both hops
   with their actual payloads.
9. The agent composes a reply distinguishing the authoritative portion from its
   own reasoning, and returns it.

**Extensions.**

- **4a. Depth limit reached.** The system refuses, returns an explicit refusal
  naming the reason, and records it as a refusal rather than an error. The
  calling agent reports it could not consult, rather than answering unaided.
- **6a. Timeout.** The per-target timeout expires. The agent returns its own
  portion with an explicit marker that the authoritative consultation did not
  complete. It does **not** substitute model recall for the missing facts.
- **6b. Authentication rejected.** Reported as an authentication failure
  distinct from a remote error, so the remedy is directed at the identity rather
  than at the route.
- **7a. `CTS` has no record.** A negative answer is returned and presented as
  authoritative absence, not as failure to consult — the two mean different
  things to the business owner.
- **5a. Required data cannot be minimised.** The interaction genuinely needs
  personal data the seam does not permit. The system refuses at the boundary and
  records the refusal as a finding, since it bounds what this seam can do.

**Postconditions.** An attributed answer is returned; both hops are recorded with
payloads and correlation; consumption is recorded per billed category.

**Traces to.** FR-201, FR-202, FR-204, FR-205, FR-206, FR-401, FR-403, FR-507;
BR-201, BR-203, BR-302

---

## UC-02 — Reach the closed platform as though it were a protocol peer

**Primary actor.** An agent on a division whose platform speaks only a standard
inter-agent protocol
**Stakeholders.** `CTS`; Enterprise Architecture, which needs to know the result
is mediated
**Preconditions.** Inbound mediation configured for `CTS`; the calling platform
knows only a protocol address
**Trigger.** A divisional agent needs `CTS` data and can only speak its protocol

**Main success scenario.**

1. The calling platform retrieves the published description of the `CTS`
   endpoint, anonymously.
2. It issues a standard protocol request to that endpoint, unaware mediation is
   involved.
3. The mediation determines the caller's generation of the protocol and
   translates the request if it differs from its own.
4. The mediation extracts the delegation context, including any carried in
   message content rather than metadata.
5. It invokes `CTS` through the platform's own interface with the appropriate
   scoped identity.
6. It translates the response back into the caller's generation and returns it.
7. Both the inbound protocol exchange and the platform call are recorded, with
   the capability marked **mediated**.

**Extensions.**

- **1a. Description rejected.** The caller rejects the description as malformed
  because it lacks fields its generation requires. The published description
  must satisfy every supported generation simultaneously; a description valid
  only for the newer generation fails here, and the failure occurs before any
  request is sent.
- **3a. Unknown method.** The caller's generation names the operation
  differently and the request is rejected as an unknown method. Translation must
  cover method naming, not only message structure.
- **5a. Vendor interface unavailable.** Reported as an agent-level failure in the
  caller's protocol form, not as a transport error, so the caller's own error
  handling behaves correctly.

**Postconditions.** The caller received a conformant response; the record shows
the capability as mediated.

**Traces to.** FR-302, FR-303, FR-304, FR-305, FR-402, FR-104; BR-201, BR-502

---

## UC-03 — Decompose a business event across divisions concurrently

**Primary actor.** Divisional business owner facing a cross-cutting event
**Stakeholders.** Every contributing division; Platform Operations
**Preconditions.** Three or more divisions onboarded with dedicated agents
**Trigger.** An event arises whose implications span divisions — a regulatory
change, a client incident, a supply disruption

**Main success scenario.**

1. The business owner submits the event description with the questions to be
   answered.
2. The orchestrator decomposes it into independent sub-requests, one per
   contributing division.
3. All sub-requests are dispatched concurrently, each carrying its own delegation
   context and per-leg timeout.
4. Each division's agent answers within its own platform.
5. The orchestrator collects the responses.
6. It composes one result with a section per expected contribution, each
   attributed to its division and source type.
7. It attaches a coverage statement.
8. The result is returned, and every leg is recorded with payloads and
   correlation.

**Extensions.**

- **4a. One leg fails or times out.** Its section is **present** and contains an
  explicit marker naming the missing contribution and why. The coverage
  statement reflects the shortfall, and the interaction does not report
  unqualified success.
- **4b. Several legs fail.** As above. There is no threshold below which the
  result is suppressed — a result with one contribution and an accurate coverage
  statement is more useful than no result and more honest than a silent one.
- **3a. A leg's platform is cold.** Cold-start latency may exceed the leg's
  timeout. The recorded measurement notes the cold state, so the finding
  concerns scale-to-zero economics rather than the division's responsiveness.
- **2a. Orchestration declared to a remote platform.** The decomposition is
  declared to a platform's own orchestration mechanism instead. Ordering is then
  guaranteed by that platform, but failure markers survive only if each
  participating model relays them faithfully — which is itself a measurement.

**Postconditions.** A composed, attributed result with an accurate coverage
statement; every leg recorded; join rate measurable across participants.

**Traces to.** FR-501, FR-502, FR-503, FR-504, FR-505, FR-506, FR-507, FR-607;
BR-202, BR-203, BR-204

---

## UC-04 — Complete long-running work asynchronously and deliver it into a business system

**Primary actor.** Divisional business owner
**Stakeholders.** Platform Operations; the receiving business system's owner
**Preconditions.** A target supporting genuine asynchronous completion; a
delivery path into a business system
**Trigger.** Work is required whose duration exceeds any interactive limit

**Main success scenario.**

1. The work is submitted; the system receives an acknowledgement and an
   identifier without waiting for completion.
2. The system records the submission against the correlation identifier.
3. Processing proceeds on the remote platform.
4. The system retrieves the result when available.
5. The result is delivered into an existing business system, attributed and
   correlated to the originating request.
6. The business owner is notified through their normal channel.

**Extensions.**

- **1a. Submission accepted but unretrievable.** The platform returns an
  identifier no request shape can read back. Recorded as
  *accepted-but-unretrievable* — a per-platform capability finding, distinct
  from both support and non-support.
- **3a. Frozen runtime.** The remote runtime suspends between invocations, so
  work progresses only while being polled. The submission remains incomplete
  indefinitely without polling. Recorded as a property of the hosting model, not
  of the protocol — asynchrony removes a gateway ceiling without buying
  unattended progress.
- **4a. Result never arrives.** After a configured limit the work is abandoned,
  recorded as abandoned with elapsed time, and the business owner is notified
  that it did not complete. Silence is not an acceptable outcome.
- **5a. Delivery fails.** The result is retained and delivery retried; the result
  is not lost because its destination was unavailable.

**Postconditions.** Either a delivered, attributed, correlated result, or an
explicit non-completion notification. Never silence.

**Traces to.** FR-207, FR-206; BR-206, BR-207, BR-203

---

## UC-05 — Compare one scenario across protocols

**Primary actor.** Integration engineer / evaluator
**Stakeholders.** Enterprise Architecture; Group CTO as consumer of findings
**Preconditions.** A scenario whose participants support more than one protocol
**Trigger.** A protocol comparison finding is required

**Main success scenario.**

1. The evaluator selects a scenario and the protocols to compare.
2. The evaluator warms the route's scale-to-zero components.
3. The scenario is executed over the first protocol; the run is recorded with
   its warm state.
4. The scenario is repeated over each remaining protocol, with only configuration
   differing and no participant redeploying.
5. The evaluator compares the runs on response content, latency, hop count and
   consumption.
6. A finding is recorded with its conditions, classified as measured, and
   referenced to the supporting runs.

**Extensions.**

- **4a. A protocol is unsupported for this pairing.** The combination is refused
  before execution with a reason, rather than producing a failure that would
  enter the record as a false finding.
- **4b. A protocol only works through mediation.** The comparison remains valid
  but the result is marked mediated wherever reported, since it does not
  generalise to the platform itself.
- **5a. Differences fall within run-to-run variance.** Recorded as *no
  detectable difference* — a legitimate finding, and one that must not be
  discarded for being unexciting.

**Postconditions.** A recorded, reproducible, classified finding with its
conditions attached.

**Traces to.** FR-103, FR-203, FR-602, FR-605, FR-607, FR-701, FR-702, FR-704;
BR-103, BR-501

---

## UC-06 — Diagnose a failed cross-platform interaction

**Primary actor.** Integration engineer
**Stakeholders.** Platform Operations; the divisions involved
**Preconditions.** A recorded interaction that failed or returned a suspect result
**Trigger.** An interaction fails, or returns a result that looks complete and is
not

**Main success scenario.**

1. The engineer locates the interaction by correlation identifier.
2. They review the hops in sequence with status, latency and classification.
3. They identify the first hop that deviated.
4. They inspect that hop's actual request and response payloads.
5. They determine the cause and classify it — transport, authentication,
   protocol generation, agent-level failure, timeout, or refusal.
6. Where the cause is a platform behaviour rather than a defect, it is recorded
   as a finding.

**Extensions.**

- **2a. The chain is broken at a mediation point.** The correlation was not
  preserved across translation. This is itself a defect against FR-305, because
  both halves appear complete and the break is invisible.
- **3a. Every hop reports success and the result is still wrong.** The
  characteristic failure. Diagnosis proceeds from payload content rather than
  status, and the finding concerns a platform returning success with content
  absent.
- **4a. Payloads were not captured for this hop.** A defect: wire capture is
  required at every hop, and a gap makes the whole record untrustworthy rather
  than merely incomplete.
- **5a. The cause is a protocol generation mismatch.** Identified from the
  payloads — an unknown method, or a description rejected for missing fields —
  and resolved by extending translation rather than by changing a participant.

**Postconditions.** A classified cause; where relevant, a recorded finding with
supporting payloads.

**Traces to.** FR-206, FR-305, FR-604, FR-701; BR-305, BR-501

---

## UC-07 — Demonstrate compliance for a cross-border interaction

**Primary actor.** Data Protection Officer
**Stakeholders.** Internal Audit; divisional architects; Group CTO
**Preconditions.** A seam carrying data between jurisdictions
**Trigger.** A periodic review, a new seam, or a regulator's enquiry

**Main success scenario.**

1. The DPO selects a seam and a period.
2. The system reports what categories of data crossed, in which direction,
   between which parties.
3. It reports where model inference occurred for those interactions.
4. It reports what was removed or pseudonymised before transmission, and by
   what rule.
5. It reports refusals where the boundary rules prevented a transmission.
6. The DPO confirms the report derives from what was actually transmitted, not
   from configuration.

**Extensions.**

- **3a. Inference location unavailable for a platform.** Recorded as a gap in
  the compliance record rather than as an assumption of compliance. This may be
  sufficient grounds to withdraw the seam.
- **4a. A transmission occurred that should have been minimised.** Treated as an
  incident, and the record must be sufficient to determine its scope — what was
  transmitted, to whom, and how often.
- **6a. The report derives from configuration rather than traffic.** Rejected.
  Configuration states intent; only the traffic record states what happened, and
  the difference between them is precisely what a review exists to find.

**Postconditions.** A per-seam data-flow record derived from actual traffic,
sufficient for a regulator.

**Traces to.** FR-701; DR- requirements in document 06; BR-301, BR-302, BR-303,
BR-305

---

## UC-08 — Onboard an additional division

**Primary actor.** Integration engineer
**Stakeholders.** The joining divisional architect; already-onboarded divisions;
Information Security
**Preconditions.** The joining division has agreed to participate and can issue a
scoped service identity
**Trigger.** A division joins the evaluation

**Main success scenario.**

1. The engineer records the division's platform capabilities: inbound and
   outbound surfaces, protocol generations, asynchronous support, observability
   exposure.
2. A scoped service identity is obtained and stored in the secret store.
3. Targets are configured for the division's agents, declaring protocol,
   address, authentication and timeout.
4. The seam is verified against the loopback baseline first, then against the
   division's real agent.
5. Delegation context is verified to survive a hop through the platform.
6. Observability exposure is assessed, and what will not join is recorded.
7. No already-onboarded division deploys any change.

**Extensions.**

- **1a. The platform cannot be reached directly in one direction.** Mediation is
  required for that direction, and every capability reached through it is marked
  mediated.
- **5a. Delegation context does not survive.** An alternative channel is
  determined by measurement. If none survives, the division cannot participate
  in multi-hop delegation, and that is recorded as a constraint rather than
  worked around.
- **6a. The platform exposes no usable execution record.** Recorded as a
  structural observability gap, distinguished from a merely unconfigured one —
  the first cannot be fixed, the second can.
- **7a. An existing division requires a change.** A defect against BR-104. The
  cause is investigated rather than accepted, because it means the seam is not
  standard.

**Postconditions.** The division participates; its capabilities and gaps are
recorded; onboarding effort is logged for the sublinearity hypothesis.

**Traces to.** FR-202, FR-204, FR-303, FR-402, FR-606; BR-104, BR-306, BR-502

---

## User stories

Complementary to the use cases: smaller units of value, phrased from the
persona's position, each pointing at the requirements that satisfy it.

### Integration engineer

| ID | Story | Satisfied by |
|---|---|---|
| US-101 | As an integration engineer, I need to see the actual bytes exchanged at every hop, so that I can diagnose failures whose two ends each appear correct | FR-604, OR- |
| US-102 | As an integration engineer, I need to run every protocol pairing with no external credentials, so that I can separate our plumbing from a platform's behaviour | FR-606 |
| US-103 | As an integration engineer, I need to change a route's protocol by configuration, so that comparisons do not require anyone's deployment | FR-203 |
| US-104 | As an integration engineer, I need failures classified by kind, so that I know whether to fix an identity, a route, or a translation | FR-206 |
| US-105 | As an integration engineer, I need to warm a route before measuring it, so that latency figures are comparable | FR-607 |

### Divisional architect

| ID | Story | Satisfied by |
|---|---|---|
| US-201 | As a divisional architect, I need to permit specific callers rather than all of them, so that I can participate without accepting unbounded exposure | FR-401, SR- |
| US-202 | As a divisional architect, I need my platform reached through one seam per direction, so that another division's protocol change is not my problem | FR-102, FR-201 |
| US-203 | As a divisional architect, I need delegation depth enforced outside my agents, so that I do not have to trust every other division's implementation | FR-403, FR-405 |
| US-204 | As a divisional architect, I need onboarding not to require changes from divisions already participating, so that joining late is not a burden on others | FR-202, BR-104 |

### Data Protection Officer

| ID | Story | Satisfied by |
|---|---|---|
| US-301 | As a DPO, I need minimisation enforced at the boundary rather than by the caller, so that a reconfigured caller cannot defeat it | DR-, FR-305 |
| US-302 | As a DPO, I need to know where inference occurred, so that residency analysis covers transfers and not only storage | DR- |
| US-303 | As a DPO, I need the compliance report derived from actual traffic, so that it reflects what happened rather than what was intended | FR-701, UC-07 |
| US-304 | As a DPO, I need refusals recorded, so that I can show the boundary rules are active rather than merely configured | FR-404 |

### Platform Operations

| ID | Story | Satisfied by |
|---|---|---|
| US-401 | As an operator, I need liveness and readiness on everything we run, so that a cold start is not indistinguishable from an outage | FR-105 |
| US-402 | As an operator, I need partial results to signal non-success, so that automated consumers do not act on incomplete answers | FR-505 |
| US-403 | As an operator, I need consumption reported in the categories that are billed, so that a cost anomaly is visible before the invoice | OR-, BR-401 |
| US-404 | As an operator, I need the timeout chain documented, so that my alert thresholds mean something | NFR- |

### Divisional business owner

| ID | Story | Satisfied by |
|---|---|---|
| US-501 | As a business owner, I need to know which parts of an answer are authoritative, so that I know what I can act on | FR-507, BR-203 |
| US-502 | As a business owner, I need missing contributions stated in the answer, so that I do not act on a partial result believing it complete | FR-503, FR-504 |
| US-503 | As a business owner, I need long-running results delivered where I already work, so that I do not have to visit an evaluation tool | UC-04, BR-207 |
| US-504 | As a business owner, I need to be told when work did not complete, so that silence is never the outcome | UC-04 extension 4a |

### Evaluator / Group CTO

| ID | Story | Satisfied by |
|---|---|---|
| US-601 | As an evaluator, I need every finding reproducible by someone who did not run it, so that findings survive challenge from the vendors they concern | FR-704 |
| US-602 | As an evaluator, I need mediated capability labelled as mediated, so that I do not conclude a platform supports something our code supports | FR-303 |
| US-603 | As an evaluator, I need what was not established published alongside what was, so that the decision is not taken on a partial picture | FR-703 |
| US-604 | As an evaluator, I need findings readable after the environment is gone, so that the decision can be reviewed later | FR-705 |

## Coverage check

Every `FR-` requirement appears in at least one use case or story above, and
every use-case step traces to a requirement. Gaps found by this check are
recorded in `90-traceability/01-traceability-matrix.md` rather than silently
repaired, since a gap usually indicates a missing requirement rather than a
missing reference.
