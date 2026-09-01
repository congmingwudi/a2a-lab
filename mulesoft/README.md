# MuleSoft Agent Fabric — WS10 SP1 walking skeleton

This directory holds the committed **Agent Fabric** `agentic-network` project
for the A2A Interop Lab (WS10, sprint SP1). It registers the lab's six
bearer-auth hosted A2A faces as fabric-visible agents and puts a single
one-hop broker (`broker1`) in front of them, so an Agent Fabric gateway can
reach the lab the same way any other A2A client does.

Design context and the current deploy status live in
`plan/15-mulesoft-agent-fabric-gateway-blocker.md` and the sprint spec,
`docs/superpowers/specs/2026-08-31-mulesoft-agent-fabric-broker-design.md`.
The hosted Agentforce A2A shim one of the six faces proxies through is `D28`.

## File layout

```
mulesoft/
  README.md                          — this file
  .gitignore                         — ignores build output (target/) and the
                                        rendered exchange.json
  agent-network/
    exchange.json.template           — COMMITTED Exchange descriptor: name,
                                        classifier, main file, GAV coordinates
                                        (org id as a ${A2ALAB_MULE_ORG_ID}
                                        placeholder), and the deploy-variable
                                        contract (no secret values, no org id)
    exchange.json                    — GENERATED, gitignored: the template with
                                        the real org id substituted from .env
    render_exchange.py               — renders the template → exchange.json,
                                        substituting A2ALAB_MULE_ORG_ID from .env
    agent-network.yaml               — the v2 agentic-network descriptor:
                                        registry.agents.{claude,openai,strands,
                                        guide,agentforce,langgraph}, one A2A
                                        connection per face, and brokers.broker1
    brokers/
      broker1.agent                  — AgentScript source for the one-hop broker
```

`agentNetwork: 2.0.0`, the `broker.kind: AgentScript` shape, and the
`secret: true` deploy-variable mechanism are taken verbatim from the AF
plugin's own `templates/agentic-network/*.template` files and the published
`agent-network-v2.d.ts` schema (spec §3, §4.7) — not invented here.

## What's committed vs. what's supplied at deploy time

Nothing committed here names a face URL, a MuleSoft org id, a business group,
or a secret value — consistent with the repo-wide rule that no account
identifier is hardcoded anywhere (see the root `CLAUDE.md`).

The Agent Fabric descriptor requires Exchange GAV coordinates
(`organizationId` / `groupId` / `assetId` / `version`), and `groupId` **is**
the MuleSoft org id — `project publish` checks the authenticated session owns
the org that `groupId` names. `assetId` (`a2a-interop-lab-fabric`) and
`version` (`1.0.0`) are safe literals and are committed; `groupId` /
`organizationId` are a `${A2ALAB_MULE_ORG_ID}` placeholder in
`exchange.json.template`. Before build/publish/deploy, render the real
descriptor once in the worktree (the org id comes from `.env`, and the output
is gitignored — the same pattern as `A2ALab_GCP.externalCredential-meta.xml`):

```
uv run python mulesoft/agent-network/render_exchange.py
```

Every face URL and both gateway OAuth credentials are **deploy variables**
declared in the descriptor with empty defaults, supplied at deploy time:

```
agent-network project deploy --gateway agent-network-shared-gw \
  --property claude.url:https://... \
  --property openai.url:https://... \
  --property strands.url:https://... \
  --property guide.url:https://... \
  --property agentforce.url:https://... \
  --property langgraph.url:https://... \
  --property gwClientId:<client-id> \
  --property gwClientSecret:<client-secret>
```

`gwClientId` / `gwClientSecret` are marked `"secret": true` in
`exchange.json`, so the toolchain treats them as secured variables rather
than plaintext deploy properties. All six connections authenticate the same
way — `oauth2-client-credentials` against the lab console's public
client-credentials endpoint, `https://console-lab.agenticthings.com/oauth/token`
— which is a checked-in literal (it is the console's public hostname, not an
account identifier; the same literal already appears in
`deploy/tunnel/config.yml` and in plan docs).

## Lifecycle commands

```
uv run python mulesoft/agent-network/render_exchange.py   # render exchange.json from .env (first)
anypoint-cli-v4 agent-network project build      # compiles + validates the AgentScript broker
anypoint-cli-v4 agent-network project publish    # publishes agent-network.yaml + exchange.json to Exchange
anypoint-cli-v4 agent-network project deploy --gateway agent-network-shared-gw \
  --property <id>.url:<face-url> ... --property gwClientId:... --property gwClientSecret:...
```

`agent-network-shared-gw` is the AF gateway path provisioned for this lab
(see `plan/15-mulesoft-agent-fabric-gateway-blocker.md` for how the earlier
entitlement gate was cleared).

## Operator-vs-Claude division (spec §4.9)

This SP1 task authored the descriptors above by hand from the plugin's own
templates and the v2 schema — the deterministic YAML/JSON shape that
`tests/unit/test_mulesoft_descriptors.py` guards. Three things are
deliberately **not** done here and remain the operator's:

- **Render**: `render_exchange.py` materializes the real `exchange.json`
  from the committed template, substituting the org id from `.env`
  (`A2ALAB_MULE_ORG_ID`). The GAV shape is taken verbatim from the plugin's
  `templates/agentic-network/exchange.json.template` (`organizationId` /
  `groupId` / `assetId` / `version`); confirm how the toolchain handles the
  YAML anchor (`&gwOauth` / `*gwOauth`) used to keep the six identical auth
  blocks DRY when `build` first runs.
- **Build**: `agent-network project build` is the only compiler validation
  of `broker1.agent` — the highest-uncertainty artifact in this set (its
  action-input binding and `@request.payload` reference are taken from the
  plugin's template but unverified against the real compiler). A first-pass
  build error there is expected feedback, not evidence the descriptor is
  wrong.
- **Publish / deploy**: pushing the project to Exchange and deploying it to
  `agent-network-shared-gw` with the real face URLs and gateway OAuth
  credentials as `--property` values.

## Test coverage

`tests/unit/test_mulesoft_descriptors.py` asserts, without needing any
MuleSoft toolchain or credentials: the registry has exactly the six faces;
every connection is `kind: a2a` with `oauth2-client-credentials` pointed at
the console token URL; the broker references `./brokers/broker1.agent`;
`exchange.json.template` declares the gateway secret as a secured variable
with no committed value and carries GAV with the org id as a
`${A2ALAB_MULE_ORG_ID}` placeholder; and no account identifier appears in any
committed file under `mulesoft/agent-network/`.
