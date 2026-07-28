# Acceptance and Verification

How conformance is established. Every requirement in this specification carries a
verification method; this document defines what those methods mean in practice,
the test strategy that delivers them, and the acceptance criteria that gate
delivery.

**The governing rule:** a requirement whose verification has not been executed is
not satisfied, regardless of how confident anyone is that the system does it.
This applies with particular force to the honesty requirements — the ones stating
that the system must report what it did not establish — because those are
satisfied by an absence and an absence is easy to declare and hard to notice.

## 1. Verification methods

| Method | Means | Acceptable for |
|---|---|---|
| **Test** | Automated, repeatable, pass/fail with no human judgement | Any requirement. Required for all Must requirements unless noted |
| **Demonstration** | Executed and observed; outcome evident but not automatically asserted | Requirements whose outcome is a human judgement, or that involve a third party |
| **Inspection** | Established by reading code, configuration, or output | Structural constraints and presence-of-artefact requirements |
| **Analysis** | Established by reasoning over measurements or enumerations | Completeness properties — that *every* path traverses a control |

Every Must requirement needs a method stronger than Inspection, or an explicit
note saying why it cannot have one. Two classes legitimately cannot:

- **Structural completeness properties** (TR-105, FR-405, OR-104) require
  Analysis, because a test can prove a specific bypass fails but not that no
  bypass exists. Analysis here means enumerating paths and demonstrating each
  traverses the control — plus a test that a deliberately constructed bypass
  fails.
- **Third-party behavioural findings** require Demonstration, because they depend
  on a platform the programme does not control and cannot force into a state.

## 2. Test strategy

Five levels, each answering a question the level below cannot.

### L1 — Contract tests

The canonical model, its mapping to each protocol, and each protocol
implementation in isolation. No network, no platform.

**Answers:** does each protocol implementation conform to the documented mapping?

### L2 — Loopback matrix

Every client-by-server pairing against the deterministic agent (TR-501). No
external platform, no credentials.

**Answers:** does the system interoperate with itself over every protocol?

This is the level that makes every higher level interpretable. Without a passing
loopback matrix, a failure at L3 is ambiguous between the system's own behaviour
and the remote platform's — which is the single most expensive ambiguity in this
domain.

### L3 — Fault injection

Every classified failure induced deterministically (TR-503), without depending on
a platform misbehaving: unreachable, authentication rejection, protocol error,
agent-level failure, timeout, throttling, refusal, partial decomposition, and
structurally-successful-with-absent-content.

**Answers:** does the system behave correctly when things fail?

This level carries disproportionate weight. Partial failure is the normal
operating condition (BR-204), and error paths are where this class of system
actually fails — so they cannot be left to be exercised by accident.

### L4 — Live platform tests

Against real platforms with real credentials. Separable and excluded by default
(TR-502).

**Answers:** does each platform behave as its documentation claims?

Failures here are frequently **findings rather than defects**, and the distinction
must be made deliberately at each one. A live test failing because a platform
does not support what it advertises is a result, not a bug — and recording it as
a bug is how a finding gets fixed out of existence.

### L5 — Scenario execution

Full business scenarios end to end, measured and recorded.

**Answers:** does the capability deliver the business outcome, and at what cost
and latency?

### Level applicability

| Level | Runs in | Credentials | Gate |
|---|---|---|---|
| L1 | Every change | None | Blocking |
| L2 | Every change | None | Blocking |
| L3 | Every change | None | Blocking |
| L4 | Deliberate invocation | Live | Non-blocking; failures triaged as defect or finding |
| L5 | Per evaluation cycle | Live | Non-blocking; produces findings |

L1–L3 must pass in an environment holding no external credentials. That is what
allows anyone to verify a change, and it is what keeps the system from depending
on one person's access (NFR-405).

## 3. Acceptance criteria

Grouped by capability. Each is a gate: the capability is not accepted until its
criteria pass.

