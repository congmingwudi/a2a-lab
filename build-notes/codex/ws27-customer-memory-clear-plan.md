# WS27 batch 2 proposal — customer-memory curation and fresh-start reset

## Problem

The `Customer memory — teach Claude` experiment always uses the selected customer's
deterministic owner conversation. After the first run, `GetContext` can return both curated
profile memories and the earlier `CONVERSATION_SUMMARY`, so the teaching prompt sees matching
context and often has nothing new to store. The console has no supported way to inspect or remove
those memories before repeating the experiment.

The repository currently says that memories can only be cleared by deleting an Agent
Configuration registration. That was true when D93 was recorded, but it is stale: Salesforce now
documents profile-scoped Memory Management APIs that list entries (including `memoryId`) and
permanently delete selected entries at `DELETE /api/v1/agentic/memory`.

## Scope and semantics

Add two operator-only capabilities to the customer-memory experiments:

1. **Manage memories** lists the selected customer's curated `sys_PROFILE_FACT` and
   `sys_PROFILE_PREFERENCES` entries and allows selected permanent deletion.
2. **Start fresh** deletes all currently listed entries in those two categories and advances the
   selected customer to a new experiment conversation namespace shared by the Claude-teach and
   OpenAI-recall panes.

The second operation is intentionally broader than "delete all listed memories." The screenshot
shows a `CONVERSATION_SUMMARY`, which is episodic context and is not addressable by the documented
Memory Management delete API. Advancing the experiment namespace prevents the next run from
reading that prior conversation while retaining the old conversation in Data 360 under its normal
governance/retention rules. The UI must say this plainly; it must not claim that historical
conversation data was deleted.

All operations are scoped to the selected configured demo customer. Resetting Maya must not delete
Jordan's memories. The shared `A2ALab_Memory_Case` registration must never be deleted or recreated.

## Proposed changes

### 1. Handle the confirmed edge/content refusal before adding destructive controls

Files:

- `src/interop/memory.py`
- `src/interop/memory_adapter.py`
- `tests/unit/test_memory.py`
- `tests/unit/test_memory_adapter.py`

Claude's critique corrected the initial lifecycle diagnosis. Trace `ff19e135…` opened a fresh
session and its first entries POST received the same bare-nginx HTML 403; the session had never been
closed. A controlled synthetic probe then separated the causes (`build-notes/codex/ws27-memory-403-probe.md`):

- URL/API-path patterns in a fresh session: 403 HTML (`8254320e…`);
- the same synthetic content with those patterns removed: accepted (`44243dc2…`);
- benign content written after closing a session: accepted (`811667c7…`).

Retain D94's one-session-per-chat lifecycle. Add a narrow content-refusal classification in
`MemoryClient._call`: only the observed HTTP 403 plus HTML/nginx body shape qualifies. When an
entries ingest gets that response, retry exactly once after deterministic memory-ingest
normalization converts URLs and slash-form API paths into readable, non-URL text (for example,
`https://runbooks.example.invalid/supplier-api` → `runbooks.example.invalid supplier-api`, and
`/v1/orders` → `API endpoint v1 orders`). JSON 403 responses remain authorization/governance
failures and are not retried.

Trace both attempts. Response metadata must report that normalization was required so the console
does not present the original 403 as an auth failure or silently imply byte-for-byte storage.
Normalization applies only to the tenant ingest copy; the agent still receives and answers the
original message. Preserve content-derived idempotency by deriving the retry entry id from the
normalized content.

Tests cover HTML-403 classification, one normalized retry, no retry for JSON 403/other statuses,
readable normalization, retry success/failure evidence, and continued acceptance of a second write
to a closed session.

### 2. Preserve IDs in the memory client and add deletion

Files:

- `src/interop/memory.py`
- `tests/unit/test_memory.py`

Change `list_memories` to return a small typed entry (`memory_id`, `content`, category/source fields
that the live response actually supplies) rather than discarding `memoryId`. Keep rendering through
a content-only helper so existing prompt behavior does not change. Add `create_memories` for the
server-generated reset marker and `delete_memories` for curation/reset. Deletion:

- calls `DELETE /api/v1/agentic/memory` once per category;
- sends `profile_id`, `profile_type=individual`, and the exact category as query parameters;
- sends only `{"memories": [{"memoryId": ...}]}` in the body;
- uses the existing `MemoryClient` token path and `Hop` tracing, so no new credential or untraced
  tenant seam is introduced;
