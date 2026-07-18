"""Aurora Postgres access layer for the hosted obs store (ADR D23).

One store for five consumers: trace hops (PostgresSink), harvested platform
logs (PgObsStore, drop-in for the sqlite ObsStore's write surface), hosted
console reads, the analyst's ad-hoc SQL (via the obs MCP server), and M10's
Data 360 zero-copy federation.

Two access backends behind one ``PgClient.execute(sql, params)``:

- **data-api** — the RDS Data API (boto3 ``rds-data``). IAM-authed HTTPS,
  so Lambdas need no VPC attachment and the cluster's 5432 ingress stays
  closed to them entirely. Selected when A2ALAB_PG_CLUSTER_ARN +
  A2ALAB_PG_SECRET_ARN are set. Which DB role you act as = which secret
  ARN you hold (writer vs reader), enforced by Postgres grants.
- **dsn** — direct pg8000 connection (pure-Python driver, no binary
  wheels) for the lab host: backfills, provisioning, local console reads.
  Selected by A2ALAB_PG_DSN (postgres://user:pass@host:5432/a2alab).

Both backends take ``:name`` params. JSON values are passed as text and
cast in the SQL (``CAST(:x AS jsonb)``) so one SQL string serves both.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any
from urllib.parse import unquote, urlparse

from observability.store import _clip_json

CLUSTER_ARN_ENV = "A2ALAB_PG_CLUSTER_ARN"
SECRET_ARN_ENV = "A2ALAB_PG_SECRET_ARN"
DSN_ENV = "A2ALAB_PG_DSN"
DATABASE_ENV = "A2ALAB_PG_DATABASE"
DEFAULT_DATABASE = "a2alab"
SCHEMA = "lab"

# One statement per entry — the Data API executes single statements only.
DDL: list[str] = [
    f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}",
    f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.trace_events (
        trace_id             text NOT NULL,
        hop_seq              integer NOT NULL,
        ts                   double precision NOT NULL,
        ts_at                timestamptz,
        source               text,
        target               text,
        protocol             text,
        transport_detail     text,
        status               text,
        latency_ms           integer,
        platform_ref         text,
        request_payload_raw  jsonb,
        response_payload_raw jsonb,
        inserted_at          timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (trace_id, hop_seq, ts)
    )""",
    f"CREATE INDEX IF NOT EXISTS idx_lab_trace_events_ts ON {SCHEMA}.trace_events (ts)",
    f"""CREATE INDEX IF NOT EXISTS idx_lab_trace_events_platform_ref
        ON {SCHEMA}.trace_events (platform_ref)""",
    f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.obs_sessions (
        platform        text NOT NULL,
        native_id       text NOT NULL,
        lab_session_id  text,
        title           text,
        status          text,
        created_at      text,
        updated_at      text,
        usage_json      jsonb,
        raw_json        jsonb,
        harvested_at    double precision,
        PRIMARY KEY (platform, native_id)
    )""",
    f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.obs_events (
        platform            text NOT NULL,
        native_session_id   text NOT NULL,
        event_id            text NOT NULL,
        event_type          text,
        processed_at        text,
        summary             text,
        usage_json          jsonb,
        raw_json            jsonb,
        harvested_at        double precision,
        PRIMARY KEY (platform, event_id)
    )""",
    f"""CREATE INDEX IF NOT EXISTS idx_lab_obs_events_session
        ON {SCHEMA}.obs_events (platform, native_session_id)""",
    f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.obs_harvest (
        platform        text PRIMARY KEY,
        last_harvest_at double precision,
        status          text,
        detail          text
    )""",
    f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.obs_briefs (
        id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        brief_date   date NOT NULL,
        session_id   text,
        queries_run  integer,
        brief_md     text NOT NULL,
        created_at   timestamptz NOT NULL DEFAULT now()
    )""",
]


