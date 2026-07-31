# Exact-As-Built Change Register

## 1. Consequence of choosing this track

An exact-as-built rewrite is not a normal update to the present suite. It changes
the subject from “a system a team should build for Meridiaan Group” to “the A2A
Interop Lab at commit `769ac77`.” Product names, repository components,
deployment choices and known nonconformances become necessary facts.

This track should be a separate specification edition or generated profile. It
should not silently replace the reusable suite: doing so would invalidate
editorial rule R3 and the autonomous-build experiment.

Environment identifiers, account numbers, tenant/project IDs, secret values and
personal access details remain excluded. Exact does not mean unsafe.

## 2. Foundational rewrite

### EA-001 — Replace the stated subject

| Field | Recommendation |
|---|---|
| Severity | **Critical** |
| Affects | README, generation plan and all executive/context documents |
| Action | Rewrite |
| Current mismatch | The suite commissions a fictional enterprise system; the repository is a practitioner-built cross-platform evaluation lab |
| As-built change | Name the A2A Interop Lab, its evaluation purpose, public hosted console, active operating state and point-in-time commit |
| Evidence | Repository description, plans and deployed artefacts |
| Evidence status | **Both** |

### EA-002 — Replace the fictional divisions with the actual platform estate

The context diagram and glossary should name:

- Salesforce Agentforce as the closed CRM-embedded hub and business-record
  destination;
- Anthropic Managed Agents and the self-hosted Claude Agent SDK twin;
- OpenAI Agents SDK hosted on Bedrock AgentCore;
- Google ADK on Vertex AI Agent Engine;
- Microsoft Foundry Agent Service;
- lab-operated bridge, Agentforce shims, hosted protocol faces, console,
  observability workers and PostgreSQL state.

The document should distinguish a vendor platform, an agent implementation, a
runtime, a protocol face and an experimental participant. The current “five
divisions” count should not be reused for a different five-platform count.

### EA-003 — Reclassify the three lenses

| Lens | Exact-as-built treatment |
|---|---|
| Enterprise | A hypothetical applicability analysis, not the owner or deployed context of the system |
| Salesforce field | A real intended consumption lens for demonstrating cross-platform behavior; still not a customer production deployment |
| Practitioner learning | Primary system purpose and owner context |

Business requirements derived only from the fictional enterprise should move to
an “enterprise adoption profile” or be marked not implemented. They must not be
presented as current system behavior.

### EA-004 — Change conventions from commissioned requirements to as-built assertions

The exact edition should use fields such as `Implemented behavior`, `Known
limitation`, `Evidence`, `Verification state` and `Applies to`. `SHALL` should be
reserved for invariants actively enforced by the build, not desired controls.
Unsatisfied target requirements should remain in a separate conformance appendix.

The rewrite must also replace the current one-schema/one-numbering-rule claim
with explicit schemas for business requirements, system requirements, use cases,
acceptance, cost and RAID records. Existing permanent IDs must not be renumbered
to make the conventions appear true.

Its build-input manifest must name one exact bundle. The current suite alternates
between `50-system/` + `60-cost/` alone and those directories plus `10-context/`;
an as-built/autonomous-build edition cannot preserve both statements.

## 3. Architecture and deployment rewrite

### EA-005 — Replace the system context and deployment model

The authoritative topology should be derived from `plan/09-deployment-map.md`,
not the local-first diagram in `plan/01-architecture.md`. It should show:

- a public console and bridge behind the hosted ingress path;
- a path-routed faces service mounting multiple REST/MCP/A2A apps;
- an egress-only long-running briefs watcher;
- scheduled/on-demand observability and credential collectors;
- managed-agent deployments for briefs and analyses;
- Aurora PostgreSQL for hosted observability, typed briefs, durable state and
  usage events;
- SQLite and file state only as local/offline fallbacks;
- cloud-native participant runtimes across AWS, Google Cloud, Azure and
  Salesforce.

