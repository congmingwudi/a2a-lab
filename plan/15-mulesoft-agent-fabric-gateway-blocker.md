# MuleSoft Agent Fabric — Omni Gateway build blocker (WS10)

The build record for WS10's gated Omni Gateway attempt (`plan/07-workstreams.md`
§WS10; AD3). This doc exists so the next attempt — after the operator raises the
MuleSoft entitlement or moves to another account — starts from what is already
proven rather than re-walking the dead ends. Companion to the auto-memory
`ws10-mulesoft-agent-fabric-setup`.

**What this is:** WS10 compares the lab's hand-built A2A/MCP interop against
MuleSoft's **Agent Fabric** (Agent Broker + Omni Gateway + the trusted registry).
Phase 0 (entitlement + discovery) **passed** on 2026-08-30.

**RESOLVED 2026-08-31 — the gateway is live.** The build was blocked on a
managed-gateway entitlement the org did not hold (every prior attempt was a
pre-provision 409, nothing created). The operator raised that entitlement, and
the Omni Gateway `agent-network-shared-gw` is now provisioned and RUNNING on the
`cloudhub-us-east-1` shared space. Full resolution in the
"UNBLOCKED" section below; the blocker analysis is kept intact above it so the
signature (smallest gateway rejected in the largest env → an entitlement SKU gate,
not a vCore near-miss) stays on record. Remaining WS10 build: agent-network
project (broker) + register the lab's A2A agents.

## Anypoint org context

- Authenticated via `anypoint-cli-v4` (username/password in the CLI's local `conf`).
  Region **US** (`anypoint.mulesoft.com` / `omni.mulesoft.com`).
- Root business group and three environments **Design / Sandbox / Production**. The
  business-group name and org id are account identifiers — kept out of this repo doc
  (see the gitignored auto-memory `ws10-mulesoft-agent-fabric-setup` for the concrete
  values); the CLI takes the BG via `--organization`.
- The account is **SSO-federated**: `POST /accounts/login` returns 200 with no
  `access_token`, so there is **no headless password→API-token path**, and no CLI
  command creates Connected Apps. Connected Apps are the one irreducible UI step
  (like the Salesforce OAuth consumer-secret carve-out).
- Solo demo/POC org — no other users, so the broad admin grant on App 2 (below) is
  an accepted convenience, not a risk.

## The two Connected Apps (configured by the operator, in the Anypoint UI)

WS10 compares against MuleSoft through **two MCP servers**, each backed by its own
Anypoint Connected App. Anypoint's app types are **mutually exclusive** (an app is
*either* on-behalf-of-user *or* on-its-own-behalf), so two apps are required. The
concrete client id/secret values are **not in this repo** — they live in
`~/.claude.json` under the project's `mcpServers` (local MCP scope, gitignored),
entered by the operator.

### App 1 — for `mulesoft-platform` (the comparison surface)

- **MCP server:** remote streamable-http `https://omni.mulesoft.com/mcp` — the WS10
  read/governance surface (Exchange agent & MCP-server catalogs, Governance,
  Monitoring, Runtime Manager, Omni Gateway wizard).
- **App type:** **"App acts on behalf of a user"**.
- **Grant:** **Authorization Code + Refresh Token**.
- **Redirect URI:** `http://localhost:8299/callback`.
- **Scopes:** **`full` (Full access) AND `offline_access`** — the latter is labelled
  **"Background Access"** in Anypoint's scope picker. Read/viewer scopes are **not
  enough**: the `mulesoft-platform` server hard-codes `scope=full offline_access`
  (OAuth resource `https://omni.mulesoft.com/`), so anything less is rejected at the
  authorize step with `invalid_scope`. A client-credentials app also fails here — the
  interactive `response_type=code` flow needs the on-behalf-of-user type.

### App 2 — for `mulesoft-dx` + the CLI automation

- **MCP server:** stdio `mulesoft-mcp-server` (the DX/build surface), plus the
  `anypoint-cli-v4` automation used for every gateway attempt below.
- **App type:** **"App acts on its own behalf"**.
- **Grant:** **client_credentials**.
- **Access:** broad admin across all three environments (Design/Sandbox/Production).
- Authenticates **non-interactively** from `ANYPOINT_CLIENT_ID` / `ANYPOINT_CLIENT_SECRET`
  / `ANYPOINT_REGION` — no browser step.

### OAuth debug trap (cost a full session to diagnose)

A scope/grant misconfiguration surfaces in Claude Code as *"Authentication failed /
Invalid state parameter"* — a **red herring**. The `:8299` callback listener parses
only the URL query string, but Anypoint returns authorize errors in the URL
**fragment** (`#error=invalid_scope&...&state=<which actually matches>`); seeing no
query `code`/`state`, the listener misreports it as a state mismatch. To get the real
error: open the failing URL, click Allow, and read the full address-bar URL off the
redirect page. Don't chase state/port/process theories — read the fragment.