### AC-1 — Protocol foundation

| # | Criterion | Method |
|---|---|---|
| AC-101 | Every client-by-server pairing completes against the deterministic agent with no external credentials | Test (L2) |
| AC-102 | One agent implementation answers over every supported protocol with semantically equivalent results | Test (L2) |
| AC-103 | A route's protocol is changed by configuration alone; no participant redeploys | Test (L2) |
| AC-104 | Each protocol's concept mapping is verified against the documented mapping table | Test (L1) |
| AC-105 | Published descriptions are accepted by a client of every supported protocol generation | Test (L4) |

### AC-2 — Failure behaviour

| # | Criterion | Method |
|---|---|---|
| AC-201 | Every classified failure is inducible deterministically and produces its specified behaviour | Test (L3) |
| AC-202 | An agent-level failure delivered over a successful transport exchange is classified as a failure | Test (L3) |
| AC-203 | A structurally-successful response with absent expected content is classified as a failure | Test (L3) |
| AC-204 | A decomposition losing one leg returns all sections, the missing one marked, with an accurate coverage statement and a non-success signal | Test (L3) |
| AC-205 | A timeout is distinguishable from a remote error at every seam | Test (L3) |

AC-203 is the criterion most likely to be quietly dropped, because it requires
building a fault the system must detect rather than avoid. It is also the
estate's characteristic failure.

### AC-3 — Delegation control

| # | Criterion | Method |
|---|---|---|
| AC-301 | A delegation chain is refused at the configured depth, with an explicit recorded refusal | Test (L3) |
| AC-302 | A deliberately constructed delegation cycle is refused | Test (L3) |
| AC-303 | Delegation context is recovered intact after a hop through every platform in the estate | Test (L4) |
| AC-304 | Every delegation path is enumerated and demonstrated to traverse the control; a constructed bypass fails | Analysis + Test |
| AC-305 | A request attempting to raise its own permitted depth is refused | Test (L3) |

### AC-4 — Data protection

| # | Criterion | Method |
|---|---|---|
| AC-401 | Content excluded by a seam's classification is absent from the recorded outbound payload | Test (L3) |
| AC-402 | An interaction requiring excluded content is refused with a reason, not silently degraded | Test (L3) |
| AC-403 | An interaction over residency-constrained data cannot reach an endpoint outside the permitted geography | Test (L3) |
| AC-404 | A platform with undetermined inference geography is refused for constrained data | Test (L3) |
| AC-405 | A cross-divisional interaction completes using a derived conclusion, with no special-category content crossing the boundary | Demonstration (L5) |
| AC-406 | Personal data is erased from records while interactions remain reconstructable in structure | Test |
| AC-407 | A per-seam data-flow report is produced from traffic and reconciles against a deliberately induced divergence from configuration | Test |

AC-407 is the criterion that distinguishes a real compliance capability from a
configuration listing. A report generated from configuration will always show
compliance.

### AC-5 — Security and identity

| # | Criterion | Method |
|---|---|---|
| AC-501 | Each seam is attributable to its own identity in the target platform's access records | Demonstration (L4) |
| AC-502 | Removing any granted permission from an identity causes a demonstrable failure | Test (L4) |
| AC-503 | Each identity's capability is proven by exercising its actual work | Test (L4) |
| AC-504 | An endpoint with no configured authentication refuses all requests | Test (L3) |
| AC-505 | A credential presented in a query string is refused | Test (L3) |
| AC-506 | Anonymous description retrieval succeeds; anonymous invocation is refused | Test (L2) |
| AC-507 | Anonymously-retrievable responses contain no environment-derived content | Test |
| AC-508 | No credential material appears in source, configuration, images, or records | Test |
| AC-509 | A rotated credential takes effect without redeployment; a revoked one disables only its seam | Test (L4) |

### AC-6 — Observability

