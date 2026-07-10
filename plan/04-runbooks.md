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
6. **Finish the credential** (Setup → Named Credentials → External
   Credentials → A2ALab Bridge): create a Named Principal
   (`A2ALabPrincipal`), add parameter `BridgeToken` = the `BRIDGE_TOKEN`
   value, grant access to the integration user's permission set.
7. **Attach the action** (Agent Builder): add the Apex action
   `A2ALab: Ask Remote Agent` to the research topic; instruct the agent to
   delegate open-ended research questions to it.

## 3. Cloudflare tunnel + DNS (M6)

1. In the Cloudflare Enterprise account: create subdomain zone
   `lab.agenticthings.com`; at GoDaddy add the two NS records delegating
   `lab` (GoDaddy stays primary for the apex).
2. `cloudflared tunnel login && cloudflared tunnel create a2a-lab`
3. Route DNS per hostname in deploy/tunnel/config.yml:
   `cloudflared tunnel route dns a2a-lab bridge.lab.agenticthings.com` (etc.)
4. Run: `cloudflared tunnel --config deploy/tunnel/config.yml run a2a-lab`
5. **Verify:** `curl https://bridge.lab.agenticthings.com/healthz`

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
