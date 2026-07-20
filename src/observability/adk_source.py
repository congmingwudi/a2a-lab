"""ADK / Vertex AI Agent Engine log + metrics source (WS2, M11.2).

What the platform exposes today (A2A serving is Preview): **Cloud Logging**
entries per ReasoningEngine — container app logs and request lines — and
**Cloud Monitoring** metrics: per-engine request counts/latencies, the
literal billing meters (`cpu/allocation_time` vCPU-seconds and
`memory/allocation_time` GiB-seconds — Agent Engine bills allocated
compute, not tokens), and per-model token counts from the Vertex publisher
metrics (`publisher/online_serving/token_count`, project-level — the lab
project runs only the ADK agent, so this is effectively its usage). There
is still no session/turn read API on the preview A2A surface, and A2A
contextIds do not appear in the default logs, so the honest shape is one
obs "session" per deployed engine with its log entries as events plus a
daily metrics rollup. (Contrast columns: Salesforce = queryable
session/step DMOs, Anthropic = deep per-session events, OpenAI =
write-only. GCP lands between: real, queryable telemetry — request-level
and billing-grade, but not agent-semantic.)

Auth rides the same ADC credentials the lab already uses for the data
plane; no extra client library — plain Logging/Monitoring REST via
AuthorizedSession.
"""

from __future__ import annotations

import os
from typing import Any

from observability.base import HarvestResult, PlatformLogSource
from observability.store import ObsStore

LOGGING_URL = "https://logging.googleapis.com/v2/entries:list"
MONITORING_URL = "https://monitoring.googleapis.com/v3/projects/{project}/timeSeries"
WINDOW_HOURS = 24
MAX_ENTRIES = 500

# Estimate rates for the dashboard — NOT billing truth. Agent Engine list
# prices (us-central1, checked 2026-07-20): $0.0994/vCPU-hour and
# $0.0105/GiB-hour of allocated compute; gemini-2.5-flash-lite list prices
# $0.10 / $0.40 per 1M input/output tokens.
VCPU_HOUR_USD = 0.0994
GIB_HOUR_USD = 0.0105
TOKEN_USD_PER_M = {"input": 0.10, "output": 0.40}

_METRIC_FILTERS = {
    "requests": 'metric.type="aiplatform.googleapis.com/reasoning_engine/request_count"',
    "cpu_s": 'metric.type="aiplatform.googleapis.com/reasoning_engine/cpu/allocation_time"',
    "gib_s": 'metric.type="aiplatform.googleapis.com/reasoning_engine/memory/allocation_time"',
    "tokens": 'metric.type="aiplatform.googleapis.com/publisher/online_serving/token_count"',
}


def summarize_metrics(series_by_name: dict[str, list[dict]]) -> dict[str, Any]:
    """Aggregate raw Monitoring timeSeries into the dashboard/brief rollup.
    Pure function so the math is testable without GCP credentials."""

    def total(ts: dict) -> float:
        return sum(
            float(p["value"].get("int64Value") or p["value"].get("doubleValue") or 0)
            for p in ts.get("points", [])
        )

    requests: dict[str, int] = {}
    for ts in series_by_name.get("requests", []):
        code = (ts.get("metric", {}).get("labels", {}) or {}).get("response_code", "?")
        requests[code] = requests.get(code, 0) + int(total(ts))
    cpu_s = sum(total(ts) for ts in series_by_name.get("cpu_s", []))
    gib_s = sum(total(ts) for ts in series_by_name.get("gib_s", []))
    tokens = {"input": 0, "output": 0}
    by_model: dict[str, dict[str, int]] = {}
    for ts in series_by_name.get("tokens", []):
        labels = ts.get("metric", {}).get("labels", {}) or {}
        kind = labels.get("type", "input")
        model = labels.get("model_user_id", "?")
        n = int(total(ts))
        tokens[kind] = tokens.get(kind, 0) + n
        by_model.setdefault(model, {})[kind] = by_model.setdefault(model, {}).get(kind, 0) + n
    compute_usd = (cpu_s / 3600) * VCPU_HOUR_USD + (gib_s / 3600) * GIB_HOUR_USD
    token_usd = sum(tokens.get(kind, 0) / 1e6 * rate for kind, rate in TOKEN_USD_PER_M.items())
    return {
        "window_hours": WINDOW_HOURS,
        "requests": requests,
        "cpu_alloc_s": round(cpu_s, 1),
        "mem_alloc_gib_s": round(gib_s, 1),
        "input_tokens": tokens.get("input", 0),
        "output_tokens": tokens.get("output", 0),
        "tokens_by_model": by_model,
        "est_compute_usd": round(compute_usd, 4),
        "est_token_usd": round(token_usd, 4),
        "est_cost_usd": round(compute_usd + token_usd, 4),
    }


