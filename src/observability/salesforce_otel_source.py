"""Agentforce Session Trace OTel log source — built, not yet the live path.

Pull surface: the Agentforce Session Trace OpenTelemetry API,
`GET /services/data/v66.0/einstein/audit/otel/{session-id}` (beta,
https://developer.salesforce.com/docs/ai/agentforce/guide/otel-api.html).
It returns the session's trace as an OTLP/JSON `resourceSpans` document —
turns, messages, LLM calls, actions, metric scores and feedback, each a span
— pre-joined and standards-shaped, over the SAME Data 360 data the live
`SalesforceSource` assembles by hand from four STDM DMOs.

WHY this exists but is NOT the live harvest (D73, WS23): the OTel API is
single-session (no bulk read), 72h lookback, and beta. Those limits mean it
cannot back the "harvested from all platforms" coverage sweep the way the DMO
path can — a bulk `SELECT FIELDS(ALL)` over every session in the store. So the
DMO source stays the live path (`salesforce`), and this source ships alongside
it under its OWN platform name (`salesforce-otel`) so the two can be compared
without either clobbering the other's rows. When the API leaves beta and grows
a bulk read, switching the live path is a one-line change in obs_harvest's
`PLATFORM_SOURCES`, not a rewrite — that is the whole point of building it now.

What the OTel path BUYS over the DMO path, on the same data: the join is the
server's (no manual interaction→session FK walk, so no orphan class — the bug
that stranded 823 events, see salesforce_source.py), a stable OTLP schema
instead of drift-prone `ssot__*` column heuristics, and one round trip per
session instead of paged `FIELDS(ALL)` across four DMOs. What it does NOT buy:
different data. It is the same Data 360 record read through a standard view.

Session enumeration: the API takes ONE session id, so a harvest needs a list.
Two honest sources, in order: `A2ALAB_OTEL_SESSION_IDS` (comma-separated,
explicit — the deterministic path used by the test and by a targeted re-pull),
else a thin id-only query against the session DMO (the same Data Cloud surface
the live path already reads) capped at `A2ALAB_OTEL_MAX_SESSIONS`. The runtime
session id the OTel endpoint wants is the agent-session id; STDM exposes it as
`ssot__AiAgentSessionId__c` on newer orgs and only the surrogate `ssot__Id__c`
on others, so the enumerator prefers the former and falls back — and because
that mapping is unverified against a live beta org, a 404 per id is reported,
not raised.

Auth: the F6 per-caller identity — the a2a_lab_obs External Client App, the
same client-credentials app the live DMO harvest uses (D37).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx

from observability.base import HarvestResult, PlatformLogSource
from observability.store import ObsStore

API_VERSION = "v66.0"
# One GET per session and a beta endpoint — keep the default pull small; raise
# with A2ALAB_OTEL_MAX_SESSIONS when a fuller pull is wanted.
DEFAULT_MAX_SESSIONS = 25

SESSION_DMO = "ssot__AiAgentSession__dlm"
# Preferred runtime-session-id column, then the Data Cloud surrogate PK.
SESSION_ID_FIELDS = ("ssot__AiAgentSessionId__c", "ssot__Id__c")
# Session start time — the ORDER BY that makes the picker return the NEWEST
# sessions (without it, LIMIT returns an arbitrary page and recent runs are
# invisible). Present on this org's session DMO; the query falls back if absent.
SESSION_START_FIELD = "ssot__StartTimestamp__c"


def _nano_to_iso(nano: Any) -> str | None:
    """OTLP unix-nanoseconds (a string or int) → ISO8601 UTC, or None."""
    try:
        secs = int(nano) / 1e9
    except (TypeError, ValueError):
        return None
    if secs <= 0:
        return None
    return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat()


def _attr_value(value: dict[str, Any]) -> Any:
    """Unwrap an OTLP AnyValue oneof to a plain Python value."""
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "boolValue", "intValue", "doubleValue"):
        if key in value:
            return value[key]
    if "arrayValue" in value:
        items = (value.get("arrayValue") or {}).get("values", [])
        return [_attr_value(v) for v in items]
    if "kvlistValue" in value:
        return _attrs((value.get("kvlistValue") or {}).get("values", []))
    return None


def _attrs(kvlist: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Flatten an OTLP KeyValue list into a plain dict."""
    out: dict[str, Any] = {}
    for kv in kvlist or []:
        key = kv.get("key")
        if key:
            out[key] = _attr_value(kv.get("value", {}))
    return out


def _span_type(name: str, attrs: dict[str, Any]) -> str:
    """A stable event_type for a span — a semantic attribute if present, else
    the span name; always prefixed `otel.` so it reads as this source's."""
    for hint in ("gen_ai.operation.name", "operation.name", "span.type", "agentforce.type"):
        val = attrs.get(hint)
        if isinstance(val, str) and val:
            return f"otel.{val}"
    return f"otel.{(name or 'span').strip()[:60]}"