Do not turn every process into an independently deployable component. The actual
unit is sometimes a multi-app image/service, deliberately so.

### EA-006 — Name actual component boundaries

| As-built component | Requirement treatment |
|---|---|
| `interop` | Canonical models, protocol servers/clients, registry, identity, delegation and trace |
| `bridge` | Agentforce outbound mediation and delegated fan-out route |
| `faces` | One process mounting hosted protocol twins by target-name path |
| Agentforce shims | MCP/A2A facades over Agent API |
| `console` | Auth, scenario execution, evidence, operations, project and monitoring UI/API |
| `orchestration` / fan-out MCP | Shared leg dispatch and host-tool orchestration |
| `observability` / obs MCP | Platform extraction, store selection, aggregation and analyst tools |
| `briefs` | Managed-agent session watcher and CRM result delivery |
| deploy scripts | Build, publish, secret assembly, schema and service activation owners |

The exact specification should state that configuration files are runtime
content rendered by the console and are often packaged into deployed images.

### EA-007 — Correct storage architecture

Replace one generic “record store” with four record classes:

1. inter-agent trace events and raw envelopes;
2. harvested platform/build observability records and aggregates;
3. typed generated briefs;
4. mutable durable lab state and append-only usage events.

For hosted observability, Aurora PostgreSQL is authoritative. SQLite is an
offline/local snapshot or fallback and must be labelled. File-backed reviews and
watcher state are local equivalents, synchronized explicitly where supported.

### EA-008 — State actual credential-loading semantics

The exact edition should say that hosted services receive references/values
assembled by deployment scripts and load them at process/container start.
Rotation normally requires updating the runtime secret and restarting/redeploying
the consuming service, but not rebuilding the image when code is unchanged.
Token issuers receive private and public key material; verifier-only seams receive
the public half.

This replaces `SR-402` as currently written rather than claiming the build meets
zero-redeploy rotation.

## 4. Functional rewrite

### EA-009 — Describe the implemented protocol matrix, not universal support

List runnable, blocked, mediated and declined cells from `plan/02-matrix.md`.
Important qualifications include:

- Agentforce outbound REST/MCP/A2A comparisons are bridge-mediated;
- Agentforce inbound MCP/A2A are shim-mediated over Agent API;
- managed Claude's Agentforce consultation differs from the SDK A2A channel;
- OpenAI protocol faces are lab wrappers, not native platform protocol support;
- ADK-to-Foundry A2A was measured, while the reverse is blocked by identity;
- local loopback proves the lab's own three protocol implementations;
- A2A async lifecycle support is measured separately from message/send support.

`IR-101` should become a description of lab surface coverage, not a claim that
every platform natively supports every protocol in both directions.

### EA-010 — Describe actual experiment execution

The exact requirements should name the scenario and target registries, hosted
mode resolution, per-run Agentforce channel/route/topology controls, trace/run
recording and warm-up controls. They should state the D55 invariant: a mode may
change address but not backend/platform/protocol identity.

`FR-605` should be marked partially implemented: runs can be inspected and
contrasted through evidence, but a complete first-class comparison workflow was
not found.

### EA-011 — Describe all three supplier orchestrators

The as-built edition should specify the same supplier-disruption task and three
legs under:

- a Managed Agents host tool that exposes remote MCP tools to the model;
- a Google ADK declared parallel graph;
- an Agentforce Agent Script orchestrator with delegated and serial topologies.

The serial Agentforce topology is a deliberate constraint demonstration; the
delegated topology makes one bridge call and runs the legs concurrently off
platform. A successful transport cannot turn missing leg content into complete
business success.

### EA-012 — Describe actual asynchronous flows

Separate three unrelated async shapes:

- A2A `submit`/`poll`, including endpoint-specific support and task-store limits;
- fire-and-forget invocation of the observability harvest owner plus durable
  status polling;
- scheduled managed-agent sessions serviced by a long-running watcher and
  delivered to CRM.

