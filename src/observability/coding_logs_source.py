"""Coding-agent BEHAVIOURAL telemetry — what building the lab looked like (WS16, D59).

WS9's `coding_source` reads Claude Code's eight aggregate **metrics** (cost,
tokens, sessions, commits) over PromQL. This is its sibling for the second
signal: the OTLP **log events** Claude Code emits per tool call, per API
request and per prompt. From their metadata — never their content — it derives
a class of insight the metrics cannot express:

  - edit-acceptance rate (how often the human kept the agent's proposed edits)
  - tool mix + MCP usefulness + per-tool latency
  - per-request latency by model
  - reliability: API errors, refusals and retries
  - prompt cadence (how many, how long — length only, never text)

**Content flags are OFF and this source assumes nothing else.** Every field it
reads — `decision`, `tool_name`, `mcp_server_scope`, `success`, `duration_ms`,
the token counts, `status_code`, `attempt`, `prompt_length` — ships regardless
of the content flags (which default off and are never masked, never sent). So
there is no raw prompt, file content or tool argument anywhere in this pipeline
to leak, and the store keeps only the aggregates below.

**Read model differs from metrics.** OTLP metrics land in a Prometheus store
read over PromQL (`promql.py`); OTLP logs land in a CloudWatch **log group**
read over SigV4 `FilterLogEvents` — which boto3's `logs` client signs natively,
so unlike the PromQL path this needs no hand-rolled signer. Ingestion is the
asymmetric half (bearer token, `logs.amazonaws.com` credential, the two
`x-aws-log-*` headers — see `scripts/setup_cw_logs_otlp.py` and
`scripts/claude_otel.sh`); read-back is a plain boto3 call with the same AWS
auth D39 already established as the lab's one human login. No new read secret.

**Aggregates-only, and summable.** Durations and prompt lengths are folded into
fixed-edge histograms rather than kept as raw samples, because histograms add
across days: a window-level p90 is the p90 of the summed buckets, which is
honest, where averaging per-day p90s is not. Means (sum/count) are kept
alongside for the headline numbers.

Deliberately NOT a sixth Observability platform column, for the same reason
`coding_source` isn't (see its module docstring): Claude Code is the tool that
built the lab, not a partner agent platform. It shares the harvest plumbing and
the store, and lands under the DevOps category's Coding Agents Telemetry section
(WS17), never the Observability coverage panel.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from collections import defaultdict
from typing import Any

from observability.base import HarvestResult, PlatformLogSource
from observability.store import ObsStore

# The obs-store platform key for this signal. Distinct from `coding` (the
# metrics) so the two harvest independently and the console reads each on its
# own: same section, different subject, different Harvest scope.
PLATFORM = "coding-logs"

WINDOW_DAYS = int(
    os.environ.get(
        "A2ALAB_CODING_LOGS_WINDOW_DAYS", os.environ.get("A2ALAB_CODING_WINDOW_DAYS", "7")
    )
)

LOG_GROUP = os.environ.get("A2ALAB_CW_LOGS_GROUP", "/a2alab/coding-agents/otlp")
LAB_REGION = "us-east-1"
# Same region-resolution note as promql.py: this laptop exports
# AWS_DEFAULT_REGION=us-west-2 ambiently while .env sets AWS_REGION=us-east-1,
# and boto3 prefers the former — which reads back an empty log group and looks
# exactly like "no telemetry yet". Explicit override, then AWS_REGION, then home.
REGION = os.environ.get("A2ALAB_CW_LOGS_REGION") or os.environ.get("AWS_REGION") or LAB_REGION

# Only these Claude Code events carry the behavioural signal. Codex's log
# schema is not mirrored here yet (its metrics are, in coding_source) — a Codex
# extension lands the same way, by adding its event names and a tool prefix.
EVENT_NAMES = (
    "claude_code.tool_decision",
    "claude_code.tool_result",
    "claude_code.api_request",
    "claude_code.api_error",
    "claude_code.api_refusal",
    "claude_code.user_prompt",
)

# Duration histogram edges in milliseconds (upper-inclusive; a final +inf
# bucket catches the tail). Summable across days, so a window p50/p90 is exact
# on the bucket grid. Chosen to straddle the interesting range for an agent:
# sub-second tool calls up to multi-second model requests.
DURATION_EDGES_MS = (100, 250, 500, 1000, 2000, 5000, 10000, 30000, 60000)
# Prompt-length histogram edges in CHARACTERS (prompt_length is a count, never
# the text). Coarse — cadence is the question, not exact size.
PROMPT_LEN_EDGES = (50, 200, 500, 1000, 2000, 5000, 10000)


def _bucket_index(value: float, edges: tuple[int, ...]) -> int:
    """Index of the first edge >= value, or len(edges) for the +inf tail."""
    for i, edge in enumerate(edges):
        if value <= edge:
            return i
    return len(edges)


def _new_hist(edges: tuple[int, ...]) -> list[int]:
    return [0] * (len(edges) + 1)


def percentile_from_hist(hist: list[int], edges: tuple[int, ...], q: float) -> float | None:
    """Approximate q-quantile (0..1) from a summable bucket histogram.

    Returns the UPPER edge of the bucket the quantile falls in — an honest
    over-estimate on the bucket grid, never a fabricated point value. The +inf
    tail returns the top edge (we cannot bound it from above), which is stated
    as ">= top edge" in the UI. None when the histogram is empty.
    """
    total = sum(hist)
    if total == 0:
        return None
    target = q * total
    cum = 0
    for i, count in enumerate(hist):
        cum += count
        if cum >= target:
            return float(edges[i]) if i < len(edges) else float(edges[-1])
    return float(edges[-1])


def _empty_day(day: str) -> dict[str, Any]:
    return {
        "date": day,
        # edit-acceptance
        "decisions": {
            "accept": 0,
            "reject": 0,
            "by_source": defaultdict(lambda: {"accept": 0, "reject": 0}),
        },
        # tool mix / MCP / per-tool latency
        "tools": defaultdict(
            lambda: {
                "count": 0,
                "success": 0,
                "fail": 0,
                "mcp": False,
                "dur_hist": _new_hist(DURATION_EDGES_MS),
                "dur_sum": 0.0,
                "dur_n": 0,
            }
        ),
        # per-request latency + token attribution by model
        "models": defaultdict(
            lambda: {
                "count": 0,
                "dur_hist": _new_hist(DURATION_EDGES_MS),
                "dur_sum": 0.0,
                "dur_n": 0,
                "input": 0,
                "output": 0,
                "cache_read": 0,
                "cache_creation": 0,
            }
        ),
        # reliability
        "errors": defaultdict(int),  # keyed by status_code (str)
        "refusals": 0,
        "retries": defaultdict(int),  # keyed by attempt number (str)
        # prompt cadence
        "prompts": {"count": 0, "len_sum": 0, "len_hist": _new_hist(PROMPT_LEN_EDGES)},
    }


# Token attributes on api_request use camelCase `type` values, matching the
# metrics path: input / output / cacheRead / cacheCreation.
_TOKEN_KEYS = {
    "input": ("input", "input_tokens"),
    "output": ("output", "output_tokens"),
    "cache_read": ("cacheRead", "cache_read_tokens", "cache_read_input_tokens"),
    "cache_creation": ("cacheCreation", "cache_creation_tokens", "cache_creation_input_tokens"),
}


def _num(attrs: dict[str, Any], *keys: str) -> float:
    for k in keys:
        if k in attrs and attrs[k] not in (None, ""):
            try:
                return float(attrs[k])
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def summarize_records(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Fold parsed OTLP log records into one aggregate bucket per day.

    Pure function — same testability rationale as coding_source.summarize_series.
    Each record: {"event": <event.name>, "attrs": {...}, "timestamp": datetime}.
    Returns {"<YYYY-MM-DD>": <day aggregate>} with defaultdicts flattened.
    """
    days: dict[str, dict[str, Any]] = {}
    for rec in records:
        ts = rec.get("timestamp")
        day = ts.strftime("%Y-%m-%d") if isinstance(ts, dt.datetime) else str(ts)[:10]
        d = days.setdefault(day, _empty_day(day))
        event = rec.get("event") or ""
        a = rec.get("attrs") or {}

        if event == "claude_code.tool_decision":
            decision = str(a.get("decision") or "").lower()
            source = str(a.get("source") or "unknown")
            if decision in ("accept", "reject"):
                d["decisions"][decision] += 1
                d["decisions"]["by_source"][source][decision] += 1

        elif event == "claude_code.tool_result":
            name = str(a.get("tool_name") or "unknown")
            t = d["tools"][name]
            t["count"] += 1
            success = a.get("success")
            if success in (True, "true", "True", 1, "1"):
                t["success"] += 1
            elif success in (False, "false", "False", 0, "0"):
                t["fail"] += 1
            scope = a.get("mcp_server_scope") or a.get("mcp_server")
            if scope:
                t["mcp"] = True
            dur = _num(a, "duration_ms", "duration")
            if dur > 0:
                t["dur_hist"][_bucket_index(dur, DURATION_EDGES_MS)] += 1
                t["dur_sum"] += dur
                t["dur_n"] += 1

        elif event == "claude_code.api_request":
            model = str(a.get("model") or "unknown")
            m = d["models"][model]
            m["count"] += 1
            dur = _num(a, "duration_ms", "duration")
            if dur > 0:
                m["dur_hist"][_bucket_index(dur, DURATION_EDGES_MS)] += 1
                m["dur_sum"] += dur
                m["dur_n"] += 1
            for dest, keys in _TOKEN_KEYS.items():
                m[dest] += int(_num(a, *keys))

        elif event == "claude_code.api_error":
            code = str(a.get("status_code") or a.get("status") or "unknown")
            d["errors"][code] += 1
            attempt = a.get("attempt")
            if attempt not in (None, ""):
                d["retries"][str(attempt)] += 1

        elif event == "claude_code.api_refusal":
            d["refusals"] += 1

        elif event == "claude_code.user_prompt":
            d["prompts"]["count"] += 1
            length = int(_num(a, "prompt_length", "length"))
            if length > 0:
                d["prompts"]["len_sum"] += length
                d["prompts"]["len_hist"][_bucket_index(length, PROMPT_LEN_EDGES)] += 1

    # Flatten defaultdicts for JSON storage and assertion.
    for d in days.values():
        d["decisions"]["by_source"] = {s: dict(v) for s, v in d["decisions"]["by_source"].items()}
        d["tools"] = {n: dict(v) for n, v in d["tools"].items()}
        d["models"] = {n: dict(v) for n, v in d["models"].items()}
        d["errors"] = dict(d["errors"])
        d["retries"] = dict(d["retries"])
    return days


