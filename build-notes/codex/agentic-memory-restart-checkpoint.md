# Agentic Memory restart checkpoint

Recorded: 2026-11-11

Purpose: resume the Agentic Memory investigation after restarting Codex so the newly migrated
user-level Salesforce skills are discovered. This is a same-agent restart checkpoint, not a D79
implementation/review batch, so no `scripts/handoff.py` state transition is required.

## Start here after restart

1. Read `plan/18-agentic-memory-context.md` for the intended memory architecture.
2. Read the latest evidence at `plan/03-results.md` under
   “2026-11-11 — Agentic Memory CRUD is query-visible in its DLO and DMO”.
3. Read `build-notes/tooling-inventory.md` for the Data 360 skill and CLI configuration.
4. Confirm the restarted Codex catalog exposes `data360-orchestrate`, `data360-query`,
   `data360-schema-get`, and the other Salesforce skills from `~/.codex/skills`.

## What the live probe established

The controlled probe created one `sys_PROFILE_FACT` through
`POST /api/v1/agentic/memory`, queried it through both the Memory API and Data 360 SQL, deleted
it through the Memory API, and checked every surface again.

- Probe run: `ef230911d7`
- Synthetic owner: `a2alab-gov-p-ef230911d7`
- Agent registration: `A2ALab_Memory_Case`
- Memory id: `01a1cb31-3d1e-72fb-b433-9d2da6340533`
- Marker: `Governance probe ef230911d7: prefers aisle seats`
- Category: `sys_PROFILE_FACT`
- Raw trace: `traces/governance/governance-ef230911d7.json` (gitignored)

The API-created memory was query-visible in both layers:

```text
POST /api/v1/agentic/memory
  -> AgenticConnector_AiParticipantI_1232197F__dll
  -> AiParticipantInsight_std__dlm
```

While active:

- DLO `eventId__c` equaled the Memory API `memoryId`.
- DMO `Id__c` equaled the same `memoryId`.
- The DLO and DMO exposed the content, `sys_PROFILE_FACT`, profile id, agent identifier and
  `active` status.
- The DMO existed on the first DMO query 89 seconds after creation. This is only an upper-bound
  observation; it does not prove the actual ingestion latency.
- The Data Stream record itself was hidden from the observability identity, although DLO metadata
  and rows were queryable.

After API deletion:

- The Memory API record was absent.
- The DMO row was absent.
- The DLO retained an `inactive` tombstone with the same `eventId__c`, agent, profile and category,
  but a null payload.

This disproves the earlier working assumption that curated memories are confined to an internal,
unqueryable store on this tenant. It does not prove whether the service writes DLO then DMO or
dual-writes them; their source timestamps differed by only 1 ms.

## Registration behavior observed

- The creating registration returned the fact after 16.8 seconds at the probe's polling
  resolution.
- `A2ALab_Memory_Case_Graph_Ind` did not return it after 300 seconds.
- An unregistered `aid` was refused during token exchange.
- A mismatched `xid` could still read the memory, consistent with the existing D115 concern.

The physical records are exposed in a shared Data 360 model, while Memory API retrieval behaved
as registration-scoped in this tenant. Do not generalize this into a claim about every tenant or
every authentication mode without another controlled probe.

## Tooling state

- All 74 Salesforce skills formerly copied into this project now live canonically at
  `~/.codex/skills/<skill>/`.
- Claude Code uses links at `~/.claude/skills/<skill>` pointing to the Codex copy.
- The ignored project-local Salesforce directories were removed; `.agents/skills/handoff`
  remains as the project-specific tracked link.
- `@gthoppae/sf-cli-plugin-data360` 0.0.7 is linked from
  `~/.sf-community-tools/datacloud/sf-cli-plugin-data360`.
- Salesforce CLI uses Node 24.22.1 through `~/.local/share/oclif/node/node-custom` because the
  plugin's current JWT dependency is incompatible with the CLI's bundled Node 26 runtime.
- Post-migration verification succeeded: `sf data360 man` listed the command topics, and a live
  SQL count against `AiParticipantInsight_std__dlm` returned 307 rows.

## Open work to resume

1. Locate and show the exact application code that retrieves `sys_PROFILE_FACT` and
   `sys_PROFILE_PREFERENCE` (or the actual preference category used by this repository), including
   how `agent_id`, profile id and category filters are supplied.
2. Run a controlled non-`default` data-space probe. Establish whether the Agentic Memory API has
   an explicit data-space selector, inherits a space from registration/client configuration, or
   cannot address custom spaces. Keep API-contract evidence separate from tenant observations.
3. Run a controlled request with `agent_id` absent using a client-authenticated path that the
   public documentation says supports clients. Determine which identity/configuration scopes the
   read, rather than inferring it from the wording alone.
4. Update stale explanatory text in `src/console/static/index.html` and related comments in
   `src/observability/memory_backend.py` that still reflect the 2026-11-06 empty-DMO observation.
   The corrected model must explain active DLO/DMO rows and the retained DLO deletion tombstone.
5. Decide whether the reproducible memory/DLO/DMO probe belongs in a maintained script or live
   test instead of remaining a one-off governance trace.

## Resume outcome — 2026-11-11