class AdkSource(PlatformLogSource):
    name = "adk"

    def harvest(self, store: ObsStore) -> HarvestResult:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        engine = os.environ.get("ADK_AGENT_ENGINE_ID")  # full resource name
        if not project or not engine:
            result = HarvestResult(
                platform=self.name,
                status="blocked",
                detail="GOOGLE_CLOUD_PROJECT / ADK_AGENT_ENGINE_ID unset — deploy WS2 first",
            )
            store.set_harvest_status(self.name, result.status, result.detail)
            return result

        engine_id = engine.rsplit("/", 1)[-1]
        try:
            import datetime as dt

            from google.auth import default as google_default
            from google.auth.transport.requests import AuthorizedSession

            # cloud-platform: the harvest reads Logging AND Monitoring.
            credentials, _ = google_default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            session = AuthorizedSession(credentials)
            since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=WINDOW_HOURS)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            body: dict[str, Any] = {
                "resourceNames": [f"projects/{project}"],
                "filter": (
                    'resource.type="aiplatform.googleapis.com/ReasoningEngine" '
                    f'resource.labels.reasoning_engine_id="{engine_id}" '
                    f'timestamp>="{since}"'
                ),
                "orderBy": "timestamp desc",
                "pageSize": MAX_ENTRIES,
            }
            r = session.post(LOGGING_URL, json=body, timeout=30)
            r.raise_for_status()
            entries = r.json().get("entries", [])
        except Exception as exc:
            result = HarvestResult(
                platform=self.name,
                status="error",
                detail=f"{type(exc).__name__}: {exc}",
            )
            store.set_harvest_status(self.name, result.status, result.detail)
            return result

        # Cloud Monitoring rollup (soft-fail: metrics enrich the column, a
        # Monitoring hiccup must not lose the log harvest).
        metrics: dict[str, Any] | None = None
        try:
            series_by_name: dict[str, list[dict]] = {}
            for name, flt in _METRIC_FILTERS.items():
                mr = session.get(
                    MONITORING_URL.format(project=project),
                    params={
                        "filter": flt,
                        "interval.startTime": since,
                        "interval.endTime": dt.datetime.now(dt.timezone.utc).strftime(
                            "%Y-%m-%dT%H:%M:%SZ"
                        ),
                        "aggregation.alignmentPeriod": f"{WINDOW_HOURS * 3600}s",
                        "aggregation.perSeriesAligner": "ALIGN_SUM",
                    },
                    timeout=30,
                )
                mr.raise_for_status()
                series_by_name[name] = mr.json().get("timeSeries", [])
            metrics = summarize_metrics(series_by_name)
        except Exception:  # noqa: BLE001 - metrics are additive
            metrics = None

        result = HarvestResult(platform=self.name, status="ok")
        store.upsert_session(
            self.name,
            engine_id,
            title=f"Agent Engine {engine_id} (a2alab-adk-researcher)",
            status="active",
            usage=metrics,
            raw={
                "resource_name": engine,
                "note": "one session per engine — "
                "preview A2A exposes no session/turn API; events are Cloud "
                "Logging entries plus a Cloud Monitoring daily rollup",
            },
        )
        result.sessions = 1
        for e in entries:
            payload = e.get("textPayload") or str(e.get("jsonPayload", ""))[:500]
            store.upsert_event(
                self.name,
                engine_id,
                e.get("insertId", ""),
                event_type=e.get("severity", "DEFAULT"),
                processed_at=e.get("timestamp"),
                summary=(payload or "").strip()[:500] or None,
                raw=e,
            )
            result.events += 1
        if metrics:
            # One rollup event per day: the analyst brief reads events, so
            # the compute/token/cost picture must exist as a queryable row,
            # not only inside the session's usage_json.
            day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
            ok = sum(n for c, n in metrics["requests"].items() if c.startswith("2"))
            store.upsert_event(
                self.name,
                engine_id,
                f"metrics-{day}",
                event_type="metrics-rollup",
                processed_at=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                summary=(
                    f"Cloud Monitoring last {WINDOW_HOURS}h: {ok} ok requests "
                    f"({metrics['requests']}), {metrics['input_tokens']} in / "
                    f"{metrics['output_tokens']} out tokens, "
                    f"{metrics['cpu_alloc_s']} vCPU-s + {metrics['mem_alloc_gib_s']} GiB-s "
                    f"allocated ≈ ${metrics['est_cost_usd']:.2f} est. (compute "
                    f"${metrics['est_compute_usd']:.2f} + tokens ${metrics['est_token_usd']:.2f})"
                ),
                raw=metrics,
            )
            result.events += 1
        result.detail = (
            f"{result.events} log entries (last {WINDOW_HOURS}h)"
            + (
                f" · {metrics['input_tokens'] + metrics['output_tokens']} tokens ≈ "
                f"${metrics['est_cost_usd']:.2f} est. 24h cost (Cloud Monitoring)"
                if metrics
                else " — Monitoring rollup unavailable this pass"
            )
            + " — request-level telemetry; no session/turn API on the preview A2A surface"
        )
        store.set_harvest_status(self.name, result.status, result.detail)
        return result
