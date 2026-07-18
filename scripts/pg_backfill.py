"""Backfill the hosted Aurora store from the local traces/lab.db (D23).

    uv run python scripts/pg_backfill.py            # trace_events + obs_* tables

Idempotent (upserts / ON CONFLICT DO NOTHING). Needs A2ALAB_PG_DSN (or the
Data API env pair) pointing at the writer role.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

from observability.pg import SCHEMA, PgClient
from observability.store import default_db_path


def _jsonify(raw) -> str:
    """sqlite stored payloads as json.dumps strings; pass valid JSON through
    verbatim, wrap anything else as a JSON string."""
    if raw is None:
        return "null"
    try:
        json.loads(raw)
        return raw
    except (ValueError, TypeError):
        return json.dumps(str(raw), ensure_ascii=False)


def main() -> int:
    load_dotenv()
    db_path = default_db_path()
    if not db_path.exists():
        print(f"no local store at {db_path}")
        return 1
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    pg = PgClient.from_env()
    try:
        pg.ensure_schema()
    except Exception as exc:  # noqa: BLE001 - schema is provisioned by master; writer may lack DDL
        print(f"ensure_schema skipped ({type(exc).__name__}) — assuming provisioned")

    counts = {}
    for row in conn.execute("SELECT * FROM trace_events"):
        pg.execute(
            f"""INSERT INTO {SCHEMA}.trace_events
                (trace_id, hop_seq, ts, ts_at, source, target, protocol, transport_detail,
                 status, latency_ms, platform_ref, request_payload_raw, response_payload_raw)
                VALUES (:trace_id, :hop_seq, :ts, to_timestamp(:ts), :source, :target,
                        :protocol, :transport_detail, :status, :latency_ms, :platform_ref,
                        CAST(:request AS jsonb), CAST(:response AS jsonb))
                ON CONFLICT (trace_id, hop_seq, ts) DO NOTHING""",
            {
                "trace_id": row["trace_id"],
                "hop_seq": row["hop_seq"],
                "ts": row["ts"],
                "source": row["source"],
                "target": row["target"],
                "protocol": row["protocol"],
                "transport_detail": row["transport_detail"],
                "status": row["status"],
                "latency_ms": row["latency_ms"],
                "platform_ref": row["platform_ref"],
                "request": _jsonify(row["request_payload_raw"]),
                "response": _jsonify(row["response_payload_raw"]),
            },
        )
        counts["trace_events"] = counts.get("trace_events", 0) + 1

    for row in conn.execute("SELECT * FROM obs_sessions"):
        pg.execute(
            f"""INSERT INTO {SCHEMA}.obs_sessions
                (platform, native_id, lab_session_id, title, status, created_at,
                 updated_at, usage_json, raw_json, harvested_at)
                VALUES (:platform, :native_id, :lab_session_id, :title, :status,
                        :created_at, :updated_at, CAST(:usage AS jsonb),
                        CAST(:raw AS jsonb), :harvested_at)
                ON CONFLICT (platform, native_id) DO UPDATE SET
                  status = EXCLUDED.status, updated_at = EXCLUDED.updated_at,
                  usage_json = EXCLUDED.usage_json, raw_json = EXCLUDED.raw_json,
                  harvested_at = EXCLUDED.harvested_at""",
            {
                "platform": row["platform"],
                "native_id": row["native_id"],
                "lab_session_id": row["lab_session_id"],
                "title": row["title"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "usage": _jsonify(row["usage_json"]),
                "raw": _jsonify(row["raw_json"]),
                "harvested_at": row["harvested_at"],
            },
        )
        counts["obs_sessions"] = counts.get("obs_sessions", 0) + 1

    for row in conn.execute("SELECT * FROM obs_events"):
        pg.execute(
            f"""INSERT INTO {SCHEMA}.obs_events
                (platform, native_session_id, event_id, event_type, processed_at,
                 summary, usage_json, raw_json, harvested_at)
                VALUES (:platform, :native_session_id, :event_id, :event_type,
                        :processed_at, :summary, CAST(:usage AS jsonb),
                        CAST(:raw AS jsonb), :harvested_at)
                ON CONFLICT (platform, event_id) DO NOTHING""",
            {
                "platform": row["platform"],
                "native_session_id": row["native_session_id"],
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "processed_at": row["processed_at"],
                "summary": row["summary"],
                "usage": _jsonify(row["usage_json"]),
                "raw": _jsonify(row["raw_json"]),
                "harvested_at": row["harvested_at"],
            },
        )
        counts["obs_events"] = counts.get("obs_events", 0) + 1

    for table, n in counts.items():
        print(f"{table}: {n} rows")
    if not counts:
        print("nothing to backfill")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
