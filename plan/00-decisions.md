# Decision log (ADRs)

Running log — newest at the bottom. Each entry: date, decision, why, status.

## 2026-07-09 — D1: Salesforce environment = user's production org
Lab metadata is strictly namespaced `A2ALab*`; a dedicated least-privilege
integration run-as user holds the Agentforce permission set. Consequences:
Apex deploys require test runs with ≥75% coverage (the invocable ships with
`A2ALabInvokeRemoteAgentTest`), and Einstein requests draw on real licensing
(matrix runs stay small; sessions are reused). Fallback: free Agentforce DE org.

## 2026-07-09 — D2: Language = Python everywhere outside Salesforce
Apex/Flow inside the org; `uv`-managed Python 3.11 project for everything else.

## 2026-07-09 — D3: Phase 1 covers both Agentforce↔Claude directions
Path A (Agentforce→Claude) and Path B (Claude→Agentforce) both land before
the OpenAI pair (Path C).

## 2026-07-09 — D4: OpenAI runtime = Bedrock AgentCore Runtime
Framework-agnostic containers, one protocol mode per deployment (HTTP :8080,
MCP :8000, A2A :9000). Accepted cost: 3–4 deployments per agent.

## 2026-07-09 — D5: Local exposure = cloudflared named tunnel
Enterprise subdomain zone `lab.agenticthings.com`, NS-delegated from GoDaddy
(GoDaddy stays primary DNS for the apex). Ingress in deploy/tunnel/config.yml.

## 2026-07-09 — D6: Demo scenario = research assistant
Agentforce fields the end-user question and delegates open-ended research/
summarization; transcripts make each agent's contribution obvious.

## 2026-07-09 — D7: Wire visibility is a core requirement
Every hop records a TraceEvent with the raw wire payloads; the lab console
(:8200) shows per-hop protocol badges and side-by-side raw request/response
with SSE live tail.

## 2026-07-09 — D8: Bridge for Path A, shims for Path B
Agentforce's GA outbound is REST-only → thin FastAPI bridge keeps the
MCP/A2A comparison alive (cells recorded **via-bridge**). Agentforce has no
GA MCP/A2A inbound → thin proxy shims over the Agent API (cells recorded
**via-shim**). Native SF MCP actions stay **blocked-beta** until access lands.

## 2026-07-09 — D9 (revised): Claude hosting = Anthropic Managed Agents (beta) first
Originally the self-hosted Claude Agent SDK was primary and Managed Agents an
optional late variant. **Revised at user request**: Managed Agents (beta) is
now the *default* Claude backend (`CLAUDE_BACKEND=managed`) — it's a piece the
lab explicitly wants to exercise. The self-hosted SDK backend remains fully
supported (`CLAUDE_BACKEND=sdk`) as (a) the fallback, (b) the AgentCore
containerization path (M8 — Managed Agents can't be self-deployed), and
(c) the latency comparison cell: managed sessions provision a per-session
container, so first-turn latency vs. the warm SDK server is itself a lab
finding that matters for the Agentforce action-timeout budget (~60s, to be
measured). Both backends sit behind the same `AgentAdapter`; nothing else in
the stack knows which one is running.
Path B symmetry: under `managed`, `ask_agentforce` is a CMA **custom tool**
handled host-side by our orchestrator (Salesforce credentials never enter
the sandbox); under `sdk` it's an in-process SDK MCP tool.

## 2026-07-09 — D10: Salesforce build/deploy tooling = Salesforce MCP servers
**At user request**: the Agentforce agent and all supporting `A2ALab*`
metadata are built and deployed using Salesforce's official MCP servers
rather than hand-run `sf` CLI commands. `.mcp.json` at the repo root
registers the DX MCP server (`@salesforce/mcp`, toolsets orgs/metadata/data/
testing/users) so Claude Code drives org auth checks, `salesforce/` metadata
deploys, and Apex test runs through MCP tools. The raw `sf` CLI remains the
documented fallback in plan/04-runbooks.md. Agent build/publish still happens
in Agent Builder where no MCP/CLI surface exists yet.

## 2026-07-09 — D11: Streaming out of scope for v1
Apex callouts are buffered request/response. One A2A SSE demo ships as a
capability comparison only (M2 verify step).
