"""Wire-visibility trace layer.

Every hop — inbound (a protocol server handling a request) and outbound
(a RemoteAgentClient calling a remote agent) — records one TraceEvent
carrying the *raw wire payloads* (JSON-RPC envelopes for MCP/A2A, HTTP
bodies for REST, Agent API JSON for Agentforce).

Where events go is pluggable (ADR D13): TraceRecorder fans each event out
to one or more TraceSinks, selected by A2ALAB_TRACE_SINK (comma-separated):

- "jsonl"             append to traces/YYYY-MM-DD.jsonl — the append-only
                      raw archive. Local-dev only — on AWS the container
                      filesystem is ephemeral and per-service.
- "sqlite"            insert into traces/lab.db (table trace_events) — the
                      console's query path (ADR D19: timeline bucketing,
                      platform filters, joins against the harvested
                      observability tables live in the same file).
- "dynamodb"          put_item per event into a DynamoDB table
                      (A2ALAB_TRACE_TABLE, default "a2alab-traces") —
                      superseded as the cloud path by "postgres" (D23) but
                      kept runnable.
- "postgres"          insert into lab.trace_events on the Aurora Postgres
                      obs store (D23) — the durable cloud store and the
                      table Data 360's zero-copy Aurora connector reads for
                      TableauNext reporting (M10). Config via
                      A2ALAB_PG_CLUSTER_ARN+A2ALAB_PG_SECRET_ARN (Data API)
                      or A2ALAB_PG_DSN (direct).

Default is "jsonl,sqlite" (D19). JSONL can rebuild the DB at any time via
scripts/trace_import.py.

Correlation to *platform-interior* logs (M11): platform_ref carries the
native execution id of the remote platform a hop touched — the CMA session
id on managed-backend hops, the Agent API session id on Agentforce hops —
recorded at emit time so the join to harvested platform logs is never
reconstructed after the fact.

Correlation: a trace_id is minted at the edge and propagated on every hop
(HTTP header X-Trace-Id for REST/bridge, message metadata for A2A,
tool-arg passthrough for MCP).
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

TRACE_DIR_ENV = "A2ALAB_TRACE_DIR"
DEFAULT_TRACE_DIR = "traces"
TRACE_SINK_ENV = "A2ALAB_TRACE_SINK"
TRACE_TABLE_ENV = "A2ALAB_TRACE_TABLE"
DEFAULT_TRACE_TABLE = "a2alab-traces"
TRACE_TTL_DAYS_ENV = "A2ALAB_TRACE_TTL_DAYS"
DEFAULT_TRACE_TTL_DAYS = 14
DEFAULT_DB_NAME = "lab.db"

_MAX_PAYLOAD_CHARS = 100_000


def _clip(payload: Any) -> Any:
    """Keep raw payloads raw, but bound pathological sizes."""
    if isinstance(payload, str) and len(payload) > _MAX_PAYLOAD_CHARS:
        return payload[:_MAX_PAYLOAD_CHARS] + f"...[clipped {len(payload)} chars total]"
    return payload


@dataclass
class TraceEvent:
    trace_id: str
    source: str
    target: str
    protocol: str  # rest | mcp | a2a | agentforce-api | internal
    transport_detail: str  # e.g. "POST /invoke", "tools/call ask", "message/send"
    request_payload_raw: Any
    response_payload_raw: Any = None
    status: str = "ok"  # ok | error | pending
    latency_ms: int | None = None
    hop_seq: int = 0
    ts: float = field(default_factory=time.time)
    # Native execution id on the platform this hop touched (M11.1): CMA
    # session id, Agentforce Agent API session id, OpenAI response id.
    platform_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["request_payload_raw"] = _clip(d["request_payload_raw"])
        d["response_payload_raw"] = _clip(d["response_payload_raw"])
        return d


class TraceSink:
    """One destination for trace events. Implementations must be safe to
    call from multiple threads and must never raise into the request path —
    recording a trace can't break the hop it describes."""

    def emit(self, event_dict: dict[str, Any]) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class JsonlFileSink(TraceSink):
    """Appends events to a JSONL file, one file per day. File-based on
    purpose for local dev — no DB, easy to tail, easy to ship."""

    def __init__(self, trace_dir: str | Path | None = None):
        self.trace_dir = Path(trace_dir or os.environ.get(TRACE_DIR_ENV, DEFAULT_TRACE_DIR))
        self._lock = threading.Lock()

    def _current_file(self) -> Path:
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        return self.trace_dir / (time.strftime("%Y-%m-%d") + ".jsonl")

    def emit(self, event_dict: dict[str, Any]) -> None:
        line = json.dumps(event_dict, default=str, ensure_ascii=False)
        with self._lock:
            with self._current_file().open("a", encoding="utf-8") as f:
                f.write(line + "\n")


