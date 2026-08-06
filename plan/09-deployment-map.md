# Deployment map — where everything runs, and why

**What this file is for.** `plan/01-architecture.md` answers *how the code is
shaped* (the two seams, the protocol mapping, the trace layer).
`plan/00-decisions.md` answers *why each choice was made*, one decision at a
time, in the order they happened. Neither answers the question you actually ask
when you open a cloud console: **what is deployed, where, and why there.**

That question got harder on 2026-07-26, when the Path A bridge moved to ECS
Fargate behind an ALB while every other hosted component stayed on Lambda
behind API Gateway. Two components, same account, opposite hosting decisions —
which is correct, and confusing, unless the reason is written down next to the
picture.

**How to read it.** Ten levels, each a diagram plus what it is and why it is
that way. L0 is the whole estate on one screen. L6 maps repo files to deployed
artifacts. Read down until you have the detail you need, then stop.

- **L0** — the estate: the console drives six platforms across four clouds
- **L0.5** — the same estate as a call graph: who reaches whom
- **L1** — AWS: four hosting shapes, four different reasons
- **L2** — Path A end to end: Salesforce reaches out
- **L3** — Path B end to end: the platforms reach into Salesforce
- **L4** — identity: who authenticates as what, and which way federation runs
- **L5** — observability: five interiors, one store
- **L5.5** — DNS: the four hostnames, and what each one is for
- **L5.7** — scheduled and long-running processes (the async inventory)
- **L6** — code → deployment: which file becomes which running thing

**What this is written from.** The deploy scripts in this repo and the measured
results in `plan/03-results.md` — not generated from live cloud state. Every
number is one the lab recorded, and "Checking reality" at the end gives the
commands that print what is actually deployed right now. Every file named
below links to the source.

---

## L0 — The estate: the console drives six platforms across four clouds

```mermaid
flowchart TB
  subgraph DRV["THE LAB CONSOLE — the driver"]
    direction LR
    C1["Experiments · Protocol calls<br/>Run All, multi-turn, wire traces"]
    C2["Observability · Insights<br/>Architecture · Lab Guide"]
    C3["ECS Fargate · ALB<br/>console-lab · faces-lab"]
  end

  subgraph SF["SALESFORCE — Agentforce"]
    SF1["Agents: one twin per platform<br/>+ Research Assistant + Supply Orchestrator"]
    SF2["In: Agent API · MCP shim · A2A shim"]
    SF3["Out: Apex invocable to the bridge"]
    SF4["Obs: Data Cloud session-tracing DMOs"]
  end

  subgraph ANT["ANTHROPIC — Managed Agents"]
    AN1["Agents: researcher · brief · obs analyst<br/>cost sentinel · fan-out orchestrator"]
    AN2["In/Out: REST · MCP · A2A"]
    AN3["Obs: sessions + per-session events"]
  end

  subgraph GCP["GOOGLE CLOUD — Vertex AI Agent Engine"]
    G1["Agents: researcher · logistics<br/>supply orchestrator (ADK)"]
    G2["In/Out: A2A native (preview)"]
    G3["Obs: Cloud Logging + billing meters"]
  end

  subgraph AZ["MICROSOFT AZURE — AI Foundry"]
    Z1["Agents: researcher · commercial"]
    Z2["In/Out: A2A, Entra-only"]
    Z3["Obs: App Insights gen_ai spans"]
  end

  subgraph AWS["AWS — the lab's OWN infrastructure, and two agents"]
    direction TB
    A1["Agents: Claude + OpenAI + Strands SDK twins<br/>Bedrock AgentCore, IAM data plane"]
    A2["Bridge — Path A's front door<br/>ECS Fargate, 45s budget"]
    A3["Protocol faces ×11<br/>REST · MCP · A2A, one process"]
    A4["Shims + fan-out<br/>Lambda + API Gateway"]
    A5[("Aurora Postgres<br/>traces · obs · briefs · state")]
    A6["Scheduled: harvest 6h · brief watcher<br/>credential expiry"]
  end

  DRV ==>|"runs every experiment"| SF
  DRV ==> ANT
  DRV ==> GCP
  DRV ==> AZ
  DRV ==> AWS

  SF <-->|"consults, both directions"| ANT
  SF <--> GCP
  SF <--> AZ
  SF <--> AWS

  SF4 -.->|"harvested"| A5
  AN3 -.-> A5
  G3 -.-> A5
  Z3 -.-> A5
  A5 -.->|"reads"| DRV
```

**What you are looking at.** The console at the top is not a viewer — it is the
**driver**: every experiment in this lab is fired from it, over the protocol the
experiment names, and every hop's raw wire bytes come back to it. Below it are
the six agent platforms, each showing the three things that actually matter for
interop: **what agents live there**, **which protocols it speaks in and out**,
and **what its own execution logs expose**.

**The two asymmetries worth seeing immediately.** Salesforce sits in the middle
of the platform row because it is the hub every other platform consults, in both
directions — that is the lab's subject. And **AWS is not a peer**: it holds two
agents like the others, but it also holds the lab's own infrastructure — the
bridge Path A depends on, the eleven protocol faces, the shims, the fan-out
server, the Postgres store every observability number comes from, and the
scheduled jobs that keep it current. The dotted lines are that store filling up:
each platform's *interior* record of the runs the console drove, harvested back
into one place.

**Why spread across four clouds at all.** Not for redundancy — because the
subject *is* cross-platform interop. An agent on each vendor's own home turf is
the experiment; running everything as containers in one cloud would test our
containers, not the platforms. The levels below take this apart: L0.5 the same estate
as a call graph, L1 the four AWS hosting shapes and why each, L2 and L3 the two call paths end to end, L4
identity, L5 observability, L5.5 DNS, L5.7 the scheduled processes, L6 which
file becomes which running thing.

## L0.5 — The same estate as a call graph: who reaches whom

L0 answers *what is where*. This answers *what talks to what* — the same
components, drawn by the paths between them rather than by the boundaries around
them. Both are worth having: the first is the one to open with, this is the one
to point at when somebody asks how a request actually gets from Salesforce to a
Google agent.

```mermaid
flowchart TB
  subgraph SF["Salesforce production org"]
    AF["Agentforce agents<br/>one twin per platform"]
    APEX["Apex invocable<br/>A2ALabInvokeRemoteAgent"]
  end

  subgraph AWS["AWS — us-east-1<br/>the lab's runtime account"]
    BRIDGE["Bridge<br/>ECS Fargate + ALB"]
    RUNTIMES["Claude + OpenAI + Strands agents<br/>Bedrock AgentCore"]
    SHIM["Agentforce A2A/MCP shim<br/>Lambda + API Gateway"]
    FANOUT["Fan-out MCP server<br/>Lambda + API Gateway"]
    OBS["Obs harvest + obs MCP<br/>Lambda + Aurora"]
    CONSOLE["Lab console<br/>ECS Fargate, rule on the bridge ALB"]
    FACES["Eleven protocol faces<br/>ECS Fargate, one process, by path"]
    WATCH["Brief watcher<br/>ECS Fargate, no inbound path"]
  end

  subgraph GCP["GCP — us-central1"]
    ADK["ADK agents on<br/>Vertex AI Agent Engine"]
  end

  subgraph AZ["Azure — eastus"]
    FOUNDRY["Foundry prompt agents<br/>Agent Service"]
  end

  subgraph ANT["Anthropic platform"]
    CMA["Managed Agents<br/>agent + scheduled deployment"]
  end

  subgraph LAP["The laptop — dev only"]
    LOCAL["run_local.sh stack<br/>dev convenience only"]
    TUNNEL["cloudflared tunnel"]
  end

  APEX -->|"HTTPS, Named Credential"| BRIDGE
  BRIDGE --> RUNTIMES
  BRIDGE --> ADK
  BRIDGE --> FOUNDRY
  BRIDGE --> AF
  RUNTIMES -->|"consult"| SHIM
  ADK -->|"consult"| SHIM
  FOUNDRY -->|"consult"| SHIM
  CMA -->|"MCP tools"| FANOUT
  SHIM --> AF
  FANOUT --> ADK
  FANOUT --> FOUNDRY
  FANOUT --> RUNTIMES
  OBS -.->|"pull logs"| AF
  OBS -.-> CMA
  OBS -.-> ADK
  OBS -.-> FOUNDRY
  OBS -.->|"OpenAI + Strands<br/>(AWS/Bedrock meters)"| RUNTIMES
  TUNNEL -.-> LOCAL
```

