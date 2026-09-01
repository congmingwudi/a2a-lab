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