def _day_summary_line(day: dict[str, Any]) -> str:
    dec = day["decisions"]
    total = dec["accept"] + dec["reject"]
    parts = []
    if total:
        parts.append(f"{dec['accept']}/{total} edits kept ({100 * dec['accept'] / total:.0f}%)")
    tool_calls = sum(t["count"] for t in day["tools"].values())
    if tool_calls:
        parts.append(f"{tool_calls} tool calls")
    if day["prompts"]["count"]:
        parts.append(f"{day['prompts']['count']} prompts")
    errs = sum(day["errors"].values()) + day["refusals"]
    if errs:
        parts.append(f"{errs} API errors/refusals")
    return " · ".join(parts) or "no behavioural events"


class CodingLogsSource(PlatformLogSource):
    """SigV4-read CloudWatch Logs behavioural telemetry for Claude Code.

    `client` (a boto3 `logs` client) is injectable so tests run against canned
    `filter_log_events` payloads with no AWS. In production it is constructed
    with the region the ARN/`.env` names — signed by the AWS auth D39 made the
    lab's single human login. No new credential, like `coding_source`.
    """

    name = PLATFORM

    def __init__(self, client: Any = None, log_group: str | None = None):
        self._client = client
        self._log_group = log_group or LOG_GROUP
        self.read_error: str | None = None

    # ---- fetch -------------------------------------------------------------

    def _records(self, client: Any) -> list[dict[str, Any]]:
        """Every claude_code.* log record in the window, parsed to {event, attrs, ts}.

        FilterLogEvents over the whole group (all streams). The message is an
        OTLP/JSON log record whose event name is in `attributes["event.name"]`
        (verified live 2026-07-30), with the datapoint fields as sibling
        attributes. Records that are not JSON, or carry no recognised event
        name, are skipped rather than failing the harvest.
        """
        end_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
        start_ms = end_ms - WINDOW_DAYS * 86400 * 1000
        records: list[dict[str, Any]] = []
        token: str | None = None
        pages = 0
        while True:
            kwargs = {
                "logGroupName": self._log_group,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 10000,
            }
            if token:
                kwargs["nextToken"] = token
            resp = client.filter_log_events(**kwargs)
            for evt in resp.get("events", []):
                rec = self._parse(evt.get("message") or "")
                if rec:
                    records.append(rec)
            token = resp.get("nextToken")
            pages += 1
            # Defensive ceiling: a week of one developer's sessions is well
            # under this, and it stops a pathological group from looping.
            if not token or pages >= 200:
                break
        return records

    @staticmethod
    def _parse(message: str) -> dict[str, Any] | None:
        try:
            rec = json.loads(message)
        except (ValueError, TypeError):
            return None
        attrs = rec.get("attributes") or {}
        event = attrs.get("event.name") or rec.get("body")
        if not isinstance(event, str) or event not in EVENT_NAMES:
            return None
        ts_ns = rec.get("timeUnixNano") or rec.get("observedTimeUnixNano")
        try:
            ts = dt.datetime.fromtimestamp(int(ts_ns) / 1e9, dt.timezone.utc)
        except (TypeError, ValueError):
            ts = dt.datetime.now(dt.timezone.utc)
        return {"event": event, "attrs": attrs, "timestamp": ts}

    # ---- harvest -----------------------------------------------------------

    def harvest(self, store: ObsStore) -> HarvestResult:
        client = self._client
        if client is None:
            try:
                import boto3

                client = boto3.client("logs", region_name=REGION)
            except Exception as exc:  # noqa: BLE001
                result = HarvestResult(
                    platform=self.name,
                    status="blocked",
                    detail=f"no CloudWatch Logs client ({type(exc).__name__}) — AWS auth required",
                )
                store.set_harvest_status(self.name, result.status, result.detail)
                return result

        try:
            records = self._records(client)
        except Exception as exc:  # noqa: BLE001
            self.read_error = f"{type(exc).__name__}: {exc}"
            result = HarvestResult(platform=self.name, status="error", detail=self.read_error[:300])
            store.set_harvest_status(self.name, result.status, result.detail)
            return result

        if not records:
            detail = (
                "no claude_code.* log events in CloudWatch — launch this project "
                "with scripts/claude_otel.sh (behavioural logs are opt-in; a plain "
                "`claude` exports metrics only) and run at least one turn. Telemetry "
                "is not retroactive: work done before logs were switched on cannot be "
                "measured afterwards"
            )
            result = HarvestResult(platform=self.name, status="blocked", detail=detail)
            store.set_harvest_status(self.name, result.status, result.detail)
            return result

        days = summarize_records(records)
        result = HarvestResult(platform=self.name, status="ok")
        total_events = 0
        for day, agg in sorted(days.items()):
            key = f"claude-code:{day}"
            dec = agg["decisions"]
            decided = dec["accept"] + dec["reject"]
            tool_calls = sum(t["count"] for t in agg["tools"].values())
            total_events += tool_calls + agg["prompts"]["count"] + decided
            store.upsert_session(
                self.name,
                key,
                title=f"claude-code behaviour · {day}",
                status="complete",
                created_at=f"{day}T00:00:00+00:00",
                updated_at=f"{day}T23:59:59+00:00",
                usage={
                    "edits_accepted": dec["accept"],
                    "edits_decided": decided,
                    "tool_calls": tool_calls,
                    "prompts": agg["prompts"]["count"],
                    "api_errors": sum(agg["errors"].values()) + agg["refusals"],
                },
                raw={
                    "tool": "claude-code",
                    "date": day,
                    "aggregates": agg,
                    "content_flags": "off — no prompt, file or tool-argument content emitted (D59)",
                },
            )
            result.sessions += 1
        result.events = total_events

        accept = sum(d["decisions"]["accept"] for d in days.values())
        decided = sum(d["decisions"]["accept"] + d["decisions"]["reject"] for d in days.values())
        rate = f"{100 * accept / decided:.0f}% edits kept" if decided else "no edit decisions yet"
        result.detail = (
            f"{len(days)} day(s) over {WINDOW_DAYS}d · {rate} · "
            f"{total_events} behavioural events (content off)"
        )
        store.set_harvest_status(self.name, result.status, result.detail)
        return result
