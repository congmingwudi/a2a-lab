"""The Lab Guide's read tools — curated accessors, not a store surface.

Read-only by construction (plan/07-workstreams.md, Lab Guide): briefs come
from the hosted analyst's output in Aurora, traces from the local jsonl
archive merged with recent Aurora hops (hosted-mode containers write their
hops there, D23/D26). No SQL surface — that stays the analyst's (D23).
Every accessor soft-fails to an honest empty/"unavailable" shape so the
guide can say so instead of erroring.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from interop.trace import DEFAULT_TRACE_DIR, TRACE_DIR_ENV

_MAX_PAYLOAD_CHARS = 900
_REMOTE_WINDOW_S = 6 * 3600


def _trace_dir() -> Path:
    return Path(os.environ.get(TRACE_DIR_ENV, DEFAULT_TRACE_DIR))


def _local_events(max_files: int = 4) -> list[dict]:
    files = sorted(_trace_dir().glob("*.jsonl"))[-max_files:]
    events: list[dict] = []
    for f in files:
        try:
            with f.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return events


def _remote_events() -> list[dict]:
    """Recent Aurora hops (hosted runtimes write there directly); [] when
    the PG store isn't configured or reachable — local-only is honest."""
    try:
        from observability.pg import SCHEMA, PgClient

        if not PgClient.configured():
            return []
        client = PgClient.from_env()
        try:
            return client.execute(
                f"""SELECT trace_id, hop_seq, ts, source, target, protocol,
                           transport_detail, status, latency_ms, platform_ref,
                           request_payload_raw::text AS request_payload_raw,
                           response_payload_raw::text AS response_payload_raw
                    FROM {SCHEMA}.trace_events
                    WHERE ts > :since ORDER BY ts LIMIT 2000""",
                {"since": time.time() - _REMOTE_WINDOW_S},
            )
        finally:
            client.close()
    except Exception:
        return []


def _merged_events() -> list[dict]:
    events = _local_events()
    seen = {
        (e.get("trace_id"), e.get("hop_seq"), round(e.get("ts") or 0, 4), e.get("source"))
        for e in events
    }
    for ev in _remote_events():
        key = (ev.get("trace_id"), ev.get("hop_seq"), round(ev.get("ts") or 0, 4), ev.get("source"))
        if key not in seen:
            events.append(ev)
    return events


def _clip(value: Any) -> Any:
    raw = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    if raw and len(raw) > _MAX_PAYLOAD_CHARS:
        return raw[:_MAX_PAYLOAD_CHARS] + f"… [clipped, {len(raw)} chars]"
    return value


def list_recent_runs(limit: int = 10, target_contains: str | None = None) -> list[dict]:
    """Recent lab runs, newest first: one row per trace with the hop count
    and the targets/protocols touched — enough to pick a trace_id for
    get_trace without reading payloads."""
    by_trace: dict[str, dict] = {}
    for ev in _merged_events():
        tid = ev.get("trace_id")
        if not tid:
            continue
        row = by_trace.setdefault(
            tid,
            {"trace_id": tid, "started": ev.get("ts"), "hops": 0, "targets": [], "protocols": []},
        )
        row["hops"] += 1
        row["started"] = min(row["started"] or ev.get("ts"), ev.get("ts") or row["started"])
        for key, val in (("targets", ev.get("target")), ("protocols", ev.get("protocol"))):
            if val and val not in row[key]:
                row[key].append(val)
    rows = sorted(by_trace.values(), key=lambda r: r["started"] or 0, reverse=True)
    if target_contains:
        needle = target_contains.lower()
        rows = [r for r in rows if any(needle in t.lower() for t in r["targets"])]
    return rows[:limit]


def get_trace(trace_id: str) -> dict:
    """One run's full hop list (payloads clipped to budget) from the merged
    local+Aurora view — the actual wire record, not a summary."""
    hops = [e for e in _merged_events() if e.get("trace_id") == trace_id]
    if not hops:
        return {"trace_id": trace_id, "hops": [], "note": "no hops found for this trace id"}
    hops.sort(key=lambda e: (e.get("ts") or 0, e.get("hop_seq") or 0))
    out = []
    for e in hops:
        out.append(
            {
                "ts": e.get("ts"),
                "source": e.get("source"),
                "target": e.get("target"),
                "protocol": e.get("protocol"),
                "transport_detail": e.get("transport_detail"),
                "status": e.get("status"),
                "latency_ms": e.get("latency_ms"),
                "request": _clip(e.get("request_payload_raw")),
                "response": _clip(e.get("response_payload_raw")),
            }
        )
    return {"trace_id": trace_id, "hops": out}


def list_briefs(limit: int = 5) -> list[dict]:
    """The hosted obs analyst's findings briefs (D23) — headers only; read
    one with read_brief. [] when the Aurora store isn't configured."""
    try:
        from observability.pg import PgClient, PgObsStore

        if not PgClient.configured():
            return []
        store = PgObsStore()
        try:
            rows = store.list_briefs(limit=limit)
        finally:
            store.close()
        return [
            {
                "id": r.get("id"),
                "brief_date": r.get("brief_date"),
                "queries_run": r.get("queries_run"),
                "preview": (r.get("brief_md") or "")[:200],
            }
            for r in rows
        ]
    except Exception:
        return []


def read_brief(brief_id: int) -> dict:
    """One analyst brief in full."""
    try:
        from observability.pg import PgClient, PgObsStore

        if not PgClient.configured():
            return {"error": "hosted obs store not configured (A2ALAB_PG_*)"}
        store = PgObsStore()
        try:
            rows = store.list_briefs(limit=50)
        finally:
            store.close()
        for r in rows:
            if r.get("id") == brief_id:
                return {
                    "id": r.get("id"),
                    "brief_date": r.get("brief_date"),
                    "queries_run": r.get("queries_run"),
                    "brief_md": r.get("brief_md"),
                }
        return {"error": f"no brief with id {brief_id}"}
    except Exception as exc:
        return {"error": f"brief store unavailable: {type(exc).__name__}"}
