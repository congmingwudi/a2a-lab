# Defect draft: Data 360 Agentic Memory acknowledges writes and does not persist them

Prepared 2026-10-18 for product management and engineering (WS27, `plan/18-agentic-memory-context.md`).
The evidence is in `plan/03-results.md` (2026-10-18 entries); the decisions behind the
lab's memory seam are D83, D86, D90, D91 and D92 in `plan/00-decisions.md`.

The org id, tenant host and user ids are deliberately left out of this repo. Fill
them in from `.env` / the CDP token exchange when filing.

## Summary

Since roughly 2026-10-16/17, the Agentic Memory tenant API returns success for
every write (create conversation 201, create session 201, add participants 202,
add entries 202, close session 202 `"closed"`) but persists nothing:

- `GET /api/v1/agentic/conversations/{id}?scope=all` returns 404 "Conversation not
  found" for the conversation just created, still 404 after 10 minutes.
- Re-creating the same `conversationId` returns 201 `created` instead of 409.
- The owner's conversation list is `[]`.
- `GET /api/v1/agentic/context` returns 200 with `contextId` and an empty
  `ontology`, and **no `userContext`**: both for fresh writes and for owners whose
  memory was returned correctly on 2026-10-15/16.
- The `AgenticConnector_*` data streams take in no new Conversation or Message
  records after the writes.

## Timeline (UTC)

| When | Observation |
|---|---|
| 2026-10-13 → 2026-10-16 02:48 | GetContext returns `userContext` (`SYS_PROFILE_PREFERENCES`, `SYS_PROFILE_FACT`) for every written owner; conversations are readable immediately |
| 2026-10-16 22:00 → 2026-10-17 07:52 | System-initiated org changes (Setup Audit Trail): `Customer 360 Data Platform Integration` converted to integration permission set `sfdc_a360`; standard permission set groups recalculated with `Data Space default` removed and re-added; Data Cloud object/field and record-level policies regenerated; an org-wide provisioning push; new agentic connector DLO fields created |
| 2026-10-17 18:23 → now | GetContext never returns `userContext` (bridge and SDK-style callers alike) |
| 2026-10-17 | Conversations still stored, but readable only after minutes (404 at +15 s, then 200); no extraction |
| 2026-10-18 | Conversations never stored (404 at +6 and +10 min; re-create → 201) |

Corroboration: the realtime team's 6-hourly smoke driver (`#agent-conversation-script-results`)
last produced memory 2026-10-15 19:00Z, and has failed with `500 "[AgentConversation]
Failed to create conversation" COMMON_ERROR_GENERIC` on every run since 2026-10-16 19:00Z.

## Repro (about 6 minutes)

As an API-only integration user with Data Cloud User + Context Service runtime,
using a CDP token exchanged with `aid` = a registered agent (`isMemoryGenerationEnabled: true`):

1. `POST /api/v1/agentic/conversations` `{conversationId, conversationOwnerId, ownerType: "individual", name, channel: {type: "web", id: "agent-sdk"}}` → 201
2. `POST /api/v1/agentic/conversations/{cid}/sessions` (owner participant) → 201
3. `POST …/sessions/{sid}/participants` (agent participant) → 202
4. `POST /api/v1/agentic/conversations/{cid}/entries` (a `user_message` stating preferences, plus an `agent_response`) → 202
5. `GET /api/v1/agentic/context?owner_id&owner_type=individual&agent_id&conversation_id` with `ctx-agent-id` and `ctx-dataspace-ds_name: default` at +60/+180/+360 s → 200, no `userContext`
6. `GET /api/v1/agentic/conversations/{cid}?scope=all` → 404
7. Repeat step 1 with the same id → 201 (expected 409)

Script: `scripts/setup_agentic_memory.py probe concierge --apply` makes the same five calls (steps 1–5, one read);
the full controlled run with stream counts and the close-session A/B are recorded in
`plan/03-results.md`.

## Ruled out on the client side

The request shape (paths, params, headers) is the same as on 2026-10-15, when recall worked;
`conversation_id` has always been sent; per-agent memory generation is true and
unchanged since registration; the agentic setup is enabled and unchanged since
2026-10-13; the memory user's grants are unchanged since 2026-10-15 03:07Z; the
token carries the right `aid`, data space and tenant; the sequence matches the
product smoke driver; closing the session and using a distinct `sessionId` change
nothing.

## Asks

1. Check ingest for this tenant in `cdp-realtime-service` (writes acked, never
   persisted) and `GenerateLearning received` in `cdp-realtime-learning-service`.
2. Check whether the 2026-10-16 22:00Z `sfdc_a360` licenseless migration or the
   2026-10-17 01:00Z record-level policy regeneration cut the service identity
   off from the agentic stores.
3. Check the `enablePerAgentMemoryGenerationConfig` gate state for this cell
   (precedent: `#cdp-realtime-service`, 2026-10-06, W-23936246) and
   decisioning-pipeline PR #2151 / W-24459714 (Context Pack layered cache).
4. Secondary: the `AgenticConnector_AiParticipantInsight` stream has 59 records
   and **no DLO→DMO mapping**, so `AiParticipantInsight` (and its chunk/index
   DMOs) has always queried 0 rows. Is that expected?

## Where to file

Posted 2026-10-19 to `#d360-agent-memory-context-team` (the feature team's channel) asking
which queue owns this, with the details in the thread. `#cdp-realtime-service` is the
precedent, not a confirmed owner: one similar case (2026-10-06, memory distillation
stopped, per-agent gate) was triaged there through its intake form, but there the writes
were stored and here they are not. Other channels: `#p13n-psp-pipeline-collab` (the
GetContext server) and `#agent-conversation-script-results` (the product smoke test).
