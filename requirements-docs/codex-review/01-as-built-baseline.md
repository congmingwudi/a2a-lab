# As-Built Baseline and Evidence Map

## 1. What is demonstrably built

This baseline is descriptive, not a claim that the reference build conforms to
the commissioned requirements. “Built” means the behavior is present in source,
configuration or deployable artefacts and normally pinned by tests. “Live” means
the plans also record a measured execution.

| Capability | As-built state | Evidence status | Principal evidence |
|---|---|---|---|
| Canonical agent request/response model | Shared model behind inbound adapters and outbound clients | **Both** | `plan/01-architecture.md`; `src/interop/` |
| REST, MCP and A2A protocol faces | Common adapter exposed over three protocol classes | **Both** | `src/interop/servers/`; loopback tests |
| REST, MCP, A2A and platform-native clients | Logical targets resolve to protocol-specific clients | **Both** | `config/targets.yaml`; `src/interop/clients/` |
| Outbound bridge | Constrained platform calls bridge, which selects target/protocol | **Both** | D8, D30, D61; `src/bridge/app.py` |
| Inbound shims | MCP and A2A surfaces proxy to the closed platform's Agent API | **Both** | matrix Paths B/C; `src/platforms/agentforce/` |
| Protocol generation compatibility | Translation and compatibility workarounds exist for measured dialect walls | **Both** | matrix findings; A2A client/server code |
| Synchronous and A2A fire-then-poll clients | Blocking `ask`, non-blocking submit and polling exist; support differs by platform | **Both** | D47; async probe and e2e test |
| Delegation guard and rider | Caller, purpose/trace and depth travel through a versioned text channel | **Both** | D27, D34, D37; delegation and identity modules |
| Fan-out orchestration | Same supplier task runs through host-tool, declared-graph and Agentforce variants | **Both** | D41, D61; orchestration code, config and agent metadata |
| Partial-result presentation | Expected sections, attribution and coverage are modelled | **Both** | FR-5xx origins; orchestration tests and results |
| Wire trace capture | Raw protocol envelopes captured with correlation and credential scrubbing | **Both** | architecture trace layer; `src/interop/trace.py` |
| Platform-log harvesting | Deterministic sources collect six platform/build streams where available | **Both** | D49, D54, D59; observability sources and Lambda handler |
| Authoritative observability store | Postgres is hosted authority; SQLite is an explicit local/fallback view | **Both** | D49; `make_obs_store()` and PG read tests |
| Hosted operator console | Authenticated console renders scenarios, traces, architecture, insights, monitoring and project data | **Both** | D48, D57, D60, D62; console app/static bundle |
| Hosted protocol faces | Multiple ASGI faces share one process and are addressed by path | **Both** | D51; `src/faces/` and deployment artefacts |
| Hosted account-brief watcher | Long-running service services managed-agent tool calls and persists its serviced set | **Both** | D52; briefs runner, state tests and deployment script |
| Durable operator state | Insight sign-off, watcher state and operational reports use `lab_state` when hosted | **Both** | D50, D52; PG state API and sync tools |
| Role-based console access | Viewer, operator, reviewer grant and distinct owner credential are enforced server-side | **Both** | D36, D63; users config, identity helpers and tests |
| Credential health | Hosted collector and report surface track credential state | **Both** | WS14; expiry tooling and scheduled handler |
| Build cost telemetry | Coding-agent metrics are harvested with explicit token-category semantics | **Both** | D44, WS9/WS12; coding source and cost sentinel |
| Build behavior telemetry | Content-off log events are reduced to metadata aggregates | **Both** | D59; coding-log source, setup script and tests |
| Anonymous console usage analytics | Three closed-set events enter through a same-origin proxy and aggregate in monitoring | **Both** | D62; usage store, endpoints, UI and tests |
| Findings and sign-off | Claims, evidence class, review state and decision links are published in the console/repo | **Both** | D38, D50; insights and reviews config/code |
| One-way Jira projection | Workstreams generate a delivery view; console reads the repo, not Jira | **Both** | D58, D60; Jira sync, project endpoint and tests |
| Operations documentation | Deployment, rotation, state sync and recovery procedures have a dedicated record | **Both** | D53; `plan/10-operations.md` |

## 2. Actual estate versus requirements estate

