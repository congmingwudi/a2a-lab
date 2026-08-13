"""Cross-cloud infrastructure metrics sources — Track B of the Moirai
exploration (plan/explore-moirai-timeseries-forecasting.md).

The sibling of the M11 per-platform agent-log harvest. Those sources pull each
platform's AGENT-semantic interior view (sessions, turns, tokens); these pull
the same estate's SRE-grade RUNTIME telemetry — CPU, memory, request counts,
latency, connections — from the clouds' own monitoring surfaces (CloudWatch,
GCP Cloud Monitoring, Azure Monitor) as a DENSE, REGULAR time grid. That shape
is the point: a time-series foundation model fits regular grids, and the A2A
experiment traces are sparse and episodic. It is also a real coverage gap —
nothing harvested AWS or Azure runtime metrics before this.

Each source reads config/infra_metrics.yaml (its own cloud's block), pulls each
configured series as evenly spaced points over the window, and writes them via
``store.upsert_metrics`` — one row per point, keyed (cloud, resource, metric,
ts_at), so a re-harvest of an overlapping window updates in place. Same honest
degradation as the log sources: a series whose resource identifier is unset is
SKIPPED with a reason; a whole cloud with no identifiers reports "blocked", and
an API failure reports "error" — the console coverage tile renders exactly that.

Credentials follow the same rule as the log harvest (credentials.py): AWS auth
is the only human login; GCP/Azure identities come from the harvest secret via
prepare(). These sources never fall back to a developer's gcloud/az login.

The parse-and-normalize step of each cloud is a PURE function
(``normalize_*_series``) taking the cloud API's already-decoded response and
returning the store rows, so the math is unit-testable without live creds —
the same split adk_source.summarize_metrics uses.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any

import yaml

from observability.base import HarvestResult, PlatformLogSource

CONFIG_PATH = Path(os.environ.get("A2ALAB_INFRA_METRICS_PATH") or "config/infra_metrics.yaml")

DEFAULT_WINDOW_HOURS = 24
DEFAULT_PERIOD_S = 300

# The ${VAR} expander is the registry's, verbatim (D-rule: no second copy of a
# thing that must behave the same). A series with an unresolved required
# identifier expands to "" and is skipped, exactly like an unset auth header.
from interop.registry import _expand_env  # noqa: E402


def load_infra_config(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    """The infra_metrics.yaml spec with ${VAR} expanded. Empty dict when the
    file is absent (a fresh checkout with no config still imports)."""
    p = Path(path)
    if not p.exists():
        return {}
    raw = yaml.safe_load(p.read_text()) or {}
    return _expand_env(raw)


def _defaults(cfg: dict[str, Any]) -> tuple[int, int]:
    d = cfg.get("defaults") or {}
    return (
        int(d.get("window_hours") or DEFAULT_WINDOW_HOURS),
        int(d.get("period_s") or DEFAULT_PERIOD_S),
    )


def _iso_z(when: dt.datetime) -> str:
    """UTC ISO8601 with a trailing Z — the ts_at text format the store keys on,
    identical across all three clouds so a mixed query sorts correctly."""
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _window(window_hours: int) -> tuple[dt.datetime, dt.datetime]:
    end = dt.datetime.now(dt.timezone.utc)
    return end - dt.timedelta(hours=window_hours), end


def _finish(store, name: str, result: HarvestResult) -> HarvestResult:
    store.set_harvest_status(name, result.status, result.detail)
    return result


# --------------------------------------------------------------------------- #
# AWS — CloudWatch GetMetricData
# --------------------------------------------------------------------------- #

CLOUD_AWS = "aws"


def normalize_cloudwatch(results: list[dict[str, Any]], series_meta: dict[str, dict]) -> list[dict]:
    """CloudWatch GetMetricData ``MetricDataResults`` → store rows.

    Pure: takes the already-decoded boto3 response list and a map from the
    per-query id to its {resource, metric, unit, label, stat, dims} meta, and
    zips each result's parallel Timestamps/Values arrays into one row per point.
    """
    rows: list[dict] = []
    for r in results:
        meta = series_meta.get(r.get("Id", ""))
        if not meta:
            continue
        for ts, value in zip(r.get("Timestamps", []), r.get("Values", [])):
            when = ts if isinstance(ts, dt.datetime) else dt.datetime.fromisoformat(str(ts))
            rows.append(
                {
                    "cloud": CLOUD_AWS,
                    "resource": meta["resource"],
                    "metric": meta["metric"],
                    "ts_at": _iso_z(when),
                    "value": float(value),
                    "unit": meta.get("unit"),
                    "labels": {
                        "label": meta.get("label"),
                        "stat": meta.get("stat"),
                        "dims": meta.get("dims"),
                    },
                }
            )
    return rows


class AwsInfraSource(PlatformLogSource):
    name = "infra-aws"

    def __init__(self, cw_client=None):
        # Injectable for tests (mirrors StrandsSource) — real runs build a
        # region-pinned boto3 client inside harvest().
        self._cw = cw_client

    def harvest(self, store) -> HarvestResult:
        cfg = load_infra_config()
        series = cfg.get(CLOUD_AWS) or []
        if not series:
            return _finish(
                store,
                self.name,
                HarvestResult(
                    platform=self.name,
                    status="not-built",
                    detail="no aws series in config/infra_metrics.yaml",
                ),
            )
        window_hours, period_s = _defaults(cfg)
        start, end = _window(window_hours)

        # Build one GetMetricData query per series with a resolved dimension set.
        queries: list[dict[str, Any]] = []
        series_meta: dict[str, dict] = {}
        skipped: list[str] = []
        for i, s in enumerate(series):
            dims = s.get("dims") or {}
            missing = [k for k, v in dims.items() if not v]
            if missing:
                skipped.append(f"{s.get('resource')}·{s.get('metric')} (unset {','.join(missing)})")
                continue
            qid = f"m{i}"
            series_meta[qid] = {
                "resource": s["resource"],
                "metric": s["metric"],
                "unit": s.get("unit"),
                "label": s.get("label"),
                "stat": s.get("stat") or "Average",
                "dims": dims,
            }
            queries.append(
                {
                    "Id": qid,
                    "MetricStat": {
                        "Metric": {
                            "Namespace": s["namespace"],
                            "MetricName": s["metric"],
                            "Dimensions": [{"Name": k, "Value": v} for k, v in dims.items()],
                        },
                        "Period": period_s,
                        "Stat": s.get("stat") or "Average",
                    },
                    "ReturnData": True,
                }
            )
        if not queries:
            return _finish(
                store,
                self.name,
                HarvestResult(
                    platform=self.name,
                    status="blocked",
                    detail="every aws series skipped — unset resource ids: " + "; ".join(skipped),
                ),
            )

        try:
            cw = self._cw
            if cw is None:
                import boto3

                region = os.environ.get("AWS_REGION") or "us-east-1"
                cw = boto3.client("cloudwatch", region_name=region)
            results: list[dict[str, Any]] = []
            token: str | None = None
            while True:
                kwargs: dict[str, Any] = {
                    "MetricDataQueries": queries,
                    "StartTime": start,
                    "EndTime": end,
                    "ScanBy": "TimestampAscending",
                }
                if token:
                    kwargs["NextToken"] = token
                resp = cw.get_metric_data(**kwargs)
                results.extend(resp.get("MetricDataResults", []))
                token = resp.get("NextToken")
                if not token:
                    break
        except Exception as exc:  # noqa: BLE001 - report, do not raise into the pass
            return _finish(
                store,
                self.name,
                HarvestResult(
                    platform=self.name, status="error", detail=f"{type(exc).__name__}: {exc}"
                ),
            )

        rows = normalize_cloudwatch(results, series_meta)
        store.upsert_metrics(rows)
        detail = (
            f"{len(rows)} points across {len(queries)} series (last {window_hours}h, "
            f"{period_s // 60}m grid) — CloudWatch runtime metrics for the Fargate + "
            f"Aurora + Lambda estate"
        )
        if skipped:
            detail += f"; skipped {len(skipped)} (unset ids)"
        result = HarvestResult(platform=self.name, status="ok", events=len(rows), detail=detail)
        return _finish(store, self.name, result)


# --------------------------------------------------------------------------- #
# GCP — Cloud Monitoring timeSeries.list
# --------------------------------------------------------------------------- #

CLOUD_GCP = "gcp"
_GCP_MONITORING_URL = "https://monitoring.googleapis.com/v3/projects/{project}/timeSeries"


def normalize_gcp_timeseries(time_series: list[dict[str, Any]], meta: dict[str, Any]) -> list[dict]:
    """One GCP Monitoring ``timeSeries`` payload (the list under the JSON key)
    → store rows. Pure. `meta` carries {resource, metric, unit, label} for the
    series this payload answers. Each point's `interval.endTime` is the sample
    timestamp; the value is int64Value/doubleValue."""
    rows: list[dict] = []
    for ts in time_series:
        m_type = (ts.get("metric") or {}).get("type") or meta.get("metric")
        labels = (ts.get("metric") or {}).get("labels") or {}
        for p in ts.get("points", []):
            end_time = (p.get("interval") or {}).get("endTime")
            if not end_time:
                continue
            val = p.get("value") or {}
            raw = val.get("int64Value")
            if raw is None:
                raw = val.get("doubleValue")
            when = dt.datetime.fromisoformat(str(end_time).replace("Z", "+00:00"))
            rows.append(
                {
                    "cloud": CLOUD_GCP,
                    "resource": meta["resource"],
                    "metric": m_type,
                    "ts_at": _iso_z(when),
                    "value": float(raw or 0),
                    "unit": meta.get("unit"),
                    "labels": {"label": meta.get("label"), "metric_labels": labels},
                }
            )
    return rows


class GcpInfraSource(PlatformLogSource):
    name = "infra-gcp"

    def harvest(self, store) -> HarvestResult:
        cfg = load_infra_config()
        series = cfg.get(CLOUD_GCP) or []
        if not series:
            return _finish(
                store,
                self.name,
                HarvestResult(
                    platform=self.name,
                    status="not-built",
                    detail="no gcp series in config/infra_metrics.yaml",
                ),
            )
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project:
            return _finish(
                store,
                self.name,
                HarvestResult(
                    platform=self.name,
                    status="blocked",
                    detail="GOOGLE_CLOUD_PROJECT unset — deploy WS2 first",
                ),
            )
        window_hours, period_s = _defaults(cfg)
        start, end = _window(window_hours)

        try:
            from google.auth import default as google_default
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google_default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            session = AuthorizedSession(credentials)
            all_rows: list[dict] = []
            covered = 0
            for s in series:
                engine = s.get("engine_dim")
                flt = s["filter"]
                if engine:
                    engine_id = str(engine).rsplit("/", 1)[-1]
                    flt = f'{flt} resource.labels.reasoning_engine_id="{engine_id}"'
                mr = session.get(
                    _GCP_MONITORING_URL.format(project=project),
                    params={
                        "filter": flt,
                        "interval.startTime": _iso_z(start),
                        "interval.endTime": _iso_z(end),
                        "aggregation.alignmentPeriod": f"{period_s}s",
                        "aggregation.perSeriesAligner": s.get("aligner") or "ALIGN_MEAN",
                    },
                    timeout=30,
                )
                mr.raise_for_status()
                meta = {
                    "resource": s["resource"],
                    "metric": s["filter"].split('"')[1] if '"' in s["filter"] else s["filter"],
                    "unit": s.get("unit"),
                    "label": s.get("label"),
                }
                all_rows.extend(normalize_gcp_timeseries(mr.json().get("timeSeries", []), meta))
                covered += 1
        except Exception as exc:  # noqa: BLE001
            return _finish(
                store,
                self.name,
                HarvestResult(
                    platform=self.name, status="error", detail=f"{type(exc).__name__}: {exc}"
                ),
            )

        store.upsert_metrics(all_rows)
        result = HarvestResult(
            platform=self.name,
            status="ok",
            events=len(all_rows),
            detail=(
                f"{len(all_rows)} points across {covered} series (last {window_hours}h, "
                f"{period_s // 60}m grid) — Cloud Monitoring runtime metrics for the "
                f"Vertex Agent Engine"
            ),
        )
        return _finish(store, self.name, result)


# --------------------------------------------------------------------------- #
# Azure — Azure Monitor metrics REST API (over an ARM bearer token)
# --------------------------------------------------------------------------- #

CLOUD_AZURE = "azure"


def normalize_azure_metrics(metrics: list[Any], meta_by_name: dict[str, dict]) -> list[dict]:
    """Azure Monitor metrics ``value[]`` → store rows. Pure.

    Each metric has timeseries[].data[] points; a point carries a timestamp
    (`timeStamp` over REST, `timestamp` in the SDK object model — both accepted)
    plus one populated aggregation field (total/average/count/...). `meta_by_name`
    maps the Azure metric name to its {resource, unit, label, aggregation}.

    Tolerant of both plain dicts (the REST JSON) and attribute objects (the SDK
    types), so the same function serves the live REST caller and a lightweight
    test stand-in.
    """

    def attr(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    rows: list[dict] = []
    for metric in metrics:
        m_name = attr(metric, "name")
        # azure names can be a {"value": ...} localized object
        if isinstance(m_name, dict):
            m_name = m_name.get("value")
        meta = meta_by_name.get(str(m_name))
        if not meta:
            continue
        agg = (meta.get("aggregation") or "Average").lower()
        for tseries in attr(metric, "timeseries") or []:
            for point in attr(tseries, "data") or []:
                # SDK object model spells it `timestamp`; the Monitor REST API
                # spells it `timeStamp`. Accept either so the same normalizer
                # serves both callers.
                when = attr(point, "timestamp") or attr(point, "timeStamp")
                value = attr(point, agg)
                if value is None or when is None:
                    continue
                if isinstance(when, str):
                    when = dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
                rows.append(
                    {
                        "cloud": CLOUD_AZURE,
                        "resource": meta["resource"],
                        "metric": str(m_name),
                        "ts_at": _iso_z(when),
                        "value": float(value),
                        "unit": meta.get("unit"),
                        "labels": {
                            "label": meta.get("label"),
                            "aggregation": meta.get("aggregation"),
                        },
                    }
                )
    return rows


class AzureInfraSource(PlatformLogSource):
    name = "infra-azure"

    def harvest(self, store) -> HarvestResult:
        cfg = load_infra_config()
        series = cfg.get(CLOUD_AZURE) or []
        if not series:
            return _finish(
                store,
                self.name,
                HarvestResult(
                    platform=self.name,
                    status="not-built",
                    detail="no azure series in config/infra_metrics.yaml",
                ),
            )
        # Group series by the resource ARM id they read (one query per resource,
        # many metric names). Skip any whose resource_uri did not resolve.
        by_uri: dict[str, list[dict]] = {}
        skipped: list[str] = []
        for s in series:
            uri = s.get("resource_uri")
            if not uri:
                skipped.append(f"{s.get('resource')}·{s.get('metric')} (unset resource_uri)")
                continue
            by_uri.setdefault(uri, []).append(s)
        if not by_uri:
            return _finish(
                store,
                self.name,
                HarvestResult(
                    platform=self.name,
                    status="blocked",
                    detail="every azure series skipped — unset AZURE_FOUNDRY_RESOURCE_ID: "
                    + "; ".join(skipped),
                ),
            )

        from observability.credentials import azure_credential, azure_missing

        if azure_missing():
            return _finish(
                store,
                self.name,
                HarvestResult(
                    platform=self.name,
                    status="blocked",
                    detail=f"Azure service principal not configured — missing "
                    f"{', '.join(azure_missing())}",
                ),
            )
        window_hours, period_s = _defaults(cfg)
        start, end = _window(window_hours)

        try:
            # Azure Monitor's metrics REST API directly, over an ARM bearer
            # token — not azure-monitor-query, whose 2.0 release dropped the
            # metrics client into a separate package we do not depend on (the
            # LogsQueryClient foundry_source uses still ships in 2.0). Same
            # shape as the GCP AuthorizedSession path: build one request per
            # resource, hand the decoded JSON to the pure normalizer.
            import httpx

            token = azure_credential().get_token("https://management.azure.com/.default").token
            all_rows: list[dict] = []
            covered = 0
            for uri, uri_series in by_uri.items():
                meta_by_name = {
                    s["metric"]: {
                        "resource": s["resource"],
                        "unit": s.get("unit"),
                        "label": s.get("label"),
                        "aggregation": s.get("aggregation") or "Average",
                    }
                    for s in uri_series
                }
                # Union of the aggregations the series ask for (Average, Total,
                # …); the normalizer picks each series' configured field back out.
                aggs = sorted({(s.get("aggregation") or "Average") for s in uri_series})
                resp = httpx.get(
                    f"https://management.azure.com{uri}/providers/Microsoft.Insights/metrics",
                    params={
                        "api-version": "2018-01-01",
                        "metricnames": ",".join(s["metric"] for s in uri_series),
                        "timespan": f"{_iso_z(start)}/{_iso_z(end)}",
                        "interval": f"PT{period_s // 60}M",
                        "aggregation": ",".join(aggs),
                    },
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=40,
                )
                resp.raise_for_status()
                all_rows.extend(normalize_azure_metrics(resp.json().get("value", []), meta_by_name))
                covered += len(uri_series)
        except Exception as exc:  # noqa: BLE001
            who = (
                f"{os.environ.get('AZURE_CLIENT_ID', '?')}@{os.environ.get('AZURE_TENANT_ID', '?')}"
            )
            return _finish(
                store,
                self.name,
                HarvestResult(
                    platform=self.name,
                    status="error",
                    detail=f"{type(exc).__name__} (as sp {who}): {exc}",
                ),
            )

        store.upsert_metrics(all_rows)
        detail = (
            f"{len(all_rows)} points across {covered} series (last {window_hours}h, "
            f"{period_s // 60}m grid) — Azure Monitor runtime metrics for the Foundry project"
        )
        if skipped:
            detail += f"; skipped {len(skipped)} (unset ids)"
        result = HarvestResult(platform=self.name, status="ok", events=len(all_rows), detail=detail)
        return _finish(store, self.name, result)
