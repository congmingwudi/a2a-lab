# Security and Identity Requirements

The system publishes endpoints reachable over untrusted networks, holds
credentials for five independently-operated platforms, and captures complete
payloads. Each of those is a security requirement generator, and the third is
the one most often overlooked because it is framed as observability.

**The governing principle:** one human authentication, and every other identity
is a service identity retrieved from a secret store. Any credential a person
knows, types, or pastes is a defect.

## Block allocation

| Block | Theme |
|---|---|
| `SR-1xx` | Identity model |
| `SR-2xx` | Authentication of seams |
| `SR-3xx` | Authorisation and caller policy |
| `SR-4xx` | Credential lifecycle |
| `SR-5xx` | Exposure and network posture |
| `SR-6xx` | Human access to the evaluation surface |

---

## SR-1xx — Identity model

### SR-101 — One identity per calling seam

**Statement.** Each seam that calls a remote platform SHALL present its own
distinct service identity. Identities SHALL NOT be shared between seams.

**Rationale.** A shared identity makes attribution impossible in the target
platform's own access records, makes least privilege unachievable, and turns
revocation into an outage affecting every caller.

**Priority.** Must
**Verification.** Test — each seam is attributable to its own identity in the
target platform's access records.
**Traces to.** BR-306

---

### SR-102 — Each identity is scoped to what its seam actually calls

**Statement.** Each service identity SHALL hold only the permissions its seam
exercises, established by determining what that seam actually calls rather than
by copying an existing identity's grants.

**Rationale.** A shared identity tends to accumulate the *union* of every
caller's needs — at which point it looks over-granted, and every individual
permission is load-bearing for somebody. Shrinking it is impossible without
first splitting it. Least privilege here is an identity-modelling problem
wearing a permission-configuration costume, and treating it as the latter fails.

**Priority.** Must
**Verification.** Analysis and test — each identity's grants are reconciled
against its seam's exercised calls; removing any granted permission causes a
demonstrable failure.
**Traces to.** BR-306

---

### SR-103 — Identity capability is proven by exercise, not by configuration

**Statement.** The system SHALL provide a check proving each identity can perform
its seam's actual work, by exercising that work rather than by inspecting
configuration.

**Rationale.** Platform authorisation has non-obvious prerequisites that
configuration inspection cannot reveal — an application may authenticate
perfectly and still be refused by the specific interface it needs, for a reason
recorded nowhere in its grant list. Only exercising the capability finds this,
and it typically surfaces as an unexplained not-found rather than as an
authorisation error.

**Priority.** Must
**Verification.** Test — the check exercises each identity's capability and fails
when a prerequisite is absent.
**Traces to.** BR-306

---

### SR-104 — Human identity is separate from service identity

**Statement.** Human access SHALL use human identity from the corporate identity
provider. Service identities SHALL NOT be usable by people, and human identities
SHALL NOT be used by components.

**Rationale.** Attribution collapses when the two mix, and it is the property
every subsequent investigation depends on.

**Priority.** Must
**Verification.** Inspection and test — no component authenticates as a person;
no person can authenticate as a component.
**Traces to.** BR-306

---

## SR-2xx — Authentication of seams

### SR-201 — Every exposed endpoint authenticates independently

**Statement.** Every endpoint the system exposes SHALL enforce its own
authentication, and SHALL NOT rely on network position, an upstream proxy, or a
tunnel edge to have done so — except for the exempt endpoints enumerated below,
which SHALL be a closed set.

**Exempt endpoints.** Three, each exempt for a stated reason and no others:

| Exempt | Why | Constrained by |
|---|---|---|
| Protocol-required discovery descriptions | Conformant clients must be able to retrieve them anonymously; requiring credentials breaks interoperability | SR-503, SR-504, IR-503 |
| Liveness indication | Must be answerable when the component cannot serve, including before credentials resolve | FR-105 |
| Readiness indication | Same | FR-105 |

Adding a fourth exempt endpoint is a change to this requirement, not a
configuration decision.

**Rationale.** Endpoints published over untrusted networks are reachable by
anyone who learns the address, and edge components frequently perform no
authentication at all. An endpoint trusting an upstream it does not control is
unauthenticated in practice.

The exemptions are enumerated rather than described by rule because "endpoints
that need to be open" is a category that grows. A closed list makes each addition
a visible decision; a rule makes the next addition somebody's judgement call.
Every exempt endpoint is additionally constrained by SR-504 and SR-505 — being
unauthenticated, anything they return is public.

**Priority.** Must
**Verification.** Test — each endpoint refuses unauthenticated requests when
reached directly.
**Traces to.** BR-306

---

### SR-202 — Absent authentication configuration fails closed

**Statement.** Where authentication material is not configured, an endpoint SHALL
refuse requests rather than serving them unauthenticated.

**Rationale.** Fail-open is the single most consequential defect available here:
a misconfiguration silently publishes the system, including complete recorded
payloads, and nothing reports a problem. Any convenience of an
authentication-optional mode is not worth the failure mode it creates.