The requirements estate is a fictional five-division organisation. The actual
estate is five agent-platform families across four cloud/vendor boundaries, plus
lab-operated mediation, hosting and evidence services. “Five” therefore means
different things in the two documents.

| Requirements concept | Actual implementation |
|---|---|
| Five divisions with independent business ownership | Five platform families used as experimental participants |
| Corporate identity provider for people | Lab-owned username/password personas issuing JWTs |
| Per-division regulatory classification and role | Scenario/config metadata and technical caller identities; no complete divisional governance model |
| Residency-aware inference routing | Region-specific platform deployment choices; no general policy engine evidenced |
| Boundary minimisation and pseudonymisation | Credential scrubbing and selected content controls; no general personal-data transformation layer evidenced |
| Traffic-derived compliance data-flow report | Trace and platform-log records; no complete controller/processor or lawful-basis report evidenced |
| Production-shaped internal evaluation | Publicly reachable hosted lab console with authenticated control surfaces and anonymous shell/analytics events |
| Eventual decommission-or-promote decision | Continuing lab with active workstreams, scheduled jobs, Jira projection and operating runbooks |

The exact-as-built track must replace the left column. The standards-compliant
track should retain it as the target system while adding a verification ledger
showing the right column's conformance.

## 3. Plan/code conflict and currency register

| # | Subject | Plan evidence | Implementation evidence | Status | Review treatment |
|---|---|---|---|---|---|
| PC-01 | Primary topology | `plan/01-architecture.md` still foregrounds local ports, local JSONL and local console | Hosted bridge, console, faces, workers and Postgres are deployable and tested | **Conflict** | Cite deployment map as current topology; mark architecture overview historical/stale |
| PC-02 | Observability store | Historical notes and local paths retain SQLite language | `make_obs_store()` selects Postgres when configured; hosted readers use PG | **Conflict** | Specify authoritative-store selection and explicit offline snapshot behavior |
| PC-03 | Coding logs | D59 begins with an unverified hard gate | Coding log reader, provisioning script, schema, UI and tests now exist | **Conflict** | Treat the implementation as built; live end-to-end delivery remains not re-probed here |
| PC-04 | Workstream completion | `plan/07-workstreams.md` contains completed, open, declined and superseded items in one chronology | Code contains many later phases, but external operator actions remain | **Conflict** | Never infer delivery status from prose; use explicit status and committed artefacts |
| PC-05 | Platform count in harvest | D54 says the Lambda is the full owner and fixes console drift | Source registry includes platform and coding streams, whose count changes by context | **Both** | Require named inventory rather than “all” or a fixed count |
| PC-06 | Managed analyses | Some plans call analysts scheduled; others record paused/on-demand deployments | UI explicitly reports paused/on-demand state and typed brief kinds | **Both** | Require schedule/state to travel with analysis output |
| PC-07 | A2A async support | Endpoint cards imply A2A support but matrix notes lifecycle is a separate claim | Probe records shim/Foundry support and Agent Engine submit-only behavior | **Both** | Add lifecycle capability states; do not infer from protocol label |
| PC-08 | Route remapping | Hosted mode once remapped managed-agent experiments to SDK twins | Tests/config now constrain remaps to the same experiment identity | **Both** | Add invariant: location can change, experiment semantics cannot |
| PC-09 | Credential rotation | Requirements say no redeployment | Operations record says container-start loading requires redeploy to consume new secrets | **Conflict** | Mark `SR-402`/`AC-509` nonconformant or revise the target behavior |
| PC-10 | Human identity | Requirements assume corporate IdP | Config implements lab password roles and JWT issuance | **Conflict** | Standards track records failure; exact track rewrites identity requirements |
| PC-11 | Jira authority | Jira is used operationally | Import is one-way and console refuses live read-back | **Both** | Specify plan/repo authority and stale-view behavior |
| PC-12 | “Native” labels | Matrix uses native, bridge and shim labels with contextual qualifications | Wrappers expose protocols for platforms that do not natively implement them | **Both** | Preserve designation at point of display and include hosting/mediation axis |
| PC-13 | Brief watcher hosting | Deployment map lists ECS `a2alab-briefs` running, then calls the watcher the last laptop dependency; the older runbook says to keep the local watcher running (`plan/09-deployment-map.md:722`, `plan/09-deployment-map.md:821`, `plan/04-runbooks.md:189`) | Hosted deploy path and persistent state exist; deployment warns two watchers can duplicate delivery (`deploy/briefs/deploy_briefs.sh:143`, `tests/unit/test_briefs_watch_state.py:56`) | **Conflict** | Make hosted watcher current truth and remove unsafe local imperative; retain history as dated rationale |
| PC-14 | Hosted protocol faces | One deployment row calls the inventory both nine and eleven faces and later equates hosted equivalents with AgentCore (`plan/09-deployment-map.md:810`, `plan/09-deployment-map.md:826`) | `FACES` contains eleven same-adapter path-routed ECS twins (`src/faces/__init__.py:43`; `config/targets.yaml:292`) | **Conflict** | Correct count and distinguish ECS protocol faces from AgentCore agent runtimes |
| PC-15 | AgentCore runbook shape | Current runbook deploys three Claude protocol runtimes plus a bridge runtime and repoints Salesforce directly (`plan/04-runbooks.md:134`) | Deploy script creates one HTTP runtime per Claude/OpenAI platform; bridge and faces deploy separately (`deploy/agentcore/deploy.sh:21`; `deploy/agentcore/deploy.sh:171`) | **Conflict** | Replace imperative steps with the current runtime/face/bridge topology |
| PC-16 | Observability MCP exposure | Runbook rejects Function URLs in favor of API Gateway, then says `expose_mcp.sh` creates a Function URL (`plan/04-runbooks.md:223`, `plan/04-runbooks.md:236`) | Script creates/updates Lambda code and API Gateway (`deploy/obs/expose_mcp.sh:1`; `deploy/obs/expose_mcp.sh:40`) | **Conflict** | Correct the “Finish once” instruction and verification language |
| PC-17 | OpenAI handoff branch | Standing handoff says work from `lab-scaffold-m0-m4` because main lags (`plan/06-openai-codex-handoff.md:3`) | That branch is an ancestor of current `main`; its technical contract is otherwise substantially corroborated | **Conflict** | Remove stale branch direction or mark the handoff historical |
| PC-18 | Deliberate backlog | LangGraph, Strands, MuleSoft and WS6 U3-U6 remain planned/deferred (`plan/07-workstreams.md:433`, `plan/07-workstreams.md:474`, `plan/07-workstreams.md:1342`, `plan/07-workstreams.md:562`) | No corresponding platform/deploy units exist; scenarios honestly label upcoming items | **Plan only** | Keep visibly deferred; do not count as reference-build capability or defect |
| PC-19 | Current live state | Plans contain READY/running/schedule and Jira-count snapshots (`plan/09-deployment-map.md:722`; `plan/11-delivery.md:77`) | Repository artifacts cannot establish current cloud/Jira state; deployment map itself states it is repo-derived (`plan/09-deployment-map.md:30`) | **Not verifiable** | Treat as dated observations and re-probe only when live state is needed |

