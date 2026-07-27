"""Minimal CloudWatch PromQL client (WS9).

OTLP metrics ingested through CloudWatch's native endpoint do **not** appear in
the classic `ListMetrics` / `GetMetricStatistics` APIs. They land in a
Prometheus-compatible store queried over SigV4-signed HTTP at
`https://monitoring.{region}.amazonaws.com/api/v1/{operation}`, with the OTLP
data model flattened into PromQL labels.

This was found the hard way, and it is the kind of mistake that stays silent:
the first version of `coding_source` used ListMetrics, ingestion returned
HTTP 200, and discovery returned nothing — so the harvest would have reported
"no coding metrics yet, switch the exporters on" forever while the exporters
were working perfectly.

Label conventions worth knowing (verified live 2026-07-26):
- metric name is `__name__`, and selectors MUST name a metric — a bare
  `{__name__=~".+"}` is rejected with "Selector must have a metric name"
- OTel resource attributes become `@resource.<attr>` (e.g. `@resource.tool`)
- datapoint attributes are bare (e.g. `model`, `type`)
- AWS adds `@aws.account`, `@aws.region`
- `/api/v1/label/__name__/values` is NOT supported (404) — there is no metric
  enumeration, so callers must know the names they want
"""

from __future__ import annotations

import os
import urllib.parse
from typing import Any

# The lab's telemetry lives in us-east-1. `AWS_DEFAULT_REGION` is deliberately
# NOT consulted: this machine exports AWS_DEFAULT_REGION=us-west-2 ambiently
# while .env sets AWS_REGION=us-east-1, and boto3 prefers the former. That has
# now silently misdirected two different lab components — the Secrets Manager
# client on 2026-07-25 (reported ResourceNotFound, which reads as "wrong ARN")
# and this PromQL client on 2026-07-26 (returned an empty result set, which
# reads as "no telemetry yet"). Both failures pointed at the wrong problem.
# Explicit override first, then AWS_REGION, then the lab's home region.
LAB_REGION = "us-east-1"
DEFAULT_REGION = os.environ.get("A2ALAB_CW_REGION") or os.environ.get("AWS_REGION") or LAB_REGION


class PromQLClient:
    """SigV4-signed POSTs to the CloudWatch Prometheus-compatible API."""

    def __init__(self, region: str | None = None, session: Any = None):
        self.region = region or DEFAULT_REGION
        self._session = session
        self._creds = None

    def _credentials(self):
        if self._creds is None:
            import boto3

            session = self._session or boto3.Session()
            frozen = session.get_credentials()
            if frozen is None:
                raise RuntimeError("no AWS credentials for the PromQL endpoint")
            self._creds = frozen.get_frozen_credentials()
        return self._creds

    def _post(self, operation: str, params: dict[str, Any]) -> dict:
        import httpx
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest

        url = f"https://monitoring.{self.region}.amazonaws.com/api/v1/{operation}"
        body = urllib.parse.urlencode(params, doseq=True)
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        signed = AWSRequest(method="POST", url=url, data=body, headers=headers)
        SigV4Auth(self._credentials(), "monitoring", self.region).add_auth(signed)
        resp = httpx.post(url, content=body, headers=dict(signed.headers), timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") != "success":
            raise RuntimeError(f"promql {operation}: {payload.get('error', payload)}")
        return payload.get("data") or {}

    def query_range(self, query: str, start: float, end: float, step_s: int) -> list[dict]:
        """Range query. Returns the raw `result` list (matrix)."""
        data = self._post(
            "query_range",
            {"query": query, "start": f"{start:.3f}", "end": f"{end:.3f}", "step": f"{step_s}s"},
        )
        return data.get("result") or []

    def query(self, query: str) -> list[dict]:
        """Instant query. Returns the raw `result` list (vector)."""
        return (self._post("query", {"query": query}) or {}).get("result") or []

    def labels(self) -> list[str]:
        return self._post("labels", {}) or []