**What you are looking at.** Five places hold something the lab depends on, and
one of them — the laptop — no longer holds anything on a live call path.

**Read it by following one arrow.** Path A starts at the Apex invocable and ends
wherever `config/targets.yaml` points the bridge that day, which is the whole
argument for having a bridge: switching a platform or a protocol is a config
edit, not a Salesforce redeploy. Path B is the return direction — each platform's
agent consulting Agentforce through the hosted shim — and the fan-out server is
the one-to-many case, where a single model turn reaches three clouds. There are
now **two** one-to-many paths: the fan-out MCP server for the Managed Agents
orchestrator, and — for the Agentforce orchestrator (D61) — the bridge's own
`fanout:` route, which runs the same three legs off-platform when an Apex callout
targets `fanout:supplier-disruption` (the delegated topology). Both reuse
`orchestration.dispatch()`; the bridge route exists because Agentforce's only GA
outbound is a serial Apex callout that cannot fan out on-platform.

**Why it is spread like this.** Not for redundancy; deliberately, because the
lab's subject *is* cross-platform interop. An agent on each major platform, each
in its vendor's own native home, is the experiment. The alternative — running
everything as containers in one cloud — would test our containers, not the
platforms.

**The asymmetry the arrows make obvious:** AWS is not just a peer. Almost every
line passes through something the lab itself hosts there — the bridge, the shim,
the fan-out server, the observability store — while the other four clouds hold
agents and nothing else.

**The dotted lines are the harvest**, not a call path: the observability job
pulling each platform's own record of what happened, on a schedule, in the
opposite direction from the traffic.

---

## L1 — AWS: four hosting shapes, four different reasons

```mermaid
flowchart LR
  subgraph GW["API Gateway HTTP API — 30s hard ceiling"]
    SHIM["a2alab-af-shim<br/>Lambda · work measures 10-19s"]
    FAN["a2alab-fanout-mcp<br/>Lambda · legs capped 25s"]
    OMCP["a2alab-obs-mcp<br/>Lambda · SQL reads"]
  end

  subgraph ALB["Application Load Balancer — idle timeout 120s"]
    BR["a2alab-bridge<br/>ECS Fargate · ARM64 · 512/1024<br/>client timeout 45s"]
  end

  subgraph AC["Bedrock AgentCore Runtime — no public HTTP"]
    ACC["a2alab_claude<br/>claude-agent-sdk container"]
    ACO["a2alab_openai<br/>openai-agents container"]
    ACS["a2alab_strands<br/>strands-sdk container (D66)"]
  end

  subgraph SCHED["EventBridge — no front door at all"]
    HARV["a2alab-obs-harvest<br/>Lambda · every 6h"]
  end

  STORE[("Aurora Serverless v2<br/>obs store, Data API")]
  SEC[("Secrets Manager<br/>one secret per seam")]

  SHIM --> SEC
  FAN --> SEC
  BR --> SEC
  ACC --> SEC
  ACO --> SEC
  ACS --> SEC
  HARV --> STORE
  OMCP --> STORE
```

**What you are looking at.** Four hosting shapes in one account, and the shape
is chosen by **what the component's work costs in seconds**, not by preference.

| Shape | Who | Why this one |
|---|---|---|
| Lambda + API Gateway HTTP API | shim, fan-out MCP, obs MCP | Cheapest thing with a public URL. Its integration timeout maxes at **30s and is not adjustable** — fine, because each of these finishes well inside it. |
| ECS Fargate + ALB | **the bridge** | Path A's budget is **45s** (action ~85-90s → Apex 110s → bridge 45s). An HTTP API would have silently cut 15s off the lab's sync research depth. An ALB's `idle_timeout` is a settable attribute — set to **120s** on every deploy so a console edit cannot reintroduce the ceiling. |
| Bedrock AgentCore Runtime | Claude + OpenAI + Strands self-hosted agents | The point of D26: an agent runtime with an **IAM data plane and no public HTTP endpoint**. Callers use `invoke_agent_runtime` with SigV4; there is no URL to leak. |
| EventBridge → Lambda | obs harvest | Nothing calls it. It wakes up, pulls each platform's logs, writes Aurora, sleeps. |

**The rule to take away:** *host to the measured ceiling, not the observed
average.* Six of six pathways through the hosted bridge measured **1.2–12.6s**
— every one would have fit an API Gateway. The ALB is there for the delegating
turns the 45s budget exists to cover, not for the median call.

**The trap this shape hides.** Federation is not portable across AWS compute
types. The AWS→GCP federation built for the fan-out **Lambda** failed on
**Fargate**: google-auth's default AWS supplier looks at `AWS_ACCESS_KEY_ID`
env vars, then EC2 IMDS. Lambda sets the env vars; Fargate sets neither, because
an ECS task's credentials come from `$AWS_CONTAINER_CREDENTIALS_RELATIVE_URI`.
Same code, same role, same pool, different compute shape, stops working. Fixed
by delegating to **botocore**, which resolves all of them.

---

## L2 — Path A end to end: Salesforce reaches out

```mermaid
flowchart LR
  USER(["User in Agentforce"]) --> AFA["Agentforce agent<br/>A2ALab Research Assistant"]
  AFA -->|"action"| APEX["Apex A2ALabInvokeRemoteAgent<br/>callout timeout 110s"]
  APEX -->|"Named Credential<br/>A2ALab_Bridge"| DNS["bridge-lab.agenticthings.com<br/>Cloudflare proxied, Full strict"]
  DNS --> ALBX["ALB a2alab-bridge<br/>idle timeout 120s"]
  ALBX --> BRX["Fargate task :8100<br/>bridge client timeout 45s"]
  BRX -->|"per config/targets.yaml"| T1["claude-rest / openai-rest<br/>AgentCore, SigV4"]
  BRX --> T2["google-adk-a2a<br/>Agent Engine, federated"]
  BRX --> T3["foundry-*-a2a<br/>Entra service principal"]
  BRX --> T4["agentforce-rest<br/>Agent API"]
```

**What you are looking at.** The one path that starts *inside* Salesforce.
Agentforce's outbound is REST-only, so Apex calls one endpoint — the bridge —
and the bridge fans out to whatever `config/targets.yaml` says. **Switching the
protocol or the target platform needs no Salesforce redeploy.** That indirection
is the whole reason the bridge exists.

**The budget chain is the design.** Every number is measured, not assumed:
Agentforce action ~85–90s (measured 2026-07-25, not the ~60s long assumed) →
Apex callout 110s → bridge client 45s → agent-side caps. Each layer must be
comfortably inside the one above it, and the ALB's 120s idle timeout sits above
all of it.

