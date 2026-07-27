"""Coding-agent telemetry source — what the lab cost to build (WS9).

Claude Code and the Codex CLI both export OpenTelemetry, and CloudWatch accepts
OTLP directly on a managed endpoint (no collector, no sidecar). Point both tools
at it and their metrics land as CloudWatch metrics; this source reads them back
into the obs store so the console can show the lab's own construction cost
beside the agent telemetry it already collects.

Deliberately NOT a sixth platform column. The Observability coverage panel
answers "what did the agents do, per platform", and its honesty depends on all
five columns being the same kind of thing. Claude Code is not a partner agent
platform — it is the tool that built the lab. It gets its own console section
and shares only the harvest plumbing and the store.

**Read via PromQL, not ListMetrics.** OTLP metrics ingested through
CloudWatch's native endpoint do not appear in the classic
`ListMetrics`/`GetMetricStatistics` APIs at all — they land in a
Prometheus-compatible store (see `observability/promql.py`). The first version
of this file used ListMetrics and would have reported "no coding metrics yet"
forever while the exporters worked perfectly: ingestion returns HTTP 200 and
discovery returns nothing, so both halves look healthy in isolation.

**Metric names are a fixed list, because the API offers no enumeration.**
`/api/v1/label/__name__/values` is unsupported and a selector without a metric
name is rejected, so there is no way to ask "what coding metrics exist". The
Claude Code names come from its published OTel schema; Codex's are best-effort
and extendable via A2ALAB_CODING_METRICS.

Cost honesty: `claude_code.cost.usage` is a **client-side USD estimate computed
from token counts at list prices**, not an invoice, and on subscription or
credit plans it is not money that changed hands. Anything published from it must
say "modelled build cost at list price".
"""

from __future__ import annotations

import datetime as dt
import os
from collections import defaultdict
from typing import Any

from observability.base import HarvestResult, PlatformLogSource
from observability.store import ObsStore

WINDOW_DAYS = int(os.environ.get("A2ALAB_CODING_WINDOW_DAYS", "7"))

# Query step. NOT the reporting granularity — the daily rollup happens in
# summarize_series, in Python. This is 5 minutes because CloudWatch aligns a
# range query's evaluation points to epoch multiples of the step, so a
# daily-stepped query's last point is last midnight and everything since is
# invisible. Measured 2026-07-26: the same query returned 0 series at step
# 86400/3600/900/600 and 4 series at step 300, purely because the only
# telemetry that existed was 24 minutes old. A build-cost view that cannot see
# today reads exactly like an exporter that is switched off — which is how this
# was found, days after the exporters started working.
PERIOD_S = 300

# There is no metric enumeration on the PromQL surface, so the names are a
# list. Claude Code's are from its published OTel schema; Codex's mirror them
# on its own prefix and are best-effort until observed live.
CLAUDE_METRICS = (
    "claude_code.cost.usage",
    "claude_code.token.usage",
    "claude_code.session.count",
    "claude_code.active_time.total",
    "claude_code.lines_of_code.count",
    "claude_code.commit.count",
    "claude_code.pull_request.count",
    "claude_code.code_edit_tool.decision",
)
# OBSERVED LIVE 2026-07-26 (not mirrored from Claude Code's schema, which is
# what the previous three names here were — `codex.cost.usage`,
# `codex.token.usage` and `codex.session.count` do not exist and never
# returned a series). Codex names its metrics on its own scheme, and the
# differences are not cosmetic:
#
#   - There is NO cost metric. Claude Code reports `cost.usage` in USD; Codex
#     reports nothing equivalent, so cross-tool cost has to be modelled from
#     tokens and a price table.
#   - `codex.turn.token_usage` is a delta HISTOGRAM dimensioned by
#     `token_type` (input / cached_input / cache_write_input / output /
#     reasoning / tool), where Claude Code's `token.usage` is a delta SUM
#     dimensioned by `type`. sum_over_time returns the series but no scalar
#     for a histogram on this surface, so tokens are deliberately NOT wired up
#     here yet — see the note in build-notes/claude/08.
#
# Only the Sums are listed: they are the ones this module's arithmetic can
# consume correctly today.
CODEX_METRICS = (
    "codex.thread.started",
    "codex.conversation.turn.count",
)


