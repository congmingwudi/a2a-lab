# Cross-tool cost comparison — why only Claude Code gets dollars

The Coding Agents Telemetry section compares three tools that all export to the
same CloudWatch managed OTLP endpoint. They do **not** publish the same metric
shapes, and the console is honest about that: tools without a cost metric show
**n/a**, not `$0.00`. A zero would read as "this tool was free"; **n/a** reads
as "we cannot see it."

This note is the full comparison behind that UI choice. Cross-read
[build-notes/claude/08-coding-agent-telemetry.md](../claude/08-coding-agent-telemetry.md)
for the shared endpoint, PromQL read path, and silent-failure modes;
[01-coding-agent-telemetry.md](01-coding-agent-telemetry.md) for Cursor's hook
pipeline.

## Engineering takeaway

**Cost is not withheld — it is unavailable.** Claude Code's native exporter
publishes billable token buckets as delta **Sums** plus a client-side USD
estimate. Codex and Cursor publish neither a cost metric nor token data in a
shape this harvest can turn into a scalar. Sessions and activity counters are
the honest metrics for those two until histogram support or a price-table path
lands.

---

## 1. Three telemetry shapes side by side

| | Claude Code | Codex CLI | Cursor |
|---|---|---|---|
| **OTEL mechanism** | Native exporter + `otelHeadersHelper` | Native exporter (three separate exporters) | **None** — lifecycle hooks → [cursorscope](https://github.com/last9/cursorscope) |
| **Setup** | `.claude/settings.local.json`; optional `scripts/claude_otel.sh` for behavioural logs | `scripts/codex_otel.sh` at every launch | `scripts/cursor_otel.sh` once + checked-in `.cursor/hooks.json` |
| **Cost metric** | `claude_code.cost.usage` (USD estimate) | **None** | **None** |
| **Token metric** | `claude_code.token.usage` — delta **Sum**, 4 buckets (`input`, `cacheRead`, `cacheCreation`, `output`) | `codex.turn.token_usage` — delta **Histogram** by `token_type` | `gen_ai.client.token.usage` — **Histogram** (when hook payload exposes tokens) |
| **Sessions** | `claude_code.session.count` (delta Sum) | `codex.thread.started` (delta Sum) | `cursor_session_total` (cumulative counter → `increase()`) |
| **What the Run tab shows** | Cost, all four token buckets, sessions, active time | Sessions and turns only (`n/a` cost/tokens) | Sessions and activity counters only (`n/a` cost/tokens) |
| **Attribution** | `@resource.tool` / `repo` / `project` via `OTEL_RESOURCE_ATTRIBUTES` | Same via `scripts/codex_otel.sh` at launch | `@resource.service.name` / `service.namespace` / `deployment.environment` — cursorscope does not emit the primary labels (D64) |

All three share `scripts/otel_headers.sh` for the CloudWatch **metrics** bearer
token and the same endpoint:
`https://monitoring.<region>.amazonaws.com/v1/metrics`.

---

## 2. Why Claude Code is the only tool with dollars today

Claude Code does two things the other two do not:

1. **Publishes a cost metric.** `claude_code.cost.usage` is a client-side USD
   estimate computed from token counts at list prices — not an invoice, and on
   subscription or credit plans not money that changed hands. The console and
   cost sentinel lead with that caveat (D44).

2. **Publishes tokens as delta Sums in four buckets.** Each bucket bills at a
   different multiple (~1× uncached input, ~0.1× cache read, 1.25–2× cache
   write, output rate). The harvest queries them with `sum_over_time()` and the
   arithmetic in `coding_source.py` rolls them up per (tool, day).

That combination is what makes "modelled build cost" meaningful in the Run tab.

---

## 3. Why Codex cost is not estimated (yet)

Codex's native exporter is real and attributed, but its schema diverges from
Claude Code's in ways that block the same arithmetic:

- **No `codex.cost.usage`.** Cross-tool cost would have to be *modelled* from
  tokens and a price table — the same path Cursor would need.
- **`codex.turn.token_usage` is a Histogram**, not a Sum. CloudWatch's PromQL
  surface returns the series but **no scalar** for a histogram on `sum_over_time`
  / `increase()` — verified live 2026-07-26. The harvest cannot consume it
  today.
- **What IS wired:** the Sums `codex.thread.started` and
  `codex.conversation.turn.count`.

Earlier versions of this lab queried invented names (`codex.cost.usage`,
`codex.token.usage`) mirrored from Claude Code's schema. They never existed and
returned nothing forever while the exporter worked — the same silent failure
mode as pointing ListMetrics at OTLP metrics.

---

## 4. Why Cursor cost is not estimated (yet)

Cursor is one step further from billable telemetry than Codex:

### No native exporter — hooks are not a bill

Cursor exposes lifecycle hooks (`beforeSubmitPrompt`, `postToolUse`, `sessionEnd`,
…), not an OTEL exporter. cursorscope turns hook JSON into OTLP **metrics**:
sessions, prompts, tool executions, hook events. That is **behaviour telemetry**
— it describes what the Agent did, not Cursor's internal token accounting or
invoice data.

Cursor's forum confirms first-party OTEL remains on the backlog; every working
integration today intercepts hook events.

### No cost metric, no consumable token Sum

cursorscope may emit `gen_ai.client.token.usage` when the hook payload exposes
token estimates. Two blockers:

1. **Histogram shape** — same PromQL scalar problem as Codex. This pipeline
   cannot roll histograms into daily totals without new query logic.
2. **Estimate quality** — even if extracted, hook-side token figures lack Claude
   Code's four-bucket cache split. Cache mix dominates agent-session cost; a
   single total multiplied by a list price would be fiction.

What the harvest reads today (`CURSOR_METRICS` in `coding_source.py`, D64):

| Metric | Role |
|---|---|
| `cursor_hook_events_total` | Every lifecycle hook — proves the pipe is live |
| `cursor_session_total` | Agent sessions |
| `cursor_prompt_total` | Prompt submissions |
| `cursor_tool_executions_total` | Tool calls |
| `cursor_lines_of_code_total`, `cursor_mcp_invocations_total`, … | Activity counters (best-effort by cursorscope version) |

These are **cumulative** counters (`_total` suffix,
`OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative`), so the harvest
uses `increase()`, not `sum_over_time()` — summing a cumulative counter adds
the running total at every step and over-counts by orders of magnitude.

### Subscription pricing is a third gap

Even a perfect token estimate would not match Cursor Pro/Business flat-rate
billing. Claude Code's estimate is explicitly "modelled at list price"; Cursor
subscription economics are a different contract entirely.

---

## 5. What the lab refuses to do

From the cost sentinel's system prompt and the console Details pane (WS12/D44):

- **Never state a dollar figure not traced to a query** against real harvested
  data.
- **Never present a combined cross-tool total** as if it covered all three tools.
  A combined dollar line would be Claude Code's cost wearing Codex and Cursor's
  names.
- **Render `n/a`, not `$0.00`**, where a tool publishes no such measure.
- **Lead with the list-price caveat** once per brief — cost movement is usage
  movement; it may not be a billing movement.

The Behaviour tab (`coding-logs`) is Claude Code-only by design (WS16/D59).
Cursor has no equivalent OTLP logs path wired in this lab.

---

## 6. What it would take to estimate Codex or Cursor cost

Not impossible — just not implemented, and the lab will not ship a number until
the path is honest:

| Step | Codex | Cursor |
|---|---|---|
| Obtain token counts in a summable form | Teach harvest to read `codex.turn.token_usage` Histogram, or exporter emits Sums | Same for `gen_ai.*`, or cursorscope adds Sum exporters |
| Model + bucket labels on every datapoint | Already on some series | Hook payloads — version-dependent |
| Apply list-price table | New module; label **modelled**, not invoice | Same |
| Cache bucket split | Codex histogram has `token_type` — closer than Cursor | Cursor hook estimates likely lack cache read/write split |

Until then: **sessions and activity for Codex and Cursor; dollars for Claude
Code only.**

---

## Evidence and limits

- **Repository-backed:** `src/observability/coding_source.py` (`CLAUDE_METRICS`,
  `CODEX_METRICS`, `CURSOR_METRICS`, `CUMULATIVE_METRICS`, `_metric_rows`),
  `scripts/setup_cost_sentinel.py` (system prompt rules 2–3),
  `src/console/app.py` (`BUILD_TELEMETRY_TOOL_NOTES`).
- **Measured:** Claude Code and Codex metric shapes live 2026-07-26; Cursor
  harvest end-to-end with service.* attribution 2026-07-31 (D64).
- **Still open:** Histogram scalar extraction for Codex/Cursor tokens; price-table
  modelling; Cursor behavioural logs.

---

## Put this in the presentation

**Slide headline:** Same endpoint, three different honesty levels.

- Claude Code: native billing buckets → modelled dollars (with caveat).
- Codex: native exporter, no cost metric, histogram tokens not consumable yet.
- Cursor: hook-derived activity, no billing telemetry at all.

**Visual:** three-column table (this doc §1) with the Cost and Token rows
highlighted — only Claude Code's column is green for both.

---

**In this repo:** `build-notes/cursor/02-cross-tool-cost-comparison.md`,
`build-notes/claude/08-coding-agent-telemetry.md`, `plan/00-decisions.md` D64,
`src/observability/coding_source.py`, `src/console/app.py`
(`BUILD_TELEMETRY_COMPARISON_MD`).
