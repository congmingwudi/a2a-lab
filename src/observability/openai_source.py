"""OpenAI log source (M9/M11, built with D24/D25).

There is still no pull API — the Traces dashboard is ingestion-only and
Responses have no list endpoint — so this source works entirely from ids
the lab captured at emit time (the M9 requirement): `platform_ref` on
agents-sdk trace hops (works locally and for AgentCore runs whose hops
land in Aurora) plus the local `.a2alab/openai_responses.json` join file.
Each id is fetched via GET /v1/responses/{id} (responses are stored 30
days by default) and cached into the obs store — the durable record after
OpenAI's TTL expires.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

import httpx

from observability.base import HarvestResult, PlatformLogSource
from observability.store import ObsStore

API_BASE = "https://api.openai.com/v1"
MAX_IDS_ENV = "A2ALAB_OBS_MAX_SESSIONS"
DEFAULT_MAX_IDS = 50


def _default_fetch(response_id: str) -> dict[str, Any]:
    r = httpx.get(
        f"{API_BASE}/responses/{response_id}",
        headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
        timeout=20.0,
    )
    r.raise_for_status()
    return r.json()


def _iso(epoch: Any) -> str | None:
    if not epoch:
        return None
    import time as _time

    return _time.strftime("%Y-%m-%dT%H:%M:%S+00:00", _time.gmtime(float(epoch)))


def _item_summary(item: dict[str, Any]) -> str | None:
    itype = item.get("type")
    if itype == "message":
        parts = [
            c.get("text", "")
            for c in item.get("content") or []
            if isinstance(c, dict) and c.get("type") == "output_text"
        ]
        return "\n".join(p for p in parts if p)[:2000] or None
    if itype == "function_call":
        return f"{item.get('name', '?')}({str(item.get('arguments', ''))[:400]})"
    if itype == "function_call_output":
        return str(item.get("output", ""))[:500] or "(tool output)"
    if itype == "reasoning":
        return "(reasoning)"
    return None


class OpenAISource(PlatformLogSource):
    name = "openai"

    def __init__(self, fetch: Callable[[str], dict[str, Any]] | None = None):
        self._fetch = fetch or _default_fetch
        self.max_ids = int(os.environ.get(MAX_IDS_ENV, DEFAULT_MAX_IDS))

    def _join_file_records(self) -> dict[str, str | None]:
        """response_id -> lab session_id from the local emit-time join file."""
        path = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "openai_responses.json"
        if not path.exists():
            return {}
        try:
            records = json.loads(path.read_text() or "[]")
            return {r["response_id"]: r.get("session_id") for r in records if r.get("response_id")}
        except (ValueError, KeyError, TypeError):
            return {}

    def harvest(self, store: ObsStore) -> HarvestResult:
        result = HarvestResult(platform=self.name, status="ok")
        if not os.environ.get("OPENAI_API_KEY"):
            result.status = "blocked"
            result.detail = "OPENAI_API_KEY not set — cannot fetch stored responses"
            store.set_harvest_status(self.name, result.status, result.detail)
            return result
        try:
            join = self._join_file_records()
            ids: list[str] = []
            ids_from_store = getattr(store, "openai_response_ids", None)
            if callable(ids_from_store):
                ids.extend(ids_from_store(self.max_ids))
            ids.extend(rid for rid in join if rid not in ids)
            ids = ids[: self.max_ids]
            if not ids:
                result.detail = "no captured response ids yet (run the openai scenarios)"
            for rid in ids:
                if store.session_updated_at(self.name, rid) is not None:
                    result.sessions += 1  # responses are immutable — already cached
                    continue
                try:
                    resp = self._fetch(rid)
                except Exception as exc:  # noqa: BLE001 - expired/404 ids shouldn't kill the run
                    result.errors.append(f"{rid}: {type(exc).__name__}: {exc}")
                    continue
                created = _iso(resp.get("created_at"))
                store.upsert_session(
                    self.name,
                    rid,
                    lab_session_id=join.get(rid),
                    title=(resp.get("model") or "response"),
                    status=str(resp.get("status") or ""),
                    created_at=created,
                    updated_at=created,  # immutable — created is the terminal state
                    usage=resp.get("usage"),
                    raw=resp,
                )
                result.sessions += 1
                for i, item in enumerate(resp.get("output") or []):
                    if not isinstance(item, dict):
                        continue
                    store.upsert_event(
                        self.name,
                        rid,
                        item.get("id") or f"{rid}:{i}",
                        event_type=item.get("type"),
                        processed_at=created,
                        summary=_item_summary(item),
                        usage=None,
                        raw=item,
                    )
                    result.events += 1
            if result.errors:
                result.detail = (
                    result.detail
                    + f" · {len(result.errors)} id(s) failed "
                    + "(expired past OpenAI's 30-day TTL or not stored)"
                ).strip(" ·")
        except Exception as exc:  # noqa: BLE001 - report, don't raise
            result.status = "error"
            result.detail = f"{type(exc).__name__}: {exc}"
        store.set_harvest_status(self.name, result.status, result.detail)
        return result
