"""Local observability store (M11.1/D19): obs_* tables in traces/lab.db.

Harvest-and-cache, not live-proxy: platform logs lag, cost credits, or
expire (CMA events die with the session; OpenAI responses in 30 days), so
this store is the durable superset and the console reads only from here.
Same ethos as the wire traces (D7): every harvested record keeps the raw
platform payload alongside the normalized columns.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from interop.trace import DEFAULT_DB_NAME, DEFAULT_TRACE_DIR, TRACE_DIR_ENV

_MAX_RAW_CHARS = 100_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS obs_sessions (
    platform        TEXT NOT NULL,
    native_id       TEXT NOT NULL,
    lab_session_id  TEXT,
    title           TEXT,
    status          TEXT,
    created_at      TEXT,
    updated_at      TEXT,
    usage_json      TEXT,
    raw_json        TEXT,
    harvested_at    REAL,
    PRIMARY KEY (platform, native_id)
);
CREATE TABLE IF NOT EXISTS obs_events (
    platform            TEXT NOT NULL,
    native_session_id   TEXT NOT NULL,
    event_id            TEXT NOT NULL,
    event_type          TEXT,
    processed_at        TEXT,
    summary             TEXT,
    usage_json          TEXT,
    raw_json            TEXT,
    harvested_at        REAL,
    PRIMARY KEY (platform, event_id)
);
CREATE INDEX IF NOT EXISTS idx_obs_events_session
    ON obs_events (platform, native_session_id);
CREATE TABLE IF NOT EXISTS obs_harvest (
    platform        TEXT PRIMARY KEY,
    last_harvest_at REAL,
    status          TEXT,
    detail          TEXT
);
"""


def _clip_json(value: Any) -> str:
    raw = json.dumps(value, default=str, ensure_ascii=False)
    if len(raw) > _MAX_RAW_CHARS:
        return json.dumps({"_clipped": True, "chars": len(raw), "head": raw[:_MAX_RAW_CHARS]})
    return raw


def default_db_path() -> Path:
    return Path(os.environ.get(TRACE_DIR_ENV, DEFAULT_TRACE_DIR)) / DEFAULT_DB_NAME