**Priority.** Must
**Verification.** Test — an endpoint with no configured authentication refuses
all requests.
**Traces to.** BR-306

---

### SR-203 — Credentials never travel in a URL

**Statement.** Authentication material SHALL NOT be conveyed in a request path or
query string, and requests presenting credentials that way SHALL be refused.

**Rationale.** URLs are logged by intermediaries, retained in histories, and
included in referrer headers — placing the credential in several systems the
organisation does not control. Refusal rather than acceptance-and-warning is
required because otherwise the practice persists.

**Priority.** Must
**Verification.** Test — a credential presented in a query string is refused.
**Traces to.** BR-306

---

### SR-204 — Transport is encrypted end to end

**Statement.** All traffic between the system and any external party SHALL be
encrypted in transit, including traffic traversing components the organisation
operates.

**Priority.** Must
**Verification.** Inspection and test.
**Traces to.** BR-306, BR-301

---

## SR-3xx — Authorisation and caller policy

### SR-301 — Authorisation decisions use the delegating caller's identity

**Statement.** Where a request arrives through delegation, authorisation SHALL be
determined from the originating caller's identity and purpose, not solely from
the identity of the immediate transport.

**Rationale.** Otherwise every delegated request is authorised as the system
itself, and a division permitting the system implicitly permits every division
that can reach it — which is precisely the exposure divisional architects refuse
(BR-105).

**Priority.** Must
**Verification.** Test — a request whose originating caller is not permitted is
refused, even when the immediate transport identity is permitted.
**Traces to.** BR-105, BR-306

---

### SR-302 — Per-caller, per-purpose policy is expressible

**Statement.** A division SHALL be able to permit or deny requests by calling
division and by declared purpose, independently of whether it participates at
all.

**Rationale.** All-or-nothing exposure is what a divisional architect declines.
Granularity is also what makes purpose limitation enforceable rather than
declarative.

**Priority.** Must
**Verification.** Test — a denial for one caller is demonstrated while others
continue to be served.
**Traces to.** BR-105, BR-301

---

### SR-303 — Denials are explicit and recorded

**Statement.** An authorisation denial SHALL return an explicit, distinguishable
response and SHALL be recorded with the caller, purpose and rule applied.

**Rationale.** A denial indistinguishable from an error will be retried and
escalated as an outage. The record is also the evidence that the control is
active rather than merely configured.

**Priority.** Must
**Verification.** Test — denials are distinguishable from failures and appear in
the record with their reason.
**Traces to.** BR-105, BR-305

---

### SR-304 — Delegation depth is a security control

**Statement.** The delegation depth limit SHALL be enforced as a security
control, changeable only through the same path as other security configuration,
and SHALL NOT be overridable by request content.

**Rationale.** If depth can be raised by something in the request, it is not a
control — and the request content is partly model-generated, which makes it
attacker-influenceable in any interaction touching untrusted input.

**Priority.** Must
**Verification.** Test — a request attempting to raise its own permitted depth is
refused.
**Traces to.** BR-307

---

## SR-4xx — Credential lifecycle

### SR-401 — Credentials are resolved at runtime from a secret store

**Statement.** Components SHALL hold a reference to a credential and resolve its
value from the secret store at start or at use, and SHALL refuse to operate when
resolution fails.

**Rationale.** A reference can be committed, reviewed and deployed safely; a
value cannot. Refusing to start on a failed resolution prevents the worst
outcome — a component operating credential-less or falling back to a broader
identity.

**Priority.** Must
**Verification.** Test — a component with an unresolvable reference refuses to
start.
**Traces to.** BR-306

---

### SR-402 — Rotation requires no redeployment

**Statement.** Rotating a credential SHALL take effect without redeploying or
rebuilding the component using it.

**Rationale.** Rotation that requires deployment is rotation that does not
happen, across five platforms with independent schedules.

**Priority.** Must
**Verification.** Test — a rotated credential takes effect without redeployment.
**Traces to.** BR-306

---

### SR-403 — Revocation is per identity and non-disruptive to others

**Statement.** Revoking one service identity SHALL disable only its seam.

**Priority.** Must
**Verification.** Test — one identity is revoked; other seams continue to
operate.
**Traces to.** BR-306

---

### SR-404 — Secret material outside the store is inventoried and protected

**Statement.** Any secret-bearing artefact that cannot be held in the secret
store SHALL be inventoried, encrypted at rest, and backed up outside the machine
that holds it.

**Rationale.** Some material — private keys, certain configuration files — does
not fit a secret store's shape and ends up on one workstation, which is both a
single point of loss and an unmanaged copy. A file whose secret-bearing nature
is not evident from its name or location is the one that gets missed, so the
inventory is the control.

**Priority.** Must
**Verification.** Inspection — the inventory exists, is current, and every entry
is encrypted at rest and backed up.
**Verification note.** Inspection-only by exception class 3 (documentation and artefact-presence): the property is the existence and adequacy of a recorded artefact, which no execution can assert.
**Traces to.** BR-306

---

### SR-405 — Credential material never enters the interaction record

**Statement.** Authentication material SHALL be removed from payloads at the
point of writing, before storage.