- treats an empty selection as a no-op and surfaces tenant refusals rather than pretending success.

Tests cover request method/path/query/body, response parsing, empty selection, 404/refusal, trace
detail, marker creation, and preservation of the existing saved-memory rendering/deduplication
behavior.

### 3. Add authenticated console management endpoints

Files:

- `src/console/app.py`
- `tests/unit/test_console.py`

Add scenario-scoped endpoints to list and delete memories for a configured customer key. Resolve
the customer key against `config/memory_customers.yaml`, then resolve the org-specific profile ID
only through `A2ALAB_MEMORY_CUSTOMER_MAP`; never return that profile ID to the browser. Use the
shared `A2ALab_Memory_Case` agent identity.

Add `A2ALAB_MEMORY_CUSTOMER_MAP` to the hosted console task's secret keys with the same JSON-object
shape validation used by `deploy/faces/deploy_faces.sh`. Add a deployment-script regression test;
the map currently reaches only the faces task, so this is required before the endpoints can work
hosted.

Authorization requires `_is_operator`, so every permanent operation is attributable to a verified
human operator/master; viewer, machine, and shared service-token callers receive 403. Also register
the routes in `_OPERATOR_ONLY` for defence in depth. Reject unknown scenario names, scenarios without
`memory_customer_selector`, unknown customer keys, malformed memory IDs/categories, and categories
outside the two allowlisted profile categories. Generate a new trace ID for each management call
and return it for evidence.

Deletion accepts the exact IDs selected by the UI, but the server first re-lists the selected
customer/category and intersects requested IDs with that governed result. This prevents the
endpoint from becoming an arbitrary memory-ID deletion oracle.

### 4. Introduce a tenant-backed shared experiment generation

Files:

- `src/console/app.py`
- `src/console/static/index.html`
- `src/interop/memory_adapter.py`
- `tests/unit/test_console.py`
- `tests/unit/test_memory_adapter.py`

On successful **Start fresh**, the console writes an opaque server-generated generation marker to
a dedicated custom Memory API category (for example `A2ALAB_EXPERIMENT_GENERATION`) for the selected
profile. This makes the reset effective for every operator/browser without adding an Aurora state
table or exposing profile IDs. Marker content is versioned timestamp + random nonce, contains no
customer data, and is validated before use. If a race leaves multiple markers, choose the valid
marker with the greatest timestamp deterministically and let the next reset delete all markers.
An absent marker means the customer has never been reset and retains the legacy conversation; a
present but malformed marker fails closed to a no-memory turn rather than exposing the legacy
summary.

`MemoryAdapter` reads the generation category for the two customer-memory scenarios, validates the
marker at the face trust boundary, and derives the effective conversation with the same
owner/scenario/key hashing shape as `case_conversation`. Never place caller text into a tenant id.
When no valid marker exists, retain D94's deterministic owner conversation for backward
compatibility. Pass the effective `conversation_id` through `open_and_read`, `write_turn`, and the
response evidence field; none may continue reporting or using `owner_conversation(owner)` after a
generation is active.

The browser stamps the observed generation in each turn's toggles for transcript evidence, not as
the authority. Reopening an archived pre-reset chat is read-only (or requires an explicit new-chat
fork) once its stamped generation differs from the current tenant marker, preventing an old
transcript from silently continuing in the new namespace.

Tests prove:

- the same customer/generation produces the same conversation for Claude and OpenAI;
- a new generation produces a different conversation for the same customer;
- a second browser/operator observes the tenant marker without local storage;
- Maya and Jordan remain isolated;
- malformed/multiple marker handling is deterministic; malformed present markers suppress memory;
- requests without a generation retain current behavior;
- pre-reset archived chats cannot continue silently under the new generation;
- Run All remains memory-off and does not mutate generations.

### 5. Add the management UI with destructive confirmation

Files:

- `src/console/static/index.html`
- `tests/unit/test_console.py`

Add a compact `Manage memories` action beside the Customer context row on both customer-memory
experiments. The panel lists facts and preferences with checkboxes, category labels, an empty state,
and a refresh action. It offers:

- `Delete selected` — permanent deletion of the checked curated entries;
- `Start fresh for Maya/Jordan` — selects/deletes all currently returned entries and advances the
  experiment generation.

Both destructive actions require an explicit confirmation naming the customer and count. Disable
controls while a request is pending, prevent double submission, preserve the panel on failure, and
show the tenant error plus trace ID. On success, update the list without a page reload and reset the
visible chat only for **Start fresh**.