**What is still manual here, deliberately:** TLS and DNS. The cutover moved
`bridge-lab.agenticthings.com` from the Cloudflare tunnel to the ALB. It is a
Salesforce-visible hostname, so it was verified on the ALB's own hostname over
HTTP first — a DNS change against a known-good target rather than a deploy and
a hope.

---

## L3 — Path B end to end: the platforms reach into Salesforce

```mermaid
flowchart LR
  subgraph HOSTS["Each agent, in its own vendor's home"]
    C["Claude agent<br/>Managed Agents or AgentCore"]
    O["OpenAI agent<br/>AgentCore"]
    A["ADK agent<br/>Agent Engine"]
    F["Foundry agent<br/>Agent Service"]
  end
  C -->|"ask_agentforce tool"| SHIMX
  O -->|"ask_agentforce tool"| SHIMX
  A -->|"A2A"| SHIMX
  F -->|"A2A 0.3 dialect"| SHIMX
  SHIMX["a2alab-af-shim<br/>Lambda behind API Gateway<br/>+ wiretap + 0.3 to 1.0 compat"]
  SHIMX -->|"Agent API"| AFT["Agentforce twin agent<br/>one per calling platform, D25"]
  AFT --> CRM[("Salesforce data")]
```

**What you are looking at.** The mirror of Path A. Agentforce has no GA
inbound MCP or A2A surface, so the lab supplies one: a shim that speaks A2A and
MCP on the outside and the Agent API on the inside.

**Why the shim is hosted rather than local.** Two platform-native A2A callers
(Foundry, and Agent Engine) must reach a public endpoint, and pointing them at a
laptop through a tunnel would make the experiment about the tunnel. Hosting it
also bought the **wiretap**: the shim records the raw inbound A2A envelope, which
is the only place the lab can see what a vendor's A2A client actually sends.

**Two things the shim absorbs that are worth naming:**
- **Dialect translation.** Foundry speaks A2A **0.3**; Agent Engine requires
  **1.0**. `interop/servers/a2a_compat.py` translates, so neither vendor has to
  be wrong for the other to work.
- **The 29s gateway wall.** The shim is on API Gateway, and Foundry's A2A tool
  does **not** retry — so a cold twin session behind that ceiling fails the tool
  call outright. The lab's answer is a warm-up path rather than a retry, because
  the caller is a platform whose retry behaviour is not ours to change.

**Every calling platform gets its own Agentforce twin** (D25), so each
experiment stays a closed two-platform system rather than four platforms
sharing one agent's session history.

---

## L4 — Identity: who authenticates as what, and which way federation runs

```mermaid
flowchart TB
  HUMAN(["The ONE human login<br/>AWS SSO"])
  HUMAN --> SM[("Secrets Manager<br/>a2alab/runtime/*<br/>a2alab/obs/harvest")]

  subgraph AWSID["AWS workloads — role identity, no stored keys"]
    TR["bridge task role"]
    LR["lambda roles"]
    AR["AgentCore runtime roles"]
  end
  SM --> TR
  SM --> LR
  SM --> AR

  subgraph SFID["Salesforce — one External Client App PER caller"]
    E1["a2a_lab_claude"]
    E2["a2a_lab_openai"]
    E3["a2a_lab_shim"]
    E4["a2a_lab_obs"]
  end
  TR --> E1
  LR --> E3
  SM --> E4

  subgraph FED["Federation — direction matters"]
    G2A["GCP to AWS:<br/>AWS trusts accounts.google.com<br/>ONE role"]
    A2G["AWS to GCP:<br/>pool + provider + mapping<br/>+ condition + impersonation<br/>FIVE objects"]
  end
  AR -.-> A2G
  A2G --> ADKX["Agent Engine"]
  ADKX -.-> G2A
  G2A --> AWSID

  subgraph OPS["Operational agents — not experiments"]
    EXP["expiry_report.py<br/>collector: AWS, Entra, GCP, ACM<br/>in the 6h harvest Lambda (WS14)"]
    CANA["credential analyst<br/>one Claude API call · ad-hoc<br/>judgment, never arithmetic"]
    EXP --> CANA
  end
  SM -.-> EXP

  subgraph PUB["Console — two audiences, one page"]
    ANON["anonymous visitor<br/>titles, notes, screenshots<br/>NO console deep links"]
    OPER["signed-in operator<br/>+ deep links into each<br/>vendor console"]
  end
  SM -.->|"env_sync.py<br/>.env lives here too"| OPER
```

**What you are looking at.** The rule from D39, scoped to the runtime/data
plane: **at runtime, AWS SSO is the only interactive human login the lab's data
plane needs.** The harvest and every hosted component fetch each other platform
credential as a service identity from Secrets Manager — never an `az login`,
never a `gcloud auth`, never a value that exists only in someone's `.env`
(enforced by `observability/credentials.py`). Provisioning is the deliberate
exception: standing up the Foundry agent (`deploy/foundry/provision_foundry.py`)
and the AWS→GCP federation (`deploy/bridge/gcp_federation.sh`,
`deploy/fanout/provision_gcp_federation.py`) require the operator's own
`az login` and `gcloud auth application-default login`, and the ad-hoc credential
analyst's collection needs all three (see "It is ad-hoc, not scheduled" below).

**Why per-caller Salesforce apps.** One shared External Client App held the
*union* of four callers' needs. Splitting it into four apps was not about a
shorter scope list — Salesforce attributes client-credentials calls to the
**app**, so one shared app made every lab caller look like one integration user
in the org's own audit trail. The modelling error showed up as an observability
failure.

**Why the federation box is lopsided.** AWS trusts `accounts.google.com`
natively and needs **one** role. Google needs **five** objects before it will
trust AWS at all. "Keyless federation" costs very different amounts depending on
direction, and Google's side is where the identity is *shaped* rather than
merely accepted.

**The last file that broke the rule, and how it was closed.** D39 says every
credential is a service identity fetched with the one human login — but `.env`
itself was a plaintext file holding every platform's keys, existing on exactly
one laptop. Losing it would not lose the code; it would lose the ability to run
or deploy it. It is now stored in Secrets Manager alongside the others and moved
with `scripts/env_sync.py pull|push|diff`, so onboarding is **clone →
`aws sso login` → `env_sync.py pull`**. `.env.example` stays the checked-in
contract describing what each key means; only the values live in the secret, and
only for identities the secret's IAM policy already admits.

Two related rules make that safe rather than merely convenient:

- **No environment identifier is hardcoded in the repo** — no account ids, no
  project ids, no SSO profile name, and no `${VAR:-default}` fallbacks, which
  are hardcodes that only reveal themselves on someone else's machine. Shell
  reads `${VAR:?set VAR in .env}` so a missing value fails at the top instead of
  quietly targeting the wrong cloud. A test fails the build if an AWS account id
  or profile name reappears in a tracked file.
- **Every AWS deploy proves its target account first.** `deploy/aws_preflight.sh`
  resolves the session with `sts get-caller-identity` and refuses to continue
  unless it matches `A2ALAB_AWS_ACCOUNT_ID`. Removing the account label from the
  repo makes a wrong-account deploy *easier* to attempt, so the guard went in
  with the scrub, not after it.
- **The console's public surface names no accounts either.** `/api/scenarios`
  and `/api/targets` are unauthenticated — the landing exhibit renders from
  them — and they carried the vendor console deep links, which is where the
  Salesforce org's my-domain, the GCP project id and an Azure tenant id all
  live. An anonymous caller now gets the component titles, notes and
  screenshots; the links resolve only for a signed-in caller, and the UI says
  *"sign in to open"* rather than *"not yet available"*, which would be a claim
  about the lab instead of a statement about the viewer.