The exact edition should record that an in-memory A2A task store on a
scale-to-zero function is not durable production asynchrony.

### EA-013 — Describe the operator console as a product surface

Add implemented sections and their source-of-truth rules:

- scenario matrix and traces;
- architecture and deployment views;
- observability dashboard, observability analysis and cost analysis;
- credential health;
- insights and durable sign-off;
- DevOps/build telemetry and repository-derived project view;
- usage monitoring;
- Lab Guide/document access.

Each canvas area follows the implemented content/Details/empty-state convention.
This is an as-built UI contract, not a general technical architecture constraint.

### EA-014 — Describe durable sign-off and typed analysis

Insight approvals are durable in hosted state and synchronized back to the repo
by an explicit tool. Observability and cost briefs share a table but are selected
by type; each UI names its producer, subject and schedule state. Failed sign-off
writes surface rather than falsely displaying success.

### EA-015 — Describe one-way delivery projection

`plan/07-workstreams.md` is the work-scope authority,
`plan/00-decisions.md` the reasoning authority, and Jira a generated delivery
view. `scripts/jira_sync.py` supports only recognized source shapes and does not
invent stories, sprints or completion from narrative text. The console project
view renders from the repository and only links to Jira.

## 5. Identity, privacy and observability rewrite

### EA-016 — Replace corporate human identity with actual personas

The exact identity model is:

- username/password entries in `config/users.yaml`;
- server-issued JWT authentication;
- viewer and operator-tier authorization;
- a distinct owner role with operator-equivalent capabilities and a separate
  credential;
- reviewer as an independent per-user grant;
- server-side operator checks through a shared capability helper.

This is not corporate SSO. `SR-104` and corresponding enterprise assumptions
should be marked not implemented rather than paraphrased into a pass.

### EA-017 — Enumerate actual anonymous and authenticated surfaces

The exact endpoint inventory should include anonymous health, protocol discovery,
static/doc surfaces where applicable, login and `/api/track`, with their bounded
response/write behavior. Invocation, monitoring aggregates, traces, operations
and payload-bearing APIs require authentication; operator-only actions have an
additional server-side role gate.

### EA-018 — Replace unimplemented enterprise privacy controls

The exact edition must not claim general lawful-basis enforcement,
controller/processor mapping, personal-data redaction/pseudonymisation,
residency-aware routing, special-category confinement, data-subject lookup,
erasure or per-content-class retention. Repository evidence does not establish
them.

Retain only controls actually evidenced:

- credential scrubbing from traces;
- no committed environment identifiers under the publication rules;
- content-off coding telemetry and aggregate-only storage;
- minimized anonymous usage events without IP or request content;
- scenario- and deployment-specific regional choices;
- service identities and federation patterns on implemented seams.

The removed target controls should appear in a nonconformance/adoption-profile
appendix so their absence remains visible.

### EA-019 — Describe anonymous usage analytics precisely

The console emits `site_visit`, `persona_login` and top-level `nav` events to a
same-origin server proxy. Events are closed-set and fire-and-forget. The server
uses its own time, a verified persona where present, a coarse country header,
locale and a random browser visitor identifier. It stores no IP, name, prompt or
interaction payload. Aggregate reads require sign-in. Storage/forwarding failure
does not block the console.

The exact edition must add retention and deletion state as “not established” if
the repository still contains no enforced policy for these identifiers.

### EA-020 — Describe build telemetry as a separate observability domain

The exact edition should preserve D59's boundary: agent-platform telemetry and
coding-agent telemetry are different domains. Coding metrics preserve billed
token categories and configured attribution. Coding logs retain only metadata
needed for tool mix, decisions, durations, status and cadence; content flags are
off and derived aggregates are stored. The console may display aggregates and
analysis, not raw prompts/tool content.

### EA-021 — State actual platform-log limitations

