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
  .gitignore                         — ignores build output (target/)
  agent-network/
    exchange.json                    — Exchange descriptor: name, classifier,
                                        main file, and the deploy-variable
                                        contract (no secret values, no org id)
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

Nothing in this directory names a face URL, a MuleSoft org id, a business
group, or a secret value — consistent with the repo-wide rule that no account
identifier is hardcoded anywhere (see the root `CLAUDE.md`). Every face URL
and both gateway OAuth credentials are **deploy variables** declared in
`exchange.json` with empty defaults, supplied at deploy time:

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
anypoint-cli-v4 agent-network project create    # scaffold (operator, one-time reconcile)
anypoint-cli-v4 agent-network project build      # compiles + validates the AgentScript broker
anypoint-cli-v4 agent-network project publish    # publishes agent-network.yaml + exchange.json to Exchange
anypoint-cli-v4 agent-network project deploy --gateway agent-network-shared-gw \
  --property <id>.url:<face-url> ... --property gwClientId:... --property gwClientSecret:...
```

`agent-network-shared-gw` is the AF gateway path provisioned for this lab
(see `plan/15-mulesoft-agent-fabric-gateway-blocker.md` for how the earlier
entitlement gate was cleared).

## Operator-vs-Claude division (spec §4.9)

This SP1 task authored the five files above by hand from the plugin's own
templates and the v2 schema — the deterministic YAML/JSON shape that
`tests/unit/test_mulesoft_descriptors.py` guards. Three things are
deliberately **not** done here and remain the operator's:

- **Reconcile**: running `anypoint-cli-v4 agent-network project create` in a
  throwaway directory to emit the canonical scaffold, then diffing it
  against the committed `exchange.json` to lock the `groupId` / `assetId` /
  `version` conventions (an org id must never be committed, so the operator
  supplies these at `create`/`publish` time) and confirm how the toolchain
  handles the YAML anchor (`&gwOauth` / `*gwOauth`) used to keep the six
  identical auth blocks DRY.
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
`exchange.json` declares the gateway secret as a secured variable with no
committed value; and no account identifier appears anywhere under
`mulesoft/agent-network/`.
