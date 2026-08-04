# Antigravity → CloudWatch OTLP — a research idea, not yet a build

**Status: undecided.** This note gathers what's knowable from Antigravity's
published docs (pulled 2026-08-02) plus one un-run probe, so the "is it worth
it?" call is evidence-based. No `scripts/antigravity_otel.sh` exists, no
`hooks.json` is written, and `coding_source.py` has no `antigravity` entry.
Do not describe this as wired.

## Engineering takeaway

Antigravity is the lab's **Cursor pattern with a wider gap**: no documented
native OTEL exporter, and a hook payload that carries **no model, no tokens, no
cost** — only conversation/tool/lifecycle metadata. So the honest ceiling of a
hook forwarder is *sessions and tool executions*, the same floor Cursor sits on,
minus even Cursor's `gen_ai.*` token histograms. The one thing that could lift
it to Claude-Code parity is unknown until probed: **whether the Antigravity CLI
honors standard `OTEL_EXPORTER_OTLP_*` env vars.** Run that probe before writing
a line of forwarder code — it decides which of two very different builds this is.

## What Antigravity actually is

From `antigravity.google/docs/getting-started`:

- A **desktop IDE** (Antigravity 2.0) where agents operate within a project.
- An **Antigravity CLI** (v1.1.9) for terminal use.
- An **Antigravity SDK** (v0.1.7) for programmatic integration.
- Gemini-backed. Usage is metered as **"AI Credits"** — no documented dollar or
  token telemetry surface.

Extensibility: **MCP, Skills, Plugins, Rules/Workflows, Hooks, Sidecars.** Of
these, **Hooks** is the only telemetry-relevant extension point (Sidecars are
auxiliary processes — a possible home for a forwarder, but not a signal source).

## The two candidate paths

### Path W — CLI honors OTEL env (the good outcome)

If the CLI/SDK, being newer and of Gemini lineage, respects the OpenTelemetry
SDK's standard env vars, this collapses to the **Codex/Claude Code pattern**: a
thin launch wrapper (mirror `scripts/claude_otel.sh` / `scripts/codex_otel.sh`)
that injects the OTLP endpoint + the CloudWatch metrics bearer token
(`scripts/otel_headers.sh`) at exec time. You then get real **tokens and model**
for free, because the exporter reads them from the agent's own instrumentation
rather than from a hook payload that omits them.

This is unverified. Nothing in the docs promises OTEL env support; it's a
plausible-but-unproven property of the CLI. **The probe below settles it.**

### Path H — no native OTEL (the Cursor pattern, the safe assumption)

Hooks are the documented extension point. Config is a `hooks.json` in a
customization dir (`.agents/` in-workspace, or `~/.gemini/config/`), events fire
on stdin/stdout JSON — architecturally identical to `.cursor/hooks.json` →
cursorscope → CloudWatch.

Hook events: `PreToolUse`, `PostToolUse`, `PreInvocation`, `PostInvocation`,
`Stop`. Payload common fields: `conversationId`, `workspacePaths`,
`transcriptPath`, `artifactDirectoryPath`. Event-specific: `PreToolUse` gets
`toolCall{name,arguments}` + `stepIdx`; `PostToolUse` gets `stepIdx` + optional
`error`; `Stop` gets `executionNum`, `terminationReason`, `fullyIdle`.

**The gap that caps this path:** the payload has **no model, no token counts, no
cost**. A forwarder built on it can honestly emit only:

| Lab metric | Antigravity via hooks |
|---|---|
| session count | ✅ (`Stop` / lifecycle) |
| tool executions | ✅ (`PostToolUse` — carries tool name via the matcher) |
| tokens | ❌ not in payload |
| cost | ❌ (no tokens to model from) |
| model | ❌ not in payload |
| edit-acceptance (WS16) | maybe — if a file-edit tool exists and `PreToolUse` `decision` is observable |