**Rationale.** Payload capture is comprehensive by design and will capture
credentials on authenticated hops. Removal at display leaves the value in
storage, where it outlives the display layer and travels with every export. The
write path is the only safe control point.

**Priority.** Must
**Verification.** Test — credential-shaped content in a payload is absent from
storage.
**Traces to.** BR-306

---

## SR-5xx — Exposure and network posture

### SR-501 — Exposure is deliberate and enumerated

**Statement.** Every endpoint reachable from outside the system's own hosts SHALL
be enumerated with its purpose, authentication and the parties expected to reach
it. An endpoint not on that list SHALL NOT be exposed.

**Priority.** Must
**Verification.** Inspection and test — reachable endpoints are reconciled against
the enumeration.
**Traces to.** BR-306

---

### SR-502 — No inbound network path is opened to internal hosts

**Statement.** Making a component externally reachable SHALL NOT require opening
an inbound path to a host on an internal network.

**Rationale.** An inbound path to an internal host is a durable increase in
attack surface, granted for an evaluation and rarely withdrawn afterwards.
Outbound-initiated exposure achieves the same reachability without it.

**Priority.** Must
**Verification.** Analysis — network paths to internal hosts are enumerated and none is inbound.
**Traces to.** BR-306

---

### SR-503 — Anonymous discovery is permitted; anonymous invocation is not

**Statement.** Retrieval of a published agent description MAY be permitted
without authentication where the protocol requires it. Invocation SHALL always
require authentication.

**Rationale.** Open discovery is a deliberate protocol design decision that
conformant clients depend on, so requiring credentials for it breaks
interoperability. The security consequence is handled by scoping what the
description reveals (SR-504), not by closing the endpoint.

**Priority.** Must
**Verification.** Test — description retrieval succeeds unauthenticated;
invocation unauthenticated is refused.
**Traces to.** BR-306

---

### SR-504 — Published descriptions reveal no environment detail

**Statement.** A published agent description SHALL contain only what a caller
needs to decide whether and how to invoke, and SHALL NOT contain account
identifiers, internal addresses, organisation identifiers, or platform console
locations.

**Rationale.** The description is retrievable anonymously, so anything in it is
public. This is exactly where an environment identifier reappears after a source
scrub — assembled at runtime from configuration and served to anyone. Removing
identifiers from source does not remove them from what is served, and only a
check at the serving edge catches it.

**Priority.** Must
**Verification.** Test — anonymously retrieved descriptions are inspected for
environment detail.
**Traces to.** BR-306

---

### SR-505 — Unauthenticated surfaces are checked at the serving edge

**Statement.** Every unauthenticated response SHALL be checked at the point of
serving for content derived from environment configuration, and such content
SHALL be withheld from unauthenticated callers.

**Rationale.** The general lesson behind SR-504 and worth more than the specific
fix: **a source scrub is not a boundary.** Identifiers assembled at runtime from
configuration survive any amount of source cleaning, so anything derived from
configuration must be checked at the edge it is served from.

**Priority.** Must
**Verification.** Test — each unauthenticated response is inspected;
configuration-derived content is absent.
**Traces to.** BR-306

---

## SR-6xx — Human access to the evaluation surface

### SR-601 — Human access requires human authentication

**Statement.** Access to the evaluation surface SHALL require authentication as a
person, distinct from any service credential the system uses.

**Rationale.** The surface exposes complete recorded payloads, which may contain
personal data from several divisions. A service credential shared for
convenience makes every viewing unattributable.

**Priority.** Must
**Verification.** Test — the surface refuses access without human
authentication.
**Traces to.** BR-306, BR-305

---

### SR-602 — Authorisation is enforced server-side

**Statement.** What a role may see and do SHALL be enforced server-side. Hiding
an action in the interface SHALL NOT be the control.

**Rationale.** An interface that hides what a role cannot do is a usability
feature; the refusal is the security control. Systems where the two are confused
are permissive to anyone who issues the request directly.

**Priority.** Must
**Verification.** Test — a request for an action outside a role's permissions is
refused when issued directly.
**Traces to.** BR-306

---

### SR-603 — Access to recorded payloads is attributable

**Statement.** Viewing or exporting recorded payloads SHALL be attributable to a
person and recorded.

**Rationale.** The record may contain personal data from divisions that consented
to an interaction, not to unrestricted inspection. Attribution is what makes the
DPO's assurance to those divisions truthful.

**Priority.** Must
**Verification.** Test — payload access is recorded with the accessing identity.
**Traces to.** BR-301, BR-305

---

### SR-604 — Streaming surfaces carry credentials out of band

**Statement.** Where the surface streams data continuously, authentication SHALL
be conveyed by a mechanism that does not place credentials in a URL.

**Rationale.** The straightforward implementation of browser streaming puts the
credential in the query string, violating SR-203 by default. Meeting the
requirement constrains the transport choice, so it is stated rather than left to
be discovered.

**Priority.** Must
**Verification.** Test — streaming connections authenticate without credentials
in the URL.
**Traces to.** BR-306
