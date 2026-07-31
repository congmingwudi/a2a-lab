# Cursor OTEL → CloudWatch — hooks, not a native exporter

Claude Code and the Codex CLI ship OpenTelemetry exporters. Cursor does not.
There is no `CLAUDE_CODE_ENABLE_TELEMETRY` equivalent, no settings-file `env`
block, and no first-party `otelHeadersHelper`. The only supported extension
point for Agent lifecycle events is **Cursor hooks** — shell commands Cursor
invokes on stdin JSON for events like `beforeSubmitPrompt`, `preToolUse`, and
`stop`.

This note documents how the lab bridges that gap: project hooks forward events
to [cursorscope](https://github.com/last9/cursorscope), a small Node ingestor
that exports OTLP **metrics** (plus traces/logs the lab intentionally does not
route to CloudWatch) to the same managed endpoint Claude Code and Codex already
use.

## Engineering takeaway

When the tool has no native OTEL, the hook pipeline **is** the telemetry
system — and it must be proved at the destination the same way. A installed
hook and a green local health check are not evidence that labelled metrics
arrived in CloudWatch.

---

## 1. Why Cursor is not symmetrical with Claude Code or Codex

| Concern | Claude Code | Codex | Cursor |
|---|---|---|---|
| OTEL built in | Yes — eight `claude_code.*` metrics | Yes — `codex.*` Sums (+ Histogram caveats) | **No** — community/on-demand via hooks |
| Configuration surface | `.claude/settings.local.json` + optional wrapper | `scripts/codex_otel.sh` at launch | `scripts/cursor_otel.sh` + `.cursor/hooks.json` |
| Credential refresh | `otelHeadersHelper` re-runs ~every 29 min | Token resolved once at launch | Token written to `~/.cursorscope/.env`; re-run setup on rotation |
| Attribution hook | `OTEL_RESOURCE_ATTRIBUTES` in project settings | Same via wrapper export | `OTEL_SERVICE_NAME` / `OTEL_RESOURCE_ATTRIBUTES` in cursorscope `.env` |
| Metric namespace | `claude_code.*` | `codex.*` | `cursor_*`, `gen_ai.*` (hook-derived) |

Cursor's own forum confirms first-party OTEL remains on the backlog; every
working integration today intercepts hook events. That matters for two reasons
this lab cares about:

1. **Cloud Agents and other headless Cursor surfaces may not run project
   hooks** — the same limitation called out for third-party hook tools
   elsewhere.
2. **Hook metrics describe Agent *behaviour* (prompts, tools, sessions), not
   the same native billing buckets** Claude Code publishes (`token.usage` with
   cacheRead / cacheCreation splits). Comparing spend across tools requires
   knowing which columns are comparable and which are not.

Cross-read: [Claude Code telemetry note](../claude/08-coding-agent-telemetry.md)
for the shared CloudWatch endpoint, PromQL read path, and silent-failure modes.

---

## 2. Architecture

```
Cursor Agent event
       │
       ▼
.cursor/hooks.json  ──►  .cursor/hooks/forward.sh
       │                        │
       │                        ▼
       │              ~/.cursorscope/scripts/cursorscope-forward.sh
       │                        │
       │                        ├── ensure ingestor (localhost:4327)
       │                        └── POST hook JSON → ingestor
       │                                      │
       │                                      ▼
       │                            cursorscope (Node)
       │                                      │
       │                    ┌─────────────────┼─────────────────┐
       │                    ▼                 ▼                 ▼
       │               traces            metrics              logs
       │          localhost:4318    CloudWatch OTLP    localhost:4318
       │          (fail quietly)   /v1/metrics         (fail quietly)
       │                           bearer token
       ▼
  Agent continues (hook must not block)
```

Three deliberate choices in that diagram:

- **Project hooks, not global.** The lab checks in `.cursor/hooks.json` under
  this repo so attribution and opt-in travel with the clone. Global hooks in
  `~/.cursor/hooks.json` (for example devbar analytics on this machine) are
  left alone. Cursor merges hook sources; opening **`a2a-lab` as the workspace
  root** is required — parent-folder workspaces do not load this repo's
  `.cursor/hooks.json`.
- **Metrics-only to CloudWatch.** The metrics bearer token from
  `scripts/otel_headers.sh` authenticates **only**
  `https://monitoring.<region>.amazonaws.com/v1/metrics`. It cannot carry logs
  or traces. cursorscope still constructs trace and log exporters; they default
  to `localhost:4318` and fail harmlessly. Do not point those signals at the
  metrics endpoint — that produces silent 403s, the same lesson as putting
  `OTEL_LOGS_EXPORTER=otlp` on the metrics URL for Claude Code.
- **Degrade, never block.** `.cursor/hooks/forward.sh` exits 0 if cursorscope
  is not installed yet. A missing telemetry path must not break Agent sessions
  (D39 posture applied to hooks).

---

## 3. Setup — `scripts/cursor_otel.sh`

There is no launch wrapper equivalent to `scripts/codex_otel.sh`. Cursor is a
GUI application; OTEL destination is configured in the cursorscope ingestor, not
in the IDE process environment.

```bash
scripts/cursor_otel.sh              # install + configure + restart ingestor
scripts/cursor_otel.sh --check      # token, ingestor health, OTLP probe
```

What the setup script does:

1. **Pin `AWS_PROFILE`** from the repo `.env` when the shell has none — same fix
   as `scripts/otel_headers.sh` (hook subprocesses are not your terminal).
2. **Fetch the metrics bearer token** via `scripts/otel_headers.sh` and the
   Secrets Manager secret `a2alab/telemetry/cw-metrics-api-key`.
3. **Derive attribution from git** — `project` from the remote repo name (not
   the checkout directory basename), `repo` as `owner/name`, `tool=cursor`.
   Same rule as `scripts/codex_otel.sh` after the 2026-07-26 `rc-a2a` /
   `a2a-lab` mismatch.
4. **Install cursorscope** to `~/.cursorscope` via
   `npx @last9/cursorscope setup --no-hooks --yes` if missing (`--no-hooks`
   keeps global `~/.cursor/hooks.json` untouched).
5. **Write `~/.cursorscope/.env`** with metrics endpoint, headers, service
   identity, and content-off privacy flags.
6. **Restart the local ingestor** on port 4327.

After setup: **restart Cursor**, open this repo as the workspace, send one
Agent turn, then run `--check` and query CloudWatch.

### The generated `.env` (shape)

```bash
OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=https://monitoring.us-east-1.amazonaws.com/v1/metrics
OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer <token>

OTEL_SERVICE_NAME=cursor
OTEL_SERVICE_NAMESPACE=a2a-lab
DEPLOYMENT_ENVIRONMENT=congmingwudi/a2a-lab
OTEL_RESOURCE_ATTRIBUTES=tool=cursor,project=a2a-lab,repo=congmingwudi/a2a-lab

CURSOR_LOG_USER_PROMPTS=false
CURSOR_LOG_TOOL_DETAILS=false
CURSOR_MASK_USER_EMAIL=true
```

cursorscope maps `service.name`, `service.namespace`, and
`deployment.environment` into OTLP resource attributes (`@resource.*` in
CloudWatch's PromQL store). `OTEL_RESOURCE_ATTRIBUTES` is also set for backends
that merge env resource attrs; cursorscope's Node SDK builds its resource block
primarily from the service.* keys above.

**Credential tradeoff:** Claude Code never stores the bearer token on disk;
Codex holds it for one process lifetime; Cursor/cursorscope writes it to
`~/.cursorscope/.env` because the ingestor is a long-lived daemon with no
helper-hook equivalent. Re-run `scripts/cursor_otel.sh` when the service
credential rotates (~90 days). The token is not checked into this repository.

---

## 4. Project hooks — `.cursor/hooks.json`

Checked-in hooks register cursorscope on every Agent lifecycle event the lab
cares about for build telemetry:

| Hook event | Role |
|---|---|
| `sessionStart` / `sessionEnd` | Session counters |
| `beforeSubmitPrompt` / `stop` | Prompt / generation spans |
| `preToolUse` / `postToolUse` / `postToolUseFailure` | Tool execution metrics |
| `beforeShellExecution` / `afterShellExecution` | Shell activity |
| `beforeMCPExecution` / `afterMCPExecution` | MCP calls |
| `subagentStart` / `subagentStop` | Task/subagent fan-out |
| `afterAgentResponse` / `afterAgentThought` | Response lifecycle |
| `afterFileEdit` / `beforeReadFile` | File operations |

Each entry calls `.cursor/hooks/forward.sh`, which delegates to
`~/.cursorscope/scripts/cursorscope-forward.sh` when cursorscope is installed.

- Logs: `~/.cursor/cursorscope.log`
- Health: `http://127.0.0.1:4327/healthz`
- OTLP probe: `http://127.0.0.1:4327/debug/otlp-probe`

---

## 5. What lands in CloudWatch

cursorscope emits hook-derived metrics, not Claude Code's native schema. Expect
names along these lines (exact set depends on cursorscope version):

| Metric (examples) | Meaning |
|---|---|
| `cursor_hook_events_total` | Hook events by type |
| `cursor_session_total` | Agent sessions |
| `cursor_prompt_total` | Prompt submissions |
| `cursor_tool_executions_total` | Tool calls |
| `gen_ai.client.operation.duration` | Operation latency histogram |
| `gen_ai.client.token.usage` | Token estimates where hook payload exposes them |

Query with the same PromQL surface as Claude Code metrics — see
`src/observability/promql.py`. Example acceptance filter after one Agent turn:

```promql
{__name__=~"cursor_.*|gen_ai.*", service.name="cursor"}
```

**Console gap:** `src/observability/coding_source.py` discovers `claude_code.*`
and `codex.*` prefixes today. Cursor series can arrive in CloudWatch before they
appear in the Coding Agents Telemetry section; extending the harvest is a
separate follow-up.

---

## 6. Acceptance test

Same rule as the Claude/Codex note: **query the destination.**

1. `aws sso login` (or whatever refreshes the profile in `.env`).
2. `scripts/cursor_otel.sh`
3. Restart Cursor; open **`a2a-lab`** as workspace root.
4. Send one Agent message (any prompt).
5. `scripts/cursor_otel.sh --check` — expect token OK, ingestor healthy,
   metrics probe `ok: true` with HTTP 200 against the CloudWatch endpoint.
6. PromQL query above returns at least one series with `service.name="cursor"`
   (or `@resource.tool="cursor"` if resource attrs merge as expected).
7. Optional: `uv run python scripts/obs_harvest.py coding` once the reader
   knows how to discover `cursor_*` names.

Until step 6 returns your labels, treat the path as **configured, not proven**
— the header on `scripts/cursor_otel.sh` says so explicitly.

---

## 7. Comparison with the Claude logs wrapper

`scripts/claude_otel.sh` is a **behavioural logs** opt-in on top of metrics
that are already on via settings. There is no Cursor equivalent wired in this
lab yet. CloudWatch **logs** OTLP requires a separate credential
(`logs.amazonaws.com`, secret `a2alab/telemetry/cw-logs-api-key`) and the
`x-aws-log-group` / `x-aws-log-stream` headers — see
`scripts/setup_cw_logs_otlp.py` and the Claude note § behavioural logs. A
future Cursor logs path would be a second hook exporter or a cursorscope logs
endpoint configuration, not a change to the metrics setup above.

---

## Evidence and limits

- **Vendor-documented:** Cursor hooks schema and events (Cursor docs / hook
  skill in this environment); cursorscope OTLP export and env configuration
  ([cursorscope README](https://github.com/last9/cursorscope)); Cursor forum
  confirmation that first-party OTEL is not shipped.
- **Repository-backed:** `scripts/cursor_otel.sh`, `.cursor/hooks.json`,
  `.cursor/hooks/forward.sh`, shared `scripts/otel_headers.sh`.
- **Measured 2026-07-31:** `scripts/cursor_otel.sh` setup on this machine;
  `scripts/cursor_otel.sh --check` returned metrics OTLP probe HTTP 200 against
  `https://monitoring.us-east-1.amazonaws.com/v1/metrics`; traces/logs probes to
  localhost failed as expected.
- **Still open:** End-to-end proof that hook-derived `cursor_*` / `gen_ai.*`
  series land with the intended `@resource.project` / `@resource.repo` labels
  after a real Agent session and survive harvest into the console. Token
  refresh over multi-day sessions without re-running setup. Parity of token/cost
  numbers with Claude Code's native `claude_code.token.usage` buckets.

---

## Put this in the presentation

**Slide headline:** No native OTEL means the hook chain is the product.

- Cursor exposes lifecycle hooks, not exporters — cursorscope fills the gap.
- Same CloudWatch metrics endpoint and credential family as Claude Code/Codex;
  different metric names and weaker spend parity.
- Prove labels at the destination; local hook install is not enough.

**Visual:** three-column comparison (Claude native / Codex wrapper / Cursor
hooks → cursorscope → CloudWatch), with Cursor's column marked *behaviour
metrics yes, native billing buckets no*.

---

**In this repo:** `scripts/cursor_otel.sh`, `.cursor/hooks.json`,
`.cursor/hooks/forward.sh`, `scripts/otel_headers.sh` (shared metrics token),
[build-notes/claude/08-coding-agent-telemetry.md](../claude/08-coding-agent-telemetry.md)
(shared endpoint and failure modes), `src/observability/coding_source.py`
(harvest — Cursor prefixes not yet included).
