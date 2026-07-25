# Runbooks — manual/credentialed steps

Everything here needs accounts or consoles the repo can't reach. Each
section ends with its verification command.

## 1. Anthropic Managed Agents (Claude backend, default)

1. Ensure `ANTHROPIC_API_KEY` is set (or `ant auth login`).
2. Provision the control-plane resources once:
   ```sh
   uv run python scripts/setup_managed_agent.py
   ```
   This creates the `a2a-lab` cloud environment and the "A2ALab Research
   Assistant" agent (full prebuilt toolset incl. web_search/web_fetch, plus
   the `ask_agentforce` custom tool for Path B) and writes the IDs to
   `.a2alab/managed.json`. Re-run with `--recreate` to version the agent.
3. **Verify:**
   ```sh
   uv run python -m platforms.claude --protocol rest --port 8001 &
   curl -s -X POST localhost:8001/invoke -H 'content-type: application/json' \
     -d '{"message": "In one sentence, what is the A2A protocol?"}'
   ```
   Watch the session live in the Anthropic Console (the trace URL pattern is
   `https://platform.claude.com/workspaces/<workspace>/sessions/<id>`).
4. Fallback / latency variant: `CLAUDE_BACKEND=sdk` (self-hosted
   claude-agent-sdk; needs the Claude Code CLI on PATH, which `uv run`
   resolves via the bundled dependency).

## 2. Salesforce org onboarding (M5) — via Salesforce MCP servers

Per decision D10, org work is driven through the **Salesforce DX MCP
server** (`@salesforce/mcp`), registered in `.mcp.json` at the repo root —
open this repo in Claude Code locally and the `salesforce-dx` MCP tools are
available. Raw `sf` CLI equivalents are noted as fallback.

1. **Authenticate the org** (one-time, browser): `sf org login web --alias
   a2alab-prod --set-default` — the MCP server reads the same auth store.
   Verify with `sf org display --target-org a2alab-prod`.

   **Always pass `-o a2alab-prod` explicitly; never rely on the default or on
   an org listing.** Two independent traps, both live on this machine as of
   2026-07-25 and both silent:
   - the CLI's global `target-org` belongs to whichever project was touched
     last (it currently reads `hls-mega-demo2-demo`), so an unqualified
     `sf project deploy` aims this lab's metadata at someone else's org;
   - the **DX MCP server lists an allow-listed subset of orgs, and
     `a2alab-prod` is not in it** — `list_all_orgs` returns only the default,
     which reads exactly like "the lab org isn't authenticated" when it is.
     Fall back to `sf org list` to see the truth; the MCP tools still accept
     `usernameOrAlias: a2alab-prod` when you pass it.
   The alias is recorded as `SF_TARGET_ORG` in `.env` so it is a lookup rather
   than a memory.
2. **External Client App** (Setup → App Manager → New External Client App):
   - OAuth: client-credentials flow enabled; scopes `api`, `chatbot_api`,
     `sfap_api`. (`refresh_token` was dropped in F3 — the client-credentials
     and JWT-bearer flows never issue one. This shared app is the
     local-development identity; per-caller apps are §11.)
   - Run-as user: the dedicated `a2alab.integration` user (create it first:
     minimal profile + Agentforce permission set; API-only if available).
   - Record Consumer Key/Secret → `SF_CLIENT_ID` / `SF_CLIENT_SECRET` in
     `.env`; `SF_MY_DOMAIN` = the org's My Domain host.
3. **Build the agent** (Agent Script — D14): edit the authoring bundle at
   `salesforce/force-app/main/default/aiAuthoringBundles/A2ALab_Research_Assistant_Script/`,
   then `sf agent validate authoring-bundle -n A2ALab_Research_Assistant_Script -o a2alab-prod`,
   `sf agent publish authoring-bundle -n ... -o a2alab-prod`, and
   `sf agent activate --api-name A2ALab_Research_Assistant_Script -o a2alab-prod`.
   Record the agent Id (`SELECT Id, DeveloperName FROM BotDefinition`) →
   `SF_AGENT_ID`. The agent user needs the `A2ALab_Agent_Actions`
   permission set (Apex action + object read access).
4. **Go/no-go gate:** `uv run python scripts/sf_smoke.py` — token, session,
   round-trip, delete. If this fails on licensing, stop and resolve before
   any further Salesforce work.
