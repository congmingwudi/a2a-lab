# WS33 batch 1 plan — Agentforce as the third shared-memory participant

**Proposer:** Codex  
**Implementer:** Codex after D79 critique and operator settlement  
**Reviewer:** Claude Code  
**Agent Spec:** `build-notes/codex/a2alab-agentforce-shared-memory-agent-spec.md`

## Scope

Add one Agentforce Service Agent and one live console experiment under **Agent Memory**. The agent
uses the existing deterministic `A2ALabMemoryTurn` Apex action and passes
`A2ALab_Memory_Case`, so Agentforce, Claude, and OpenAI address one Data 360 registration and the
same server-resolved Maya/Jordan Contact owner. Park the DC1 crossover; do not change memory
registration configuration, native Agentforce memory arms, or existing research-assistant agents.

## Item 1 — settle the architectural and delivery record

- **Files:** `plan/00-decisions.md`, `plan/07-workstreams.md`,
  `plan/18-agentic-memory-context.md`, `plan/09-deployment-map.md`.
- **Change:** record D121 and WS33 after critique: shared registration, explicit Apex route,
  trusted backend owner binding, generation parity, bridge allowlisting/correlation, separate
  Agentforce target/id, no CRM mutation action, and DC1 explicitly out of scope.
- **Test:** source-parser/console tests still recognize every ADR/WS reference; grep all old
  two-participant claims.
- **Deploy:** documentation is baked into the console image, so the final console deploy is a full
  rebuild.

## Item 2 — generate the Agentforce authoring bundle

- **Files:** generated directory
  `salesforce/force-app/main/default/aiAuthoringBundles/A2ALab_Customer_Memory_Agentforce/`;
  `scripts/publish_agent.py` if placeholder rendering needs to cover the new bundle.
- **Change:** use `sf agent generate authoring-bundle --json --no-spec`, then implement the approved
  single-subagent Agent Script. Native memory flags remain off. `before_reasoning` calls
  `apex://A2ALabMemoryTurn` with the current user input, trusted External owner, threaded
  conversation id, expected generation, lab correlation, and fixed
  `agentApiName="A2ALab_Memory_Case"`. Bind context, ok, trace id, and conversation outputs. Source
  the established research-assistant identity from `SF_MEMORY_AGENT_RUNAS_USER`.
- **Backing logic:** extend `A2ALabMemoryTurn` and its test; no new Flow, Prompt Template, or custom
  object.
- **Test:** `sf agent validate authoring-bundle --json`, then live preview with
  `--use-live-actions`; inspect the planner trace to prove the hook/action binding and fixed shared
  registration. Preview without an owner must stay outside Maya/Jordan.
- **Deploy:** publish/activate only after the later explicit deployment checkpoint. Record the new
  BotDefinition id as `SF_MEMORY_AGENT_ID` without replacing `SF_AGENT_ID`.

## Item 3 — make Agentforce session variables request-scoped and immutable

- **Files:** `src/platforms/agentforce/client.py`, `src/interop/registry.py`,
  `tests/unit/test_agentforce_client.py`, registry tests if needed.
- **Change:** support a target option identifying the trusted customer-memory binding. Resolve
  `metadata.memory_customer` through the verified `owner_of` path, pass the Contact id and expected
  generation only when creating the Agentforce session, and retain an `(owner, generation)`
  fingerprint. End and refuse stale-generation sessions before an Agent API message. Refuse
  customer switches, unsigned/machine callers, missing/unknown customers, raw owners, and empty
  target agent ids. Never mutate shared `session_variables`.
- **Tests first:** Maya/Jordan session bodies; raw/unknown owner rejection; same-owner reuse;
  cross-owner and stale-generation refusal; unsigned/machine rejection; concurrent different-owner
  creation; static variables unchanged; one-shot cleanup; real Agentforce session id preserved.
- **Deploy:** code ships in the console image because the console owns target clients. No faces
  rebuild is needed for this target.

## Item 4 — add the dedicated target and environment contract

- **Files:** `config/targets.yaml`, `.env.example`, `deploy/console/deploy_console.sh`,
  `deploy/bridge/deploy_bridge.sh`, `scripts/identity_preflight.py`, environment contract tests.
- **Change:** add `agentforce-memory-rest`, authenticated with existing Agent API caller credentials
  and agent id `${SF_MEMORY_AGENT_ID}`, with the request-scoped memory-owner option. Ship the new id
  through the console secret/task definition and validate it is non-empty. Ship the customer map
  to the bridge, add the run-as identity to preflight, and keep every existing target unchanged.