| # | Criterion | Method |
|---|---|---|
| AC-601 | Recorded payloads are byte-identical to what traversed the network, including for library-mediated protocols | Test (L2) |
| AC-602 | A multi-platform interaction is reconstructed end to end from the record by someone who did not run it | Demonstration |
| AC-603 | Join rate is computed at two, three and four participants, with each failure classified structural or fixable | Test (L5) |
| AC-604 | Consumption is reported per billed category and reconciled against a provider's own figures within a stated tolerance | Test (L4) |
| AC-605 | An induced capture failure produces an explicit gap marker, never silent omission | Test (L3) |
| AC-606 | A constructed interaction path that produces no record fails | Analysis + Test |

### AC-7 — Evidence and honesty

The criteria most easily declared satisfied without being satisfied. Each is
verified by producing the artefact, not by asserting the property.

| # | Criterion | Method |
|---|---|---|
| AC-701 | Every recorded finding carries conditions, an evidence classification, and a resolvable reference to its supporting interaction | Inspection |
| AC-702 | A published finding is re-derived from the record by someone who did not run it | Demonstration |
| AC-703 | Every reported capability carries a native-or-mediated designation, verified against system behaviour rather than a configuration label | Inspection |
| AC-704 | The not-established register exists, is published alongside findings, and marks each claimed capability exercised or merely declared | Inspection |
| AC-705 | Findings are read and interpreted with the system stopped | Demonstration |
| AC-706 | Every monetary figure carries a modelled-at-stated-rates label | Inspection |

### AC-8 — Operability

| # | Criterion | Method |
|---|---|---|
| AC-801 | A clean environment reaches a running system by the documented sequence with one human authentication | Demonstration |
| AC-802 | Each required configuration value, removed in turn, produces an immediate named failure | Test |
| AC-803 | For every artefact, the deployed version matches the built version; a build-without-deploy is reported | Test |
| AC-804 | A deployment against an unintended target is refused before any resource is created, on every deployment path | Test |
| AC-805 | Each operational procedure is executed from its documentation by someone who did not write it | Demonstration |
| AC-806 | An automated check fails when an environment identifier is introduced into a tracked file | Test |

## 4. Definition of done for the evaluation

Distinct from the acceptance criteria above, which gate the *system*. These gate
the *programme* (BR-504).

1. Every Must requirement has an executed verification with a recorded result.
2. Every Should requirement is either verified or explicitly recorded as
   deferred with a reason.
3. Every value hypothesis is resolved — supported, disconfirmed, or explicitly
   not tested with a reason.
4. The not-established register is complete and published.
5. Unit economics are measured for every interaction shape in the scenario
   library, and reconciled at least once against a provider's own figures.
6. The build-versus-buy comparison is complete, or its inability to proceed is
   documented with the specific obstruction named.
7. A recommendation is issued with its evidence attached, and the pre-registered
   negative conditions are addressed explicitly — including where none were met.
8. Findings are exported and verified readable with the system stopped.

## 5. What cannot be verified, and why

Recorded so these are not mistaken for oversights, and so nobody later claims
them as verified.

| Property | Why not verifiable | What is done instead |
|---|---|---|
| That no delegation bypass exists anywhere | Absence of a construct cannot be tested, only enumerated | Analysis over enumerated paths, plus a test that a constructed bypass fails |
| That a platform will continue to behave as measured | Third-party behaviour, changeable without notice | Findings carry observation dates; re-verification before external use |
| That findings would replicate in another organisation's estate | Different estate, different platforms and constraints | Conditions recorded so a reader can judge applicability |
| That the derived-conclusion pattern satisfies a regulator | Requires a regulator's judgement | DPO review recorded; the position is documented rather than asserted as approved |
| That the cost model matches a future invoice | Rates, commitments and usage all change | Reconciliation against provider figures per period; figures labelled modelled |
| That a negative recommendation would have been made | Counterfactual | Negative conditions pre-registered before evaluation begins |