5. **Deploy the lab metadata** (Apex invocable + test + credentials) via the
   MCP metadata tools — deploy `salesforce/force-app` to `a2alab-prod`,
   running local tests (prod requires ≥75% coverage; the MCP testing
   toolset runs `A2ALabInvokeRemoteAgentTest`). Fallback:
   `sf project deploy start -d salesforce/force-app -o a2alab-prod -l RunSpecifiedTests -t A2ALabInvokeRemoteAgentTest`.
6. **Finish the credential** — the principal (`A2ALabPrincipal`) deploys
   with the External Credential metadata; set its `BridgeToken` parameter =
   the `BRIDGE_TOKEN` value via the Connect API (no Setup clicking):
   ```sh
   sf api request rest /services/data/v62.0/named-credentials/credential \
     --method POST --body @cred.json -o a2alab-prod
   # cred.json: {"externalCredential":"A2ALab_Bridge","principalName":"A2ALabPrincipal",
   #   "principalType":"NamedPrincipal","credentials":{"BridgeToken":{"value":"<BRIDGE_TOKEN>","encrypted":true}}}
   # (PATCH instead of POST to rotate an existing value)
   ```
   Principal access for the bot user rides in the `A2ALab_Agent_Actions`
   permission set (`externalCredentialPrincipalAccesses`).
7. **The actions are declared in the Agent Script** (D14/D15): the
   authoring bundle's `customer_account_status` subagent carries both
   `get_account_summary` (Apex `A2ALabGetAccountSummary`) and
   `ask_external_researcher` (Apex `A2ALabInvokeRemoteAgent`) — publish +
   activate the bundle (step 3) and they're live; no Agent Builder step.

## 3. Cloudflare tunnel + DNS (M6, revised for free plan — D20)

1. Create a free Cloudflare account → **Add a site** → `agenticthings.com`
   → Free plan. Review the DNS records Cloudflare auto-imports (MX etc.)
   before proceeding.
   *(Why whole-zone: subdomain-only NS delegation of `lab.agenticthings.com`
   is an Enterprise feature and partial/CNAME setup is Business — on Free,
   the entire zone's DNS moves to Cloudflare. Hostnames are single-level
   `<svc>-lab.agenticthings.com` because free Universal SSL covers only
   `*.agenticthings.com` — a two-level `bridge.lab.…` fails the TLS
   handshake at the edge without paid Advanced Certificate Manager.)*
2. At GoDaddy (stays registrar): replace the domain's nameservers with the
   two Cloudflare assigns; wait until the Cloudflare zone shows **Active**.
3. `cloudflared tunnel login` (authorize the `agenticthings.com` zone) &&
   `cloudflared tunnel create a2a-lab`
4. Route DNS per hostname in deploy/tunnel/config.yml:
   `cloudflared tunnel route dns a2a-lab bridge-lab.agenticthings.com` (etc.)
5. Run: `cloudflared tunnel --config deploy/tunnel/config.yml run a2a-lab`
6. **Verify:** `curl https://bridge-lab.agenticthings.com/healthz`

## 4. Path A end-to-end (M6)