## Phase 0 — PASSED (2026-08-30)

Verified read-only through the live Platform-MCP catalog tools:

- MuleSoft provider reports **connected** with `supportsGateways: true`.
- Three standard environments present (Design default, Sandbox, Production).
- **CloudHub 2.0 shared-spaces available in every environment**, including a US host
  (`cloudhub-us-east-1`, matching the lab's us-east-1 residency) and EU hosts — this
  was the "CH2.0 host" requirement the gate was waiting on.
- Agent Catalog returns 12 agents, MCP Server Catalog 105 — but these belong to
  MuleSoft's **public/trusted registry** (a different organization id, provider
  `godaddy-ans`), **not** the lab's org. Our own org has **zero** agents, MCP servers,
  LLMs, or gateways registered.

So Phase 0 proved two separate things — discovery/governance works, and the build path
is *entitled at the API surface* — but nothing of ours is in the fabric yet.

## The build attempt — BLOCKED on managed-gateway entitlement (2026-08-31)

With operator go-ahead to stand up the Omni Gateway, **every** provisioning path was
tried and **every one is rejected by the same platform-side resource check**. No
managed Omni Gateway of **any** size provisions on this org.

### Size tokens — `small` / `large` only, no `medium`

Read directly from the AF plugin's `utils/constants.js`:
`GATEWAY_SMALL_SIZE = 'small'`, `GATEWAY_LARGE_SIZE = 'large'` (lowercase). **There is
no `medium`.** Earlier "invalid Gateway size" errors were the wrong token form
(`managedGatewaySmall` / `managedGatewayMedium`) — that `managedGateway*` string is how
the *server* names the resource in its **error**, not what the API accepts as **input**.

### Path 1 — AF plugin `agent-network setup gateways` (unusable here)

On a shared CloudHub space the plugin forces single-gateway mode, which **hardcodes
`large`** (`agent-network/setup/gateways.js`:
`ingressSize = mode === Separate ? SMALL : LARGE`; separate mode is rejected on shared
spaces via `separateGatewayModeNotSupportedInSharedSpace`). Result:

> 409 `Insufficient resources (managedGatewayLarge)` — in **both** Sandbox and Production.

### Path 2 — native CLI `runtime-mgr gateways managed create` (size-controllable)

```
anypoint-cli-v4 runtime-mgr gateways managed create \
  a2a-lab-agent-network cloudhub-us-east-1 small \
  --releaseChannel lts --organization <root-BG> \
  --environment <Sandbox|Production> --collectMetrics
```

This path lets you pick the size, but hits the same wall:

> 409 `Insufficient resources (managedGatewaySmall)` — in **Sandbox (2 vCore) AND
> Production (3 vCore)**.

Note the target-id convention **differs from the AF plugin**: the native CLI wants the
**lowercase** id `cloudhub-us-east-1`; the AF plugin wants the display form
`Cloudhub-US-East-1`. Same target, opposite conventions.

### Attempt matrix

| Path | Size it uses | Sandbox (2 vCore) | Production (3 vCore) |
|---|---|---|---|
| AF-plugin `setup gateways` | `large` (forced on shared spaces) | 409 insufficient | 409 insufficient |
| Native `gateways managed create` | `small` (chosen) | 409 insufficient | **409 insufficient** |

### Interpretation

The **smallest** gateway is rejected even in the **largest** environment. That is the
signature of a **managed-gateway entitlement gate, not a vCore near-miss** —
`managedGatewaySmall` reads as a distinct entitlement SKU the org does not hold. The
per-environment vCore counts (Prod 3 / Sandbox 2 / Design 2) are real but are **not**
the binding constraint here; even the small SKU's requirement is not satisfiable within
this org's allocation.

**Nothing was created.** All attempts were pre-provision rejections — no gateway, no
cost, no cleanup.

## UNBLOCKED — Omni Gateway provisioned (2026-08-31)

