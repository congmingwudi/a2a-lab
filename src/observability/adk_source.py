"""ADK / Vertex AI Agent Engine log source (WS2, M11.2).

What the platform exposes today (A2A serving is Preview): **Cloud Logging**
entries per ReasoningEngine — container app logs and request lines — and
Cloud Trace spans. There is no session/turn read API on the preview A2A
surface, and A2A contextIds do not appear in the default logs, so the
honest shape is one obs "session" per deployed engine with its log entries
as events. (Contrast columns: Salesforce = queryable session/step DMOs,
Anthropic = deep per-session events, OpenAI = write-only. GCP lands
between: real, queryable telemetry — but request-level, not agent-semantic.)

Auth rides the same ADC credentials the lab already uses for the data
plane; no extra client library — plain Logging REST via AuthorizedSession.
"""

from __future__ import annotations

import os
from typing import Any

from observability.base import HarvestResult, PlatformLogSource
from observability.store import ObsStore

LOGGING_URL = "https://logging.googleapis.com/v2/entries:list"
WINDOW_HOURS = 24
MAX_ENTRIES = 500


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

            credentials, _ = google_default(
                scopes=["https://www.googleapis.com/auth/logging.read"]
            )
            session = AuthorizedSession(credentials)
            since = (
                dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=WINDOW_HOURS)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
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

        result = HarvestResult(platform=self.name, status="ok")
        store.upsert_session(
            self.name,
            engine_id,
            title=f"Agent Engine {engine_id} (a2alab-adk-researcher)",
            status="active",
            raw={"resource_name": engine, "note": "one session per engine — "
                 "preview A2A exposes no session/turn API; events are Cloud "
                 "Logging entries"},
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
        result.detail = (
            f"{result.events} log entries (last {WINDOW_HOURS}h) — request-level "
            "telemetry; no session/turn API on the preview A2A surface"
        )
        store.set_harvest_status(self.name, result.status, result.detail)
        return result