class SqliteSink(TraceSink):
    """Inserts events into traces/lab.db — the console's query backend
    (ADR D19). The observability harvest tables (src/observability/store.py)
    live in the same file so lab-trace ⋈ platform-log joins are plain SQL.
    JSONL remains the append-only raw archive."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS trace_events (
        trace_id            TEXT NOT NULL,
        hop_seq             INTEGER NOT NULL,
        ts                  REAL NOT NULL,
        source              TEXT,
        target              TEXT,
        protocol            TEXT,
        transport_detail    TEXT,
        status              TEXT,
        latency_ms          INTEGER,
        platform_ref        TEXT,
        request_payload_raw  TEXT,
        response_payload_raw TEXT,
        PRIMARY KEY (trace_id, hop_seq, ts)
    );
    CREATE INDEX IF NOT EXISTS idx_trace_events_ts ON trace_events (ts);
    CREATE INDEX IF NOT EXISTS idx_trace_events_platform_ref
        ON trace_events (platform_ref);
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(
            db_path or Path(os.environ.get(TRACE_DIR_ENV, DEFAULT_TRACE_DIR)) / DEFAULT_DB_NAME
        )
        self._lock = threading.Lock()
        self._conn = None

    def _connect(self):
        import sqlite3

        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(self.SCHEMA)
            self._conn.commit()
        return self._conn

    def emit(self, event_dict: dict[str, Any]) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                """INSERT OR REPLACE INTO trace_events
                   (trace_id, hop_seq, ts, source, target, protocol,
                    transport_detail, status, latency_ms, platform_ref,
                    request_payload_raw, response_payload_raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_dict["trace_id"],
                    int(event_dict.get("hop_seq") or 0),
                    float(event_dict.get("ts") or time.time()),
                    event_dict.get("source"),
                    event_dict.get("target"),
                    event_dict.get("protocol"),
                    event_dict.get("transport_detail"),
                    event_dict.get("status"),
                    event_dict.get("latency_ms"),
                    event_dict.get("platform_ref"),
                    json.dumps(
                        event_dict.get("request_payload_raw"), default=str, ensure_ascii=False
                    ),
                    json.dumps(
                        event_dict.get("response_payload_raw"), default=str, ensure_ascii=False
                    ),
                ),
            )
            conn.commit()


class DynamoDbSink(TraceSink):
    """put_item per event into DynamoDB — the durable trace store for cloud
    deploys, and the table Data 360's zero-copy DynamoDB connector reads.

    Table shape (create once, see plan/04-runbooks.md §6):
      PK  trace_id (S)
      SK  sk       (S)  "<ts padded>#<hop_seq>" — hops sort chronologically
      GSI day-index: PK day (S, "YYYY-MM-DD"), SK sk — "recent traces" query
      TTL expires_at (N, epoch seconds; A2ALAB_TRACE_TTL_DAYS, default 14)

    Payloads are stored as JSON strings: DynamoDB rejects floats and empty
    strings inside documents, and Data 360 maps scalar attributes cleanly.
    """

    def __init__(
        self, table_name: str | None = None, *, table: Any = None, ttl_days: float | None = None
    ):
        if table is not None:
            self._table = table  # injected for tests
        else:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "DynamoDbSink needs boto3 — install with `uv sync --extra aws`"
                ) from exc
            name = table_name or os.environ.get(TRACE_TABLE_ENV, DEFAULT_TRACE_TABLE)
            self._table = boto3.resource("dynamodb").Table(name)
        if ttl_days is None:
            ttl_days = float(os.environ.get(TRACE_TTL_DAYS_ENV, DEFAULT_TRACE_TTL_DAYS))
        self.ttl_days = ttl_days

    def emit(self, event_dict: dict[str, Any]) -> None:
        from decimal import Decimal

        ts = float(event_dict.get("ts") or time.time())
        seq = int(event_dict.get("hop_seq") or 0)
        item = {
            "trace_id": event_dict["trace_id"],
            "sk": f"{ts:017.6f}#{seq:04d}",
            "day": time.strftime("%Y-%m-%d", time.localtime(ts)),
            "ts": Decimal(str(ts)),
            "hop_seq": seq,
            "source": event_dict.get("source") or "unknown",
            "target": event_dict.get("target") or "unknown",
            "protocol": event_dict.get("protocol") or "unknown",
            "transport_detail": event_dict.get("transport_detail") or "-",
            "status": event_dict.get("status") or "ok",
            "request_payload_raw": json.dumps(
                event_dict.get("request_payload_raw"), default=str, ensure_ascii=False
            ),
            "response_payload_raw": json.dumps(
                event_dict.get("response_payload_raw"), default=str, ensure_ascii=False
            ),
            "expires_at": int(ts + self.ttl_days * 86400),
        }
        if event_dict.get("latency_ms") is not None:
            item["latency_ms"] = int(event_dict["latency_ms"])
        if event_dict.get("platform_ref"):
            item["platform_ref"] = event_dict["platform_ref"]
        self._table.put_item(Item=item)