Record per-source extraction and join coverage rather than claiming uniform
platform observability. OpenAI platform traces are not retrieved as a general
list surface; Salesforce, Anthropic, Google and Azure sources have different
identity, schema and availability constraints; lab trace/rider correlation fills
some but not all gaps. Join rate is a measured property, not a universal pass.

## 6. Acceptance rewrite

### EA-022 — Replace aspirational gates with a conformance table

The exact edition should retain the useful L1-L5 test taxonomy but attach actual
results. At minimum:

- pass where committed automated tests directly exercise the criterion;
- partial where only some components/paths are covered;
- not run where live credentials or an external platform are required;
- fail for corporate identity, no-redeploy rotation and the unimplemented
  enterprise privacy gate;
- not established for complete path enumeration, deployed-version equality and
  live least privilege.

Code presence cannot be converted into an executed acceptance result.

### EA-023 — Add late-feature acceptance

Add explicit test/demonstration results for:

- hosted service refuses missing/wrong auth while local mode remains usable;
- a secret excluded from plain environment has a secure destination and loader;
- issuer keypair produces tokens accepted by configured verification;
- authoritative/fallback store state is visible;
- sign-off and worker checkpoint survive restart, with failed writes visible;
- shared brief readers cannot cross producer types;
- hosted remaps preserve experiment identity;
- long harvest starts promptly and status advances in the owning worker;
- usage events accept only the closed schema and monitoring requires auth;
- owner role reaches exactly the operator capability set without sharing its
  credential;
- project/Jira flow remains one-way;
- content-off telemetry contains no prompt, tool argument, file or response
  content.

## 7. Cost, delivery and operations rewrite

### EA-024 — Replace the build inventory

Extend M1-M17 with the actual operational estate: bridge, multi-face service,
console, watcher, scheduled harvest/credential functions, PostgreSQL state,
managed analysts/sentinel, telemetry ingestion/harvest, insight synchronization,
Jira projection and usage analytics. Classify each as always-on, scheduled,
per-invocation, storage or external-service cost.

### EA-025 — Replace hypothetical delivery phases with a dual record

Keep a short dependency-order recommendation, but make the primary as-built
delivery record the workstream/decision history and generated Jira view. Record
that the project was local-first and was hosted incrementally; the late hosting
pass exposed authentication, store, state and packaging faults not caught by
local tests.

### EA-026 — Make operations part of the system boundary

Promote the procedures in `plan/10-operations.md` into as-built requirements for
credential/password/key rotation, code versus config deployment, watcher host
movement/state seeding, sign-off synchronization, local console iteration and
running-version diagnosis. State which require a human cloud login and which are
service-identity operations.

## 8. Document-level disposition

| Existing area | Exact-as-built disposition |
|---|---|
| README / generation plan | Rewrite purpose and baseline; retain original experiment history in an appendix |
| Conventions | Replace forward-only anatomy with implemented behavior + evidence + conformance state |
| Context | Replace fictional estate; retain it only as an enterprise adoption profile |
| Enterprise lens | Reclassify as hypothetical applicability, not as-built business owner |
| Field lens | Update with real measured/hosted evidence and known limits |
| Learning lens | Make primary rationale and add late operational findings |
| System requirements | Convert into actual behavior/invariants plus visible nonconformance appendix |
| Data/privacy | Narrow sharply to implemented controls; do not imply legal completeness |
| Acceptance | Add executed-result state rather than obligation-only gates |
| Cost | Inventory actual hosted and operational mechanisms |
| Delivery | Use actual workstream/decision/Jira projection history |
| Traceability | Trace behaviors to decisions, code/tests and measured results; keep target requirements separately |

## 9. Exact-track conclusion

The exact specification would be smaller in enterprise governance and larger in
operations. It would say less about lawful basis, residency and erasure, and much
more about hosted authentication, worker state, authoritative stores, deployment
identity, evidence provenance, build telemetry and delivery projection. That is
the honest shape of the system in this repository.
