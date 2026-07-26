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

**Namespaces are discovered, not hardcoded.** OTLP metrics arriving at
CloudWatch are grouped under a namespace derived from the exporter's resource
attributes, which depends on how the tools are configured and has changed
between releases. Rather than guess, this source calls ListMetrics and keeps
whatever namespace actually carries metrics named `claude_code.*` / `codex.*` —
the same discover-don't-hardcode posture as the Salesforce source's
`SELECT FIELDS(ALL)`. Set A2ALAB_CODING_NAMESPACES to skip discovery.

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
PERIOD_S = 86400  # daily buckets — this is a build-cost view, not a live trace

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
COST_SUFFIX = "cost.usage"
TOKEN_SUFFIX = "token.usage"
SESSION_SUFFIX = "session.count"
ACTIVE_TIME_SUFFIX = "active_time.total"


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

    Each row: {metric, tool, dimensions: {...}, timestamp: datetime, value: float}
    Returns {"<tool>:<YYYY-MM-DD>": {tool, date, cost_usd, tokens{}, sessions,
             active_time_s, by_model{}, metrics{}}}
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
                "metrics": defaultdict(float),
            },
        )
        metric = row.get("metric", "")
        value = float(row.get("value") or 0)
        dims = row.get("dimensions") or {}
        model = dims.get("model")

        b["metrics"][metric] += value
        suffix = _suffix_of(metric)
        if suffix == COST_SUFFIX:
            b["cost_usd"] += value
            if model:
                b["by_model"][model]["cost_usd"] += value
        elif suffix == TOKEN_SUFFIX:
            # `type` is input / output / cacheRead / cacheCreation
            b["tokens"][dims.get("type", "unknown")] += int(value)
            if model:
                b["by_model"][model]["tokens"] += value
        elif suffix == SESSION_SUFFIX:
            b["sessions"] += int(value)
        elif suffix == ACTIVE_TIME_SUFFIX:
            b["active_time_s"] += value

    # defaultdicts are awkward to serialize and to assert on; flatten them.
    for b in buckets.values():
        b["tokens"] = dict(b["tokens"])
        b["metrics"] = dict(b["metrics"])
        b["by_model"] = {m: dict(v) for m, v in b["by_model"].items()}
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
    """CloudWatch-backed coding-agent telemetry.

    `client` is injectable so tests run against canned payloads; in production
    it is a boto3 CloudWatch client built from the AWS auth that D39 made the
    lab's single human login. No new credential is required — the first source
    to land under that rule needing none.
    """

    name = "coding"

    def __init__(self, client: Any = None, namespaces: list[str] | None = None):
        self._client = client
        self._namespaces = namespaces

    # ---- discovery ---------------------------------------------------------

    def discover_namespaces(self, client: Any) -> list[str]:
        configured = os.environ.get("A2ALAB_CODING_NAMESPACES")
        if configured:
            return [n.strip() for n in configured.split(",") if n.strip()]
        found: set[str] = set()
        paginator_pages = client.list_metrics()
        pages = paginator_pages if isinstance(paginator_pages, list) else [paginator_pages]
        for page in pages:
            for metric in page.get("Metrics", []):
                if _tool_of(metric.get("MetricName", "")):
                    found.add(metric["Namespace"])
        return sorted(found)

    def _metric_rows(self, client: Any, namespaces: list[str]) -> list[dict[str, Any]]:
        """Every coding-agent datapoint in the window, flattened."""
        end = dt.datetime.now(dt.timezone.utc)
        start = end - dt.timedelta(days=WINDOW_DAYS)
        rows: list[dict[str, Any]] = []
        for namespace in namespaces:
            listed = client.list_metrics(Namespace=namespace)
            pages = listed if isinstance(listed, list) else [listed]
            metrics = [m for page in pages for m in page.get("Metrics", [])]
            for metric in metrics:
                name = metric.get("MetricName", "")
                tool = _tool_of(name)
                if not tool:
                    continue
                dims = {d["Name"]: d["Value"] for d in metric.get("Dimensions", [])}
                stats = client.get_metric_statistics(
                    Namespace=namespace,
                    MetricName=name,
                    Dimensions=metric.get("Dimensions", []),
                    StartTime=start,
                    EndTime=end,
                    Period=PERIOD_S,
                    Statistics=["Sum"],
                )
                for point in stats.get("Datapoints", []):
                    rows.append(
                        {
                            "metric": name,
                            "tool": tool,
                            "dimensions": dims,
                            "timestamp": point.get("Timestamp"),
                            "value": point.get("Sum", 0),
                        }
                    )
        return rows

    # ---- harvest -----------------------------------------------------------

    def harvest(self, store: ObsStore) -> HarvestResult:
        client = self._client
        if client is None:
            try:
                import boto3

                region = os.environ.get("AWS_REGION") or os.environ.get(
                    "AWS_DEFAULT_REGION", "us-east-1"
                )
                client = boto3.client("cloudwatch", region_name=region)
            except Exception as exc:
                result = HarvestResult(
                    platform=self.name,
                    status="blocked",
                    detail=f"no CloudWatch client ({type(exc).__name__}) — AWS auth required",
                )
                store.set_harvest_status(self.name, result.status, result.detail)
                return result

        try:
            namespaces = self._namespaces or self.discover_namespaces(client)
            if not namespaces:
                result = HarvestResult(
                    platform=self.name,
                    status="blocked",
                    detail=(
                        "no CloudWatch metrics named claude_code.* or codex.* — enable the "
                        "exporters (see the Build Telemetry section) and allow one export "
                        "interval; telemetry is not retroactive"
                    ),
                )
                store.set_harvest_status(self.name, result.status, result.detail)
                return result
            rows = self._metric_rows(client, namespaces)
        except Exception as exc:
            result = HarvestResult(
                platform=self.name, status="error", detail=f"{type(exc).__name__}: {exc}"[:300]
            )
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
            f"build cost at list price (client-side estimate, not an invoice) · "
            f"namespaces: {', '.join(namespaces)}"
        )
        store.set_harvest_status(self.name, result.status, result.detail)
        return result
