<!--
Draft for #cdp-realtime-service, submitted through its "CDP Real time Service Support" intake form.
Part 1 is the form fields. Part 2 is the first reply in the thread the form creates.

PRE-FILING GATE (2026-11-11): CLEARED. The memory integration user had no data-space access to
A2ALabMemoryProbe. It was granted at 20:04:49Z (A2ALab_Memory_Integration permission set,
<dataspaceScopes>). Core SQL in the space then went 403 → 201 for that user. The probes were
rerun at 20:07–20:08Z and again at 20:37–20:38Z (+32 min, ruling out grant propagation lag); all four failures reproduced unchanged both times, so this is ready to file.

POSTED 2026-11-11: form submitted and the full details (Part 2, ids filled in) posted as two
replies in the request's thread:
https://salesforce-internal.slack.com/archives/C05CBEHHQJG/p1794433736347839
Environment and record ids were stripped back to <placeholders> after posting (repo rule); the
posted thread is the copy that carries them.

REPLY 2026-11-12 07:36Z (engineer, in thread): "We have found a workaround fix which does not
depend on the upstream service to fix the regression. It's pending deployment and should be
available after 24 hours. No action item on customers. The configurations will auto-heal."
So it is a regression, not unsupported. Leave the probe registration untouched, then rerun the paired
probes after 2026-11-13 ~08:00Z. Tracking: build-notes/support-requests.md SR-01.
-->

# Part 1 — Intake form

**Urgency:** Timely, but not urgent

**Question:**
We've observed that Agentic Memory & Context does not work for a registration in a non-default
data space. The same paired test passes in `default` and fails in the custom space. Is
non-default supported?

**Details:**
Paired control on the same org, with the same clients, request bodies, operation sequence and waits.
`default` / registration `A2ALab_Memory_Case` passes. Data space `A2ALabMemoryProbe` / registration
`A2ALab_Memory_Space_Probe` fails:

- explicit fact/preference create → 400 `[AgenticMemory] Failed to create agentic memory`
- Conversation ingest → 201/201/202/202/202, then list → 200 `[]`, get session/conversation → 404
- Get Context → 500 `Failed to load agent configuration` (synthetic owner and real CRM owner)

Both registrations are ACTIVE with the same config shape: IR-specific Unified Individual DMO,
IR PUBLISHED with 146 profiles, one READY RT customer graph, extraction on. Governance is the
same in both spaces (no authored policies). The calling user has been granted the custom data
space, and the failures still reproduce with it. As far as we can find in this channel, this is the
first non-default Agentic Memory report. Full matrix in the thread.

**FI/FD:** EU55

**Tenant type:** Enterprise Edition (EE)

**DG name / sample Individual ID / unified ID (optional):**
DG: A2ALab_MemoryProbe_C360V2 (non-default space A2ALabMemoryProbe), 
Sample Individual ID: <sample Contact id> (CRM Contact id, sent as owner_id), 
Unified ID (A2ALabMemoryProbe, failing): <custom-space unified id>, 
Unified ID (default, working): <default unified id>

**Splunk logs:**
No Splunk access on our side. Repro windows 2026-11-11 ~18:05Z, 20:07–20:08Z and 20:37–20:38Z. Org/tenant ids will be shared in the thread.

**Network log of the events sent:**
Per registration, same order. CDP token exchanged at `/services/a360/token` with `aid` = the
registration, and `ctx-agent-id` + `ctx-dataspace-ds_name` headers set to that registration's space:

```
                                                   default        | A2ALabMemoryProbe
POST /api/v1/agentic/memory (fact)                 created (id)   | 400 Failed to create agentic memory
POST /api/v1/agentic/memory (preference)           created (id)   | 400
POST /api/v1/agentic/conversations                 201            | 201
POST …/conversations/{cid}/sessions                201            | 201
POST …/sessions/{sid}/participants                 202            | 202
POST …/conversations/{cid}/entries                 202            | 202
POST …/sessions/{sid}/close                        202            | 202
GET  /api/v1/agentic/conversations?owner_id=…      200, id listed | 200 []
GET  …/conversations/{cid}/sessions                200            | 404 Conversation not found
GET  /api/v1/agentic/conversations/{cid}?scope=all 200            | 404 Conversation not found
GET  /api/v1/agentic/context?owner_id=…&agent_id=… 200            | 500 Failed to load agent configuration
```

---

# Part 2 — Thread follow-up (full details)

**Org / tenant:**
- Org id: `<org id>` (FI/FD EU55)
- Tenant host: `<tenant host>`
- Tenant id: `<tenant id>`
- Calling user (memory integration user, JWT bearer → CDP exchange): `<memory user id>`
- Registrations: `A2ALab_Memory_Case` (default) / `A2ALab_Memory_Space_Probe` (`A2ALabMemoryProbe`)
- Tracking ref on our side: `AM-API-012`

**Config shared by both registrations:**
- ACTIVE, with the ruleset-specific Unified Individual Profile DMO attached
- identity resolution PUBLISHED, last job SUCCESS, 146 source/unified profiles
- one READY real-time customer graph with day-zero mapping and transform artifacts
- Automatic Memory Extraction on; no Employee RT Data Graph

**Sample ids (from the form):** the custom-space ids come from `amp_Individual__dlm` and
`amp_UnifiedLinkIndividualAmp1__dlm` (link row created 17:17Z by the AMP1 identity-resolution
run). The default unified id comes from `UnifiedLinkssotIndividualAm01__dlm`. Get Context for this
owner returns 200 in `default` and 500 in the custom space.

**Passes in `default`:** fact and preference create → read → delete; conversation
create/session/participant/entries/close; list conversations, list sessions, get conversation;
Get Context for the synthetic conversation and a real CRM customer profile.