### Credential health: a collector, and an agent above it

Every credential in the estate expires, and the dangerous ones expire quietly.
`scripts/expiry_report.py` queries each provider for the date it already knows —
the CloudWatch metrics key from IAM, the Entra app secret, GCP service-account
keys, and the Cloudflare origin certificate imported into ACM for Path A's Full
(strict) hop. It runs **inside the 6-hourly harvest Lambda** as of WS14,
authenticating as service identities rather than anyone's login, and publishes to
`lab.lab_state` for the console to read. The operator's own SSO session is
deliberately **not** among the credentials it reports: once the lab was fully
hosted that became a deploy-time credential on one machine, and listing it beside
credentials whose expiry would take the LAB down made a personal login look like
production risk. Anything no API will answer for (Salesforce
ECA secrets, the tunnel credential) is **declared** in
`config/credentials.yaml` and labelled as such, so an intention never reads as a
measurement.

Above it sits the **credential analyst** — one Claude API call, and the lab's
first agent doing operations rather than experiments. It is handed the
measured report and asked what a threshold cannot answer: what to rotate first,
what each failure would look like, and what the report does not cover. On its
first run it found something neither the script nor the operator had noticed —
the GCP key and the Entra secret expire within days of each other next July, so
that is one coordinated rotation rather than two events — and flagged that the
no-expiry GCP key is the only credential that will never prompt anyone.

Four design choices, each deliberate:

- **The collector is deterministic and the agent never computes a date.** D22's
  rule (ETL below, interpretation above) applied to credentials. The agent's
  instructions forbid citing any number not in the report.
- **It is a plain API call, not a Managed Agent — and it was built both ways.**
  The first version was a Managed Agent and it worked; then the shape was
  checked against what that abstraction is for. Hosted tool execution: no tools.
  Scheduled deployments: cannot be scheduled, see the next bullet. Session
  state: one shot. Managed sandbox: unused. What it cost was real —
  `agents.create` + `sessions.create` + `events.send` + idle detection, a setup
  step, a state file, an agent object to version, and a bug that existed only
  because of the extra surface. A single `messages.create` is the same model,
  the same prompt, the same data, in one round trip. D44 records the three tests
  it failed and names the cost sentinel as the counter-example that passes them;
  the moment collection moves server-side, this should go back.
- **It is ad-hoc, not scheduled** — which is a limitation, stated. Collection
  needs the operator's own AWS SSO session, `az` login and `gcloud` ADC, so a
  cron firing in Anthropic's sandbox would report "cannot query" every night and
  train everyone to ignore it. Nothing runs unless a person starts it.
- **It is fed, not tooled.** The obs analyst reads its store through a hosted
  MCP server; this one does not, because putting "which secrets expire when"
  behind an internet-reachable endpoint to save a copy-paste is a poor trade.
- **Read-only.** An agent that could rotate credentials across four clouds could
  lock the lab out of itself.

Operators reach it from the console's Architecture page → **Credentials**, which
shows the measured table and an **Analyze** button.

**Two things that generalise past this lab.** First: **a repo scrub is not a
boundary — the API is.** The identifiers were removed from the source and were
still being served by a public endpoint, because the endpoint assembled them at
runtime from `.env`. Anything derived from configuration has to be checked at
the edge it is served from, not only where it is written.

Second: **the same identifier can be necessary in one place and decoration in
another.** The Foundry console URL carried `?tid=<tenant-id>`, a sign-in hint
the browser does not need when the session is already in that tenant — so it
was stripped. `AZURE_TENANT_ID` remains in `.env`, because the Entra service
principal genuinely authenticates with it. Removing an identifier is only cheap
when you can tell those two cases apart; the test is whether anything stops
working without it.

---

## L5 — Observability: six interiors, one store

```mermaid
flowchart LR
  subgraph SRC["Each platform's own execution log"]
    S1["Salesforce STDM"]
    S2["Anthropic sessions"]
    S3["OpenAI"]
    S4["Cloud Logging"]
    S5["App Insights"]
    S8["AWS/Bedrock meters<br/>+ AgentCore access log<br/>(strands, WS5/D67)"]
  end
  subgraph CODE["Coding agents — NOT a platform"]
    S6["Claude Code + Codex + Cursor<br/>METRICS to CloudWatch<br/>(coding; Cursor via cursorscope hooks)"]
    S7["Claude Code OTLP LOGS<br/>to CloudWatch log group<br/>(coding-logs, WS16)"]
  end
  HARV2["a2alab-obs-harvest<br/>Lambda, every 6h<br/>+ scripts/obs_harvest.py"]
  S1 --> HARV2
  S2 --> HARV2
  S3 --> HARV2
  S4 --> HARV2
  S5 --> HARV2
  S8 --> HARV2
  S6 --> HARV2
  S7 --> HARV2
  HARV2 --> PG[("Aurora — hosted store")]
  HARV2 --> LITE[("traces/lab.db — local")]
  PG --> CONSOLE["Console: Observability<br/>+ Coding Agents Telemetry"]
  LITE --> CONSOLE
  PG --> ANALYST["Hosted analyst agent<br/>nightly, reads via a2alab-obs-mcp"]
  PG --> SENT["Cost sentinel (WS12)<br/>daily, same MCP server<br/>brief kind=cost"]
```

**What you are looking at.** Harvest-and-cache (D18): the console **never**
proxies a platform API live. Every interior view is pulled into one store first,
so the console is fast, offline-capable, and shows the same thing twice.

**The one deliberate exclusion.** `coding` and `coding-logs` share the harvest
seam and the store but are **not** columns in the coverage panel. Every column
there is an agent platform whose interior the lab harvests; Claude Code, Codex
and Cursor are the tools that *built* the lab (Cursor added 2026-07-31, D64 — a
third `@resource.tool` inside `coding`, not a new source). They get their own console section instead —
**Coding Agents Telemetry**, under the **DevOps** category (WS17/D60), with two
peer tabs (D57): **Cost** reads the `coding` metrics (WS9) and **Behaviour**
reads the `coding-logs` log signal (WS16/D59). The two are distinct obs-store
platforms so they harvest and read independently: `coding` is PromQL over the
CloudWatch metrics store, `coding-logs` is SigV4 `FilterLogEvents` over a
CloudWatch **log group**. The behavioural signal is metadata only — content flags
off end to end, so no prompt, file or tool-argument text is ever emitted.

**Two agents read this store, and only one of them earns the shape.** The
observability analyst runs on demand; the **cost sentinel** (WS12/D44) runs
**daily** (`0 7 * * *`, America/New_York — moved from weekly and resumed on
2026-07-30, D44 addendum) over the coding-agent telemetry and explains what
moved. Both reach the store through the same `a2alab-obs-mcp` server and write to
the same `lab.obs_briefs` table, separated by a `kind` column — one store, one
reader, one migration. Both **ship paused** because a scheduled firing bills a
real session; the sentinel is now resumed on purpose so the console shows a fresh
brief each morning, and its Pause/Resume/Run controls are in the console.

The sentinel is the counter-example to the credential analyst above. That one
was demoted to a plain API call because it had no tools, no possible schedule
and no state. The sentinel has all three — the delta is a SQL question, the
collection already runs in `a2alab-obs-harvest` rather than on a laptop, and
day-over-day needs history. Same lab, same rule, opposite answer.