1. Local stack up: `scripts/run_local.sh`; tunnel up (above).
2. Agent Builder preview: ask the agent a research question → reply must
   contain Claude-generated text; watch the hops land in the console
   (http://localhost:8200).
3. **Measure the real action timeout** — done 2026-07-25, rerun with
   `uv run python scripts/probe_action_timeout.py` (it owns :8100 while it
   runs: stops the stack's bridge, runs its own with `A2ALAB_DELAY_S` set per
   probe, and restores a clean bridge at the end). Result: ~85–90s, not the
   ~60s long assumed — plan/03-results.md. Rerun it after any Salesforce
   platform update; this is a vendor-side number that can move under you.
4. Switch protocol without touching Salesforce: pass `claude-mcp` (or
   `claude-a2a`) as the action's optional Target input in Agent Builder —
   or leave the Apex default and repoint the `claude-rest` entry in
   `config/targets.yaml`. Confirm the console shows JSON-RPC envelopes on
   the bridge→claude hop.

## 5. Bedrock AgentCore (M8)

1. `docker build -f deploy/agentcore/Dockerfile -t a2alab-claude .` and
   smoke-test locally (image bundles Node for the SDK CLI).
2. `agentcore` CLI: three runtime deployments of the same image with
   `PROTOCOL=rest|mcp|a2a` and matching ports (8080/8000/9000); bridge as a
   fourth (HTTP). Inbound auth SigV4 or OAuth JWT; outbound secrets via
   AgentCore Identity.
3. Repoint the `A2ALab_Bridge` Named Credential URL from the tunnel to the
   AgentCore endpoint; rerun the matrix with the tunnel off.

## 6. DynamoDB trace table (D13 / M10 prep)

Create once per AWS account (region = the AgentCore deploy region):

```sh
aws dynamodb create-table \
  --table-name a2alab-traces \
  --attribute-definitions \
      AttributeName=trace_id,AttributeType=S \
      AttributeName=sk,AttributeType=S \
      AttributeName=day,AttributeType=S \
  --key-schema AttributeName=trace_id,KeyType=HASH AttributeName=sk,KeyType=RANGE \
  --global-secondary-indexes 'IndexName=day-index,KeySchema=[{AttributeName=day,KeyType=HASH},{AttributeName=sk,KeyType=RANGE}],Projection={ProjectionType=ALL}' \
  --billing-mode PAY_PER_REQUEST
aws dynamodb update-time-to-live --table-name a2alab-traces \
  --time-to-live-specification Enabled=true,AttributeName=expires_at
```

Enable in any lab process: `uv sync --extra aws`, then
`A2ALAB_TRACE_SINK=jsonl,dynamodb` (tee: console keeps reading files while
the table fills) or `dynamodb` alone in containers. Credentials via the
standard boto3 chain (task role on AWS, `AWS_PROFILE` locally).

M10 (later phase): point Data 360's zero-copy AWS DynamoDB connector at this
table for TableauNext reporting — see plan/00-decisions.md §M10.

## 7. Async account-brief pattern (D16)

One-time setup (after §1 and §2 are done):

1. **Deploy the Salesforce metadata** — custom object `A2ALab_Account_Brief__c`
   (+ fields), `CustomNotificationType` `A2ALab_Brief_Alert`, and the updated
   `A2ALab_Agent_Actions` permission set ship in `salesforce/force-app`.
   Note: the REST delivery runs as the External Client App's run-as user —
   verify with `/services/oauth2/userinfo`; if it is not an admin, assign
   `A2ALab_Agent_Actions` to it.
2. **Provision the Anthropic side:**
   ```sh
   uv run python scripts/setup_brief_agent.py     # agent + DAILY scheduled deployment
   ```
   Writes `.a2alab/brief.json`. Tune with `CLAUDE_BRIEF_MODEL`,
   `A2ALAB_BRIEF_ACCOUNTS`, `A2ALAB_BRIEF_CRON`, `A2ALAB_BRIEF_TZ`.
   **Cost:** each firing is a real multi-minute research session. Pause:
   `Anthropic().beta.deployments.pause('<deployment_id>')`.
3. **Keep the watcher running** — `scripts/run_local.sh` starts
   `python -m briefs --watch` automatically when `.a2alab/brief.json` and
   `SF_*` exist. Cron-fired sessions idle at the `save_account_brief` tool
   until the watcher services them, so runs fired while the host was down
   complete on the next poll.
4. **Verify:**
   ```sh
   uv run python -m briefs --run-now "Omega, Inc."   # fires the job immediately
   ```
   Expect (a) web-research hops in the console trace, (b) a new
   A2ALab Account Brief record on the account (long-text Brief__c), (c) a
   completed Task on the Account, (d) the in-app bell alert (recipients:
   `SF_ALERT_USERNAME` or active System Administrators).
5. **Downstream (M10):** index `Brief__c` for vector search in Data 360 so
   the Agentforce agent grounds account answers / sales plays in the
   latest brief.

## 8. Hosted obs store + analyst (D23 / M11.5)

Provisioned 2026-07-17 in the lab-account account (D21), us-east-1:
- **Aurora Postgres Serverless v2** `a2alab-obs` (engine 16.13, min ACU 0 —
  scale-to-zero; Data API enabled; publicly accessible instance
  `a2alab-obs-1`; SG `a2alab-aurora-sg` allowlists the lab host only).
  Database `a2alab`, schema `lab`: trace_events, obs_sessions, obs_events,
  obs_harvest, obs_briefs (all jsonb payloads).
- **Roles/secrets** (Secrets Manager): master (RDS-managed),
  `a2alab/obs/writer` (lab_writer), `a2alab/obs/reader` (lab_reader —
  default_transaction_read_only, 15s statement_timeout). The Data API
  secret ARN *is* the role selection; 5432 stays closed to AWS compute.
- **Lambdas** (arm64, py3.12, role `a2alab-obs-lambda`):
  `a2alab-obs-mcp` (obs_mcp/lambda_entry.handler — the MCP server; bearer
  token in env + `.a2alab/obs_mcp.json`) and `a2alab-obs-harvest`
  (observability/lambda_handlers.handler; creds from `a2alab/obs/harvest`
  secret; EventBridge schedule `a2alab-obs-harvest-6h`).
- **Public MCP endpoint = API Gateway HTTP API** `a2alab-obs-mcp`
  (`https://<api-id>.execute-api.us-east-1.amazonaws.com`), invoking the
  Lambda via integration-credentials role `a2alab-obs-apigw`. NOT a Lambda
  Function URL: the org SCP explicitly denies `lambda:AddPermission`, so a
  public (auth NONE) Function URL can never be granted invoke access — the
  symptom is an AWS-layer 403, surfacing on the Anthropic side as
  `mcp_connection_failed_error: initialize failed: access forbidden`.
  API GW's 2.0 payload format matches the Function URL event shape, so the
  handler is unchanged.
- Rebuild + update zips: `deploy/obs/build_zips.sh`, then
  `aws lambda update-function-code --function-name <fn> --zip-file
  fileb://deploy/obs/dist/<fn>.zip`.

**Finish once (public exposure needs a human):**
1. `AWS_PROFILE=lab-account AWS_REGION=us-east-1 deploy/obs/expose_mcp.sh`
   — creates the Function URL, saves it to `.a2alab/obs_mcp.json`.
2. `uv run python scripts/setup_obs_analyst.py --recreate --run` — vault +
   static_bearer credential, analyst agent (mcp_toolset → obs-store),
   nightly deployment created **paused**, then one manual smoke run.

**Ops:** `scripts/obs_analysis.py run|status|latest|pause|resume`; console
Observability section has Analyze + an Analysis brief tab. Backfill/refresh
from local sqlite: `scripts/pg_backfill.py` (writer secret ARN in env).

**Gotcha — MCP tools evaluate as `ask` by default here.** Without an
explicit `default_config: {permission_policy: {type: "always_allow"}}` on
the `mcp_toolset`, every MCP call idles the session awaiting a
`user.tool_confirmation` — which deadlocks unattended deployment runs (no
client is connected; that's the point of D23). setup_obs_analyst.py sets
it explicitly; symptom if it regresses: session idle `requires_action` on
`agent.mcp_tool_use` events, `evaluated_permission: "ask"`.

**Data 360 (M10):** the Aurora Postgres zero-copy connector replaces the
DynamoDB one — needs the cluster endpoint reachable from Salesforce IP
ranges (extend the SG), TLS, and a `lab_reader`-style user scoped to the
`lab` schema. Set up in Data 360 UI; not automatable here.

## 9. Saved audit workflows (.claude/workflows/)

Registered multi-agent sweeps — invoke from a Claude Code session by name
(the `ultracode` keyword or an explicit "run the <name> workflow" opts in;
they fan out many subagents, so they cost real tokens):

- **matrix-honesty-sweep** — one agent per cell/ledger claim in
  plan/02-matrix.md, cross-checked against config/targets.yaml,
  plan/03-results.md, and src/; claimed discrepancies pass an adversarial
  refutation stage before being reported.
- **insights-audit** — one agent per config/insights.yaml entry: measured
  numbers must trace to plan/03-results.md or a dated ADR, observed claims
  to plan/*.md, refs to docs that discuss the topic; problems are
  adversarially verified.

Both are read-only (report, don't edit) and best run before demos or
publishing. Headless subagents can't do interactive auth, so keep deploys
and org operations out of workflow scripts.

## 10. Lab Guide (D35)

Runs with the stack (`scripts/run_local.sh`): REST :8031, MCP :8032, A2A
:8033; console chat via the 🧭 header button (needs `ANTHROPIC_API_KEY`).
Model: `GUIDE_MODEL` (falls back to `CLAUDE_AGENT_MODEL`, then Haiku).

**Claude Desktop / any MCP client** — streamable-http with the lab token:

```json
{
  "mcpServers": {
    "a2a-lab-guide": {
      "command": "npx",
      "args": ["mcp-remote", "http://localhost:8032/mcp",
               "--header", "X-Lab-Token: <A2ALAB_TOKEN>"]
    }
  }
}
```

Two tool shapes on the one server, deliberately (the meta exhibit):
`ask` runs the whole guide loop lab-side (one call, grounded answer);
`get_decision` / `read_doc` / `list_recent_runs` / `get_trace` /
`list_briefs` / `read_brief` hand the raw lab data to the CLIENT's model.
Same question both ways = whose-model-reasons comparison, live.

Public cutover: publish :8032 through the cloudflared tunnel (D20) like
the other lab servers; x-lab-token stays the app auth.

**Prompt caching — the credit math.** The guide's grounding (persona +
README + plan/01 + plan/02 + plan/08 + the ADR index, ~57KB ≈ ~15k
tokens) is ONE system block marked `cache_control: {type: ephemeral}`.
The Anthropic API caches the request prefix up to that breakpoint (tool
definitions + that block). First turn in any 5-minute window WRITES the
cache at 1.25× the base input token price; every turn after — follow-up
questions, other visitors, the next suggested prompt — READS it at 0.1×.
So a multi-turn chat pays the ~15k-token grounding bill roughly once,
then ~1.5k-token-equivalent per turn for the same grounding: a ~90%
input-cost cut on exactly the part of the prompt that never changes.
What changes per turn (the console view-context block, the chat history,
the question) sits deliberately AFTER the breakpoint so it never busts
the cache. The tool results the model reads mid-answer (get_decision,
get_trace, …) are per-turn messages — also outside the cache, also only
as big as the question needs (that is the corpus split's other half:
stuff what every question needs, tool the long tail).

## 11. Per-caller External Client Apps (D37 / F6)

Salesforce login history attributes a client-credentials call to the
**app**, not the code path — so one shared ECA makes every lab caller look
like one integration user in the org's own audit trail. F6 gives each
hosted seam its own app. **What is and isn't in the repo:**
`ExtlClntAppGlobalOauthSettings` carries the consumer key and is never
committed — this repo is public; retrieve it when you need it. The
`ExtlClntAppOauthSettings` files (scopes only, no secret) ARE tracked under
`salesforce/force-app/main/default/extlClntAppOauthSettings/`, because the
per-caller scope split is the point of F3/F6 and keeping it in git is what
makes drift from the org visible. This is the recipe for the rest.

**The apps** (created 2026-07-24 in `a2alab-prod`), and the scope split
that makes each one least-privilege — which is where F3's scope diet
actually landed:

| ECA | Scopes | Caller |
|---|---|---|
| `a2a_lab_claude` | `Chatbot, SFApiPlatform` | Claude AgentCore runtime's `ask_agentforce` |
| `a2a_lab_openai` | `Chatbot, SFApiPlatform` | OpenAI AgentCore runtime's `ask_agentforce` |
| `a2a_lab_shim` | `Chatbot, SFApiPlatform` | hosted A2A shim Lambda (Foundry/ADK inbound) |
| `a2a_lab_obs` | `Api` | M11 harvest — the only caller that queries Data Cloud DMOs |
| `a2a_lab_app` | `Api, Chatbot, SFApiPlatform` | local development (unchanged) |

Agent callers reach `api.salesforce.com/einstein/ai-agent/v1` and need no
`Api` scope at all; only the harvest does, because it reads the DMOs
through `/services/data/vXX/query`. Separating callers is what let the
grant shrink — with one shared app the union of needs IS the grant.

**To add another** (four files per app, under `salesforce/force-app/main/default/`):

1. `externalClientApps/<name>.eca-meta.xml` — `contactEmail`,
   `description`, `distributionState Local`, `isProtected false`, `label`.
   Omit `orgScopedExternalApp`; the org generates it.
2. `extlClntAppGlobalOauthSets/<name>_glbloauth.ecaGlblOauth-meta.xml` —
   `callbackUrl`, `isClientCredentialsFlowEnabled true`, `isPkceRequired
   true`, and **`isNamedUserJwtEnabled true`**. That last one is not
   optional and is the single thing that cost a day: the vendor guide says
   to enable Client Credentials Flow *and JWT-based access tokens*, and
   without the JWT flag the app authenticates perfectly, appears in login
   history, and every Agent API call returns 404 with no hint. Everything
   else false. **Omit `consumerKey`** — it is generated on first deploy and
   cannot be set from metadata.
3. `extlClntAppOauthSettings/<name>_oauth.ecaOauth-meta.xml` —
   `commaSeparatedOauthScopes` (least privilege for that caller).
4. `extlClntAppOauthPolicies/<name>_oauthPlcy.ecaOauthPlcy-meta.xml` —
   `clientCredentialsFlowUser` (the run-as user),
   `commaSeparatedProfile`, `permittedUsersPolicyType
   AdminApprovedPreAuthorized`, `ipRelaxationPolicyType Enforce`.

Deploy all four directories together (no Apex, so no test run), then
`rm -rf` them locally — do not commit. Retrieve the generated consumer key
with `sf project retrieve start -m ExtlClntAppGlobalOauthSettings:<name>_glbloauth`.
The consumer **secret** cannot be read through the Metadata API at all:
Setup → App Manager → the app → View → Manage Consumer Details.

**Wiring**: no code change. Put the pair in `.env` as
`SF_CLIENT_ID_<SEAM>` / `SF_CLIENT_SECRET_<SEAM>` (`CLAUDE`, `OPENAI`,
`SHIM`) and redeploy that seam — `deploy/agentcore/deploy.sh` and
`deploy/shim/deploy_shim.sh` ship the per-seam pair into that seam's own
Secrets Manager secret (F1), falling back to the shared app when unset.
`SF_CLIENT_ID_OBS`/`SF_CLIENT_SECRET_OBS` needs no deploy at all — the
harvest source reads it directly, so local harvests attribute correctly
the moment it is set. (The hosted harvest Lambda's secret predates F1 and
has no script path: edit it in the console or leave it shared.)

**Gate every change with `uv run python scripts/identity_preflight.py`.**
It takes each configured identity and exercises the capability it exists for
— agent callers open an Agent API session, the harvest runs a Data Cloud
query — and exits non-zero if any cannot. This is not ceremony: on
2026-07-24 the split shipped with the apps unlinked from the agents, and
every OTHER signal said it worked (deploys succeeded, tokens minted, login
history showed per-caller attribution, three scenarios returned real CRM
content because the containers still held the old credentials). An identity
is not verified by authenticating; it is verified by doing its job.

**The 404 that cost a day, and its real cause (resolved 2026-07-25).** New
per-caller apps minted tokens fine and every Agent API call 404'd. Ruled out
by test, in this order: scopes (adding `api` changed nothing), OAuth policies
(identical to the working app in every auth-relevant field), security
settings (identical), the ECA definition (both org-scoped). No metadata type
links an app to an agent, and — per the vendor guide — **no such link
exists**; two invented UI paths wasted the org admin's time before anyone
read the documentation. The cause was `isNamedUserJwtEnabled: false` on the
new apps. Bisected: enabling it alone, with least-privilege scopes
untouched, turned the shim green. Read the vendor setup guide FIRST. Each app must also be **linked to each agent** it will call — Setup
→ Agentforce Agents → the agent → Connections → add the connected app.
Until then the app mints tokens happily and every Agent API call 404s.

**Verified 2026-07-24**, all three seams live under their own apps, then
`SELECT Application, COUNT(Id) FROM LoginHistory WHERE LoginTime = TODAY
GROUP BY Application`:

```
a2a_lab_app       6     (local dev + pre-split calls)
a2a_lab_claude    2
a2a_lab_openai    2
a2a_lab_obs       1
a2a_lab_shim      1
```

Before the split every one of those rows read `a2a_lab_app`. That table is
E3's raw data — and the cheapest demo of why per-caller identity matters:
the org can finally answer "which agent asked?" without the lab telling it.