1. **Application retrieval path located.** `src/interop/memory_adapter.py` resolves both Claude
   and OpenAI to `A2ALab_Memory_Case`, maps the verified user/customer key to the server-owned
   profile id, and calls `open_and_read`. `src/interop/memory.py:saved_memories` then issues one
   `list_memories` call for each exact category in `SAVED_CATEGORIES`:
   `sys_PROFILE_FACT` and `sys_PROFILE_PREFERENCES`. Each GET supplies the registered
   `agent_id`, `profile_id=<owner>`, `profile_type=individual`, and category. The operator
   management route in `src/console/app.py:_customer_memory_snapshot` uses the same client,
   registration and constants.
2. **Non-default data-space probe blocked by tenant state, not attempted.** `sf data360
   data-space list -o a2alab-prod` returned one active space, `default`. The public Memory API
   contract says `x-sfdc-dataspace` selects the space for mTLS client calls when `agent_id` is
   absent. The measured JWT client token inherited `dataspace=default`. Creating a custom data
   space would be a material tenant mutation and was not authorized by the resume request.
3. **Client path measured.** Run `78fe4400f8` minted an a360 token with `xid` and no `aid`, then
   read the synthetic fact with no `agent_id` header (200, marker visible). Token custom
   attributes included `xid` and `dataspace=default`, and no `aid`. The same run reconfirmed the
   mismatched-`xid` read and registration-scoped behavior; cleanup deleted memory id
   `01a1cb56-0336-7fc1-b298-e8cc6c929bcb`. Raw wire:
   `traces/governance/governance-78fe4400f8.json` (gitignored).
4. **Stale explanatory copy corrected.** `src/observability/memory_backend.py` and the Memory
   Back End pane in `src/console/static/index.html` now explain active DLO+DMO visibility and the
   DLO deletion tombstone, superseding the 2026-11-06 empty-DMO interpretation.
5. **Probe placement decided.** Keep this as an explicit maintained script, not a default pytest:
   it mutates a live Limited-GA tenant, waits on eventual consistency, and must discover a
   tenant-specific DLO suffix. `scripts/memory_governance_probe.py` now includes the reproducible
   no-agent client read. The storage assertions should become an opt-in mode of that script (or a
   sibling under `scripts/`), never a unit/local test.

Remaining work: provision or identify a real non-default data space before running item 2, and
add the opt-in DLO/DMO lifecycle mode described in item 5 if this investigation continues.

## Non-default-space continuation — corrected profile foundation, 2026-11-11

The operator authorized and the lab provisioned `A2ALabMemoryProbe` plus active registration
`A2ALab_Memory_Space_Probe`. Setup took 7m17s to become ACTIVE. The generated stack includes nine
filtered AgenticConnector DLO memberships, space-prefixed modeled/search objects, and hidden
`AgenticConversation` / `AgenticUser` real-time data graphs with ID/VALUE, fragment, and state
DMOs.

The initial comparison was incomplete because the custom registration had no Profile DMO or
identity-resolution foundation. That gap is fixed and tracked as AM-TEST-001. The custom space now
has Contact Point Email mapping, successful `AMP1` identity resolution,
`amp_UnifiedIndividualAmp1__dlm`, attached Profile DMO, and automatic extraction enabled. The
first graphs used unmapped qualifier fields; corrected `A2ALab_MemoryProbe_C360V2` is READY with
day-zero artifacts and attached (AM-TEST-002; AM-D360-001 withdrawn).

The complete public runtime remains non-operational in that space (AM-API-012). In the corrected
17:23Z run, all six Ingest operations succeeded first-attempt, but list conversations returned an
empty 200, session/conversation retrieval returned 404, Context returned 500 `Failed to load agent
configuration` for both synthetic and real CRM owners, and explicit Memory create returned 400.
A paired default/custom control reproduced the Context and Memory failures only in the custom
space after the corrected graph converged, strengthening AM-API-012.
The probe also fixed two request-shape defects in the lab: ordinary sessions must omit cross-org
`orgId`, and an agent participant id must equal the registered agent API name.

The durable handoff is now:

- `build-notes/codex/agentic-memory-api-stack-readout.md` — diagrams, all 14 operations, visible
  and hidden Data 360 components, and measured results;
- `build-notes/ws27/agentic-memory-product-api-defects.md` — stable defect ledger;
- `plan/03-results.md` — exact sanitized live evidence.

## Useful SQL

Active DMO records:

```sql
SELECT Id__c, InsightType__c, InsightPayloadTxt__c,
       IndividualId__c, AgentIdentifier__c, CreatedDate__c, Status__c
FROM AiParticipantInsight_std__dlm
ORDER BY CreatedDate__c DESC
LIMIT 100
```

Source events and deletion tombstones (the DLO suffix is tenant/provisioning-specific):

```sql
SELECT eventId__c, insightType__c, insightPayloadTxt__c,
       individual__c, agentIdentifier__c, createdDate__c, Status__c
FROM AgenticConnector_AiParticipantI_1232197F__dll
ORDER BY createdDate__c DESC
LIMIT 100
```

## Working tree at checkpoint

- `plan/03-results.md` is modified with the live probe evidence.
- `build-notes/tooling-inventory.md` is new and records the Salesforce skills, Data 360 CLI and
  MCP configuration.
- This checkpoint file is new.
- `src/interop/memory.py`, `src/interop/memory_adapter.py`, and
  `src/orchestration/memory.py` are also modified. They appeared during the final checkpoint
  status check, were not changed or reviewed as part of the probe/tooling work, and must be
  treated as pre-existing or concurrent user work until their provenance is established.
- Nothing has been committed or staged during this work.