## 4. Existing requirement conformance findings

This is not a full executed verification ledger. It is the set of material
differences discoverable from repository evidence. “Not established” is used
where only a live test or practitioner judgement can resolve the claim.

### 4.1 Clearly unsatisfied or contradicted

| Requirement | Finding | Evidence status | Recommended disposition |
|---|---|---|---|
| `SR-104` | Human access must use a corporate identity provider, but the build uses lab personas, shared role passwords and JWTs (`50-system/07-security-and-identity-requirements.md:85`; `config/users.yaml:1`) | **Conflict** | Keep target and record reference-build failure, or rewrite exact track to lab personas/JWT |
| `SR-402`, `AC-509` | The requirement forbids redeployment, while hosted secrets load once per process and the operating procedure redeploys/restarts consumers (`50-system/07-security-and-identity-requirements.md:272`; `src/interop/secret_env.py:37`; `plan/10-operations.md:88`) | **Conflict** | Revise target to “no rebuild; controlled restart permitted” if this is acceptable |
| `FR-605` | A first-class comparison across content, latency, hops and consumption is required, but the UI exposes individual runs/evidence rather than that complete workflow (`50-system/01-functional-requirements.md:641`; `src/console/app.py:1978`) | **Plan only** | Mark Should deferred or add comparison surface |
| `DR-101`-`DR-603` as a set | The suite requires seam classification through purpose limitation (`50-system/06-data-and-privacy-requirements.md:25`), but no general lawful-basis, residency-policy, subject-location, erasure or purpose-limitation engine is evidenced | **Plan only** | Record nonconformance; do not delete enterprise controls merely to match the build |
| `AC-401`-`AC-407` | The complete data-protection acceptance gate is specified (`50-system/09-acceptance-and-verification.md:153`) but no implementation or executed gate result is evidenced | **Plan only** | Add explicit failed/not-run results in a reference-build ledger |
| `SR-603` | Authentication exists, but the required audit record of each payload view/export (`50-system/07-security-and-identity-requirements.md:454`) was not found | **Plan only** | Add read-audit behavior or mark unsatisfied |
| `NFR-302`, `DR-501` | Per-content-class automatic retention is required (`50-system/03-nonfunctional-requirements.md:274`; `50-system/06-data-and-privacy-requirements.md:292`), but no enforcing policy/job is evidenced | **Plan only** | Add retention policies/jobs and verification or mark unsatisfied |
| `DR-503`, `DR-504` | Data-subject location and erasure preserving structural evidence are required (`50-system/06-data-and-privacy-requirements.md:328`) but not evidenced in the build | **Plan only** | Keep as enterprise target only; exact track removes as unsupported |
| `TR-304` | Placement exists operationally, but no complete declared placement per retained data class required by `TR-304` was found (`50-system/04-technical-architecture-requirements.md:307`) | **Plan only** | Add an inventory or mark unsatisfied |

