"""Local observability store (M11.1/D19): obs_* tables in traces/lab.db.

Harvest-and-cache, not live-proxy: platform logs lag, cost credits, or
expire (CMA events die with the session; OpenAI responses in 30 days), so
this store is the durable superset and the console reads only from here.
Same ethos as the wire traces (D7): every harvested record keeps the raw
platform payload alongside the normalized columns.
"""

from __future__ import annotations

import json
import re
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
CREATE TABLE IF NOT EXISTS infra_metrics (
    cloud           TEXT NOT NULL,
    resource        TEXT NOT NULL,
    metric          TEXT NOT NULL,
    ts_at           TEXT NOT NULL,
    value           REAL,
    unit            TEXT,
    labels_json     TEXT,
    harvested_at    REAL,
    PRIMARY KEY (cloud, resource, metric, ts_at)
);
CREATE INDEX IF NOT EXISTS idx_infra_metrics_ts
    ON infra_metrics (cloud, resource, metric, ts_at);
"""


def _clip_json(value: Any) -> str:
    # Credential scrub before write (F2) — same redactor as the trace layer,
    # so harvested platform logs (which can embed auth material in captured
    # messages) get the same treatment as wire payloads. pg.py imports this,
    # so sqlite and Aurora writes both pass through here.
    from interop.trace import redact

    raw = json.dumps(redact(value), default=str, ensure_ascii=False)
    if len(raw) > _MAX_RAW_CHARS:
        return json.dumps({"_clipped": True, "chars": len(raw), "head": raw[:_MAX_RAW_CHARS]})
    return raw


def default_db_path() -> Path:
    return Path(os.environ.get(TRACE_DIR_ENV, DEFAULT_TRACE_DIR)) / DEFAULT_DB_NAME


# The rider regexes and the usage rollup are shared with PgObsStore (D49).
# They encode what the console RENDERS, so a second copy in pg.py would be a
# second thing to keep in step — and the Postgres store is now the only one the
# console reads, which is exactly when a silent divergence would go unnoticed.
CALLER_RIDER_RE = re.compile(r"caller-agent:\\?n?\s*([\w-]+)")
LAB_TRACE_RIDER_RE = re.compile(r"lab-trace:\\?n?\s*([0-9a-fA-F-]{8,})")

_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def accumulate_usage(platforms: dict[str, Any], platform: str, usage_json: Any) -> None:
    """Fold one session's harvested usage into the per-platform rollup.

    Tolerant by design: a platform that reports no usage, or reports it in a
    shape we do not recognise, must not break the whole summary — the panel is
    a coverage report, and one unparsable row should cost that row only.
    """
    try:
        usage = json.loads(usage_json) if isinstance(usage_json, str) else usage_json
        tokens = sum(int(usage.get(k) or 0) for k in _TOKEN_FIELDS)
        plat = platforms.setdefault(platform, {})
        plat["tokens"] = plat.get("tokens", 0) + tokens
        # Platforms that bill something other than tokens (Agent Engine bills
        # allocated compute) surface an estimated-cost rollup instead/in
        # addition — additive and optional.
        if usage.get("est_cost_usd") is not None:
            plat["est_cost_usd"] = round(
                plat.get("est_cost_usd", 0.0) + float(usage["est_cost_usd"]), 4
            )
    except (ValueError, TypeError, AttributeError):
        pass


def _shape_infra_series(rows: Any, *, max_points: int = 240) -> list[dict[str, Any]]:
    """Group flat infra_metrics rows into one entry per (cloud, resource,
    metric) series, downsampling each series' points to `max_points`.

    Shared by both stores (D49): each takes its own SELECT (sqlite reads
    labels_json, Aurora casts labels::text) and hands the rows here as tuples
    in the fixed order (cloud, resource, metric, ts_at, value, unit, labels,
    harvested_at), already ORDER BY cloud, resource, metric, ts_at. The caller
    is the only place that knows the store's column quirks; the grouping and
    downsampling — the part that must behave identically — lives here once.

    Each returned series carries the metadata the Metrics tab needs without a
    second query: point count, first/last timestamp, last (most recent) value,
    unit, the human label from the harvest, and the downsampled points. A
    series present with zero points cannot occur (a row IS a point), but a
    harvested series whose window returned nothing simply does not appear —
    the tab explains that from the harvest status, not from a phantom row."""
    series: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []
    for cloud, resource, metric, ts_at, value, unit, labels_json, _harvested in rows:
        key = (cloud, resource, metric)
        s = series.get(key)
        if s is None:
            label = None
            if labels_json:
                try:
                    label = (json.loads(labels_json) or {}).get("label")
                except (ValueError, TypeError):
                    label = None
            s = series[key] = {
                "cloud": cloud,
                "resource": resource,
                "metric": metric,
                "unit": unit,
                "label": label,
                "_points": [],
            }
            order.append(key)
        s["_points"].append((ts_at, value))

    out: list[dict[str, Any]] = []
    for key in order:
        s = series[key]
        pts = s.pop("_points")
        s["count"] = len(pts)
        s["first_at"] = pts[0][0]
        s["last_at"] = pts[-1][0]
        # Last NON-NULL value is the "current" reading; a trailing gap should
        # not make a live series read as blank.
        s["last_value"] = next((v for _t, v in reversed(pts) if v is not None), None)
        s["points"] = _downsample(pts, max_points)
        out.append(s)
    return out


def _downsample(points: list[tuple[str, Any]], max_points: int) -> list[dict[str, Any]]:
    """Evenly thin a point list to at most `max_points`, always keeping the
    last (the freshest reading is the one a viewer checks first). Order is
    preserved; no averaging — a sparkline shows real samples, not synthetic
    ones that could hide a spike."""
    n = len(points)
    if n <= max_points:
        kept = points
    else:
        # Reserve the final slot for the last point, and stride the remaining
        # max_points - 1 across the rest — so the result is AT MOST max_points,
        # never max_points + 1 (a naive union with {n-1} overruns by one).
        step = n / (max_points - 1)
        idx = sorted({int(i * step) for i in range(max_points - 1)} | {n - 1})
        kept = [points[i] for i in idx]
    return [{"t": t, "v": v} for t, v in kept]


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

    def upsert_metrics(self, rows: list[dict[str, Any]]) -> int:
        """Bulk-write infra_metrics points (the Track B metrics harvester).

        Each row: {cloud, resource, metric, ts_at (ISO8601 UTC), value, unit,
        labels}. Idempotent on (cloud, resource, metric, ts_at) so a re-harvest
        of an overlapping window is a no-op UPDATE, not a duplicate — the same
        re-run safety upsert_event has. Returns the count written. Duck-typed
        with PgObsStore.upsert_metrics so a source is store-agnostic."""
        now = time.time()
        with self._lock:
            for r in rows:
                self._conn.execute(
                    """INSERT OR REPLACE INTO infra_metrics
                       (cloud, resource, metric, ts_at, value, unit,
                        labels_json, harvested_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        r["cloud"],
                        r["resource"],
                        r["metric"],
                        r["ts_at"],
                        r.get("value"),
                        r.get("unit"),
                        _clip_json(r["labels"]) if r.get("labels") is not None else None,
                        now,
                    ),
                )
            self._conn.commit()
        return len(rows)

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
            accumulate_usage(out["platforms"], row["platform"], row["usage_json"])
        return out

    def session_callers(self) -> dict[str, str]:
        """(platform:native_id) -> caller-agent, extracted from the D27
        rider text visible inside harvested events — the delegating agent's
        self-identification, as recorded by the PLATFORM's own logs."""
        rider = CALLER_RIDER_RE
        out: dict[str, str] = {}
        for row in self._conn.execute(
            """SELECT platform, native_session_id, raw_json FROM obs_events
               WHERE raw_json LIKE '%caller-agent%'"""
        ):
            key = f"{row['platform']}:{row['native_session_id']}"
            if key not in out:
                match = rider.search(row["raw_json"] or "")
                if match:
                    out[key] = match.group(1)
        return out

    def session_lab_traces(self) -> dict[str, str]:
        """(platform:native_id) -> lab trace id, extracted from the
        `lab-trace:` rider line (D27 extension) visible inside harvested
        events — the text-level join between a platform's own execution
        logs and the lab run that caused them, surviving hops where no
        header or metadata field does."""
        rider = LAB_TRACE_RIDER_RE
        out: dict[str, str] = {}
        for row in self._conn.execute(
            """SELECT platform, native_session_id, raw_json FROM obs_events
               WHERE raw_json LIKE '%lab-trace%'"""
        ):
            key = f"{row['platform']}:{row['native_session_id']}"
            if key not in out:
                match = rider.search(row["raw_json"] or "")
                if match:
                    out[key] = match.group(1)
        return out

    def list_sessions(
        self, platform: str | None = None, limit: int = 200, *, include_raw: bool = False
    ) -> list[dict[str, Any]]:
        # `include_raw` is accepted for interface parity with PgObsStore, where
        # raw_json is opt-in to stay under the Data API result budget. sqlite has
        # no such cap and `SELECT s.*` already returns raw_json, so the flag is a
        # no-op here — but the signatures must match or the console reads raw
        # against Aurora and drops it against sqlite (or vice versa).
        _ = include_raw
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

    def infra_metrics_series(
        self, *, since_iso: str | None = None, max_points: int = 240
    ) -> list[dict[str, Any]]:
        """The Track B grid (lab.infra_metrics) as one entry per
        (cloud, resource, metric) series, each with its points for a sparkline.

        Duck-typed with PgObsStore.infra_metrics_series (D49) — the console
        reads whichever store make_obs_store() picked. `since_iso` bounds the
        window on the ISO8601-text ts_at (lexicographic order == chronological
        because the format is fixed-width UTC Z). Points are downsampled to
        `max_points` per series in Python (evenly, keeping the last), so a
        dense 5-minute grid renders without shipping thousands of rows."""
        where = "WHERE ts_at >= ?" if since_iso else ""
        args: list[Any] = [since_iso] if since_iso else []
        rows = self._conn.execute(
            f"""SELECT cloud, resource, metric, ts_at, value, unit, labels_json, harvested_at
                FROM infra_metrics {where}
                ORDER BY cloud, resource, metric, ts_at""",
            args,
        ).fetchall()
        return _shape_infra_series(
            (
                (
                    r["cloud"],
                    r["resource"],
                    r["metric"],
                    r["ts_at"],
                    r["value"],
                    r["unit"],
                    r["labels_json"],
                    r["harvested_at"],
                )
                for r in rows
            ),
            max_points=max_points,
        )

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