class PgClient:
    """Named-parameter SQL over either the RDS Data API or a direct
    pg8000 connection. Thread-safe (a lock guards the dsn connection)."""

    def __init__(
        self,
        *,
        cluster_arn: str | None = None,
        secret_arn: str | None = None,
        dsn: str | None = None,
        database: str | None = None,
    ):
        self.cluster_arn = cluster_arn
        self.secret_arn = secret_arn
        self.dsn = dsn
        self.database = database or os.environ.get(DATABASE_ENV, DEFAULT_DATABASE)
        self._lock = threading.Lock()
        self._rds = None
        self._conn = None
        if not ((cluster_arn and secret_arn) or dsn):
            raise ValueError("PgClient needs cluster_arn+secret_arn (Data API) or dsn")

    @classmethod
    def from_env(cls) -> "PgClient":
        cluster_arn = os.environ.get(CLUSTER_ARN_ENV)
        secret_arn = os.environ.get(SECRET_ARN_ENV)
        dsn = os.environ.get(DSN_ENV)
        if cluster_arn and secret_arn:
            return cls(cluster_arn=cluster_arn, secret_arn=secret_arn)
        if dsn:
            return cls(dsn=dsn)
        raise RuntimeError(
            f"no Postgres config: set {CLUSTER_ARN_ENV}+{SECRET_ARN_ENV} or {DSN_ENV}"
        )

    @classmethod
    def configured(cls) -> bool:
        return bool(
            (os.environ.get(CLUSTER_ARN_ENV) and os.environ.get(SECRET_ARN_ENV))
            or os.environ.get(DSN_ENV)
        )

    # ---- execute ----------------------------------------------------------

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if self.dsn:
            return self._execute_dsn(sql, params or {})
        return self._execute_data_api(sql, params or {})

    def ensure_schema(self) -> None:
        for stmt in DDL:
            self.execute(stmt)

    # ---- Data API backend -------------------------------------------------

    def _execute_data_api(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if self._rds is None:
            import boto3

            # The cluster ARN names its region (arn:aws:rds:<region>:...) —
            # parse it so the SSO profile's default region can't misroute us.
            region = self.cluster_arn.split(":")[3]
            self._rds = boto3.client("rds-data", region_name=region)
        parameters = [{"name": k, "value": self._typed(v)} for k, v in params.items()]
        # Scale-to-zero Aurora resumes on first touch and the Data API
        # throws transient errors until it's up (~15s); the HTTP endpoint
        # also flaps briefly right after being enabled. Retry those.
        resp = None
        for attempt in range(8):
            try:
                resp = self._rds.execute_statement(
                    resourceArn=self.cluster_arn,
                    secretArn=self.secret_arn,
                    database=self.database,
                    sql=sql,
                    parameters=parameters,
                    includeResultMetadata=True,
                )
                break
            except Exception as exc:  # noqa: BLE001 - classify, re-raise non-transient
                name = type(exc).__name__
                transient = name in (
                    "HttpEndpointNotEnabledException",
                    "InternalServerErrorException",
                    "ServiceUnavailableError",
                ) or ("resuming" in str(exc).lower() or "starting up" in str(exc).lower())
                if not transient or attempt == 7:
                    raise
                time.sleep(5)
        cols = [c["name"] for c in resp.get("columnMetadata") or []]
        rows: list[dict[str, Any]] = []
        for record in resp.get("records") or []:
            rows.append({cols[i]: self._untyped(f) for i, f in enumerate(record)})
        return rows

    @staticmethod
    def _typed(value: Any) -> dict[str, Any]:
        if value is None:
            return {"isNull": True}
        if isinstance(value, bool):
            return {"booleanValue": value}
        if isinstance(value, int):
            return {"longValue": value}
        if isinstance(value, float):
            return {"doubleValue": value}
        return {"stringValue": str(value)}

    @staticmethod
    def _untyped(field: dict[str, Any]) -> Any:
        if field.get("isNull"):
            return None
        for key in ("stringValue", "longValue", "doubleValue", "booleanValue"):
            if key in field:
                return field[key]
        if "arrayValue" in field:
            return field["arrayValue"]
        return next(iter(field.values()), None)

    # ---- pg8000 backend ---------------------------------------------------

    def _connect_dsn(self):
        if self._conn is None:
            import ssl

            import pg8000.native

            u = urlparse(self.dsn)
            ctx = ssl.create_default_context()
            # Aurora's cert chain isn't in certifi by default for the lab
            # host — require TLS but don't pin the RDS CA here.
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self._conn = pg8000.native.Connection(
                user=unquote(u.username or ""),
                password=unquote(u.password or ""),
                host=u.hostname,
                port=u.port or 5432,
                database=(u.path or "/").lstrip("/") or self.database,
                ssl_context=ctx,
            )
        return self._conn

    def _execute_dsn(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect_dsn()
            rows = conn.run(sql, **params)
            cols = [c["name"] for c in conn.columns or []]
            return [dict(zip(cols, r)) for r in rows or []]

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


class PgObsStore:
    """Postgres twin of the sqlite ObsStore's write surface — the four
    methods the harvest sources call (duck-typed, so sources are unchanged).
    Reads for the console live here too as they come online."""

    def __init__(self, client: PgClient | None = None):
        self.client = client or PgClient.from_env()

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
        self.client.execute(
            f"""INSERT INTO {SCHEMA}.obs_sessions
                (platform, native_id, lab_session_id, title, status,
                 created_at, updated_at, usage_json, raw_json, harvested_at)
                VALUES (:platform, :native_id, :lab_session_id, :title, :status,
                        :created_at, :updated_at, CAST(:usage_json AS jsonb),
                        CAST(:raw_json AS jsonb), :harvested_at)
                ON CONFLICT (platform, native_id) DO UPDATE SET
                  lab_session_id = COALESCE(EXCLUDED.lab_session_id,
                                            {SCHEMA}.obs_sessions.lab_session_id),
                  title = EXCLUDED.title, status = EXCLUDED.status,
                  created_at = EXCLUDED.created_at, updated_at = EXCLUDED.updated_at,
                  usage_json = EXCLUDED.usage_json, raw_json = EXCLUDED.raw_json,
                  harvested_at = EXCLUDED.harvested_at""",
            {
                "platform": platform,
                "native_id": native_id,
                "lab_session_id": lab_session_id,
                "title": title,
                "status": status,
                "created_at": created_at,
                "updated_at": updated_at,
                "usage_json": _clip_json(usage) if usage is not None else None,
                "raw_json": _clip_json(raw) if raw is not None else None,
                "harvested_at": time.time(),
            },
        )

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
        self.client.execute(
            f"""INSERT INTO {SCHEMA}.obs_events
                (platform, native_session_id, event_id, event_type,
                 processed_at, summary, usage_json, raw_json, harvested_at)
                VALUES (:platform, :native_session_id, :event_id, :event_type,
                        :processed_at, :summary, CAST(:usage_json AS jsonb),
                        CAST(:raw_json AS jsonb), :harvested_at)
                ON CONFLICT (platform, event_id) DO UPDATE SET
                  native_session_id = EXCLUDED.native_session_id,
                  event_type = EXCLUDED.event_type,
                  processed_at = EXCLUDED.processed_at,
                  summary = EXCLUDED.summary, usage_json = EXCLUDED.usage_json,
                  raw_json = EXCLUDED.raw_json, harvested_at = EXCLUDED.harvested_at""",
            {
                "platform": platform,
                "native_session_id": native_session_id,
                "event_id": event_id,
                "event_type": event_type,
                "processed_at": processed_at,
                "summary": (summary or "")[:2000] or None,
                "usage_json": _clip_json(usage) if usage is not None else None,
                "raw_json": _clip_json(raw) if raw is not None else None,
                "harvested_at": time.time(),
            },
        )

    def set_harvest_status(self, platform: str, status: str, detail: str = "") -> None:
        self.client.execute(
            f"""INSERT INTO {SCHEMA}.obs_harvest (platform, last_harvest_at, status, detail)
                VALUES (:platform, :at, :status, :detail)
                ON CONFLICT (platform) DO UPDATE SET
                  last_harvest_at = EXCLUDED.last_harvest_at,
                  status = EXCLUDED.status, detail = EXCLUDED.detail""",
            {"platform": platform, "at": time.time(), "status": status, "detail": detail[:2000]},
        )

    def session_updated_at(self, platform: str, native_id: str) -> str | None:
        rows = self.client.execute(
            f"""SELECT updated_at FROM {SCHEMA}.obs_sessions
                WHERE platform = :platform AND native_id = :native_id""",
            {"platform": platform, "native_id": native_id},
        )
        return rows[0]["updated_at"] if rows else None

    # ---- brief feed (console + save_brief tool) ---------------------------

    def insert_brief(self, brief_md: str, *, session_id: str | None, queries_run: int) -> None:
        self.client.execute(
            f"""INSERT INTO {SCHEMA}.obs_briefs (brief_date, session_id, queries_run, brief_md)
                VALUES (CURRENT_DATE, :session_id, :queries_run, :brief_md)""",
            {"session_id": session_id, "queries_run": queries_run, "brief_md": brief_md},
        )

    def list_briefs(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.client.execute(
            f"""SELECT id, CAST(brief_date AS text) AS brief_date, session_id,
                       queries_run, brief_md, CAST(created_at AS text) AS created_at
                FROM {SCHEMA}.obs_briefs ORDER BY id DESC LIMIT :limit""",
            {"limit": limit},
        )

    def close(self) -> None:
        self.client.close()


class PostgresSink:
    """TraceSink writing hops into lab.trace_events (ADR D23) — the cloud
    successor to DynamoDbSink as the durable store and the table Data 360's
    Aurora Postgres zero-copy connector federates for M10. Satisfies the
    TraceSink contract (emit(dict), never raises into the request path —
    TraceRecorder contains failures)."""

    def __init__(self, client: PgClient | None = None):
        self.client = client or PgClient.from_env()

    def emit(self, event_dict: dict[str, Any]) -> None:
        ts = float(event_dict.get("ts") or time.time())
        self.client.execute(
            f"""INSERT INTO {SCHEMA}.trace_events
                (trace_id, hop_seq, ts, ts_at, source, target, protocol,
                 transport_detail, status, latency_ms, platform_ref,
                 request_payload_raw, response_payload_raw)
                VALUES (:trace_id, :hop_seq, :ts, to_timestamp(:ts), :source, :target,
                        :protocol, :transport_detail, :status, :latency_ms, :platform_ref,
                        CAST(:request AS jsonb), CAST(:response AS jsonb))
                ON CONFLICT (trace_id, hop_seq, ts) DO NOTHING""",
            {
                "trace_id": event_dict["trace_id"],
                "hop_seq": int(event_dict.get("hop_seq") or 0),
                "ts": ts,
                "source": event_dict.get("source"),
                "target": event_dict.get("target"),
                "protocol": event_dict.get("protocol"),
                "transport_detail": event_dict.get("transport_detail"),
                "status": event_dict.get("status"),
                "latency_ms": event_dict.get("latency_ms"),
                "platform_ref": event_dict.get("platform_ref"),
                "request": json.dumps(
                    event_dict.get("request_payload_raw"), default=str, ensure_ascii=False
                ),
                "response": json.dumps(
                    event_dict.get("response_payload_raw"), default=str, ensure_ascii=False
                ),
            },
        )
