"""Salesforce Session Tracing log source (M11.2).

Pull surface: the Session Tracing Data Model DMOs, queried as SOQL over the
core REST API. Field names on STDM DMOs are org-/version-dependent (the
docs disagree with each other), so rather than hardcoding a projection we
use `SELECT FIELDS(ALL) … LIMIT n` and discover the columns from the rows —
the raw row is preserved verbatim (D7 ethos) and the normalized columns are
extracted by name heuristics.

Provisioning states observed in a2alab-prod (2026-07-17), all reported
honestly to the coverage panel:
- Data Cloud licensed but tracing never enabled → DMO entity exists, every
  query dies with UNKNOWN_EXCEPTION → status "blocked".
- Tracing enabled, DMOs materialized → queries run (0 rows until agent
  sessions are traced; ingestion lags minutes behind the session).

Auth: same client-credentials app as the Agentforce client (SF_MY_DOMAIN /
SF_CLIENT_ID / SF_CLIENT_SECRET).
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from observability.base import HarvestResult, PlatformLogSource
from observability.store import ObsStore

API_VERSION = "v62.0"
ROW_LIMIT = 200

SESSION_DMO = "ssot__AiAgentSession__dlm"
# Child DMOs harvested as obs_events, in drill-down display order.
EVENT_DMOS = [
    ("interaction", "ssot__AiAgentInteraction__dlm"),
    ("message", "ssot__AiAgentInteractionMessage__dlm"),
    ("step", "ssot__AiAgentInteractionStep__dlm"),
]

# Heuristic field matchers (STDM names drift between orgs/releases).
_TEXTY_HINTS = ("name", "txt", "text", "status", "type", "utterance", "title")


def _first_key(rec: dict[str, Any], *hints: str) -> Any:
    """Value of the first column whose lowercased name contains every hint."""
    for key, value in rec.items():
        low = key.lower()
        if value is not None and all(h in low for h in hints):
            return value
    return None


def _timestamp(rec: dict[str, Any], which: str) -> Any:
    """First non-null time column for 'start'/'end' — real a2alab-prod
    columns are ssot__StartTimestamp__c / ssot__EndTimestamp__c; docs also
    show *Dttm variants in other orgs."""
    return _first_key(rec, which, "timestamp") or _first_key(rec, which, "dttm")


def _summary_of(rec: dict[str, Any]) -> str:
    parts = []
    for key, value in rec.items():
        low = key.lower()
        if isinstance(value, str) and value and any(h in low for h in _TEXTY_HINTS):
            if "id" in low and low.endswith("id__c"):
                continue
            parts.append(f"{key.replace('ssot__', '').replace('__c', '')}={value[:120]}")
    return " · ".join(parts[:6])


class SalesforceSource(PlatformLogSource):
    name = "salesforce"

    def __init__(self, http: httpx.Client | None = None):
        self._http = http or httpx.Client(timeout=30)

    def _token(self) -> tuple[str, str]:
        domain = os.environ["SF_MY_DOMAIN"].rstrip("/")
        if not domain.startswith("https://"):
            domain = f"https://{domain}"
        resp = self._http.post(
            f"{domain}/services/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": os.environ["SF_CLIENT_ID"],
                "client_secret": os.environ["SF_CLIENT_SECRET"],
            },
        )
        resp.raise_for_status()
        return domain, resp.json()["access_token"]

    def _soql(self, domain: str, token: str, soql: str) -> list[dict[str, Any]]:
        resp = self._http.get(
            f"{domain}/services/data/{API_VERSION}/query",
            params={"q": soql},
            headers={"authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        records = resp.json().get("records", [])
        for rec in records:
            rec.pop("attributes", None)
        return records

    def harvest(self, store: ObsStore) -> HarvestResult:
        result = HarvestResult(platform=self.name, status="ok")
        if not os.environ.get("SF_MY_DOMAIN") or not os.environ.get("SF_CLIENT_ID"):
            result.status = "blocked"
            result.detail = "SF_MY_DOMAIN / SF_CLIENT_ID not set — source .env first"
            store.set_harvest_status(self.name, result.status, result.detail)
            return result
        try:
            domain, token = self._token()
            sessions = self._soql(
                domain, token, f"SELECT FIELDS(ALL) FROM {SESSION_DMO} LIMIT {ROW_LIMIT}"
            )
            for rec in sessions:
                sid = rec.get("ssot__Id__c") or _first_key(rec, "id__c")
                if not sid:
                    continue
                store.upsert_session(
                    self.name,
                    str(sid),
                    title=str(_first_key(rec, "name") or "Agentforce session"),
                    status=str(_first_key(rec, "status") or _first_key(rec, "endtype") or ""),
                    created_at=str(_timestamp(rec, "start") or "") or None,
                    updated_at=str(_timestamp(rec, "end") or "") or None,
                    raw=rec,
                )
                result.sessions += 1

            for kind, dmo in EVENT_DMOS:
                try:
                    rows = self._soql(
                        domain, token, f"SELECT FIELDS(ALL) FROM {dmo} LIMIT {ROW_LIMIT}"
                    )
                except httpx.HTTPStatusError as exc:
                    result.errors.append(f"{dmo}: HTTP {exc.response.status_code}")
                    continue
                for rec in rows:
                    event_id = rec.get("ssot__Id__c") or _first_key(rec, "id__c")
                    if not event_id:
                        continue
                    session_ref = _first_key(rec, "session", "id") or ""
                    store.upsert_event(
                        self.name,
                        str(session_ref),
                        f"{kind}:{event_id}",
                        event_type=f"sf.{kind}",
                        processed_at=str(
                            _timestamp(rec, "start") or _first_key(rec, "timestamp") or ""
                        )
                        or None,
                        summary=_summary_of(rec) or None,
                        raw=rec,
                    )
                    result.events += 1

            if not sessions:
                result.detail = (
                    "STDM reachable but empty — traced sessions land minutes "
                    "after the agent runs (ingestion lag)"
                )
            if result.errors:
                result.detail = (
                    result.detail + f" · {len(result.errors)} child DMO(s) errored"
                ).strip(" ·")
        except KeyError as exc:
            result.status = "blocked"
            result.detail = f"missing env var {exc}"
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:300]
            if "INVALID_TYPE" in body or "sObject type" in body:
                result.status = "blocked"
                result.detail = (
                    "Session Tracing DMOs not deployed in this org — enable Data "
                    "Cloud Session Tracing + Einstein audit collection in Setup "
                    "(plan/05-observability.md prereqs), then re-harvest."
                )
            elif "UNKNOWN_EXCEPTION" in body:
                # DMO metadata shell exists but the Data Cloud query runtime
                # isn't provisioned — the pre-enablement state.
                result.status = "blocked"
                result.detail = (
                    "STDM DMO exists but its query runtime is not provisioned — "
                    "enable Session Tracing + Einstein audit collection in Setup "
                    "(plan/05-observability.md prereqs), then re-harvest."
                )
            else:
                result.status = "error"
                result.detail = f"HTTP {exc.response.status_code}: {body}"
        except Exception as exc:  # noqa: BLE001 - report, don't raise
            result.status = "error"
            result.detail = f"{type(exc).__name__}: {exc}"
        store.set_harvest_status(self.name, result.status, result.detail)
        return result
