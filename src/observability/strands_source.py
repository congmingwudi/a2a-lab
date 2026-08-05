"""AWS Strands / Bedrock AgentCore obs source (WS5/D66, M11.2).

Unlike Claude and OpenAI — which run ON AgentCore but are OBSERVED through
their vendor session APIs (Anthropic Managed Agents, OpenAI Responses) —
Strands has no vendor session API. Its telemetry surface is AWS's own:

  - **Bedrock model metrics** (`AWS/Bedrock` CloudWatch namespace): per-ModelId
    `InputTokenCount`, `OutputTokenCount`, `Invocations`, `InvocationLatency`.
    The Strands runtime is the ONLY Bedrock-backed agent in this account
    (claude-sdk uses the Anthropic API, openai the OpenAI API, adk Vertex,
    foundry Azure), so the haiku-4-5 ModelId meters attribute cleanly to
    Strands. This is the token/cost picture.
  - **AgentCore runtime log group** (`/aws/bedrock-agentcore/runtimes/<id>-DEFAULT`):
    the container's access log. `POST /invocations` lines give invocation and
    error (5xx) counts. The Strands SDK emits no structured turn log at this
    version, so there is no per-turn session read — the honest shape is one obs
    "session" per deployed runtime with a daily metrics + request rollup, the
    same shape as the ADK source (Agent Engine has no session API either).

The `platform_ref` (Bedrock request-id) join to wire traces comes back null at
this SDK version (plan/09 L6, plan/12) — so like OpenAI/ADK, sessions correlate
to the runtime, not to individual lab traces, until that gap closes.

Both reads use boto3 (`cloudwatch`, `logs`), signed by the AWS auth D39 made the
lab's single human login — no new credential. The clients are injectable so
tests run against canned payloads with no AWS.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Any

from observability.base import HarvestResult, PlatformLogSource
from observability.store import ObsStore

WINDOW_HOURS = 24

# Region resolution — same trap as promql.py / coding_logs_source.py: this
# laptop exports AWS_DEFAULT_REGION=us-west-2 ambiently while .env sets
# AWS_REGION=us-east-1, and boto3 prefers the former, which reads back empty and
# looks like "no telemetry". Explicit override, then AWS_REGION, then home.
LAB_REGION = "us-east-1"
REGION = os.environ.get("A2ALAB_CW_METRICS_REGION") or os.environ.get("AWS_REGION") or LAB_REGION

# haiku-4-5 Bedrock on-demand list prices (us-east-1, checked 2026-08-04):
# $0.80 / $4.00 per 1M input/output tokens. Estimate, not an invoice.
TOKEN_USD_PER_M = {"input": 0.80, "output": 4.00}

# Bedrock model metrics we roll up, keyed as (MetricName, stat).
_BEDROCK_METRICS = {
    "input_tokens": ("InputTokenCount", "Sum"),
    "output_tokens": ("OutputTokenCount", "Sum"),
    "invocations": ("Invocations", "Sum"),
    "latency_ms_avg": ("InvocationLatency", "Average"),
}


def runtime_id_from_arn(arn: str) -> str:
    """`arn:...:runtime/a2alab_strands-a07goY5qhK` -> `a2alab_strands-a07goY5qhK`."""
    return arn.rsplit("/", 1)[-1]


def log_group_for(runtime_id: str) -> str:
    return f"/aws/bedrock-agentcore/runtimes/{runtime_id}-DEFAULT"


def summarize_bedrock(values_by_key: dict[str, float]) -> dict[str, Any]:
    """Roll up the fetched Bedrock metric scalars into the dashboard/brief usage
    block. Pure function so the arithmetic is testable without AWS."""
    inp = int(values_by_key.get("input_tokens", 0))
    out = int(values_by_key.get("output_tokens", 0))
    invocations = int(values_by_key.get("invocations", 0))
    token_usd = inp / 1e6 * TOKEN_USD_PER_M["input"] + out / 1e6 * TOKEN_USD_PER_M["output"]
    return {
        "window_hours": WINDOW_HOURS,
        "model": os.environ.get("STRANDS_MODEL_ID", "?"),
        "invocations": invocations,
        "input_tokens": inp,
        "output_tokens": out,
        "avg_latency_ms": round(values_by_key.get("latency_ms_avg", 0.0), 1),
        "est_token_usd": round(token_usd, 4),
        "est_cost_usd": round(token_usd, 4),
    }


def summarize_requests(messages: list[str]) -> dict[str, int]:
    """Count `POST /invocations` outcomes from the runtime access log lines.
    2xx = ok, 5xx = error; the SDK emits nothing richer at this version."""
    ok = err = 0
    for m in messages:
        if "/invocations" not in m:
            continue
        if '" 200 ' in m or " 200 OK" in m:
            ok += 1
        elif '" 5' in m or " 500 " in m or "Internal Server Error" in m:
            err += 1
    return {"ok": ok, "error": err}


class StrandsSource(PlatformLogSource):
    name = "strands"

    def __init__(self, cw_client: Any = None, logs_client: Any = None):
        # Injectable for tests; built from the AWS session in production.
        self._cw = cw_client
        self._logs = logs_client

    def harvest(self, store: ObsStore) -> HarvestResult:
        arn = os.environ.get("STRANDS_AGENTCORE_ARN")
        if not arn:
            result = HarvestResult(
                platform=self.name,
                status="blocked",
                detail="STRANDS_AGENTCORE_ARN unset — deploy the runtime first "
                "(deploy/agentcore/deploy.sh strands)",
            )
            store.set_harvest_status(self.name, result.status, result.detail)
            return result

        runtime_id = runtime_id_from_arn(arn)
        model_id = os.environ.get("STRANDS_MODEL_ID")
        now = dt.datetime.now(dt.timezone.utc)
        start = now - dt.timedelta(hours=WINDOW_HOURS)

        # Bedrock model metrics (the token/cost picture). Soft-fail: metrics
        # enrich the column, a CloudWatch hiccup must not lose the request
        # rollup below.
        metrics: dict[str, Any] | None = None
        try:
            cw = self._cw
            if cw is None:
                import boto3

                cw = boto3.client("cloudwatch", region_name=REGION)
            values: dict[str, float] = {}
            if model_id:
                for key, (metric_name, stat) in _BEDROCK_METRICS.items():
                    resp = cw.get_metric_statistics(
                        Namespace="AWS/Bedrock",
                        MetricName=metric_name,
                        Dimensions=[{"Name": "ModelId", "Value": model_id}],
                        StartTime=start,
                        EndTime=now,
                        Period=WINDOW_HOURS * 3600,
                        Statistics=[stat],
                    )
                    pts = resp.get("Datapoints", [])
                    values[key] = sum(p.get(stat, 0.0) for p in pts)
                metrics = summarize_bedrock(values)
        except Exception:  # noqa: BLE001 - metrics are additive
            metrics = None

        # Runtime access log (invocation / error counts). This is the read that
        # decides ok/blocked — if the log group is unreadable, say so.
        try:
            logs = self._logs
            if logs is None:
                import boto3

                logs = boto3.client("logs", region_name=REGION)
            end_ms = int(now.timestamp() * 1000)
            start_ms = int(start.timestamp() * 1000)
            messages: list[str] = []
            token: str | None = None
            pages = 0
            while True:
                kwargs: dict[str, Any] = {
                    "logGroupName": log_group_for(runtime_id),
                    "startTime": start_ms,
                    "endTime": end_ms,
                    "filterPattern": '"/invocations"',
                    "limit": 10000,
                }
                if token:
                    kwargs["nextToken"] = token
                resp = logs.filter_log_events(**kwargs)
                messages.extend(e.get("message", "") for e in resp.get("events", []))
                token = resp.get("nextToken")
                pages += 1
                if not token or pages >= 50:
                    break
            requests = summarize_requests(messages)
        except Exception as exc:  # noqa: BLE001
            result = HarvestResult(
                platform=self.name, status="error", detail=f"{type(exc).__name__}: {exc}"
            )
            store.set_harvest_status(self.name, result.status, result.detail)
            return result

        result = HarvestResult(platform=self.name, status="ok")
        store.upsert_session(
            self.name,
            runtime_id,
            title=f"Strands runtime {runtime_id} (strands-researcher, {model_id or '?'})",
            status="active",
            usage=metrics,
            raw={
                "runtime_arn": arn,
                "model_id": model_id,
                "note": "one session per AgentCore runtime — the Strands SDK "
                "exposes no session/turn API at this version, so events are a "
                "CloudWatch daily rollup (Bedrock model meters + runtime access "
                "log). platform_ref (Bedrock request-id) is null at this SDK "
                "version, so sessions correlate to the runtime, not lab traces.",
            },
        )
        result.sessions = 1

        day = now.strftime("%Y-%m-%d")
        summary_bits = [
            f"CloudWatch last {WINDOW_HOURS}h: {requests['ok']} ok "
            f"/ {requests['error']} error invocations"
        ]
        if metrics:
            summary_bits.append(
                f"{metrics['input_tokens']} in / {metrics['output_tokens']} out tokens "
                f"over {metrics['invocations']} Bedrock calls "
                f"(avg {metrics['avg_latency_ms']:.0f}ms) ≈ ${metrics['est_cost_usd']:.2f} est."
            )
        store.upsert_event(
            self.name,
            runtime_id,
            f"metrics-{day}",
            event_type="metrics-rollup",
            processed_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            summary=" · ".join(summary_bits),
            raw={"requests": requests, "metrics": metrics},
        )
        result.events = 1

        result.detail = (
            f"{requests['ok']} ok / {requests['error']} error invocations (last {WINDOW_HOURS}h)"
            + (
                f" · {metrics['input_tokens'] + metrics['output_tokens']} tokens ≈ "
                f"${metrics['est_cost_usd']:.2f} est. 24h cost (Bedrock model meters)"
                if metrics
                else " — Bedrock model metrics unavailable this pass"
            )
            + " — runtime-level telemetry; the Strands SDK exposes no session/turn API"
        )
        store.set_harvest_status(self.name, result.status, result.detail)
        return result