class ObsStore:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else default_db_path()
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ---- writes -----------------------------------------------------------

    def upsert_session(
        self,
        platform: str,
        native_id: str,
        *,
        lab_session_id: str | None = None,
        title: str | None = None,
        status: str | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
        usage: Any = None,
        raw: Any = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO obs_sessions
                   (platform, native_id, lab_session_id, title, status,
                    created_at, updated_at, usage_json, raw_json, harvested_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (platform, native_id) DO UPDATE SET
                     lab_session_id = COALESCE(excluded.lab_session_id, obs_sessions.lab_session_id),
                     title = excluded.title, status = excluded.status,
                     created_at = excluded.created_at, updated_at = excluded.updated_at,
                     usage_json = excluded.usage_json, raw_json = excluded.raw_json,
                     harvested_at = excluded.harvested_at""",
                (
                    platform,
                    native_id,
                    lab_session_id,
                    title,
                    status,
                    created_at,
                    updated_at,
                    _clip_json(usage) if usage is not None else None,
                    _clip_json(raw) if raw is not None else None,
                    time.time(),
                ),
            )
            self._conn.commit()

    def upsert_event(
        self,
        platform: str,
        native_session_id: str,
        event_id: str,
        *,
        event_type: str | None = None,
        processed_at: str | None = None,
        summary: str | None = None,
        usage: Any = None,
        raw: Any = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO obs_events
                   (platform, native_session_id, event_id, event_type,
                    processed_at, summary, usage_json, raw_json, harvested_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    platform,
                    native_session_id,
                    event_id,
                    event_type,
                    processed_at,
                    (summary or "")[:2000] or None,
                    _clip_json(usage) if usage is not None else None,
                    _clip_json(raw) if raw is not None else None,
                    time.time(),
                ),
            )
            self._conn.commit()

    def set_harvest_status(self, platform: str, status: str, detail: str = "") -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO obs_harvest
                   (platform, last_harvest_at, status, detail) VALUES (?, ?, ?, ?)""",
                (platform, time.time(), status, detail[:2000]),
            )
            self._conn.commit()

    # ---- reads (console API) ---------------------------------------------

    def session_updated_at(self, platform: str, native_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT updated_at FROM obs_sessions WHERE platform = ? AND native_id = ?",
            (platform, native_id),
        ).fetchone()
        return row["updated_at"] if row else None

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {"platforms": {}}
        for row in self._conn.execute(
            """SELECT platform, COUNT(*) AS sessions FROM obs_sessions GROUP BY platform"""
        ):
            out["platforms"].setdefault(row["platform"], {})["sessions"] = row["sessions"]
        for row in self._conn.execute(
            """SELECT platform, COUNT(*) AS events FROM obs_events GROUP BY platform"""
        ):
            out["platforms"].setdefault(row["platform"], {})["events"] = row["events"]
        for row in self._conn.execute("SELECT * FROM obs_harvest"):
            out["platforms"].setdefault(row["platform"], {})["harvest"] = {
                "at": row["last_harvest_at"],
                "status": row["status"],
                "detail": row["detail"],
            }
        # token totals per platform (from harvested usage)
        for row in self._conn.execute(
            "SELECT platform, usage_json FROM obs_sessions WHERE usage_json IS NOT NULL"
        ):
            try:
                usage = json.loads(row["usage_json"])
                tokens = sum(
                    int(usage.get(k) or 0)
                    for k in (
                        "input_tokens",
                        "output_tokens",
                        "cache_creation_input_tokens",
                        "cache_read_input_tokens",
                    )
                )
                plat = out["platforms"].setdefault(row["platform"], {})
                plat["tokens"] = plat.get("tokens", 0) + tokens
                # Platforms that bill something other than tokens (Agent
                # Engine bills allocated compute) surface an estimated-cost
                # rollup instead/in addition — additive and optional.
                if usage.get("est_cost_usd") is not None:
                    plat["est_cost_usd"] = round(
                        plat.get("est_cost_usd", 0.0) + float(usage["est_cost_usd"]), 4
                    )
            except (ValueError, TypeError, AttributeError):
                pass
        return out

    def list_sessions(self, platform: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        q = """SELECT s.*, (
                 SELECT COUNT(*) FROM obs_events e
                 WHERE e.platform = s.platform AND e.native_session_id = s.native_id
               ) AS event_count,
               (
                 SELECT COUNT(DISTINCT t.trace_id) FROM trace_events t
                 WHERE t.platform_ref = s.native_id
               ) AS lab_trace_count
               FROM obs_sessions s"""
        args: list[Any] = []
        if platform:
            q += " WHERE s.platform = ?"
            args.append(platform)
        q += " ORDER BY COALESCE(s.created_at, '') DESC LIMIT ?"
        args.append(limit)
        rows = self._safe_query(q, args)
        return [dict(r) for r in rows]

    def list_events(self, platform: str, native_session_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT * FROM obs_events
               WHERE platform = ? AND native_session_id = ?
               ORDER BY COALESCE(processed_at, ''), event_id""",
            (platform, native_session_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def openai_response_ids(self, limit: int = 50) -> list[str]:
        """Newest-first OpenAI response ids captured at emit time as
        platform_ref on agents-sdk hops (M9/D18 — the only join key that
        exists; OpenAI has no list/read-back API)."""
        rows = self._safe_query(
            """SELECT platform_ref, MAX(ts) AS ts FROM trace_events
               WHERE target = 'openai-platform' AND platform_ref IS NOT NULL
               GROUP BY platform_ref ORDER BY ts DESC LIMIT ?""",
            [limit],
        )
        return [r["platform_ref"] for r in rows]

    def lab_traces_for(self, native_id: str) -> list[str]:
        rows = self._safe_query(
            "SELECT DISTINCT trace_id FROM trace_events WHERE platform_ref = ?",
            [native_id],
        )
        return [r["trace_id"] for r in rows]

    def _safe_query(self, q: str, args: list[Any]):
        """trace_events lives in the same DB but is created by SqliteSink —
        tolerate its absence (e.g. fresh checkout, jsonl-only sink)."""
        try:
            return self._conn.execute(q, args).fetchall()
        except sqlite3.OperationalError:
            if "trace_events" in q:
                stripped = q.replace(
                    """(
                 SELECT COUNT(DISTINCT t.trace_id) FROM trace_events t
                 WHERE t.platform_ref = s.native_id
               ) AS lab_trace_count""",
                    "0 AS lab_trace_count",
                )
                if stripped != q:
                    return self._conn.execute(stripped, args).fetchall()
            return []

    def close(self) -> None:
        self._conn.close()