def metric_names() -> tuple[str, ...]:
    extra = os.environ.get("A2ALAB_CODING_METRICS", "")
    names = list(CLAUDE_METRICS) + list(CODEX_METRICS)
    names += [n.strip() for n in extra.split(",") if n.strip()]
    return tuple(dict.fromkeys(names))


# OTel resource attributes flatten to `@resource.<attr>`; datapoint attributes
# stay bare. `tool` is set through OTEL_RESOURCE_ATTRIBUTES on each exporter, so
# it is the resource-scoped one.
TOOL_LABEL = "@resource.tool"
REPO_LABEL = "@resource.repo"
PROJECT_LABEL = "@resource.project"

# Datapoints that predate the exporter being attributed — or that come from a
# checkout with no git remote — carry no repo label. They are kept and shown
# under this name rather than dropped: an unattributed cost is still a cost,
# and hiding it would make the per-repo view silently disagree with the total.
UNATTRIBUTED = "unattributed"

# Metric-name prefixes that identify coding-agent telemetry, per tool.
TOOL_PREFIXES = {
    "claude-code": "claude_code.",
    "codex": "codex.",
}

# Metric SUFFIXES, matched after the tool prefix is stripped. Suffixes rather
# than full names on purpose: the names are documented for Claude Code
# (`claude_code.cost.usage`) but Codex's exporter uses its own prefix, and an
# earlier version of this file compared full names and silently scored every
# Codex metric as zero. Anything whose suffix is unrecognised still lands in
# `metrics` verbatim rather than being dropped.
# Tuples rather than single strings because the two tools name the same
# concept differently: a Claude Code session is `session.count`, a Codex one is
# `thread.started`. Anything unmatched still lands in `metrics` verbatim.
COST_SUFFIX = ("cost.usage",)
TOKEN_SUFFIX = ("token.usage",)
SESSION_SUFFIX = ("session.count", "thread.started")
ACTIVE_TIME_SUFFIX = ("active_time.total",)


def _tool_of(metric_name: str) -> str | None:
    for tool, prefix in TOOL_PREFIXES.items():
        if metric_name.startswith(prefix):
            return tool
    return None


def _suffix_of(metric_name: str) -> str:
    """The metric name with its tool prefix removed ('' if it has none)."""
    for prefix in TOOL_PREFIXES.values():
        if metric_name.startswith(prefix):
            return metric_name[len(prefix) :]
    return ""


