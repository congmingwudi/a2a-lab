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
   Verify with the MCP org tools (list orgs / describe default org) or
   `sf org display`.
2. **External Client App** (Setup → App Manager → New External Client App):
   - OAuth: client-credentials flow enabled; scopes `api`, `chatbot_api`,
     `sfap_api`, `refresh_token`.
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
3. **Measure the real action timeout**: set `A2ALAB_DELAY_S` on the bridge
   host (or add a sleep in the target adapter) at 10/30/60/90s and record
   outcomes in plan/03-results.md.
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