**The obs rule that keeps biting:** registering a source in
`scripts/obs_harvest.py` is *half* the job. The hosted Lambda
(`observability/lambda_handlers.py`) and its bundle (`deploy/obs/build_zips.sh`)
need the same entry and the client library — or the platform reads "blocked"
hosted forever while local looks fine. **Proven again on 2026-07-27** (D46): the
`coding` source existed locally for two days while Aurora held zero rows,
because the deployed bundle predated it *and* the execution role had no PromQL
grant — and an unauthorized read surfaces as the friendly "no coding metrics
yet, switch the exporters on" status, so empty and forbidden look identical.

**The store now holds two things that are not telemetry**, both because a hosted
console cannot read a laptop's filesystem (WS13):

- `lab.lab_state` — key/value, for operator artifacts the console *renders* but
  cannot *produce*. The credential expiry snapshot lives here: collecting it
  needs the operator's own AWS/az/gcloud sessions, so it can never run in the
  container. `scripts/expiry_report.py --write` publishes to both the file and
  the store; the console reads the store first.
- `lab.fanout_tasks` — the durable half of A2A fire-then-poll (WS11/D47). On a
  function runtime, in-memory task state is per-instance and background work is
  frozen between invocations, so a submit/check pair that keeps state in the
  process is broken in two separate ways.

**Schema changes go through `scripts/pg_migrate.py`**, which connects as the
table owner. `pg_backfill.py` moves rows and cannot ALTER — the split that D46
exists to record.

---

## L5.5 — DNS: the four hostnames, and what each one is for

```mermaid
flowchart LR
  subgraph CF["Cloudflare — proxied, Full (strict)"]
    H1["bridge-lab"]
    H2["console-lab"]
    H3["faces-lab"]
    H4["claude-*-lab (3)"]
  end

  ALB["ALB a2alab-bridge<br/>:443, *.agenticthings.com origin cert"]
  TUN["cloudflared tunnel<br/>local dev only"]

  H1 --> ALB
  H2 --> ALB
  H3 --> ALB
  H4 --> TUN

  ALB -->|"default action, no rule"| BR["a2alab-bridge<br/>Path A"]
  ALB -->|"host-header rule, prio 20"| CON["a2alab-console"]
  ALB -->|"host-header rule, prio 30"| FAC["a2alab-faces<br/>11 faces by path"]
```

Every public entrance to the lab is a **CNAME in Cloudflare, proxied (orange),
with SSL/TLS mode Full (strict)** — and, since the WS13 cutover, three of the
four point at the *same* ALB. Recorded here because a hostname is the one piece
of the estate no script creates: each is a hand-made record, and a lab that is
otherwise fully deployed still has four manual steps hiding in it.

| Hostname | Points at | Serves | Created |
|---|---|---|---|
| `bridge-lab` | ALB `a2alab-bridge` | Path A — the Apex callout's Named Credential target. **Salesforce-visible**, so this is the one to change carefully | 2026-07-26 (WS7 item 7) |
| `console-lab` | the same ALB | The lab console, routed by a host-header rule (priority 20) | 2026-07-28 (WS13 item 1) |
| `faces-lab` | the same ALB | All eleven protocol faces, rule priority 30, addressed by **path**: `/<target-name>/...` | 2026-07-28 (WS13 item 2) |
| `claude-rest-lab`, `claude-mcp-lab`, `claude-a2a-lab` | the `cloudflared` tunnel | Local development only. Superseded for hosted use by `faces-lab`; kept because the tunnel is now a dev convenience rather than the front door | M6 |

**Why three hostnames and one load balancer.** The ALB terminates TLS on :443
with the imported Cloudflare Origin certificate for `*.agenticthings.com`, so
every lab hostname is already covered and a new face needs **no new
certificate** — just a listener rule and a target group. The bridge stays the
listener's *default action* and carries no rule, which is what makes adding a
face safe: a malformed host condition can only make the new face unreachable,
never Path A.

**Why `faces-lab` is one record and not nine.** Nine faces addressed by hostname
would be nine hand-made DNS records and nine listener rules. Addressed by path
they are one of each (D51), and the mount prefix is the target name — so
`claude-mcp` lives at `https://faces-lab.../claude-mcp/mcp`.

**The corporate-proxy caveat.** The operator's proxy blocks this whole domain at
DNS, so none of these hostnames resolve from the work laptop with the proxy on
(measured: a hostname that never existed still hangs 30s, plan/03-results.md).
That is not a deployment fault and no front door fixes it — with nothing running
locally there is no cost to dropping the proxy to look. Verify from inside AWS,
or against the ALB's own hostname with a `Host:` header, which is how every
cutover in this file was checked.

---

## L5.7 — Scheduled and long-running processes (the async inventory)

**Why this exists.** Everything else in this file is request-shaped: something
calls, something answers. The processes below are the ones **nobody calls** —
they fire on a clock, poll in a loop, or run for minutes after the request that
started them has returned. They are the easiest things in the estate to forget,
the hardest to notice when they stop, and they spend money while nobody is
watching. This is the one place they are all listed.

```mermaid
flowchart LR
  subgraph CLK["Clocks"]
    EBS["EventBridge Scheduler<br/>rate(6 hours) UTC"]
    ANT["Anthropic scheduled<br/>deployments (cron)"]
  end
  subgraph LOOP["Always-on loops"]
    BW["a2alab-briefs<br/>poll every 60s"]
  end
  subgraph WORK["What they drive"]
    HARV["Lambda a2alab-obs-harvest<br/>8 sources -> Aurora"]
    BRIEF["Brief agent session<br/>-> Salesforce record"]
    ANLY["Obs analyst -> lab.obs_briefs"]
    COST["Cost sentinel -> lab.obs_briefs"]
  end
  EBS --> HARV
  ANT --> BRIEF
  ANT --> ANLY
  ANT --> COST
  BW -->|"services the stalled tool call"| BRIEF
```

| # | Process | Where it runs | Cadence | State (2026-07-30) | What it writes |
|---|---|---|---|---|---|
| 1 | **Observability harvest** | Lambda `a2alab-obs-harvest`, fired by **EventBridge Scheduler** `a2alab-obs-harvest-6h` | `rate(6 hours)`, UTC | **ENABLED** | `lab.obs_sessions`, `obs_events`, `obs_harvest` — six agent platforms (Salesforce, Anthropic, OpenAI, ADK, Foundry, Strands — the last WS5/D67) + the two coding sources (`coding` metrics, `coding-logs` behaviour, WS16), eight harvest sources in all |
| 2 | **Account brief agent** (D16) | Scheduled Claude Managed Agent | `0 6 * * *`, America/Denver | **active** | an `A2ALab_Account_Brief__c` in Salesforce, via a host-side tool |
| 3 | **Brief watcher** (D52) | ECS service `a2alab-briefs` | poll loop, `A2ALAB_BRIEF_POLL_S` = 60s | **running 1/1** | services #2's stalled tool call; `lab.lab_state` serviced-set |
| 4 | **Observability analyst** (D23) | Scheduled Claude Managed Agent | `0 6 * * *`, America/New_York (**paused**, so on-demand only) | **paused** | `lab.obs_briefs` with `kind='observability'` |
| 5 | **Cost sentinel** (WS12/D44) | Scheduled Claude Managed Agent | `0 7 * * *`, America/New_York | **active** (daily since 2026-07-30, D44 addendum) | `lab.obs_briefs` with `kind='cost'` |

**Always-on but request-shaped**, listed so the inventory is complete: the four
ECS services (`a2alab-bridge`, `-console`, `-faces`, `-briefs`) all run 1/1.
Only `-briefs` does work with no caller.

**Three things this table is for, each of which it has already caught:**