def summarize_series(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Roll raw datapoints into one bucket per (tool, day).

    Pure function so the arithmetic is testable without AWS — the same reason
    `adk_source.summarize_metrics` exists.

    Each row: {metric, tool, repo, project, dimensions: {...},
               timestamp: datetime, value: float}
    Returns {"<tool>:<YYYY-MM-DD>": {tool, date, cost_usd, tokens{}, sessions,
             active_time_s, by_model{}, by_repo{}, metrics{}}}

    The bucket key stays (tool, day) rather than becoming (tool, repo, day):
    that keeps `native_id` stable for rows already in the store, and the
    per-repo split rides inside the bucket as `by_repo`. Same numbers, one
    level finer, no migration.
    """
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        tool = row.get("tool") or _tool_of(row.get("metric", "")) or "unknown"
        ts = row.get("timestamp")
        day = ts.strftime("%Y-%m-%d") if isinstance(ts, dt.datetime) else str(ts)[:10]
        key = f"{tool}:{day}"
        b = buckets.setdefault(
            key,
            {
                "tool": tool,
                "date": day,
                "cost_usd": 0.0,
                "tokens": defaultdict(int),
                "sessions": 0,
                "active_time_s": 0.0,
                "by_model": defaultdict(lambda: defaultdict(float)),
                "by_repo": {},
                "metrics": defaultdict(float),
            },
        )
        metric = row.get("metric", "")
        value = float(row.get("value") or 0)
        dims = row.get("dimensions") or {}
        model = dims.get("model")

        repo = row.get("repo") or UNATTRIBUTED
        r = b["by_repo"].setdefault(
            repo,
            {
                "repo": repo,
                "project": row.get("project") or UNATTRIBUTED,
                "cost_usd": 0.0,
                "tokens": defaultdict(int),
                "sessions": 0,
                "active_time_s": 0.0,
            },
        )
        # A repo can legitimately report several project names over time (a
        # rename, or two checkouts labelled differently). Keep the first real
        # one rather than letting "unattributed" overwrite it.
        if r["project"] == UNATTRIBUTED and (row.get("project") or UNATTRIBUTED) != UNATTRIBUTED:
            r["project"] = row["project"]

        b["metrics"][metric] += value
        suffix = _suffix_of(metric)
        if suffix in COST_SUFFIX:
            b["cost_usd"] += value
            r["cost_usd"] += value
            if model:
                b["by_model"][model]["cost_usd"] += value
        elif suffix in TOKEN_SUFFIX:
            # `type` is input / output / cacheRead / cacheCreation
            b["tokens"][dims.get("type", "unknown")] += int(value)
            r["tokens"][dims.get("type", "unknown")] += int(value)
            if model:
                b["by_model"][model]["tokens"] += value
        elif suffix in SESSION_SUFFIX:
            b["sessions"] += int(value)
            r["sessions"] += int(value)
        elif suffix in ACTIVE_TIME_SUFFIX:
            b["active_time_s"] += value
            r["active_time_s"] += value

    # defaultdicts are awkward to serialize and to assert on; flatten them.
    for b in buckets.values():
        b["tokens"] = dict(b["tokens"])
        b["metrics"] = dict(b["metrics"])
        b["by_model"] = {m: dict(v) for m, v in b["by_model"].items()}
        for r in b["by_repo"].values():
            r["tokens"] = dict(r["tokens"])
    return buckets


def _summary_line(bucket: dict[str, Any]) -> str:
    tokens = bucket["tokens"]
    total_tokens = sum(tokens.values())
    parts = [f"${bucket['cost_usd']:.2f} est."]
    if total_tokens:
        parts.append(f"{total_tokens:,} tokens")
    if bucket["sessions"]:
        parts.append(f"{bucket['sessions']} sessions")
    if bucket["active_time_s"]:
        parts.append(f"{bucket['active_time_s'] / 3600:.1f}h active")
    return " · ".join(parts)


class CodingSource(PlatformLogSource):
    """PromQL-backed coding-agent telemetry.

    `client` is injectable so tests run against canned payloads; in production
    it is a `PromQLClient` signing with the AWS auth that D39 made the lab's
    single human login. No new credential — the first source to land under that
    rule needing none.
    """

    name = "coding"

    def __init__(self, client: Any = None, names: tuple[str, ...] | None = None):
        self._client = client
        self._names = names
        self.query_errors: list[str] = []

    # ---- fetch -------------------------------------------------------------

    def _metric_rows(self, client: Any) -> list[dict[str, Any]]:
        """Every coding-agent datapoint in the window, flattened.

        One range query per metric name, stepped at PERIOD_S; summarize_series
        rolls the points up per day afterwards.

        The aggregation is `sum_over_time(...[<step>])` — window equal to the
        step, so the buckets tile the range exactly once with no overlap and no
        gap. Two forms were measured against the same live data on 2026-07-26
        and both were wrong:

          increase(...[5m])   2,531,607 tokens — under-counts, and silently
                              drops any series with a single sample (increase
                              needs two), so 4 of 8 series vanished
          bare selector       3,160,892 tokens — over-counts, because an
                              instant vector repeats the last sample through
                              its 5-minute lookback at every step
          sum_over_time       2,870,521 tokens — identical at step 60 and 300,
                              which is the property that makes it right: an
                              exact answer does not move when the resolution
                              does

        These are delta-temporality Sums, so each raw datapoint is already a
        delta; summing them is the whole job, and rate-style functions are
        actively harmful on them.
        """
        end = dt.datetime.now(dt.timezone.utc).timestamp()
        start = end - WINDOW_DAYS * 86400
        rows: list[dict[str, Any]] = []
        self.query_errors = []
        for name in self._names or metric_names():
            tool_of_name = _tool_of(name)
            if not tool_of_name:
                continue
            try:
                series = client.query_range(
                    f'sum_over_time({{"{name}"}}[{PERIOD_S}s])', start, end, PERIOD_S
                )
            except Exception as exc:
                # One absent metric must not sink the harvest — most of these
                # names will legitimately not exist. But swallowing the reason
                # is how a query bug becomes "no telemetry yet": remember it, so
                # a harvest that finds nothing can say whether nothing was there
                # or nothing could be asked for.
                self.query_errors.append(f"{name}: {type(exc).__name__}: {exc}"[:200])
                continue
            for entry in series:
                labels = entry.get("metric") or {}
                # the resource attribute wins; fall back to the name's prefix
                tool = labels.get(TOOL_LABEL) or tool_of_name
                dims = {
                    k: v
                    for k, v in labels.items()
                    if not k.startswith("@") and not k.startswith("__")
                }
                for point in entry.get("values") or []:
                    try:
                        ts, raw_value = point[0], float(point[1])
                    except (IndexError, TypeError, ValueError):
                        continue
                    rows.append(
                        {
                            "metric": name,
                            "tool": tool,
                            # Kept out of `dimensions` on purpose: that dict is
                            # the datapoint's own labels (type, model, …) and
                            # is what by_model keys off. repo/project are
                            # RESOURCE attributes describing where the work
                            # happened, so they get their own fields. The
                            # earlier version stripped every "@" label here,
                            # which threw away the per-repo attribution the
                            # exporters were configured to produce.
                            "repo": labels.get(REPO_LABEL) or UNATTRIBUTED,
                            "project": labels.get(PROJECT_LABEL) or UNATTRIBUTED,
                            "dimensions": dims,
                            "timestamp": dt.datetime.fromtimestamp(ts, dt.timezone.utc),
                            "value": raw_value,
                        }
                    )
        return rows

    # ---- harvest -----------------------------------------------------------

    def harvest(self, store: ObsStore) -> HarvestResult:
        client = self._client
        if client is None:
            try:
                from observability.promql import PromQLClient

                client = PromQLClient()
            except Exception as exc:
                result = HarvestResult(
                    platform=self.name,
                    status="blocked",
                    detail=f"no PromQL client ({type(exc).__name__}) — AWS auth required",
                )
                store.set_harvest_status(self.name, result.status, result.detail)
                return result

        try:
            rows = self._metric_rows(client)
        except Exception as exc:
            result = HarvestResult(
                platform=self.name, status="error", detail=f"{type(exc).__name__}: {exc}"[:300]
            )
            store.set_harvest_status(self.name, result.status, result.detail)
            return result

        if not rows:
            detail = (
                "no claude_code.* or codex.* metrics in CloudWatch — switch the "
                "exporters on (see the Build Telemetry section) and allow one export "
                "interval. Telemetry is not retroactive: whatever was built before "
                "collection started cannot be measured afterwards"
            )
            if self.query_errors:
                # An empty result and an unaskable query look identical from
                # here; only this line tells them apart.
                detail += f" · {len(self.query_errors)} query error(s): {self.query_errors[0]}"
            result = HarvestResult(platform=self.name, status="blocked", detail=detail)
            store.set_harvest_status(self.name, result.status, result.detail)
            return result

        buckets = summarize_series(rows)
        result = HarvestResult(platform=self.name, status="ok")
        total_cost = 0.0
        for key, bucket in sorted(buckets.items()):
            total_cost += bucket["cost_usd"]
            store.upsert_session(
                self.name,
                key,
                title=f"{bucket['tool']} · {bucket['date']}",
                status="complete",
                created_at=f"{bucket['date']}T00:00:00+00:00",
                updated_at=f"{bucket['date']}T23:59:59+00:00",
                usage={
                    "input_tokens": bucket["tokens"].get("input", 0),
                    "output_tokens": bucket["tokens"].get("output", 0),
                    "cache_read_input_tokens": bucket["tokens"].get("cacheRead", 0),
                    "cache_creation_input_tokens": bucket["tokens"].get("cacheCreation", 0),
                    "cost_usd_estimated": round(bucket["cost_usd"], 4),
                },
                raw={
                    "tool": bucket["tool"],
                    "by_model": bucket["by_model"],
                    "by_repo": bucket["by_repo"],
                    "metrics": bucket["metrics"],
                    "sessions": bucket["sessions"],
                    "active_time_s": bucket["active_time_s"],
                    "cost_note": "client-side USD estimate at list price, not an invoice",
                },
            )
            result.sessions += 1
            for metric, value in sorted(bucket["metrics"].items()):
                store.upsert_event(
                    self.name,
                    key,
                    f"{key}:{metric}",
                    event_type=metric,
                    processed_at=f"{bucket['date']}T23:59:59+00:00",
                    summary=f"{metric} = {value:g}",
                    raw={"metric": metric, "value": value},
                )
                result.events += 1

        result.detail = (
            f"{len(buckets)} tool-day(s) over {WINDOW_DAYS}d · ${total_cost:.2f} modelled "
            f"build cost at list price (client-side estimate, not an invoice)"
        )
        store.set_harvest_status(self.name, result.status, result.detail)
        return result