- **Tests:** registry expansion, missing-variable failure, deploy-secret allow-list, and environment
  audit.
- **Deploy:** console full rebuild; task definition carries the new id.

## Item 5 — add the third Agent Memory experiment

- **Files:** `config/scenarios.yaml`, `src/console/app.py`, `src/console/static/index.html`,
  `tests/unit/test_console.py`.
- **Change:** add `customer-memory-agentforce` after Claude/OpenAI, target
  `agentforce-memory-rest`, same customer selector/curation/POS controls, and a seeded recall prompt.
  Introduce a target-managed-memory presentation (`Shared memory always on`) instead of applying
  `MemoryAdapter` or showing a false on/off control. The run endpoint must validate the selected
  customer even though the generic memory toggle is absent and pass the customer key plus current
  generation in request metadata.
- **Tests first:** group order becomes a seeded three-platform set; selector and controls are
  present; no generic memory flag is sent; invalid customer is rejected; target receives the key;
  memory evidence, attribution, and Agentforce session-trace link render correctly.
- **Deploy:** console full rebuild and hosted smoke of the new experiment.

## Item 6 — align the bridge with D107 and join the traces

- **Files:** `src/interop/memory.py`, `src/interop/memory_adapter.py`, `src/bridge/app.py`,
  `salesforce/force-app/main/default/classes/A2ALabMemoryTurn*.cls`, bridge/memory/Apex tests.
- **Change:** extract shared generation resolution; make `/memory/turn` derive the generation
  conversation and suppress saved/profile context after Start fresh; accept only the fixed
  registration and mapped owners; carry a lab correlation through Apex and bridge hops; cap phases
  below the 20 s Apex limit and report latency/partial errors.
- **Tests first:** reset parity, malformed marker, invalid agent/owner makes no tenant call,
  correlation propagation, timeout and partial-failure evidence, and Apex request serialization.
- **Deploy:** full bridge rebuild plus Apex class/test deployment at the explicit checkpoint.

## Item 7 — update the explanatory surfaces and diagrams

- **Files:** `config/diagrams.yaml`, memory diagram constants/copy in
  `src/console/static/index.html`, relevant `docs/diagrams/*.drawio` and generated SVG,
  `plan/20-console-presentation-mode.md`, and the Agent Memory Details/presentation scenes.
- **Change:** replace two-participant claims with shared storage plus three execution provenances;
  show Claude/OpenAI generic REST seam versus Agentforce explicit Apex→bridge seam. Do not imply
  that the Agentforce reply is ingested. Add the third handoff destination without auto-sending.
- **Test:** diagram-source/twin checks, console Details assertions, presentation navigation and
  accessibility regressions, narrow-layout browser evidence.
- **Deploy:** regenerated assets must be included by the existing console Dockerfile; confirm every
  runtime-read path is copied.

## Item 8 — verification and measured result

- **Local:** baseline and final `uv run pytest`; focused Agentforce client/console suites;
  `uv run ruff check src tests scripts`; Codex readiness probes and regression harness required by
  the handoff workflow.
- **Org:** compile; live-actions preview; representative recall, correction, empty-memory, outage,
  and no-owner turns; inspect traces rather than trusting reply text alone.
- **Hosted acceptance:** Claude→Agentforce, Agentforce→OpenAI, three-agent correction, Jordan
  negative control, Start fresh, POS graph, authority boundary, concurrent owner isolation, joined
  Agent API/bridge/memory trace evidence.
- **Evidence:** append measured results and limits to `plan/03-results.md`; never describe accepted
  writes as recall until a fresh session returns them.
- **Deploy:** full console rebuild (source/config/docs changed), smoke new route and existing two
  memory experiments, scan hosted Details copy, then operator marks the batch deployed.

## Explicit non-goals

- No DC1 home/companion integration.
- No new Data 360 Agent Configuration and no change to `A2ALab_Memory_Case`.
- No Agentforce native `context.memory`, `past_conversations`, or `manageUserMemory` arm.
- No CRM Case action on the new agent.
- No claim that Agentforce replies are ingested.
- No public/anonymous customer identity design; this remains the signed-in lab's D115 boundary.

## Settled identity prerequisite

D121 reuses the existing research-assistant Einstein Agent User, which already runs the memory
concierge and has `A2ALab_Agent_Actions`. Source uses
`{{env:SF_MEMORY_AGENT_RUNAS_USER}}`; the username remains deployment configuration.