def _span_summary(name: str, attrs: dict[str, Any], status: dict[str, Any]) -> str:
    parts: list[str] = []
    if name:
        parts.append(name)
    for key in ("gen_ai.request.model", "gen_ai.usage.total_tokens", "agentforce.action"):
        if key in attrs and attrs[key] not in (None, ""):
            parts.append(f"{key.split('.')[-1]}={attrs[key]}")
    msg = (status or {}).get("message")
    if msg:
        parts.append(f"status={msg}")
    return " · ".join(str(p) for p in parts)[:2000]


class SalesforceOtelSource(PlatformLogSource):
    name = "salesforce-otel"

    def __init__(self, http: httpx.Client | None = None):
        self._http = http or httpx.Client(timeout=30)

    def _token(self) -> tuple[str, str]:
        """Same F6 obs identity as the DMO source (a2a_lab_obs ECA, D37)."""
        domain = os.environ["SF_MY_DOMAIN"].rstrip("/")
        if not domain.startswith("https://"):
            domain = f"https://{domain}"
        client_id = os.environ.get("SF_CLIENT_ID_OBS") or os.environ["SF_CLIENT_ID"]
        client_secret = os.environ.get("SF_CLIENT_SECRET_OBS") or os.environ["SF_CLIENT_SECRET"]
        resp = self._http.post(
            f"{domain}/services/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        resp.raise_for_status()
        return domain, resp.json()["access_token"]

    def _max_sessions(self) -> int:
        try:
            return max(1, int(os.environ.get("A2ALAB_OTEL_MAX_SESSIONS", DEFAULT_MAX_SESSIONS)))
        except ValueError:
            return DEFAULT_MAX_SESSIONS

    def _session_ids(self, domain: str, token: str) -> list[str]:
        """Explicit ids if given, else a thin id-only query on the session DMO."""
        explicit = os.environ.get("A2ALAB_OTEL_SESSION_IDS", "")
        if explicit.strip():
            ids = [s.strip() for s in explicit.split(",") if s.strip()]
            return ids[: self._max_sessions()]
        # Newest first: without ORDER BY, LIMIT returns an ARBITRARY page, so the
        # picker showed stale sessions and a just-finished run never appeared
        # (the "last 6-7 find nothing" report, 2026-08-12). ssot__StartTimestamp__c
        # is the session DMO's start field (confirmed present on this org). Each
        # SOQL is tried in turn and the first that succeeds wins, so an org that
        # lacks the runtime-id column OR the timestamp column still degrades to a
        # bare id-only query rather than erroring.
        cols = ", ".join(SESSION_ID_FIELDS)
        ordered = f"ORDER BY {SESSION_START_FIELD} DESC"
        attempts = [
            f"SELECT {cols} FROM {SESSION_DMO} {ordered} LIMIT {self._max_sessions()}",
            f"SELECT ssot__Id__c FROM {SESSION_DMO} {ordered} LIMIT {self._max_sessions()}",
            f"SELECT ssot__Id__c FROM {SESSION_DMO} LIMIT {self._max_sessions()}",
        ]
        resp = None
        for soql in attempts:
            try:
                resp = self._http.get(
                    f"{domain}/services/data/{API_VERSION}/query",
                    params={"q": soql},
                    headers={"authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                break
            except httpx.HTTPStatusError:
                resp = None
        if resp is None:
            return []
        ids: list[str] = []
        for rec in resp.json().get("records", []):
            for field in SESSION_ID_FIELDS:
                val = rec.get(field)
                if val:
                    ids.append(str(val))
                    break
        return ids

    def _fetch_otel(self, domain: str, token: str, session_id: str) -> dict[str, Any] | None:
        """The OTLP document for one session, or None on a 404 (unknown/aged-out
        session — expected on the beta endpoint, reported not raised)."""
        resp = self._http.get(
            f"{domain}/services/data/{API_VERSION}/einstein/audit/otel/{session_id}",
            headers={"authorization": f"Bearer {token}", "accept": "application/json"},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    # -- public surface for the console Session Trace tab (WS23) --------------
    # The harvest() path above is for the batch pull into the store; the console
    # wants the two live primitives on their own — enumerate ids for a picker,
    # and fetch ONE session's trace on demand — without touching a store. Both
    # authenticate as the same F6 obs identity as harvest().

    def list_session_ids(self) -> list[str]:
        """Recent agent-session ids — explicit `A2ALAB_OTEL_SESSION_IDS` if set,
        else a thin id-only query on the session DMO, capped at
        `A2ALAB_OTEL_MAX_SESSIONS`. For the console picker and a targeted re-pull."""
        domain, token = self._token()
        return self._session_ids(domain, token)

    def fetch_trace(self, session_id: str) -> dict[str, Any] | None:
        """The LIVE OTLP document for one session, or None on a 404 (unknown /
        aged-out — 72h beta lookback). Other HTTP statuses raise
        httpx.HTTPStatusError so the caller can distinguish 'beta not enabled'
        (401/403) from a real error."""
        domain, token = self._token()
        return self._fetch_otel(domain, token, session_id)

    def _ingest(self, store: ObsStore, session_id: str, doc: dict[str, Any]) -> int:
        """Map one OTLP ResourceSpans document → one session + N span events.
        Returns the span count."""
        spans: list[tuple[dict[str, Any], dict[str, Any]]] = []  # (span, resource_attrs)
        for rspan in doc.get("resourceSpans", []):
            res_attrs = _attrs((rspan.get("resource") or {}).get("attributes"))
            for sspan in rspan.get("scopeSpans", []):
                for span in sspan.get("spans", []):
                    spans.append((span, res_attrs))

        starts = [s.get("startTimeUnixNano") for s, _ in spans]
        ends = [s.get("endTimeUnixNano") for s, _ in spans]
        created = min((_nano_to_iso(x) for x in starts if _nano_to_iso(x)), default=None)
        updated = max((_nano_to_iso(x) for x in ends if _nano_to_iso(x)), default=None)

        # Session title/status from resource attributes / the root span.
        res0 = spans[0][1] if spans else {}
        title = str(
            res0.get("service.name") or res0.get("gen_ai.agent.name") or "Agentforce session"
        )
        status = ""
        for span, _ in spans:
            if not span.get("parentSpanId"):
                status = str((span.get("status") or {}).get("message") or "")
                break

        store.upsert_session(
            self.name,
            session_id,
            title=title,
            status=status,
            created_at=created,
            updated_at=updated,
            raw=doc,
        )

        for span, _ in spans:
            span_id = span.get("spanId") or span.get("span_id")
            if not span_id:
                continue
            attrs = _attrs(span.get("attributes"))
            span_status = span.get("status") or {}
            store.upsert_event(
                self.name,
                session_id,
                f"span:{span_id}",
                event_type=_span_type(str(span.get("name") or ""), attrs),
                processed_at=_nano_to_iso(span.get("startTimeUnixNano")),
                summary=_span_summary(str(span.get("name") or ""), attrs, span_status) or None,
                raw=span,
            )
        return len(spans)

    def harvest(self, store: ObsStore) -> HarvestResult:
        result = HarvestResult(platform=self.name, status="ok")
        if not os.environ.get("SF_MY_DOMAIN") or not os.environ.get("SF_CLIENT_ID"):
            result.status = "blocked"
            result.detail = "SF_MY_DOMAIN / SF_CLIENT_ID not set — source .env first"
            store.set_harvest_status(self.name, result.status, result.detail)
            return result
        try:
            domain, token = self._token()
            session_ids = self._session_ids(domain, token)
            if not session_ids:
                result.detail = (
                    "no sessions to trace — set A2ALAB_OTEL_SESSION_IDS or run an "
                    "agent session first (72h lookback)"
                )
                store.set_harvest_status(self.name, result.status, result.detail)
                return result
            missing = 0
            for sid in session_ids:
                try:
                    doc = self._fetch_otel(domain, token, sid)
                except httpx.HTTPStatusError as exc:
                    result.errors.append(f"{sid}: HTTP {exc.response.status_code}")
                    continue
                if doc is None:
                    missing += 1
                    continue
                result.events += self._ingest(store, sid, doc)
                result.sessions += 1
            detail = f"{result.sessions}/{len(session_ids)} session trace(s) pulled via OTel API"
            if missing:
                detail += f" · {missing} not found (unknown/aged-out, 72h lookback)"
            if result.errors:
                detail += f" · {len(result.errors)} errored"
            result.detail = detail
        except KeyError as exc:
            result.status = "blocked"
            result.detail = f"missing env var {exc}"
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:300]
            if exc.response.status_code in (403, 404) and "otel" in body.lower():
                result.status = "blocked"
                result.detail = (
                    "Session Trace OTel API not enabled for this org/user — it is "
                    "beta (https://developer.salesforce.com/docs/ai/agentforce/"
                    "guide/otel-api.html); the live harvest stays on the DMO path."
                )
            else:
                result.status = "error"
                result.detail = f"HTTP {exc.response.status_code}: {body}"
        except Exception as exc:  # noqa: BLE001 - report, don't raise
            result.status = "error"
            result.detail = f"{type(exc).__name__}: {exc}"
        store.set_harvest_status(self.name, result.status, result.detail)
        return result
