# Support requests: the one place to look

Every request this lab has made to a team outside it: Slack intake, product or PM asks, vendor
tickets. Each entry shows where it was filed, what fix we are hoping for, where it stands now, and
which lab artifact holds the evidence. Defects found before filing live in their own ledgers (for
Agentic Memory, `build-notes/ws27/agentic-memory-product-api-defects.md`), and an entry here points to
the ledger id.

**Rules for this file**
- Add an entry when a request is **filed**, not when it is drafted. Drafts stay in `build-notes/`
  until they are posted.
- Update **Status** and **Last checked** every time the thread is read, even if nothing has changed.
  "No reply since <date>" is useful information.
- Org ids, tenant hosts, user ids and customer record ids never go here (CLAUDE.md). The posted
  thread is the copy that carries them.
- Before posting anything to a thread (a follow-up, a nudge, a close-out), get the operator's go-ahead.

Last updated: 2026-11-13

## Open: waiting on someone else

| # | Request | Filed | Where | Hoping for | Status (last checked) | Lab refs |
|---|---|---|---|---|---|---|
| SR-01 | **Agentic Memory & Context in a non-default data space** (AM-API-012) | 2026-11-11 | `#cdp-realtime-service` intake form, details in thread: [thread](https://salesforce-internal.slack.com/archives/C05CBEHHQJG/p1794433736347839) | A yes/no on non-default support, then a fix for the custom space: Memory create 400, ingest acked but never stored, Get Context 500. Answers to the thread's 5 questions. Side issue AM-API-003 (setup list ignores its data-space selector) was raised in the same thread. | **Engineer replied 2026-11-12 07:36Z ([reply](https://salesforce-internal.slack.com/archives/C05CBEHHQJG/p1794468964523519?thread_ts=1794433736.347839&cid=C05CBEHHQJG)):** they called it a **regression** and said they have a workaround fix that doesn't depend on the upstream service fixing it. The fix is pending deployment, ETA about 24 h (so around 2026-11-13 07:36Z). "No action item on customers. The configurations will auto-heal." Two things follow: non-default spaces are meant to be supported (they called this a regression, not a gap), and we should not re-provision or change `A2ALab_Memory_Space_Probe`. Leave it as it is so the auto-heal can be seen. Our five thread questions were not answered one by one. Before the reply, Get Context had already recovered on its own to 200 (empty) by ~22:05Z on 11-11. Memory create (400) and ingest persistence (404) were still failing. **Rerun 2026-11-13 16:54–16:58Z, about 33 h after the reply: partly healed.** Get Context in the custom space now returns 200 and serves the real customer's Profile from the AMP1 graph, which worked for the first time. Memory create still returns 400. Ingest is still acknowledged but never stored (empty list, 404). The default control passed everything. *Next: post a short status reply in the thread ("Context healed; Memory create 400 and ingest persistence still fail on `A2ALab_Memory_Space_Probe`, probe at 16:54Z"). This needs the operator's OK. Then rerun once more. AM-API-003 (the setup-list selector) was not covered by the reply.* (checked 2026-11-13 16:58Z) | `plan/03-results.md` 2026-11-11 (two sections) and 2026-11-13; draft `build-notes/ws27/agentic-memory-nondefault-support-slack-draft.md`; ledger AM-API-012 |
| SR-02 | **Agentic Memory writes acknowledged but not stored** (AM-API-001) | 2026-10-19 | `#d360-agent-memory-context-team`: [thread](https://salesforce.enterprise.slack.com/archives/C0BUTSPBE3G/p1792405397675259) | Root cause and fix for the intermittent "201/202 then 404" conversation loss. | **Redirected, not filed.** Anthony Yeung replied on 10-19 asking for a post in `#cdp-realtime-service` ("oncall will take a look"). The intake form was never submitted; no submission from us appears in that channel between 10-18 and 11-06. Part of the original filing has since been withdrawn: Get Context, 10-28 (D94); extraction, 10-27 (D93). Persistence is still intermittent, reproduced 11-02. Related context in the thread: another org's ticket got the answer that **out-of-the-box agentic governance policies were turned on for all orgs on 10-16** (DLO/DMO/DG row filtering by token `xid` or `UserId__c`). *Decide: file the intake for the narrowed persistence defect, or close this.* | `build-notes/ws27/agentic-memory-defect-2026-10-18.md`; ledger AM-API-001; `plan/03-results.md` 10-18, 10-27, 10-28, 11-02 |
| SR-03 | **Outbound `A2ARegistration` pilot flag** (WS29 native A2A pilot) | not recorded | A2A pilot PM (channel not recorded) | The pilot flag enabled on the lab org so Agentforce can register and call an outbound A2A agent natively. | Pending with the PM. *Fill in the channel and date the next time the thread is touched.* | `plan/07-workstreams.md` (WS29, ~line 3756); `plan/00-decisions.md` (~lines 4716–4720) |

## Resolved

| # | Request | Filed → resolved | Where | Outcome | Lab refs |
|---|---|---|---|---|---|
| SR-04 | **Real-time data graphs created 2026-11-07 got no real-time layer** (batch-only; `SourceRecordId` lookup key empty) | 2026-11-07 → fixed by 2026-11-10 | `#cdp-realtime-service`, RT on-call: [post](https://salesforce.enterprise.slack.com/archives/C05CBEHHQJG/p1794088733863059) | Fixed on the platform side with **no reply in the thread**. The 11-07 graphs now serve the link key, and POS orders become visible within 6 s. *Optional: a short "resolved, thanks" close-out so on-call can close it (needs operator OK).* | `plan/03-results.md` 2026-11-10 (~lines 2712–2721); WS32 |
| SR-05 | **Zscaler Site Review: recategorize `*.agenticthings.com`** (it was blocked as "Miscellaneous or Unknown") | 2026-10-13 → 2026-10-14 | Zscaler Site Review | Recategorization accepted; the hosted console is reachable from the corporate laptop. | `plan/19-zscaler-domain-unblock.md` |

## Superseded or withdrawn

| # | Request | Filed | Why it no longer matters | Lab refs |
|---|---|---|---|---|
| SR-06 | **AWS Service Quotas: raise the API Gateway 29 s integration timeout** | ~2026-07-26 (D41) | Made unnecessary by WS11 fire-then-poll, which keeps every call far under 29 s. The request id is in the AWS console, not here. | D41; `plan/07-workstreams.md` WS11 (~lines 2012–2014) |

## Planned: not filed yet

| # | Question | For whom | Why it's waiting | Lab refs |
|---|---|---|---|---|
| SR-07 | What `owner_id` does a customer-keyed graph expect for Get Context (Contact id vs Individual id vs unified id)? | Agentic Memory product team | Partly answered by measurement (`owner_id` = Contact id works when the registration config is intact). Ask only if WS32 step 5 needs the documented contract. | `plan/00-decisions.md` (~line 5186) |

## Ledgers that feed this list

- **Agentic Memory & Context:** `build-notes/ws27/agentic-memory-product-api-defects.md`, AM-API-001 to
  012. Only 001 (SR-02) and 012 (SR-01) have been raised externally. The others are recorded there
  with workarounds and are candidates if a product contact asks for a list.