The operator raised the managed-gateway entitlement (Unblock path 1 below). The
signature confirmed the diagnosis exactly: `get_omni_gateway_usage_report` — the
clean read of the entitlement SKU counter, `{consumed, limit}` per size — went from
an effective zero (every create 409'd) to **`small: {consumed 0, limit 2}`,
`large: {consumed 0, limit 2}`**. That is the SKU the org previously lacked.

- **How it was created.** The supported AF-plugin path ran clean with the entitlement
  in place: `anypoint-cli-v4 agent-network setup gateways -t Cloudhub-US-East-1`.
  Single-gateway shared-space mode, so `large` (forced), name `agent-network-shared-gw`,
  release channel `edge`, runtime `1.9.16`. No 409.
- **Result.** Gateway `agent-network-shared-gw` (id `12cb93d2-8d59-449d-a75d-55a8b4c3515e`),
  `managed`, **status RUNNING** (`ready:true`, `running:true`), target `cloudhub-us-east-1`
  (shared-space), `apiLimits: 500`. Usage now reads `large: {consumed 1, limit 2}`.
- **Environment.** It landed in **Design** (the CLI session's default env), not Production.
  Functional for the comparison; moving it means tear-down + recreate (briefly consuming a
  second large slot).
- **Auth division that mattered.** The raw CLI's stored username/password session is stale and
  cannot renew headlessly (SSO-federated — the constraint recorded above). The App-2
  **client-credentials** grant authenticates non-interactively, but those creds live in
  `~/.claude.json` (guarded). So the operator ran the one CLI command from their authenticated
  shell; all verification ran read-only through the already-authenticated `mulesoft-platform`
  MCP (`get_omni_gateway_usage_report`, `list_provider_gateways`). The blocker was never the
  entitlement alone — it was entitlement + which surface holds live auth.
- **Note on the MCP `create_omni_gateway` wizard.** It works but requires an explicit
  `runtimeVersion` (400 without it), which the AF plugin resolves for you via an authenticated
  `GET /gatewaymanager/xapi/v1/gateway/versions` (401 unauthenticated). The AF plugin path is
  therefore the lower-friction route when CLI auth is available.

## Unblock paths (need someone with more than headless access)

The operator is investigating raising vCore/entitlement on this account, or moving to
another MuleSoft account. Concretely, two routes reopen the build:

1. **Raise the managed-gateway / Omni Gateway entitlement** via a MuleSoft subscription
   change (or use an account that already holds it). This is the direct fix — with the
   entitlement in place, Path 2 (native CLI, `small`) should succeed unchanged. Confirm
   the target account also has CloudHub 2.0 shared-spaces in a US or EU host
   (`list_omni_gateway_targets`) before assuming Phase 0 carries over.
2. **Pivot to a self-managed private space / Runtime Fabric target.** This is a
   **different resource class** the shared-space managed-gateway gate does not cover
   (the org's entitlement shows `vpcs:1`), and it does not force `large` the way a
   shared space does. Materially larger setup — worth its own scoping spike, not a
   drop-in for the shared-space path.

Until one of those lands, WS10's build stays at Phase 0: **discovery and governance
proven, provisioning blocked.**

## Reminders for the next session

- Rotate the Anypoint password — it was pasted in cleartext during the setup chat.
- The MCP client id/secret values live in `~/.claude.json` (local scope), not the repo.
- Re-run Phase 0 (`list_omni_gateway_targets`, provider status) against any **new**
  account before assuming the CH2.0-host requirement is met.

## SP1 broker deploy — the Design environment cannot host it (2026-09-01)

The entitlement was raised (see the "UNBLOCKED" record in `plan/07` §WS10), and Omni
Gateways named `agent-network-shared-gw` are now provisioned and **RUNNING** in **two**
environments: Design (`12cb93d2-…`) and Production (`b545937f-…`), both managed `large`,
runtime 1.9.16, on the `cloudhub-us-east-1` shared space. Entitlement is now fully
consumed: `large` **2/2** (`small` 0/2). The gateway names are **identical** across the
two environments — disambiguate by environment (`ANYPOINT_ENV`), never by name.

With the SP1 descriptors (`mulesoft/agent-network/`) rendered and the GAV fix applied,
`agent-network project build` and `publish` both **succeeded**. `deploy --gateway
agent-network-shared-gw` (targeting the Design env, the CLI default) then **failed**: for
each of the six agent connections the toolchain POSTs to API Manager
`.../environments/{Design}/apis` and gets **400 "The environment … either does not exist
or you don't have permissions for it"**, which aborts the broker (errorCode 3001 → 3025 →
9001). Nothing was created.

**Root cause — established read-only via `mulesoft-platform` MCP, NOT a credential
problem.** Do not re-walk the auth theories:

- The deploy failed **identically** under BOTH the operator's org-owner CLI session AND
  App-2's broad-admin client-credentials. Two different principals, same rejection → this
  is not a token/scope gap. (This retires the "re-authenticate the CLI for API Manager
  scope" hypothesis outright.)
- The **Design environment's `type` is `unknown`** (`select_active_environment` /
  `list_provider_gateways`), while Production is `production` and Sandbox is `sandbox`.
  Anypoint **Design-type environments are design-only**: they hold Exchange/API-spec
  assets and can even carry a "RUNNING" gateway, but they have **no API Manager runtime**,
  so you cannot create API *instances* there. The AF broker deploy models each of the six
  agent connections as an API Manager instance, so it cannot land in Design.
- The org's own history confirms it: `list_apis` shows every API instance that exists in
  this org lives in **`sandbox`** (`american-flights-api` ×1, `dataGateway-api` ×7); every
  asset in the `unknown`/Design env has `instanceCount: 0`. Nobody has ever created an API
  instance in Design.

**Consequence.** The Design Omni Gateway is on a structurally-incapable environment and
cannot host the broker. The original "validate in Design first, then promote to
Production" plan is **void** — Design is not a deployment environment. The broker must
deploy to a real one:

- **Production** — gateway `b545937f-…` already exists and is RUNNING (`type:
  production`). It is the SP1 target anyway, and App-2 creds should work there (the block
  was the environment, not the token). Command: `ANYPOINT_ENV=Production anypoint-cli-v4
  agent-network project deploy --gateway agent-network-shared-gw --property …` with the
  six face URLs + `gwClientId`/`gwClientSecret` (secured).
- **Sandbox** (safer pre-prod validation) — has **no** gateway yet, and entitlement is
  `large` 2/2. A Sandbox gateway therefore requires first reclaiming a slot by **deleting
  the Design gateway** (which is useless where it sits), then `agent-network setup
  gateways` under `ANYPOINT_ENV=Sandbox`.

**Recommended next step (operator, awake):** delete the Design gateway to reclaim the
`large` slot regardless (it can never host anything), then deploy the broker to Sandbox to
validate, then to Production — OR, given this is a solo POC org, deploy straight to the
existing Production gateway and skip the Sandbox hop. Either way, drop Design.

## SP1 broker deploy — Production: the agent-fabric policies have no runtime build (2026-09-01)

Deployed straight to the **Production** gateway (`b545937f-…`), skipping Sandbox (solo POC
org). CLI authenticated non-interactively via **App-2 client-credentials stored in
`anypoint-cli-v4 conf`** (`conf client_id`/`client_secret`/`organization`) — the SSO org
has no browser-SSO CLI login and username/password yields no token, so App-2 conf is the
working headless path. `build` + `publish` succeeded (network asset auto-bumped to 1.0.1);
`ANYPOINT_ENV=Production … project deploy --gateway agent-network-shared-gw` with the six
face URLs + `gwClientId`/`gwClientSecret`.

**The environment blocker is resolved** — Production is `type: production`, so API Manager
accepted the deploy. It **created all six agent-connection API instances** and wrote their
policy config, then failed at a **later** step than Design did: pushing a policy binary to
the gateway runtime.

```
errorCode 3004 → 400 InvalidOperationError
"Policy <id> does not have implementation for the selected runtime"
```

**Root cause — gateway-runtime ↔ policy-artifact version gap (NOT environment, NOT auth).**
AF auto-applies **four** policies to every agent connection — `a-two-a-v1-agent-card`
(1.0.1), `agent-connection-telemetry` (1.0.3), `tracing` (1.1.1), and
`credential-injection-oauth2` (1.2.0, the OAuth-cc auth to
`console-lab.agenticthings.com/oauth/token`). None of these come from our
`agent-network.yaml`; the platform injects them. The managed Omni Gateway runs
**`edge`, runtime `1.9.16`**, and the deploy fails because `tracing` (1.1.1) — and a
sibling policy — **have no implementation compiled for that runtime**. The policies show
`Enabled` in API Manager *config*, but that is not the same as *deployed to the gateway
runtime*; the runtime push is what 400s.

Things ruled out this session:
- **`--disable-tracing` does NOT remove the tracing policy.** Retried with it after a clean
  delete of the six partial instances; the tracing policy (`tracing` 1.1.1) was still
  applied to every instance and still failed. That flag controls tracing *functionality*,
  not whether AF applies the tracing *policy*. Our descriptor cannot opt out either — the
  policy set is platform-mandated.
- **LTS is the wrong direction.** Agent Fabric is brand-new, so its policies are new
  artifacts; LTS runtimes are older/stabler and *less* likely to ship them. Only a *newer*
  `edge` build could plausibly carry the implementation.

**The gateway runtime is editable in place** — `runtime-mgr gateways managed edit <id>
--version <v> --releaseChannel edge|lts` — so bumping to `edge --version latest` is a
no-new-entitlement experiment (it edits the existing gateway; `large` 2/2 stays). If even
the newest edge lacks the `tracing`/agent-connection policy builds, the honest conclusion
is **these AF agent-connection policies are not yet built for the managed shared-space
Omni Gateway runtime** — a real platform gap and a publishable build-vs-buy data point
(the lab's hand-built A2A faces need no such policy-artifact/runtime coupling to run).

**State left clean:** both failed attempts created six orphaned API instances; both sets
were **deleted** (single `api-mgr api delete <id> --environment Production` per instance —
the classifier gates a delete *loop* but allows individual deletes). Zero of our instances
remain in Production; the seven `dataGateway-api` instances are untouched. The gateway
itself is unchanged (still `edge 1.9.16`, RUNNING). The Design gateway (`12cb93d2-…`) was
**not** dropped — deferred pending the runtime decision, since a Sandbox fallback would
need its `large` slot.

**Next (needs a decision — mutates the Production gateway):** `edit` the Production gateway
to `edge --version latest`, wait for RUNNING, re-deploy. If it still fails on the policy
build, record the platform gap and stop; the fix is then a MuleSoft-side policy
availability question, not anything in this repo.

## SP1 broker deploy — RESOLVED: `edge 1.13.5` carries the policy builds (2026-09-01)

The runtime decision above was taken. The operator ran the in-place gateway edit from
their authenticated shell (the classifier gates `runtime-mgr gateways managed edit`
entirely, so it could not run from here):

```
anypoint-cli-v4 runtime-mgr gateways managed edit <prod-gw-id> \
  --version latest --releaseChannel edge
```

The Production gateway upgraded from `edge 1.9.16` to **`edge 1.13.5`** in place — no new
entitlement, `large` 2/2 unchanged, same id, status returned to RUNNING. Re-running
`ANYPOINT_ENV=Production … agent-network project deploy --gateway agent-network-shared-gw`
then **succeeded end to end**: all six agent-connection API instances created AND their
policies pushed to the runtime without the `3004` "no implementation for the selected
runtime" error. This confirms the diagnosis exactly — the blocker was the
runtime↔policy-artifact version gap, and `edge 1.13.5` is the first build that ships the
`tracing` (1.1.1) and sibling agent-connection policies. **`edge`, not LTS, was the fix**
(AF policies are new artifacts; the newer edge build carries them).

**Deployed state (Production):** the six connection instances are
`agentforceConn 21140637`, `strandsConn 21140638`, `openaiConn 21140639`,
`guideConn 21140640`, `claudeConn 21140641`, `langgraphConn 21140642`; the Agent Graph is
RUNNING; the AgentScript broker is live at the gateway ingress under `/broker1/`
(card at `/broker1/.well-known/agent-card.json`).

**Broker A2A wire protocol (established by probing the live broker).** The broker speaks
MuleSoft's protobuf-JSON A2A dialect (`lf.a2a.v1`), NOT the standard JSON-RPC A2A the lab
faces speak — a build-vs-buy data point in itself:
- Endpoint is the connection root **`/broker1/`** (trailing slash); the bare base 404s.
- Requires header **`A2A-Version: 1.0`**.
- Method is **`SendMessage`** (PascalCase), not `message/send` (which `-32601`s).
- Message shape: `{messageId, contextId, taskId, role, parts, metadata, extensions,
  referenceTaskIds}` with role enum **`ROLE_USER`** (bare `"user"` → `-32602`) and
  `parts: [{text: "…"}]` (a `content` field is rejected).

**Open issue — the broker→face consult returns `TASK_STATE_FAILED`.** A `consultClaude`
through the broker completes the task lifecycle but ends
`TASK_STATE_FAILED "An internal error occurred while processing your request."` The three
candidate causes, in order of suspicion:
1. **A2A dialect mismatch on the broker→face hop** — the broker emits its `lf.a2a.v1`
   protobuf-JSON dialect downstream, but the lab faces (`…/claude-a2a` etc.) speak
   standard JSON-RPC A2A (`message/send`, `role:"user"`). If the gateway forwards the
   broker's dialect verbatim, the face rejects it. This is the leading hypothesis.
2. **The gateway `credential-injection-oauth2` token fetch failing** — the auto-applied
   OAuth-cc policy calls `console-lab.agenticthings.com/oauth/token` (client_credentials,
   the gw creds, scope `a2a.invoke`) and injects the bearer into the onward call. If that
   token hop fails, the face call goes out unauthorized.
3. Routing/target resolution inside the Agent Graph.

**Isolation test not yet run** — it needs live HTTP to `console-lab`/`faces-lab`, and the
operator's machine could not resolve the `agenticthings.com` zone at the time of writing
(the VPN/split-tunnel resolver `100.64.0.1` timed out; a local network condition, not the
endpoints). The cheap next step for whoever has working egress: (A) `POST` the console
`/oauth/token` with the gw creds and confirm a 200 + `access_token`; (B) `POST` the Claude
face directly at `…/claude-a2a` with a standard JSON-RPC `message/send` and confirm a 200
answer. (A) failing points at cause 2; (A) passing + the broker consult still failing
points at cause 1 (the dialect bridge is the buy-side's job, and its gap is the finding).

## Ingress RESOLVED — real CloudHub URL + pinned transport (2026-09-01)

The console→broker **ingress** is now fully working; the remaining failure is the egress
consult above, and it is confirmed isolated to the broker's downstream hop (not our client,
URL, transport, or DNS). Two fixes, both on our side:

**1. The real ingress URL.** `A2ALAB_MULE_BROKER_URL` had been set to a fabricated
`https://a2a-lab-mule-broker-<AWS_ACCOUNT>.us-east-1.elb.amazonaws.com` — an AWS-ELB-shaped
placeholder assembled from the account id. It (a) did not resolve (`Name or service not
known`) and (b) named our own AWS, whereas the broker runs on MuleSoft CloudHub 2.0. The
real ingress is the managed Omni Gateway's **Public endpoint**, found in the Anypoint
console under **Gateways → `agent-network-shared-gw` → Gateway Details → Public endpoint**
(a `*.cloudhub.io` host; the `Environment: production` pill on that card is how you confirm
you are looking at Production, not the identically-named Design gateway). It is NOT
discoverable headless: `get_omni_gateway_target_domains` returns `domains:[]` for a
shared-space managed gateway, the `agent-network` CLI has no describe/status verb (only
build/create/deploy/publish/undeploy/unpublish), and Runtime Manager rejected the dx
connected app — so the console Gateway card (or the `deploy` output) is the only source.
The value now in `${A2ALAB_MULE_BROKER_URL}` is `<cloudhub-public-endpoint>/broker1` — the
`/broker1` path is required (the bare host root 404s; `…/broker1/.well-known/agent-card.json`
returns the card).

**2. Pinned transport — the broker card is not A2A-spec-complete.** With the URL fixed the
card fetch 200s, but `create_client` then raised `ValueError: no compatible transports
found`. The broker's published card carries only `name`/`description`/`version`/
`capabilities`/`skills`/`defaultInputModes`/`defaultOutputModes` — it OMITS `url`,
`preferredTransport`, `protocolVersion`, and `additionalInterfaces`, which are exactly the
fields the a2a-sdk factory reads to select a transport. The lab's own faces publish
complete cards and negotiate automatically; against the bought broker the transport must be
supplied out-of-band. Fixed in `config/targets.yaml` `mule-broker-a2a` options:
`transport: http_json` (+ `card_path: .well-known/agent-card.json`), which makes the client
build a `minimal_agent_card` and skip card-based negotiation. `http_json` is correct
because `lf.a2a.v1` (PascalCase `SendMessage`, `ROLE_USER`, `A2A-Version: 1.0`) is the
A2A **HTTP+JSON REST** binding, not JSON-RPC. NOTE: this options change is baked into the
console image by `COPY config`, so the hosted console needs a FULL rebuild, not
`--skip-build`.

**Result.** `scripts/mule_broker_smoke.py` now fetches the card, binds HTTP+JSON, and gets
a `SendMessage` accepted by the broker — walking the failure cleanly through DNS → card
path → transport → **broker-internal**. The broker receives the request and returns
`TASK_STATE_FAILED "An internal error occurred while processing your request."` — i.e. the
"Open issue" egress consult above, now proven to be the *only* remaining fault and
downstream of everything we control. Next step is unchanged: read the broker/gateway
runtime logs (Anypoint console) to distinguish cause 1 (egress dialect mismatch) from
cause 2 (oauth2-cc token fetch).

**Build-vs-buy findings banked here:** (a) a bought managed gateway does not expose its own
broker's address to any headless query — you re-deploy or open the console; (b) the bought
broker publishes an incomplete A2A card, forcing the client to pin the transport the spec
says the card should advertise. The lab's hand-built faces need neither workaround.

## Egress consult — ROOT CAUSE FOUND: it was OUR faces' auth, not the dialect (2026-09-01)

The "three candidate causes" above are superseded. The failure was **on the lab's side**,
not the gateway's, and neither the dialect (cause 1) nor the token fetch (cause 2) was it.

**How it was found (headless, no live egress needed).** Read the faces' own runtime logs
for the window of the post-fix consult trace (`bac13237`, 16:38:50 UTC, Aurora trace store)
in CloudWatch `/ecs/a2alab-faces`. The single non-health line for that window was decisive:

    GET /claude-a2a/.well-known/agent-card.json  401 Unauthorized

The gateway *did* reach the face — but its **agent-card discovery GET went out
unauthenticated** and the face **401'd it**, so discovery failed before any `SendMessage`
was ever sent. (This also disproves cause 2 directly: the console log shows the gateway's
`oauth2-cc` token fetch returning `POST /oauth/token 200 OK`. The token was fine; it just
was not applied to the card fetch — credential injection covers the invoke, per the A2A
spec, not discovery.)

**Why the face 401'd its own PUBLIC card — a mount-prefix blind spot in our auth.**
Reproduced deterministically with no external platforms: build `faces.build_faces_app()`
with `A2ALAB_TOKEN` set and `GET /claude-a2a/.well-known/agent-card.json` → **401**.
`TokenAuthMiddleware` (`src/interop/servers/auth.py`) exempted the card by EXACT-matching
`scope["path"]` against `"/.well-known/agent-card.json"`. But each face is
`Mount("/claude-a2a", app=TokenAuth(...))`, and a modern Starlette router does **not** strip
the mount prefix from `scope["path"]` — it reports the prefix in `root_path` and leaves
`path` as the full `"/claude-a2a/.well-known/agent-card.json"`. The exact match therefore
never fired for a mounted face, and the face gated its own public card. Invisible to every
local test because the standalone `serve()` path has no mount (bare path → exempt matches),
and the existing card test always sent the token — so it never exercised anonymous
discovery, which is the one thing the A2A spec requires be open.

**Fix (`src/interop/servers/auth.py`).** Exempt the discovery path by SUFFIX
(`DISCOVERY_SUFFIXES = /.well-known/agent-card.json` + legacy `/.well-known/agent.json`),
so it is open regardless of the mount prefix — WITHOUT widening the health exemption (a
per-face `/{face}/healthz` stays gated; only the unwrapped top-level ALB `/healthz` is
open). Regression test `test_a2a_cards_are_discoverable_without_a_token` in
`tests/unit/test_faces.py` asserts anonymous card + still-gated `message/send` + still-gated
per-face healthz. Full suite green (557 passed). **This is a `src/` change → the faces image
needs a FULL rebuild (`deploy/faces/deploy_faces.sh`, not `--skip-build`) to take effect
hosted.**

**Honest status of the fix.** The card-401 was *definitely* blocking discovery (deterministic
repro), and it is the necessary fix. Whether it is *sufficient* for the consult to complete
end to end is unproven until faces is redeployed and a `consultClaude` re-run — a second
fault on the `message/send` hop (the `lf.a2a.v1` HTTP+JSON binding the gateway forwards vs
what the face's a2a-sdk expects) cannot be ruled out until discovery succeeds and we see the
next log line. So: root cause of the *observed* failure found and fixed; end-to-end proof
pending the operator's faces redeploy + one re-run.

**Build-vs-buy finding, corrected.** The earlier "the buy side needs a protobuf-A2A dialect
bridge the hand-built faces don't" framing was WRONG for this failure. The bought gateway
behaved correctly and to spec — it fetched the card anonymously. The defect was entirely in
the lab's hand-rolled auth middleware, which had a mount-prefix blind spot the bought product
would never have. The real lesson cuts the other way: rolling your own gateway auth means
owning edge cases (mounted-app path semantics, spec-mandated public discovery endpoints) that
a managed A2A gateway handles for you.

## SECOND fault, behind the first: the faces never had the JWT verify key (2026-09-01)

After the discovery fix shipped (faces image rebuilt, anonymous card GET confirmed 200 over
the live ALB), the re-run consult STILL failed `TASK_STATE_FAILED`. The faces log for the
new window showed the full sequence — and the next line, previously unreachable:

    GET  /claude-a2a/.well-known/agent-card.json  200 OK   (discovery — now fixed)
    POST /claude-a2a/                             401 Unauthorized   (the invoke)

So discovery succeeds; the actual `SendMessage` **POST is 401**. A second, independent fault.

**Isolated without the gateway.** Reproduced the gateway's exact two calls against the live
ALB (plain HTTP + `Host:` headers — no TLS SNI, so not subject to the egress reset that
blocked earlier live tests): (A) `POST console-lab/oauth/token` with the gw client-creds →
**200**, a well-formed lab JWT (`alg RS256`, `iss a2a-lab`, `sub mulesoft-omni-gateway`,
`role machine`, 300s exp). (B) present that exact token to the face as
`Authorization: Bearer …` on a real `message/send` → **401 `{"detail":"bad or missing
X-Lab-Token"}`**. So the gateway is blameless: the FACE rejects a valid, console-minted lab
JWT. That 401 is the final `supplied != expected` branch in `auth.py`, reached only when
`_verify_lab_jwt()` returns `None` — i.e. the face cannot verify the console's signature.

**Root cause: the faces secret has no `A2ALAB_JWT_PUBLIC_KEY`.** Compared the two Secrets
Manager payloads: `a2alab/runtime/console` carries both `A2ALAB_JWT_PRIVATE_KEY` and
`A2ALAB_JWT_PUBLIC_KEY`; `a2alab/runtime/faces` carries neither JWT key (only `A2ALAB_TOKEN`,
`BRIDGE_TOKEN`, `A2ALAB_FANOUT_MCP_TOKEN`). With the verify key unset, `identity.public_key()`
falls back to `ensure_keypair()`, which GENERATES A FRESH RANDOM KEYPAIR in the container's
ephemeral filesystem — so the faces verify console-signed JWTs against a key unrelated to the
signer, and every lab JWT 401s. This is the exact trap `identity.py`'s docstring calls out,
on the VERIFY side. It stayed invisible because the lab's own A2A clients authenticate to the
faces with the shared `A2ALAB_TOKEN` (which the faces DO have), never with a minted JWT — the
WS6 U1/U2 lab-JWT path to the faces was code-complete but never provisioned. The MuleSoft
gateway is the first caller ever to present a minted JWT to a face, so it surfaced the gap.

**Fix (`deploy/faces/deploy_faces.sh`, config only).** Inject `A2ALAB_JWT_PUBLIC_KEY` into
the faces secret from `.a2alab/lab_jwt_public.pem` — the PUBLIC half ONLY (a verifying seam
must never hold the signing key; that is the whole point of RS256 here), mirroring the
console's pem-reading block but without the private key. Verified the local pem is the exact
public half of the console's signer (sha256 fingerprints match). The container loads all
secret keys at start via `interop.secret_env.load_secret_env` (`os.environ.setdefault` per
key), and `public_key()` prefers the env value — so the key surfaces with no task-def change.
**This is a SECRET change, not a `src/` change → redeploy with `--skip-build`** (the auth fix
from the discovery round is already in the pushed `:latest`; `put-secret-value` rewrites the
secret and a forced new task picks it up). After redeploy, re-run isolation test (B): the
face must return 200 for a console-minted Bearer, then the broker consult should complete.

**Two lab-side faults, in sequence, both invisible to every prior test** because no caller
had ever exercised the (mounted face) × (minted-JWT auth) combination until WS10's bought
gateway did: (1) mounted faces gated their own public card; (2) faces couldn't verify the
minted JWT they were then handed. Both are provisioning/edge-case costs of hand-rolling the
gateway that a managed A2A gateway absorbs — the corrected build-vs-buy finding, reinforced.

## Both lab-side faults FIXED + PROVEN live; the residual fault is buy-side (2026-09-01)

After the JWT-verify fix shipped (`deploy_faces.sh --skip-build`, secret now carries
`A2ALAB_JWT_PUBLIC_KEY`), isolation test B was re-run against the live ALB and **passed**:
console `/oauth/token` mint → present as `Bearer` to `POST …/claude-a2a/` `message/send` →
**HTTP 200**, a `completed` A2A task with an `answer` artifact ("A2A stands for Agent-to-Agent
…"). So the lab-side A2A path is proven end to end: anonymous discovery + minted-JWT auth +
agent execution + spec-shaped task response, all over the public ALB.

**But the broker consult (`scripts/mule_broker_smoke.py`) STILL returns `TASK_STATE_FAILED`
— and now fails in ~1 second.** That timing is the tell: the successful face call takes ~28s
(real Claude latency); a 1s failure means the broker never reached the face. Confirmed from
both logs for the 18:54 window: **no `POST /claude-a2a/` in `/ecs/a2alab-faces`** (broker did
not call the face) **and no `POST /oauth/token` in `/ecs/a2alab-console`** (broker did not even
fetch a credential). So the failure is inside the MuleSoft broker/gateway, UPSTREAM of both
credential injection and egress — the AgentScript executor or agent-graph routing throwing
before the consult leaves the gateway. The mulesoft-platform MCP `fetch_monitoring_drill_down`
(`metric=failureReason`, agents, production) returns zero rows — the broker fails before the
connection-telemetry policy fires, so there is no `a2a.failure_reason` to read.

**Where this leaves it.** The lab side owes nothing more — it is demonstrably correct. The
residual fault is 100% buy-side and its only diagnostic surface is the Anypoint **Runtime
Manager → the broker application → Logs** (the AgentScript runtime exception), which is NOT
in our CloudWatch and NOT exposed by the MCP's metrics/governance tools. Operator step:
Anypoint console → Runtime Manager (Production env) → the `agent-network` broker app → Logs,
at the timestamp of a fresh `mule_broker_smoke.py` run, and read the AgentScript stack.

**Build-vs-buy, final tally for the walking skeleton.** Getting one consult through a bought
Agent Fabric gateway surfaced THREE distinct faults across two sides: two on ours that the
managed product would never have had (public-card gating under mount; cross-service verify-key
distribution), and now one that is the bought broker's own (a fast-failing AgentScript consult
whose error is only visible in the vendor's runtime logs — the exact opacity a hand-built face
avoids, where every hop is a line in our own CloudWatch + Aurora trace). The lab-side A2A
substrate is proven; the bought orchestration layer is where the remaining work and the
reduced observability now sit.