### 4.2 Partially satisfied or overstated

| Requirement | Finding | Evidence status | Recommended disposition |
|---|---|---|---|
| `FR-105` | Major operated HTTP components expose health, but “every component” needs an enumerated deployment check | **Both** | Verify against deployment inventory rather than examples |
| `FR-208` | Asynchronous briefs are delivered to CRM with state/retry characteristics, but generalized destination support is not built | **Both** | Exact track names the CRM destination; standards track keeps generic target and marks partial |
| `FR-401`-`FR-405` | Guard/rider exists at known delegation seams; completeness over every late-added path requires re-analysis | **Both** | Re-run path enumeration after D61 and hosted fan-out additions |
| `FR-505` | Partial fan-out is represented, but model/platform paths can return transport success with missing content | **Both** | Define the non-success signal independently of HTTP status and verify all orchestrators |
| `FR-506` | More than two orchestrator placements now exist; wording underspecifies the comparison axis | **Both** | Amend to compare each materially different concurrency owner |
| `FR-701`-`FR-705` | Findings/config/sign-off exist, but resolvability and export from every displayed claim varies | **Both** | Add coverage audit across the console, config and results records |
| `NFR-204` | Credential failures are surfaced through reports and startup checks, but not every remote expiry is fail-fast | **Both** | Separate serving credentials from monitored third-party expiry |
| `NFR-403` | Build-and-ship ownership is a documented rule with deploy scripts, not a universal transactional guarantee | **Both** | Add deployed-version verification for every artefact |
| `NFR-405`, `AC-801` | Runbooks exist, but the estate depends on human cloud entitlements and external operator actions | **Both** | State prerequisites and record demonstration result rather than asserting clean-checkout reproducibility |
| `NFR-701` | Interaction rows are append-oriented; operator state and corrections are mutable by design | **Both** | Clarify scope excludes typed state and read-time correction tables |
| `TR-201` | Components are separately deployed, while multi-face hosting deliberately couples several faces into one image/service | **Both** | Define independence at operated service boundary, not protocol-face boundary |
| `TR-301` | Trace, observability and state storage have different abstractions and fallback rules | **Both** | Define which “record” each storage requirement governs |
| `SR-201` | `/api/track` is an unauthenticated endpoint (`src/console/app.py:1534`) outside the requirement's closed three-endpoint exemption list (`50-system/07-security-and-identity-requirements.md:101`) | **Conflict** | Amend the closed exemption set and add write-only/minimisation constraints |
| `SR-202` | The requirement refuses service whenever auth is absent (`50-system/07-security-and-identity-requirements.md:137`), while local development intentionally opens selected behavior without it | **Conflict** | Decide whether local-open is an accepted mode; then amend the requirement or implementation and verify both modes |
| `OR-301` | Harvest is implemented per available platform, but availability and credentials differ and some sources are structurally absent | **Both** | Require a named source inventory and last-attempt state |
| `OR-403` | Platform consumption is not uniformly attributable to individual interactions | **Both** | Preserve as target; publish per-source attribution coverage |
| `FR-203`, `AC-103` | Both prohibit participant software/redeployment changes, but do not state how packaged configuration becomes active (`50-system/01-functional-requirements.md:152`; `50-system/09-acceptance-and-verification.md:123`; `plan/10-operations.md:116`) | **Not verifiable** | Clarify activation semantics and execute the criterion before declaring failure or pass |
| `CST-203` | The reusable cost model expects scale-to-zero for nearly every component and near-zero idle compute (`60-cost/01-cost-model-and-projection.md:128`), while this build deliberately operates always-on console, faces, bridge and watcher services (`plan/09-deployment-map.md:38`) | **Conflict** | Preserve scale-to-zero as one hosting shape, but add continuous baseline cost and sensitivity terms; exact track names the services |
| Watcher exactly-once delivery | The watcher persists a serviced-session replay checkpoint, but performs the external CRM delivery before saving the checkpoint (`src/briefs/runner.py:343`; `src/briefs/__main__.py:104`); a crash window can repeat the side effect, as D52 itself notes (`plan/00-decisions.md:1699`) | **Not verifiable** | Do not call the checkpoint idempotency; verify destination idempotency or add an atomic/idempotency-key design and crash-window test |