- **The harvest schedule is in EventBridge *Scheduler*, not EventBridge
  *Rules*.** They are different services with different APIs, and
  `aws events list-rules` returns nothing for it — which reads exactly like "no
  schedule exists". If you go looking, `aws scheduler list-schedules`.
- **One of the five is paused**, and it has no cron at all. The observability
  analyst (#4) had not produced a brief since **2026-07-18**, eleven days, and
  nothing surfaced that: the console's brief panel showed the newest brief of
  *any* kind, so the cost sentinel's build-cost brief appeared in the
  Observability section and looked like the analyst had changed subject (D56).
  The cost sentinel (#5) was itself paused until 2026-07-30 and is now daily —
  which is why the console's newest cost brief before then was a *manual* 7/28
  firing, and why a demo reader took the panel for "not provisioned" (D44
  addendum).
- **#2 and #3 are one mechanism split across two clouds.** The Anthropic cron
  fires a session that then *stalls* awaiting a host-side Salesforce write; the
  ECS watcher is the half that finishes it. Neither is useful alone, and
  reading either row on its own gives the wrong picture of what runs.

**When adding a scheduled or long-running process, add a row here.** The test
of whether something belongs is not "is it a cron" — it is **"if this stopped,
how would anyone find out?"** Every row above answers that with "they would
not", which is the reason to write them down.

---

## L6 — Code → deployment: which file becomes which running thing

```mermaid
flowchart LR
  subgraph REPO["This repo"]
    R1["src/bridge/"]
    R2["src/platforms/claude/sdk_backend.py"]
    R3["src/platforms/openai/"]
    R12["src/platforms/strands/<br/>(backend: Kiro-built)"]
    R4["src/platforms/agentforce/a2a_shim.py"]
    R5["src/fanout_mcp/"]
    R6["src/observability/"]
    R7["src/platforms/adk/"]
    R8["src/platforms/foundry/core.py"]
    R9["src/console/ + src/platforms/guide/"]
    R11["src/faces/"]
    R10["salesforce/"]
  end
  R1 -->|"deploy/bridge/deploy_bridge.sh"| D1["ECS service a2alab-bridge"]
  R2 -->|"deploy/agentcore/deploy.sh claude"| D2["AgentCore a2alab_claude"]
  R3 -->|"deploy/agentcore/deploy.sh openai"| D3["AgentCore a2alab_openai"]
  R12 -->|"deploy/agentcore/deploy.sh strands"| D12["AgentCore a2alab_strands"]
  R4 -->|"deploy/shim/build_zip.sh + deploy_shim.sh"| D4["Lambda a2alab-af-shim"]
  R5 -->|"deploy/fanout/build_zip.sh + deploy_fanout.sh"| D5["Lambda a2alab-fanout-mcp"]
  R6 -->|"deploy/obs/build_zips.sh + deploy_harvest.sh"| D6["Lambdas obs-harvest + obs-mcp"]
  R7 -->|"deploy/adk/deploy_adk.py"| D7["Agent Engine x3"]
  R8 -->|"deploy/foundry/provision_foundry.py"| D8["Foundry agents"]
  R9 -->|"deploy/console/deploy_console.sh"| D9["ECS service a2alab-console<br/>(rule on the bridge ALB)"]
  R11 -->|"deploy/faces/deploy_faces.sh"| D11["ECS service a2alab-faces<br/>(rule on the bridge ALB)"]
  R10 -->|"Salesforce DX MCP deploy"| D10["Production org"]
```

**The full mapping, including what each deploy actually creates:**

| Repo path | Deploy command | Becomes | Where |
|---|---|---|---|
| `src/bridge/` | `deploy/bridge/deploy_bridge.sh` | ECR image + ECS task def + service `a2alab-bridge` on cluster `a2alab`, ALB + target group + listener, roles `a2alab-bridge-task` / `-exec`, secret `a2alab/runtime/bridge`. The task role's `invoke-agentcore` policy grants `bedrock-agentcore:InvokeAgentRuntime` on the Claude, OpenAI **and Strands** runtimes (D68 added `STRANDS_AGENTCORE_ARN` — the reverse `agentforce-to-strands` cell is the first bridge → Strands-runtime path) | AWS |
| `src/platforms/claude/` (sdk backend) | `deploy/agentcore/deploy.sh claude` | AgentCore runtime `a2alab_claude` + secret `a2alab/runtime/claude` | AWS |
| `src/platforms/openai/` | `deploy/agentcore/deploy.sh openai` | AgentCore runtime `a2alab_openai` + secret `a2alab/runtime/openai` | AWS |
| `src/platforms/strands/` | `deploy/agentcore/deploy.sh strands` | AgentCore runtime `a2alab_strands` + secret `a2alab/runtime/strands` (Salesforce creds only) + `bedrock:InvokeModel` on the runtime role. **LIVE 2026-08-04** (WS5/D66): Kiro delivered the `strands-sdk` backend (`plan/12`), the runtime is created, and `strands-to-agentforce` runs against it — model `claude-haiku-4-5` on Bedrock via the runtime IAM role (no API key), matched to the Claude AgentCore twin so only the SDK differs. The D25 Strands-paired Agentforce twin (`A2ALab_Research_Assistant_Strands`, `SF_STRANDS_AGENT_ID`) is provisioned and active, so `ask_agentforce` consults it directly. Remaining caveat: `platform_ref` (Bedrock request-id) comes back null at this SDK version | AWS |
| `src/platforms/agentforce/` (shim) + `deploy/shim/handler.py` | `build_zip.sh` then `deploy_shim.sh` | Lambda `a2alab-af-shim` + API Gateway HTTP API + secret `a2alab/runtime/shim` | AWS |
| `src/fanout_mcp/` | `build_zip.sh` then `deploy_fanout.sh` | Lambda `a2alab-fanout-mcp` + API Gateway + secret `a2alab/runtime/fanout-mcp` | AWS |
| `src/observability/` | `deploy/obs/build_zips.sh` then `deploy_harvest.sh` / `expose_mcp.sh` | Lambdas `a2alab-obs-harvest` (EventBridge 6h) and `a2alab-obs-mcp` (+ API Gateway), secret `a2alab/obs/harvest`, role policy `a2alab-obs-promql`. **WS16:** the harvest role also needs `logs:FilterLogEvents` on `/a2alab/coding-agents/otlp` for the `coding-logs` source (owned by `deploy_harvest.sh`) — same shape as the PromQL grant. **WS5/D67:** and `a2alab-obs-strands` (`cloudwatch:GetMetricStatistics` for the `AWS/Bedrock` meters + `logs:FilterLogEvents` on `/aws/bedrock-agentcore/runtimes/*` for the Strands runtime access log) | AWS |
| `src/obs_mcp/` | `deploy/obs/expose_mcp.sh` (`--code` for code alone) | Function code for `a2alab-obs-mcp`. **Nothing pushed this zip until 2026-07-27** — `expose_mcp.sh` built the API and left the code to a hand-run `update-function-code` | AWS |
| `observability/pg.py` `DDL` | `scripts/pg_migrate.py` | Schema changes in Aurora `a2alab`, run as the **table owner**. `pg_backfill.py` (rows only) connects as `lab_writer`, which cannot ALTER | AWS |
| `src/platforms/adk/` | `deploy/adk/deploy_adk.py` | Agent Engine deployments `a2alab-adk-researcher`, `a2alab-supply-orchestrator-adk`, `a2alab-logistics-agent` | GCP |
| — | `deploy/adk/provision_aws_federation.py` | GCP→AWS trust: one IAM role | AWS |
| — | `deploy/fanout/provision_gcp_federation.py` | AWS→GCP: pool, provider, mapping, condition, impersonation | GCP |
| `src/platforms/foundry/core.py` | `deploy/foundry/provision_foundry.py`, `provision_leg_agent.py` | Foundry agents + RemoteA2A connection to the shim + inbound A2A | Azure |
| `src/console/` + `src/platforms/guide/` | `deploy/console/deploy_console.sh` | ECR image + task def + ECS service `a2alab-console` on cluster `a2alab`, target group + **host-header rule on the bridge's existing ALB** (no second load balancer), roles `a2alab-console-task` / `-exec`, secret `a2alab/runtime/console`. Deployed 2026-07-28; DNS still points at the tunnel | AWS |
| `src/console/app.py` `/api/track` + `lab.usage_events` | (part of `deploy/console/deploy_console.sh`; table via `scripts/pg_migrate.py`) | WS18/D62 usage analytics: the console's own `POST /api/track` proxy writes anonymous rows to Aurora `lab.usage_events` (as `lab_writer`) and **forwards** each event to the external AWS logging service below. Ships in the console image; no separate deploy. Needs `A2ALAB_LOGGING_API_URL`/`_KEY` in the console secret | AWS |
| — (external, not this repo) | operator's existing `aws-logging-service` (custom Lambda + API Gateway, us-west-2) | The Slack log sink the Claude/Codex hooks already post to; `/api/track` forwards to it fire-and-forget for the operator's cross-project notifications. An outbound **edge**, not a component this repo deploys (D62) | AWS |
| `src/faces/` (the protocol faces) | `deploy/faces/deploy_faces.sh` | ECR image + task def + ECS service `a2alab-faces`, target group + host-header rule on the bridge's ALB, roles `a2alab-faces-task` / `-exec`, secret `a2alab/runtime/faces`. **One process serves all fourteen, addressed by path** (the three strands faces still run the stub — the live Strands turn runs on the AgentCore runtime, not the faces task; the faces image would need the `strands` extra + `STRANDS_BACKEND` to serve the real backend, WS5/D66) | AWS |
| `src/briefs/` (the watcher) | `deploy/briefs/deploy_briefs.sh` | ECS service `a2alab-briefs` on the shared cluster, roles `a2alab-briefs-task` / `-exec`, secret `a2alab/runtime/briefs`. **Reuses the faces image**, no ALB, no target group — it serves nothing | AWS |
| `salesforce/` | Salesforce DX MCP deploy | Apex `A2ALabInvokeRemoteAgent`, Named/External Credentials, External Client Apps | Salesforce |
| `salesforce/.../aiAuthoringBundles/A2ALab_Supply_Orchestrator` | `sf agent validate\|publish\|activate` | Agent Script orchestrator bundle in the prod org (WS8 variant 3, D61). Fans out by **delegating** to the bridge's `fanout:` route; reuses `A2ALabInvokeRemoteAgent` — no new Apex, no new Named Credential | Salesforce |
| `src/bridge/app.py` `_fanout()` | (part of `deploy/bridge/deploy_bridge.sh`) | The bridge's `fanout:<scenario>` verb route — one Apex callout runs the three legs off-platform via `orchestration.dispatch()`. Ships with the bridge image; not a separate deploy (D61) | AWS |
| `.claude/settings.local.json` + `scripts/codex_otel.sh` | — | OTLP exporter config; **metrics** land in CloudWatch (read by `coding`) | laptop → AWS |
| `.cursor/hooks.json` + `scripts/cursor_otel.sh` | `cursor_otel.sh` (once, per credential rotation) | Cursor has no native exporter — hooks forward to the **cursorscope** ingestor, which exports **metrics** to the same CloudWatch endpoint (read by `coding` as `tool=cursor`, D64). Cumulative counters | laptop → AWS |
| `scripts/setup_cw_logs_otlp.py` + `scripts/claude_otel.sh` | `setup_cw_logs_otlp.py --apply` (once) | `logs.amazonaws.com` service credential + CloudWatch **log group** `/a2alab/coding-agents/otlp` (bearer auth) + token in Secrets Manager `a2alab/telemetry/cw-logs-api-key`; the launch wrapper exports Claude Code's **log** events there (read by `coding-logs`, WS16) | laptop → AWS |
| `.env` (gitignored) | `scripts/env_sync.py push` | Secrets Manager secret `A2ALAB_ENV_SECRET` — every platform credential, the account ids, the project ids | AWS |
| `scripts/credential_analyst.py` | — (no deploy step) | **Nothing hosted** — one `messages.create` per run, started by a person | laptop → Anthropic API |
| `scripts/setup_cost_sentinel.py`, `scripts/cost_sentinel.py` | `setup_cost_sentinel.py` | Managed Agent "A2ALab Cost Sentinel" + daily scheduled deployment (`0 7 * * *` America/New_York — shipped weekly-and-paused, moved to daily and resumed 2026-07-30, D44) + its own vault on the obs MCP server | Anthropic |

**What is still on the laptop, and whether that is a problem:**

| Still local | Problem? |
|---|---|
| The console (`:8200`) and the Lab Guide | **No longer local** — on ECS Fargate since 2026-07-28 (WS13 item 1). `cloudflared` still serves the hostname until DNS is cut over, and stays afterwards for local development. |
| The protocol servers (`:8001`–`:8003`, `:8011`–`:8013`) | No — the hosted equivalents are the AgentCore runtimes; these are the dev loop. |
| `cloudflared` tunnel | Reduced — the bridge left it on 2026-07-26. It still fronts the console and the direct protocol hostnames. |
| The brief watcher (`python -m briefs --watch`) | **No longer local** — on ECS Fargate as service `a2alab-briefs` since 2026-07-28 (D52 / WS13 item 3), running 1/1, reusing the faces image. The copy in `run_local.sh` is the dev loop, not a live-path dependency. |

### How this got here: local first, then hosted, one component at a time

That table is a progress bar, and it is worth reading as one. Every component
above started as a process on a laptop reached through a tunnel, and each moved
out only once there was a reason and a measurement:

| When | What moved | What forced it |
|---|---|---|
| Start | everything local, tunnel-fronted | fastest way to get a real cross-platform call working at all |
| D26 | Claude + OpenAI agents → AgentCore | a self-hosted agent that only exists on a laptop cannot be demonstrated as a *runtime* |
| D28 | Agentforce shim → Lambda | two platform-native A2A callers must reach a public endpoint; pointing them at a tunnel makes the experiment about the tunnel |
| D23 | observability store → Aurora, harvest → Lambda | a 6-hourly pull cannot depend on a laptop being awake |
| D41 | fan-out legs → remote MCP server | host-side tools need a laptop attached for the whole run |
| WS7 item 7 | **the bridge → Fargate + ALB** | it was the last thing on Path A's live call path; the ALB was needed because the 45s budget would not fit a gateway |
| D52 (2026-07-28) | **the brief watcher → Fargate service `a2alab-briefs`** | the last laptop dependency on a live path; a poll loop, so a service not a Lambda. The estate is now fully hosted. |

The order was not planned up front. Each move was triggered by a specific thing
the local version could not do, which is why the hosting shapes differ — they
were chosen against different constraints, years of architecture-review advice
about "consistency" notwithstanding. Keeping the sequence visible is the point:
it shows a lab that earned its way to hosted rather than starting there.

---

## Checking reality

The diagrams above describe intent as committed to the repo. These commands
print what the accounts actually hold right now:

```sh
# AWS — the four shapes
aws ecs describe-services --cluster a2alab --services a2alab-bridge \
  --query 'services[0].{status:status,running:runningCount,task:taskDefinition}'
aws elbv2 describe-load-balancers --names a2alab-bridge \
  --query 'LoadBalancers[0].DNSName'
aws lambda list-functions --query "Functions[?starts_with(FunctionName,'a2alab')].FunctionName"
aws bedrock-agentcore-control list-agent-runtimes --query 'agentRuntimes[].agentRuntimeName'
aws secretsmanager list-secrets --query "SecretList[?starts_with(Name,'a2alab')].Name"

# The one that matters for Path A's budget — must print 120
aws elbv2 describe-load-balancer-attributes --load-balancer-arn "$(aws elbv2 \
  describe-load-balancers --names a2alab-bridge --query 'LoadBalancers[0].LoadBalancerArn' \
  --output text)" --query "Attributes[?Key=='idle_timeout.timeout_seconds'].Value"

# GCP / Azure
gcloud ai reasoning-engines list --region us-central1
az account show

# The lab's own answer: every caller identity proving it can still do its job
uv run python scripts/identity_preflight.py
```

`scripts/identity_preflight.py` is the closest thing to a live map: it makes
every caller identity mint its own token and run its own call, and fails if any
cannot.

---

## Why not, in one place

The choices above that look inconsistent until you know what drove them:

| "Why didn't you…" | Because |
|---|---|
| …put the bridge on API Gateway like everything else? | 30s hard ceiling vs a 45s measured budget. The ALB's idle timeout is settable; the gateway's is not. |
| …put the shim on Fargate too, for consistency? | Its work measures 10–19s. Consistency is not a reason to pay for a load balancer. |
| …run all the agents in one cloud? | Then the lab tests our containers, not the platforms. Each agent lives in its vendor's native home on purpose. |
| …give the AgentCore runtimes a public URL? | They have an IAM data plane instead. There is no URL to leak, and callers are authorized by role. |
| …use one Salesforce connected app? | Salesforce attributes client-credentials calls to the app, so one app makes every caller look like one user in the audit trail. |
| …store platform keys in the task definitions? | D39. Task definitions carry a secret ARN; the values are fetched at start from Secrets Manager. |
| …automate the TLS/DNS cutover? | It is a Salesforce-visible hostname. Verified on the ALB's own hostname first, then one DNS change against a known-good target. |
| …give the console a CDN front door so it works behind the corporate proxy? | Tried and reverted 2026-07-27. It worked — but it solved the wrong problem. The proxy blocks the lab's whole domain at DNS (a hostname that never existed still hangs 30s), so no front door fixes the *domain*, and the operator is content to drop the proxy to view the console. What actually hurt was the laptop being on the runtime path, which is WS13. |
| …keep sqlite as the console's observability store? | D49. It was, and that was the bug: the hosted harvest wrote Aurora, the local harvest wrote `traces/lab.db`, and the console read only the file — so the dashboard showed the laptop's copy while the authoritative one drifted. Postgres is the source of truth now, chosen in one place, with sqlite kept only for offline work on a snapshot. |
| …make the brief watcher an EventBridge Lambda? | That is what WS13 item 3 assumed, and it was the wrong shape. Its work is a poll LOOP, and a Lambda would need a third zip carrying the Anthropic SDK, httpx and the Salesforce client — another bundle to build and keep in step (D46's whole subject). The faces image already contains the code and every dependency, so the watcher is that image with a different command, at ~$4/month. |
| …run each protocol face as its own service? | D51. Nine Fargate tasks (~$80/month) to run nine `uvicorn`s, when every face is an ASGI app that `build_app()` already returns without a server. One process serves all eleven, addressed by PATH rather than by nine hostnames — nine DNS records somebody creates by hand, against one, for no behavioural difference. It also sidesteps ECS's limit of five target groups per service. |
| …give the console its own load balancer? | A second ALB is ~$16/month for nothing. The bridge's already terminates TLS on :443 with the `*.agenticthings.com` origin cert, so an extra face costs a target group, a **host-header rule** and a task. The bridge stays the listener's default action and carries no rule, so a wrong host pattern can only make the console unreachable — it cannot break Path A. |
| …trust that a deploy which passes its own runbook is verified? | D48. The console's first hosted run passed every check — image, secret, rule, stable service, `/healthz` 200 — while serving every `/api` surface unauthenticated, because its runtime secret was created, shipped and never loaded, and the auth middleware treats a missing token as *auth is off*. A valid-token check proves nothing when all tokens are accepted; the negative test is the one that finds it. |
| …make the credential analyst a Managed Agent like the other two? | It was one, and it was demoted (D44). Its work is one round trip over a report a person just collected — no tools, no schedule, no state. The agent object, setup step and state file were surface with nothing behind them. |

## Presenter notes

Prep and delivery notes for the lab owner. The console renders this section
**only** for a signed-in reviewer (`config/users.yaml`, `reviewer: true`) — it
is not part of the document a reader or colleague sees.

**Before presenting this page**

- The map is written from the deploy scripts, not from live state. Run the
  "Checking reality" commands first if it has been a while — the one that
  matters most is the ALB idle timeout, because a silent reset to 30s takes
  Path A's 45s budget with it and the diagrams would still claim 120s.
- `uv run python scripts/identity_preflight.py` is the fastest single proof the
  estate is healthy: every caller identity mints its own token and makes its own
  call. If it passes, L4 is true as drawn.
- Warm the shim before any live Path B demo. Foundry's A2A tool does not retry,
  so a cold twin session behind the 29s gateway ceiling fails the tool call
  outright — and it fails as a *fabricated* answer, not an error.
- AWS SSO expires. `aws sso login --profile $AWS_PROFILE` with Zscaler ON; then VPN
  OFF again for the local console.

**Questions this deck reliably gets, and the answer that lands**

- *"Why is one component on Fargate when everything else is Lambda?"* — Do not
  answer with preference. Answer with the number: the bridge's budget is 45s,
  an HTTP API's ceiling is 30s and is not adjustable, an ALB's is an attribute.
  Then the general rule: host to the measured ceiling, not the observed average.
- *"Isn't four clouds over-engineered?"* — The subject of the lab is
  cross-platform interop. Running every agent as our own container in one cloud
  would test our containers, not the platforms. Say that before the diagram
  invites the question.
- *"How do you keep this document true?"* — `CLAUDE.md` requires updating it in
  the same change as any deploy, and the honesty sweeps audit the claims. Be
  honest that the convention alone was not sufficient: the 2026-07-27 sweep
  found five stale claims across the record.
- *"What is still on a laptop?"* — The brief watcher, and say so plainly. It is
  the open half of WS7 and pretending otherwise is the one thing that would cost
  credibility in this room.

**Where the story is strongest**

L1 is the slide that earns the most respect from infrastructure people: two
components, same account, same team, opposite hosting decisions, each driven by
what its work costs in seconds. L4's lopsided federation box is the second —
"keyless federation" costing five objects in one direction and one in the other
is concrete in a way architecture diagrams usually are not.

## Related

- `plan/01-architecture.md` — the code's shape: the two seams, protocol mapping
- `plan/00-decisions.md` — D20 tunnel, D21 AWS account, D23 hosted obs, D26
  AgentCore, D28 hosted shim, D39 one human login, D40 federation, D41 fan-out
- `plan/03-results.md` — the measured numbers every budget here is built on
- `plan/07-workstreams.md` — WS7 (what is left to host), WS11 (the next change)
- `plan/04-runbooks.md` — how to deploy and recover each piece
