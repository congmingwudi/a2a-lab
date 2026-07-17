"""Anthropic Managed Agents log source (M11.2).

Pull surface (verified 2026-07-17, beta managed-agents-2026-04-01):
- GET /v1/sessions           — paginated workspace-wide listing (no
  created_after filter, so we walk newest-first up to max_sessions).
- GET /v1/sessions/{id}/events — full persisted history: agent.message /
  agent.thinking / tool events / span events with token usage.
Events die with the session and there is no usage-aggregation API — harvest
into the local store is the durable record. Correlation: the lab persists
every CMA session id it creates (.a2alab/cma_sessions.json + platform_ref
on trace events), which we join back to lab session ids here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from observability.base import HarvestResult, PlatformLogSource
from observability.store import ObsStore

MAX_SESSIONS_ENV = "A2ALAB_OBS_MAX_SESSIONS"
DEFAULT_MAX_SESSIONS = 50


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _dump(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return {"repr": repr(obj)}


def _text_of(event: Any) -> str:
    parts = []
    for block in getattr(event, "content", None) or []:
        text = getattr(block, "text", None) or getattr(block, "thinking", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts)


def _summarize(event: Any) -> tuple[str | None, Any]:
    """(summary, usage) for one CMA event — what the drill-down lists."""
    etype = getattr(event, "type", "")
    if etype in ("agent.message", "agent.thinking", "user.message"):
        return _text_of(event) or None, None
    if etype in ("agent.tool_use", "agent.mcp_tool_use", "agent.custom_tool_use"):
        name = getattr(event, "name", "?")
        try:
            args = json.dumps(dict(getattr(event, "input", None) or {}), default=str)[:500]
        except (TypeError, ValueError):
            args = ""
        return f"{name}({args})", None
    if etype in ("agent.tool_result", "agent.mcp_tool_result"):
        return _text_of(event)[:500] or "(tool result)", None
    if etype == "span.model_request_end":
        usage = getattr(event, "model_usage", None)
        return "model request finished", _dump(usage) if usage is not None else None
    if etype == "session.status_idle":
        stop = getattr(event, "stop_reason", None)
        return f"idle ({getattr(stop, 'type', '?')})", None
    return None, None


class AnthropicSource(PlatformLogSource):
    name = "anthropic"

    def __init__(self, client=None, max_sessions: int | None = None):
        self._client = client
        self.max_sessions = max_sessions or int(
            os.environ.get(MAX_SESSIONS_ENV, DEFAULT_MAX_SESSIONS)
        )

    def _get_client(self):
        if self._client is None:
            from anthropic import Anthropic

            self._client = Anthropic()
        return self._client

    def _lab_session_map(self) -> dict[str, str | None]:
        """cma_session_id -> lab_session_id from the persisted join file."""
        path = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "cma_sessions.json"
        if not path.exists():
            return {}
        try:
            records = json.loads(path.read_text() or "[]")
            return {r["cma_session_id"]: r.get("lab_session_id") for r in records}
        except (ValueError, KeyError, TypeError):
            return {}

    def harvest(self, store: ObsStore) -> HarvestResult:
        result = HarvestResult(platform=self.name, status="ok")
        try:
            client = self._get_client()
            lab_map = self._lab_session_map()
            seen = 0
            for session in client.beta.sessions.list():
                if seen >= self.max_sessions:
                    result.detail = f"capped at {self.max_sessions} newest sessions"
                    break
                seen += 1
                sid = session.id
                updated = _iso(getattr(session, "updated_at", None))
                unchanged = (
                    updated is not None and store.session_updated_at(self.name, sid) == updated
                )
                store.upsert_session(
                    self.name,
                    sid,
                    lab_session_id=lab_map.get(sid),
                    title=getattr(session, "title", None),
                    status=str(getattr(session, "status", "") or ""),
                    created_at=_iso(getattr(session, "created_at", None)),
                    updated_at=updated,
                    usage=_dump(getattr(session, "usage", None))
                    if getattr(session, "usage", None)
                    else None,
                    raw=_dump(session),
                )
                result.sessions += 1
                if unchanged:
                    continue  # events already harvested for this state
                try:
                    for event in client.beta.sessions.events.list(session_id=sid):
                        summary, usage = _summarize(event)
                        store.upsert_event(
                            self.name,
                            sid,
                            getattr(event, "id", "") or f"{sid}:{result.events}",
                            event_type=getattr(event, "type", None),
                            processed_at=_iso(getattr(event, "processed_at", None)),
                            summary=summary,
                            usage=usage,
                            raw=_dump(event),
                        )
                        result.events += 1
                except Exception as exc:  # noqa: BLE001 - one bad session shouldn't kill the run
                    result.errors.append(f"{sid}: {type(exc).__name__}: {exc}")
            if result.errors:
                result.status = "ok"
                result.detail = (
                    result.detail + f" · {len(result.errors)} session(s) errored"
                ).strip(" ·")
        except Exception as exc:  # noqa: BLE001 - report, don't raise
            result.status = "error"
            result.detail = f"{type(exc).__name__}: {exc}"
        store.set_harvest_status(self.name, result.status, result.detail)
        return result