### 4.3 Not established by repository inspection

- Every Must requirement has an executed result.
- Every L1-L3 test level blocks every change.
- Every failure class in `TR-503` is deliberately inducible.
- Live identities retain only the documented least privilege.
- Hosted runtime and source versions currently match.
- All deployed credentials rotate/revoke with the claimed blast radius.
- Provider consumption reconciles within a stated tolerance.
- The fictional enterprise's regulatory obligations are legally adequate.
- The current hosted deployment matches the latest committed deploy artefacts.

These belong in a verification-results ledger, not in prose that implies a pass.

## 5. Late decisions: requirement or design?

| Decision | General requirement value | Exact-as-built value |
|---|---|---|
| D48 | High: hosted services fail closed and verification includes negative auth | Names runtime-secret loading and hosted-mode signal |
| D49 | High: one authoritative store selector and visible fallback mode | Names Postgres, SQLite and `make_obs_store()` |
| D50 | High: governance acts must be durable and failed writes visible | Names `lab_state` and sync script |
| D51 | Medium: hosted and local twins preserve semantics/lifecycles | Names one multi-face ASGI service and path routing |
| D52 | High: persistent loops are workers; idempotency state survives restart | Names briefs service/image and serviced-session state |
| D53 | High: secret exclusion must include relocation; issuer holds signing key | Names environment variables and container-start behavior |
| D54 | High: long jobs trigger their owning worker asynchronously | Names harvest Lambda and status polling |
| D55 | High: deployment mode cannot change experiment semantics | Names target remaps and backend markers |
| D56 | High: shared stores require typed writers and explicit reader filters | Names `obs_briefs.kind` values |
| D57 | Medium: evidence displays carry provenance, bounds and meaningful empty state | Names canvas, tabs and Details UI pattern |
| D58 | Medium: one authority and one-way delivery projection prevent drift | Names Jira importer and workstream source |
| D59 | High: collect only needed telemetry; source omission beats downstream masking | Names OTLP logs, CloudWatch and derived tables |
| D60 | Medium: operational view renders from the authority, external system is a link | Names DevOps/project console section |
| D61 | High: compare concurrency ownership; delegated parallelism must remain honest | Names Agentforce Agent Script, bridge fan-out and serial topology |
| D62 | High: anonymous analytics is minimal, closed-set, non-blocking and server-mediated | Names proxy, Aurora table, Cloudflare country and monitoring endpoint |
| D63 | High: shared operational role and owner credential are distinct; permissions are capability sets | Names current role labels and password plumbing |

## 6. Baseline conclusion

The project has built more operational infrastructure than the July 28
requirements acknowledge, and less enterprise governance than those requirements
mandate. A useful revision must say both. Adding late features without recording
the broad nonconformance would overstate the build; deleting unsatisfied
requirements to make the build “pass” would destroy the original evaluation
standard.