One escape hatch, unproven: hooks receive `transcriptPath`. If Antigravity's
transcript file records per-turn model/tokens (many agent transcripts do), a
forwarder could **parse the transcript** for what the payload omits. Treat this
as a follow-on, not the first cut — it couples the forwarder to an undocumented
file format.

## The deciding probe (run this first, ~5 min, no code)

```sh
# Adjust the endpoint to the one scripts/claude_otel.sh / otel_headers.sh use
# (the CloudWatch managed OTLP metrics endpoint). Send one short task, then look
# for ANY series carrying service.name=antigravity.
OTEL_SERVICE_NAME=antigravity \
OTEL_METRICS_EXPORTER=otlp \
OTEL_EXPORTER_OTLP_ENDPOINT=<CloudWatch OTLP metrics endpoint> \
OTEL_EXPORTER_OTLP_HEADERS=<bearer from scripts/otel_headers.sh> \
  antigravity <a short one-turn task>

# Then query CloudWatch (same PromQL path the lab's PromQLClient uses) for
# series with @resource.service.name="antigravity" or any antigravity_*/gen_ai.* name.
```

- **Series appear → Path W.** Write `scripts/antigravity_otel.sh` as a launch
  wrapper. Real tokens/model. Best case.
- **Nothing → Path H.** Write `.agents/hooks.json` + a `forward.sh` that
  SigV4-signs to the same CloudWatch OTLP metrics endpoint (mirror
  `.cursor/hooks/forward.sh` and cursorscope's endpoint resolution). Sessions +
  tool executions only, with the honest "no cost/tokens/model" caveat.

### Probe findings

> _Not yet run._ Record here: date, exact command, endpoint used, and whether any
> `service.name=antigravity` series landed in CloudWatch. That result selects
> Path W vs Path H and turns this note from a question into a plan.

## Lab-side changes if we proceed (small, same in either path)

Whatever emits must land under a tool name the harvester recognizes. In
`src/observability/coding_source.py`:

- one new `TOOL_PREFIXES` entry (e.g. `"antigravity": "antigravity"`) so
  `_tool_of` / the `@resource.service.name` fallback bucket it;
- the metric family names in a `CURSOR_METRICS`-style list (or, Path W, the
  native metric names the probe reveals).

Then it flows through `summarize_series` → the console's Coding Agents
Telemetry section with **zero UI change**, exactly like adding Cursor did. Per
the lab's own rules it would also need: this note kept in step, a `plan/09`
deployment-map entry **only if** a forwarder Lambda/sidecar is introduced, and
the "no cost/tokens" caveat travelling with the tiles (the console already
renders `n/a` honestly for Cursor/Codex — Antigravity would reuse that).

## Evidence and limits

- **Proven:** the hook contract, event set, config location, and the
  no-model/no-token/no-cost payload — all from `antigravity.google/docs`
  (getting-started + hooks), pulled 2026-08-02.
- **Assumed, unproven:** that the CLI honors OTEL env vars (Path W). The probe
  is the only thing that converts this from assumption to fact.
- **Not verified here at all:** the lab has never run Antigravity. Everything
  above is from published docs, not from a turn executed against CloudWatch. The
  same acceptance bar the Cursor note set applies — *a green local hook is not
  evidence that labelled metrics reached the destination; query CloudWatch.*
- **Cost/benefit, honest read:** if the probe lands in Path H, Antigravity adds
  a fourth "sessions + tools only" column beside Cursor and Codex — incremental,
  not novel, and it costs a forwarder to host. Path W (real tokens/model) is the
  only outcome that makes Antigravity distinctive enough to clearly justify the
  work. **Let the probe decide.**

## In this repo (if built)

- `scripts/antigravity_otel.sh` — launch wrapper (Path W) **or** setup script (Path H). Does not exist yet.
- `.agents/hooks.json` + a forwarder script — Path H only. Do not exist yet.
- `src/observability/coding_source.py` — the `TOOL_PREFIXES` + metric-name additions.
- `scripts/otel_headers.sh` — reused unchanged for the CloudWatch metrics bearer token.