Management reads use the raw typed entries, never `saved_memories` or its `MAX_SAVED=20` prompt cap.
Compare `totalCount` with the number returned; if they differ, report an incomplete listing and
refuse to claim or execute a full reset. For delete 404s, re-list the governed category, retry the
remaining still-present IDs once, and treat an empty remainder as idempotent success. Report any
remaining entries explicitly rather than returning an opaque tenant refusal.

Implement **Start fresh** as an explicit saga with per-step results: complete governed listing;
create the new generation marker; delete the previously listed facts/preferences and older marker
IDs; re-list all three categories; and report success only when no old semantic entries remain and
the new marker is the selected valid generation. If a later step fails, keep the new marker (so old
episodic summaries remain cut off), report the remaining semantic entries and retry action, and
never label the result fresh. Retrying converges through the 404/re-list behavior above.

Copy must distinguish:

- curated facts/preferences are permanently deleted and cannot be restored;
- earlier Data 360 conversation history is retained, but a fresh experiment namespace prevents it
  from being supplied to subsequent runs;
- automatic extraction is asynchronous, so a just-finished teaching turn might not be listed yet.
- the prompt currently limits recalled semantic entries to 20, while management lists the complete
  governed response; curation and prompt budgeting are different concerns.

### 6. Correct docs and record the shipped behavior

Files:

- `config/users.yaml`
- `config/scenarios.yaml`
- `plan/18-agentic-memory-context.md`
- `plan/00-decisions.md` (new ADR at settle station)
- `plan/07-workstreams.md` (new WS27 item lines at settle station; flip only after review/deploy)
- `plan/03-results.md` (live evidence after deploy)
- `plan/09-deployment-map.md`
- `deploy/console/deploy_console.sh`

Replace the stale "no delete short of deleting the agent registration" claim. The scenario Details
copy must explain curation versus episodic-history retention. No architecture box changes, but the
L6 console/tenant row should mention the new Memory Management read/delete seam.

The official sources to cite in the ADR/details are:

- Salesforce Developer, **Read Memories**:
  `https://developer.salesforce.com/docs/data/agentic-memory-context/references/agentic-memory-context-api-ref/agentic-memory-api-read-memories.html`
- Salesforce Developer, **Delete Memories**:
  `https://developer.salesforce.com/docs/data/agentic-memory-context/references/agentic-memory-context-api-ref/agentic-memory-api-delete-memories.html`
- Salesforce Developer, **Memory Management**:
  `https://developer.salesforce.com/docs/data/agentic-memory-context/guide/agentic-memory-memory-management.html`

## Validation and deployment

Before implementation, run and record the required baseline:

```sh
uv sync --all-extras
uv run pytest -q
```

Focused implementation checks:

```sh
uv run pytest -q tests/unit/test_memory.py tests/unit/test_memory_adapter.py tests/unit/test_console.py
uv run ruff check src/interop/memory.py src/interop/memory_adapter.py src/console/app.py tests/unit/test_memory.py tests/unit/test_memory_adapter.py tests/unit/test_console.py
```

Then run the full suite and Ruff over the repository-required paths. Build the console image locally
and smoke the list endpoint, forbidden viewer delete, Maya-only delete, fresh generation, and
Claude-teach → OpenAI-recall pairing.

This touches `src/`, `config/`, and `plan/`, all baked into the hosted images. Deployment requires
both a **full console rebuild** and a **full faces rebuild**; `--skip-build` is invalid for either.
Hosted acceptance:

1. teach Maya and wait until the expected facts/preferences list;
2. run a correction containing the known URL/API-path pattern and verify the original HTML 403 is
   traced, the normalized retry stores successfully, and the console labels normalization;
3. verify selective deletion removes only the chosen entry;
4. run **Start fresh**, then Teach Claude and verify no old `CONVERSATION_SUMMARY` or curated memory
   is supplied;
5. open the OpenAI sibling and verify it recalls the new teaching under the shared generation;
6. verify Jordan was unchanged;
7. record management and run trace IDs plus observed extraction latency in `plan/03-results.md`.

## Explicit non-goals

- deleting the `A2ALab_Memory_Case` Agent Configuration;
- claiming to erase retained episodic conversation records;
- exposing unified profile IDs or arbitrary tenant memory IDs outside the selected customer's
  governed list;
- clearing supplier-disruption case memory or Agentforce Employee-agent native memory;
- automatic clearing before every prompt (the destructive action remains deliberate and visible).
