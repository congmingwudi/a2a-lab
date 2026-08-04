# Kiro → CloudWatch OTLP metrics — configuration record

**Status: wired, Path H (hook forwarder).** Built 2026-08-04 in a Kiro session.
Probes 1–3 from `01` remain unrun; this takes the safe assumption (hooks +
direct OTLP emit) and delivers sessions + tools + file-ops immediately. If a
native path surfaces later, the hooks can be disabled with one JSON edit.

## What was built

Kiro has no native OTEL exporter. The integration uses the same pattern as
Cursor (`.cursor/hooks/` → cursorscope → CloudWatch), minus the separate
ingestor process: Kiro hooks are simple commands, so `forward.sh` emits OTLP
JSON directly to the CloudWatch managed metrics endpoint via a backgrounded
`curl`. No collector, no sidecar, no extra process.

### Files created

| File | Purpose |
|---|---|
| `.kiro/hooks/otel-forward.json` | Hook config — fires on 8 triggers (session, tool, file, prompt, task) |
| `.kiro/hooks/forward.sh` | Fire-and-forget forwarder — sources `.env`, maps trigger → metric name, emits one OTLP Sum datapoint per invocation, exits 0 with empty stdout |
| `scripts/kiro_otel.sh` | Setup/check script — fetches the CloudWatch bearer token via `scripts/otel_headers.sh`, writes `.kiro/hooks/.env` |

### Files modified

| File | Change |
|---|---|
| `src/observability/coding_source.py` | Added `KIRO_METRICS` tuple (9 metric names), `"kiro": "kiro_"` in `TOOL_PREFIXES`, included in `CUMULATIVE_METRICS` and `metric_names()` |
| `.gitignore` | Excludes `.kiro/hooks/.env` (contains the bearer token) |

### Metric names emitted

All are cumulative monotonic Sums (same temporality as Cursor's cursorscope
counters). The harvester queries them with `increase()`.

| Metric | Trigger | Signal |
|---|---|---|
| `kiro_session_total` | `SessionStart` | session count |
| `kiro_session_end_total` | `Stop` | session duration (pair with start) |
| `kiro_prompt_total` | `UserPromptSubmit` | prompt count |
| `kiro_tool_executions_total` | `PostToolUse` | tool use count |
| `kiro_task_executions_total` | `PostTaskExec` | task execution count |
| `kiro_file_saves_total` | `PostFileSave` | edit-acceptance (WS16) |
| `kiro_file_creates_total` | `PostFileCreate` | edit-acceptance (WS16) |
| `kiro_file_deletes_total` | `PostFileDelete` | edit-acceptance (WS16) |
| `kiro_hook_events_total` | fallback | catch-all for unmapped triggers |

### Resource attributes on every datapoint

```
service.name = kiro
tool          = kiro
project       = <derived from git remote>
repo          = <owner/name from git remote>
```

These align with the existing `@resource.tool` / `@resource.repo` /
`@resource.project` labels the harvester reads for Claude Code, Codex and
Cursor.

## How to activate (operator steps)

```bash
# 1. Ensure an AWS session is live
aws sso login

# 2. Run the setup script (fetches token, writes .kiro/hooks/.env)
scripts/kiro_otel.sh

# 3. Verify
scripts/kiro_otel.sh --check

# 4. After a few hook firings, confirm metrics landed in CloudWatch
uv run python scripts/obs_harvest.py coding
```

No Kiro restart is needed — `forward.sh` sources `.env` on every invocation, so
a running session picks up the credentials immediately. If the hooks themselves
are not firing, reload the Kiro window once (command palette → "Reload Window")
so it reads the new `.kiro/hooks/otel-forward.json`.

Re-run `scripts/kiro_otel.sh` when the CloudWatch bearer token rotates (~90
days). Same cadence as `scripts/cursor_otel.sh`.

## Coverage — what's measured, what's not

| Signal | Status | Notes |
|---|---|---|
| Sessions | ✅ | `SessionStart` / `Stop` pair |
| Tool executions | ✅ | `PostToolUse` |
| File operations (WS16) | ✅ | `PostFileSave/Create/Delete` — Kiro's advantage over Cursor/Codex |
| Prompts | ✅ | `UserPromptSubmit` |
| Tokens | ❌ | Hook payload does not expose token counts (Probe 2 unresolved) |
| Cost | ❌ | No tokens → no cost modelling |
| Model | ❌ | Hook payload does not expose model name (Probe 2 unresolved) |

The console renders `n/a` for the missing columns, same as it already does for
Cursor and Codex cost.

## Design choices

1. **No ingestor process.** Cursor needs cursorscope because its hooks deliver
   a JSON blob on stdin that must be parsed and batched. Kiro hooks are plain
   commands with no documented stdin payload — the entire signal is the trigger
   name. A single `curl` per invocation is cheaper than running a Node process.

2. **Backgrounded curl.** The `curl` is `&`-detached so the hook returns
   instantly. The 5s hook timeout in the JSON is a safety net, not the expected
   duration.

3. **Empty stdout, always exit 0.** Kiro's hook contract says stdout is added
   to agent context for `SessionStart` / `UserPromptSubmit`. A telemetry
   forwarder must never inject text — it would pollute every session.

4. **Cumulative temporality.** Each datapoint is a +1 increment, matching
   cursorscope's counters and the `CUMULATIVE_METRICS` set in `coding_source.py`.
   The harvester uses `increase()` (not `sum_over_time()`) for these.

5. **Token in .env, not committed.** Same posture as the Cursor equivalent
   (`~/.cursorscope/.env`). The `.gitignore` entry ensures `git add -A` cannot
   leak it.

## Relation to Probe 2 (hook payload)

If a future Kiro version exposes model/tokens via environment variables or stdin
to the hook command, `forward.sh` can be extended to read them and add
dimensions to the datapoint (a `model` attribute on the Sum, plus new
`kiro_token_usage_total` metrics). The harvester already handles per-model
breakdowns via the `by_model` dict in `summarize_series`. No structural change
needed — just more attributes on the same OTLP shape.

## Where Kiro now sits

| Tool | OTEL mechanism | Cost | Tokens | Model | Sessions/tools | File-ops (WS16) |
|---|---|---|---|---|---|---|
| Claude Code | Native exporter | ✅ | ✅ | ✅ | ✅ | ❌ |
| Codex | Native exporter | ❌ | ~ | ✅ | ✅ | ❌ |
| Cursor | cursorscope hooks | ❌ | ~ | mostly `default` | ✅ | ❌ |
| **Kiro** | **direct hooks → OTLP** | ❌ | ❌ | ❌ | **✅** | **✅** |
