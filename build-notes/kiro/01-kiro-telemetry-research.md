# Kiro → CloudWatch OTLP — a research idea, not yet a build

**Status: undecided.** This note gathers what's knowable from Kiro's published
docs (pulled 2026-08-02) plus un-run probes, so the "is it worth it?" call is
evidence-based. No `scripts/kiro_otel.sh` exists, no `.kiro/hooks/` telemetry
hook is written, and `coding_source.py` has no `kiro` entry. Do not describe
this as wired.

## Engineering takeaway

Kiro is the lab's **Cursor pattern with two wrinkles**. Like Cursor/Antigravity
it has no documented native OTEL exporter, and its hooks are the only clear
telemetry extension point. But (1) it is an **AWS product**, so a first-party
CloudWatch/usage path may exist that the others could never have — worth one
probe before assuming a forwarder is needed; and (2) its hook contract is
**exit-code + stdout**, not Antigravity's stdin-JSON decision model, and it
uniquely fires **file-operation events** (`PostFileSave/Create/Delete`) that map
directly onto the lab's WS16 edit-acceptance signal. The docs do **not** state
what data a hook receives, so model/token/cost availability is unknown — a
second probe settles it. **Run both probes before writing forwarder code.**

## What Kiro actually is

From `kiro.dev/docs`:

- An **agentic IDE** built around **specs** (structured planning), **steering**
  (custom guidance), and **hooks** (task automation).
- Also offered as **CLI**, **web**, and **mobile**.
- Extensibility: specs, steering, hooks, **MCP Servers**.
- No documented OTEL/OTLP export, metrics endpoint, CloudWatch integration, or
  token/cost telemetry surface on the pages reachable so far. The hooks doc notes
  hook names "appear in telemetry" but gives no export mechanism. Several
  reference sub-pages (`/docs/reference/*`, `/docs/hooks/reference/`) 404'd at
  fetch time — the payload contract in particular is unconfirmed and is Probe 2.

## Hook contract (what the docs DO say)

Config: JSON files under `.kiro/hooks/` at workspace level.

```json
{
  "version": "v1",
  "hooks": [{
    "name": "hook-identifier",
    "trigger": "PostFileSave",
    "matcher": "regex-pattern",
    "action": { "type": "command|agent", "command": "..." },
    "timeout": 30,
    "enabled": true
  }]
}
```

Triggers: `SessionStart`, `Stop`, `PreToolUse`, `PostToolUse`, `PreTaskExec`,
`PostTaskExec`, `UserPromptSubmit`, `PostFileCreate/PostFileSave/PostFileDelete`.

Command-action exit-code contract (this is the integration surface):

- `0` — success; **stdout is added to context** for `SessionStart` / `UserPromptSubmit`.
- `2` — block execution (`PreToolUse`, `UserPromptSubmit`, `PreTaskExec` only); stderr returned to agent.
- other — warning shown; execution proceeds.

The consequence for telemetry: a forwarder hook runs as a fire-and-forget
command, emits to CloudWatch as a side effect, and returns `0` with empty
stdout so it never pollutes the agent's context. That's cleaner than Cursor's
stdin-JSON path for a pure exporter — **but** it also means the hook must get
its data from **environment/template variables or a payload the docs don't yet
describe** (Probe 2), because there's no documented stdin-JSON blob like
Antigravity's.

## The candidate paths

### Path N — native AWS/CloudWatch usage export (Kiro-only possibility)

Kiro is an AWS product. It is at least plausible there is a first-party usage or
CloudWatch export (a settings toggle, an admin/usage dashboard, or a
`CloudWatch`-shaped metrics stream) that the Google/Anthropic tools could never
have. **Undocumented on the pages reached** — this is Probe 1, not a claim. If it
exists, it could beat a hook forwarder outright.

### Path W — CLI honors OTEL env (the good outcome)

If the Kiro CLI respects standard `OTEL_EXPORTER_OTLP_*` env vars, this collapses
to the **Codex/Claude Code pattern**: a launch wrapper (mirror
`scripts/claude_otel.sh` / `scripts/codex_otel.sh`) injecting the CloudWatch OTLP
endpoint + metrics bearer token (`scripts/otel_headers.sh`) at exec time, giving
real **tokens and model** for free. Unverified — Probe 3.

### Path H — hook forwarder (the Cursor pattern, safe assumption)

A `.kiro/hooks/` command hook shells metrics to a forwarder that SigV4-signs to
the same CloudWatch OTLP metrics endpoint. Honest coverage ceiling depends
entirely on Probe 2 (what the hook can see):