def sinks_from_env() -> list[TraceSink]:
    """Build the sink list from A2ALAB_TRACE_SINK (comma-separated;
    default jsonl). Unknown names raise — a typo silently dropping traces
    would defeat the lab's core requirement."""
    names = [
        n.strip().lower()
        for n in (os.environ.get(TRACE_SINK_ENV) or "jsonl,sqlite").split(",")
        if n.strip()
    ]
    sinks: list[TraceSink] = []
    for name in names:
        if name == "jsonl":
            sinks.append(JsonlFileSink())
        elif name == "sqlite":
            sinks.append(SqliteSink())
        elif name == "dynamodb":
            sinks.append(DynamoDbSink())
        elif name == "postgres":
            # D23: Aurora Postgres — the hosted successor to dynamodb as the
            # cloud sink and the M10 zero-copy source. Lazy import: the pg
            # layer lives with the observability store it shares tables with.
            from observability.pg import PostgresSink

            sinks.append(PostgresSink())
        else:
            raise ValueError(f"unknown trace sink '{name}' in {TRACE_SINK_ENV}")
    return sinks


class TraceRecorder:
    """Assigns hop sequence numbers and fans each TraceEvent out to the
    configured sinks. A sink failure is contained (stderr warning) — tracing
    must never break the hop it observes."""

    def __init__(self, trace_dir: str | Path | None = None, sinks: list[TraceSink] | None = None):
        # trace_dir kept as first positional arg for backward compatibility
        # (tests build TraceRecorder(tmp_dir)); when given, it forces a
        # single JSONL sink rooted there.
        if sinks is not None:
            self.sinks = sinks
        elif trace_dir is not None:
            self.sinks = [JsonlFileSink(trace_dir)]
        else:
            self.sinks = sinks_from_env()
        self._lock = threading.Lock()
        self._seq_by_trace: dict[str, int] = {}

    def next_hop_seq(self, trace_id: str) -> int:
        with self._lock:
            seq = self._seq_by_trace.get(trace_id, 0)
            self._seq_by_trace[trace_id] = seq + 1
            return seq

    def record(self, event: TraceEvent) -> TraceEvent:
        event_dict = event.to_dict()
        for sink in self.sinks:
            try:
                sink.emit(event_dict)
            except Exception as exc:  # noqa: BLE001 - see class docstring
                import sys

                print(
                    f"[trace] {type(sink).__name__} failed: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
        return event


_recorder: TraceRecorder | None = None
_recorder_lock = threading.Lock()


def get_recorder() -> TraceRecorder:
    global _recorder
    with _recorder_lock:
        if _recorder is None:
            _recorder = TraceRecorder()
        return _recorder


def set_recorder(recorder: TraceRecorder) -> None:
    global _recorder
    with _recorder_lock:
        _recorder = recorder


class Hop:
    """Context manager that times a hop and records its TraceEvent.

    Usage:
        with Hop(trace_id, source="bridge", target="claude", protocol="mcp",
                 transport_detail="tools/call ask", request_payload=raw) as hop:
            ...
            hop.response_payload = raw_response
    """

    def __init__(
        self,
        trace_id: str,
        *,
        source: str,
        target: str,
        protocol: str,
        transport_detail: str,
        request_payload: Any,
        recorder: TraceRecorder | None = None,
    ):
        self.recorder = recorder or get_recorder()
        self.event = TraceEvent(
            trace_id=trace_id,
            source=source,
            target=target,
            protocol=protocol,
            transport_detail=transport_detail,
            request_payload_raw=request_payload,
            hop_seq=self.recorder.next_hop_seq(trace_id),
        )
        self._start = None
        self.response_payload: Any = None
        # Set by the caller once the platform-native execution id is known
        # (e.g. the CMA session id) — lands on the event at exit (M11.1).
        self.platform_ref: str | None = None

    def __enter__(self) -> "Hop":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.event.latency_ms = int((time.perf_counter() - self._start) * 1000)
        self.event.response_payload_raw = self.response_payload
        self.event.platform_ref = self.platform_ref
        if exc is not None:
            self.event.status = "error"
            if self.response_payload is None:
                self.event.response_payload_raw = f"{exc_type.__name__}: {exc}"
        self.recorder.record(self.event)