**Fails in `A2ALabMemoryProbe`:** the results in the form. They repeated after the corrected
graph and registration had converged.

**Ruled out:**
- *Graph key qualifiers.* Our first custom graph referenced the wrong KQ fields. The replacement
  `A2ALab_MemoryProbe_C360V2` reached READY, generated its day-zero artifacts and was attached,
  and the failures still reproduced. We are not reporting the earlier graph issue as a defect.
- *Token shape.* The CDP tokens for the two registrations carry identical claim sets and scopes
  (`cdp_ingest_api cdp_profile_api cdp_query_api`). Neither token carries a data-space claim, so
  the space is selected only by the `ctx-dataspace-ds_name` header.
- *Governance policies.* Neither space has a user-authored data-governance policy. The audit
  history shows only system object/field and record-level policy regenerations, all ACTIVE before
  the repro window. So `default` has no policy that the custom space is missing.

- *Data-space access for the calling user.* The memory integration user originally reached only
  `default`, implicitly through Data Cloud User. As that user, core SQL against the custom space
  returned 403 "end-user denied authorization". We granted `A2ALabMemoryProbe` on its permission
  set (Data Space Management, 20:04:49Z), and core SQL as that user then returned 146 rows. We
  reran at 20:07Z and again at 20:37Z (+32 min after the grant): Memory create still returns
  400; ingest returns 201/201/202/202/202, then list `[]` and get/sessions 404; Get Context still returns 500 "Failed to load agent configuration".
  The tenant-plane `/api/v2/query?dataspace=A2ALabMemoryProbe` still answers 404
  "DataModelEntity … not found" for the `amp_` DMOs. And `amp_Conversation__dlm` holds 0 rows,
  ever, so no custom-space conversation has reached the lake.

**Related observation:** the agentic setup list ignores its data-space selector. Both
`?dataSpaceName=A2ALabMemoryProbe` and `?dataspaceName=…` return the `default` setup, so we list
setups globally and filter client-side. Maybe the same resolution gap, maybe a separate small bug.

**Questions:**
1. Is Agentic Memory & Context supported on registrations in a **non-default** data space today?
2. Related thread: [DataspaceResolver / data-space routing](https://salesforce.enterprise.slack.com/archives/C05CBEHHQJG/p1793688744507369?thread_ts=1793016454.585639&cid=C05CBEHHQJG).
   Does the agent-configuration cache behind Get Context resolve a registration created in a
   non-default space, or does it look only in `default`? (The 500 says "Failed to load agent configuration".)
3. Ingest returns 202, but nothing is retrievable. Does the ingest path write to the registration's
   space while list/get read `default`, or does the write drop after the ack?
4. Governance on read: per [this thread](https://salesforce.enterprise.slack.com/archives/C05CBEHHQJG/p1794326402315559?thread_ts=1794311200.148619&cid=C05CBEHHQJG),
   record-level policies are evaluated on read. Which identity is checked for data-space access
   on read: the token's end user, or the service identity? Granting the end user the space did not
   help. Does an internal service identity (e.g. `sfdc_a360`) also need a non-default space grant?
5. The tenant query plane returns 404 "DataModelEntity not found" for `amp_` DMOs that core SQL
   reads fine. Is the tenant plane resolving every table against `default`, whatever
   `?dataspace=` says? That would be one root cause for all of the above.

# Part 3 — Follow-up after the fix window (drafted 2026-11-13, POSTED 2026-11-13 as a thread reply: [message](https://salesforce-internal.slack.com/archives/C05CBEHHQJG/p1794596391736009?thread_ts=1794433736.347839&cid=C05CBEHHQJG))

<!-- Reply in the SR-01 thread, to the engineer's 2026-11-12 07:36Z message. Post only with the
operator's OK. Evidence: plan/03-results.md "2026-11-13 — AM-API-012 rerun after the promised
fix". -->

Thanks for the update and the fix. We reran the same paired default vs `A2ALabMemoryProbe` test today (2026-11-13, ~33 h after your reply), without touching the registration so the auto-heal could take effect. It's **partly healed**:

:white_check_mark: **Get Context works now.** For the real customer it returns 200 with the Profile entry from `A2ALab_MemoryProbe_C360V2`. On 11-11 it was 500 `Failed to load agent configuration`.

:x: **Memory create still fails**, for both categories:
• `sys_PROFILE_FACT` → 400 `[AgenticMemory] Failed to create agentic memory` (response timestamp `2026-11-13T16:54:09.830Z`)
• `sys_PROFILE_PREFERENCES` → same 400 (`2026-11-13T18:11:10.271Z`)

:x: **Conversation ingest is still accepted but not stored.** Create conversation/session → 201/201, then participants/entries/close → 202/202/202 (conversation `a2alab-probe-ad54ba58`, 16:54:09Z). After that, list conversations → 200 `[]`, and get conversation / list sessions → 404 `Conversation not found`. Because nothing is stored, Context in the custom space carries Profile only, with no memory or conversation entries.

The `default` control (`A2ALab_Memory_Case`) passed every step in the same runs: both categories created, readable after ~17 s and deleted, and the conversation retrievable and summarized into Context.

Two questions:
1. Is the write/persistence path (Memory create plus ingest → retrieval) covered by the same fix and just not deployed yet, or is it a separate issue we should track separately?
2. Was the setup-list selector issue from the first message (`GET /ssot/agentic/setup?dataSpaceName=…` returning the `default` setup) part of this regression? It still lists globally on our side.

We're leaving `A2ALab_Memory_Space_Probe` as it is and can rerun whenever it's useful. Happy to share request ids if that helps with Splunk.