| Lab metric | Kiro via hooks (pending Probe 2) |
|---|---|
| session count | ✅ (`SessionStart` / `Stop`) |
| tool executions | ✅ (`PostToolUse` — carries tool via matcher) |
| edit-acceptance (WS16) | ✅ likely — `PostFileSave/Create/Delete` are first-class here, unlike Cursor/Antigravity |
| tokens | ❓ unknown — depends on hook payload |
| cost | ❓ unknown — no tokens ⇒ no cost |
| model | ❓ unknown — depends on hook payload |

Kiro's **file-operation triggers are a genuine advantage** for the behavioural
(WS16) signal: Cursor and Antigravity have no comparable per-file-save event.

## The deciding probes (run these first, no code)

1. **Native path (Probe 1).** In Kiro's settings/reference and the AWS console:
   is there any usage/telemetry export or CloudWatch stream? Check
   `kiro.dev/docs/reference/*` (404'd on fetch — try in-app), and look for a
   Kiro usage metric namespace in CloudWatch for this account/region.
2. **Hook payload (Probe 2).** Write one throwaway `.kiro/hooks/` command hook
   that dumps everything it receives:
   ```json
   { "version": "v1", "hooks": [{
     "name": "probe-dump", "trigger": "Stop", "matcher": "",
     "action": { "type": "command",
       "command": "env | grep -i -E 'kiro|model|token|tool' >> /tmp/kiro-hook.env; cat >> /tmp/kiro-hook.stdin 2>/dev/null; echo" },
     "enabled": true }] }
   ```
   Run one turn, then inspect `/tmp/kiro-hook.env` and `/tmp/kiro-hook.stdin`.
   This reveals whether model/tokens/cost are reachable at all.
3. **CLI OTEL (Probe 3).** If a CLI turn is scriptable:
   ```sh
   OTEL_SERVICE_NAME=kiro OTEL_METRICS_EXPORTER=otlp \
   OTEL_EXPORTER_OTLP_ENDPOINT=<CloudWatch OTLP metrics endpoint> \
   OTEL_EXPORTER_OTLP_HEADERS=<bearer from scripts/otel_headers.sh> \
     kiro <a short task>
   # then query CloudWatch for any service.name=kiro series
   ```

### Probe findings

> _Not yet run._ Record for each: date, exact command/steps, and result.
> - **Probe 1 (native CloudWatch):** _pending_
> - **Probe 2 (hook payload contents):** _pending_
> - **Probe 3 (CLI OTEL env):** _pending_
>
> These three select the path (N ≻ W ≻ H) and the honest metric coverage, and
> turn this note from a question into a plan.

## Lab-side changes if we proceed (small, same in any path)

Whatever emits must land under a tool name the harvester recognizes. In
`src/observability/coding_source.py`:

- one new `TOOL_PREFIXES` entry (e.g. `"kiro": "kiro"`) so `_tool_of` / the
  `@resource.service.name` fallback bucket it;
- the metric family names in a `CURSOR_METRICS`-style list (or, Path W/N, the
  native metric names the probes reveal).

Then it flows through `summarize_series` → the console's Coding Agents
Telemetry section with **zero UI change**, exactly like adding Cursor did. Per
the lab's rules it would also need: this note kept in step, a `plan/09`
deployment-map entry **only if** a forwarder Lambda is introduced, and the honest
coverage caveat travelling with the tiles (the console already renders `n/a`
honestly for Cursor/Codex — Kiro would reuse that).

## Evidence and limits

- **Proven:** Kiro is an agentic IDE/CLI/web/mobile; hooks live in `.kiro/hooks/`
  with the trigger set and exit-code contract above — from `kiro.dev/docs` +
  `/docs/hooks/`, pulled 2026-08-02.
- **Unconfirmed (docs 404'd or silent):** the hook payload/variable contract
  (Probe 2), any native CloudWatch/usage export (Probe 1), and CLI OTEL env
  support (Probe 3). The reference sub-pages returned 404 at fetch time; confirm
  in-app.
- **Not verified here at all:** the lab has never run Kiro. Everything above is
  from published docs. Same acceptance bar as the Cursor note — *a green local
  hook is not evidence that labelled metrics reached the destination; query
  CloudWatch.*
- **Cost/benefit, honest read:** Kiro's file-operation triggers make it the best
  fit yet for the WS16 edit-acceptance signal, and its AWS lineage is the one
  case where a native path (Path N) might exist. If both fail and it lands in a
  bare hook forwarder with no token/model visibility, it's another "sessions +
  tools" column — incremental, and it costs a forwarder to host. **Let the
  probes decide.**

## In this repo (if built)

- `scripts/kiro_otel.sh` — launch wrapper (Path W) **or** setup script (Path H). Does not exist yet.
- `.kiro/hooks/*.json` + a forwarder script — Path H only. Do not exist yet.
- `src/observability/coding_source.py` — the `TOOL_PREFIXES` + metric-name additions.
- `scripts/otel_headers.sh` — reused unchanged for the CloudWatch metrics bearer token.
