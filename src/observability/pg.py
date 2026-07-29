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
    # WS12 settled the open question in plan/07: one briefs table, one reader,
    # a `kind` discriminator — not a second table and a second migration. The
    # ALTER is separate from the CREATE because the table predates the column
    # in every deployed environment; `IF NOT EXISTS` makes it a no-op on both
    # paths. The default backfills the observability analyst's existing rows,
    # which is correct — they were all its briefs before the sentinel existed.
    f"""ALTER TABLE {SCHEMA}.obs_briefs
        ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'observability'""",
    # WS13: small operator artifacts that the console reads but does not
    # compute — today the credential expiry snapshot, which lived only in
    # `.a2alab/expiry.json`. A hosted console has no `.a2alab`, so anything
    # read from a local file is a laptop dependency wearing a cache costume.
    # Deliberately a key/value table rather than a column per artifact: these
    # are whole documents produced by a script and rendered verbatim, and a
    # second one should not need a migration.
    f"""CREATE TABLE IF NOT EXISTS {SCHEMA}.lab_state (
        key        text PRIMARY KEY,
        payload    jsonb NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT now()
    )""",
]

# Keys used in lab.lab_state. Named here so a typo is an ImportError rather
# than a silently empty read.
STATE_EXPIRY = "expiry"
# Insight sign-offs (D38). They lived only in config/insight_reviews.yaml,
# which is baked into the console image — so a sign-off made in the HOSTED
# console was written to a container filesystem and lost on the next restart.
# An approval is a named human act; losing one silently is the worst possible
# failure for it. The file remains the local store and the diffable artifact.
STATE_INSIGHT_REVIEWS = "insight_reviews"
# Which scheduled brief sessions the watcher has already serviced (WS13 item
# 3). Hosted, this is what stops a container restart re-delivering every
# brief still listed in recent deployment runs — duplicate
# A2ALab_Account_Brief__c records in a production org.
STATE_BRIEF_SESSIONS = "brief_serviced_sessions"

# The brief kinds that share lab.obs_briefs. Unknown values are accepted on
# write (a future analyst should not need a schema change) but the console
# asks for one of these.
BRIEF_OBSERVABILITY = "observability"
BRIEF_COST = "cost"


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


# Postgres ARE equivalents of the Python rider regexes in store.py (D49). Kept
# beside each other deliberately: they must match the same text, and the reason
# there are two is the Data API size cap documented on _riders(), not a fork.
#   store.CALLER_RIDER_RE     = r"caller-agent:\\?n?\s*([\w-]+)"
#   store.LAB_TRACE_RIDER_RE  = r"lab-trace:\\?n?\s*([0-9a-fA-F-]{8,})"
CALLER_RIDER_SQL = r"caller-agent:\\?n?\s*([\w-]+)"
LAB_TRACE_RIDER_SQL = r"lab-trace:\\?n?\s*([0-9a-fA-F-]{8,})"

# The RDS Data API refuses any result over 1 MB. Harvested `raw_json` is capped
# at 100_000 chars per row at write time (_clip_json), and one session's events
# measured 2.43 MB in total — so a whole session cannot be fetched in one
# statement and list_events pages itself.
_DATA_API_RESULT_BUDGET = 900_000


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

    def openai_response_ids(self, limit: int = 50) -> list[str]:
        rows = self.client.execute(
            f"""SELECT platform_ref, MAX(ts) AS ts FROM {SCHEMA}.trace_events
                WHERE target = 'openai-platform' AND platform_ref IS NOT NULL
                GROUP BY platform_ref ORDER BY ts DESC LIMIT :limit""",
            {"limit": limit},
        )
        return [r["platform_ref"] for r in rows]

    # ---- brief feed (console + save_brief tool) ---------------------------

    def insert_brief(
        self,
        brief_md: str,
        *,
        session_id: str | None,
        queries_run: int,
        kind: str = BRIEF_OBSERVABILITY,
    ) -> None:
        self.client.execute(
            f"""INSERT INTO {SCHEMA}.obs_briefs
                    (brief_date, session_id, queries_run, brief_md, kind)
                VALUES (CURRENT_DATE, :session_id, :queries_run, :brief_md, :kind)""",
            {
                "session_id": session_id,
                "queries_run": queries_run,
                "brief_md": brief_md,
                "kind": kind,
            },
        )

    def list_briefs(
        self, limit: int = 20, kind: str | None = None, days: int | None = None
    ) -> list[dict[str, Any]]:
        """Newest first. `kind=None` returns every kind — which is what the
        analyst's own feed wants when it asks "what have I written before".

        `days` bounds the window by `created_at`, for the console's rolling
        view. It is deliberately separate from `limit`: a quiet week should
        show few briefs rather than backfilling older ones to reach a count,
        because "nothing was written this week" is exactly the state the panel
        needs to be able to express (D56).
        """
        clauses = []
        params: dict[str, Any] = {"limit": limit}
        if kind:
            clauses.append("kind = :kind")
            params["kind"] = kind
        if days:
            # NOT make_interval(days => :days): the Data API sends the value as
            # bigint and make_interval takes int, so Postgres rejects it with
            # "function make_interval(days => bigint) does not exist" — a type
            # error that reads like a missing function. Multiplying an interval
            # takes any numeric.
            clauses.append("created_at >= now() - (:days * INTERVAL '1 day')")
            params["days"] = days
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return self.client.execute(
            f"""SELECT id, CAST(brief_date AS text) AS brief_date, session_id,
                       queries_run, brief_md, kind,
                       CAST(created_at AS text) AS created_at
                FROM {SCHEMA}.obs_briefs {where} ORDER BY id DESC LIMIT :limit""",
            params,
        )

    # ---- lab_state: operator artifacts a hosted console must not read from
    # a local file (WS13) ---------------------------------------------------

    def put_state(self, key: str, payload: Any) -> None:
        """Upsert one artifact. The producer is a script with the operator's
        cloud sessions; the reader is a console that may be running in a
        container with no `.a2alab` at all."""
        self.client.execute(
            f"""INSERT INTO {SCHEMA}.lab_state (key, payload, updated_at)
                VALUES (:key, CAST(:payload AS jsonb), now())
                ON CONFLICT (key) DO UPDATE SET
                  payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at""",
            {"key": key, "payload": json.dumps(payload, ensure_ascii=False)},
        )

    def get_state(self, key: str) -> dict[str, Any] | None:
        """The stored document plus `stored_at`, or None. Returns the artifact
        itself rather than a wrapper so callers render it unchanged — the
        freshness stamp is added as a field because an operator looking at a
        credential countdown needs to know how old it is."""
        rows = self.client.execute(
            f"""SELECT payload, CAST(updated_at AS text) AS updated_at
                FROM {SCHEMA}.lab_state WHERE key = :key""",
            {"key": key},
        )
        if not rows:
            return None
        payload = rows[0]["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if isinstance(payload, dict):
            payload = {**payload, "stored_at": rows[0]["updated_at"]}
        return payload

    # ---- reads (WS13 item 6 / D49) ----------------------------------------
    # Until 2026-07-28 the console's `_obs_store()` returned the sqlite
    # ObsStore unconditionally, so the Observability section rendered whatever
    # the LOCAL harvest had written to traces/lab.db while the hosted harvest
    # Lambda filled Aurora. Two stores drifting apart, invisibly, because the
    # laptop always had a lab.db to show. Hosting the console made it obvious:
    # a container has no lab.db at all, so the section was simply empty.
    #
    # The SQL below is a direct translation of the sqlite twin's, with two
    # differences forced by Postgres: jsonb columns need `::text` to come back
    # as strings, and a LIKE against jsonb has to cast first. The rider regexes
    # and the usage rollup are imported rather than copied.

    def summary(self) -> dict[str, Any]:
        from observability.store import accumulate_usage

        out: dict[str, Any] = {"platforms": {}}
        for row in self.client.execute(
            f"SELECT platform, COUNT(*) AS sessions FROM {SCHEMA}.obs_sessions GROUP BY platform"
        ):
            out["platforms"].setdefault(row["platform"], {})["sessions"] = row["sessions"]
        for row in self.client.execute(
            f"SELECT platform, COUNT(*) AS events FROM {SCHEMA}.obs_events GROUP BY platform"
        ):
            out["platforms"].setdefault(row["platform"], {})["events"] = row["events"]
        for row in self.client.execute(
            f"SELECT platform, last_harvest_at, status, detail FROM {SCHEMA}.obs_harvest"
        ):
            out["platforms"].setdefault(row["platform"], {})["harvest"] = {
                "at": row["last_harvest_at"],
                "status": row["status"],
                "detail": row["detail"],
            }
        for row in self.client.execute(
            f"""SELECT platform, usage_json::text AS usage_json
                FROM {SCHEMA}.obs_sessions WHERE usage_json IS NOT NULL"""
        ):
            accumulate_usage(out["platforms"], row["platform"], row["usage_json"])
        return out

    def list_sessions(self, platform: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        q = f"""SELECT s.platform, s.native_id, s.lab_session_id, s.title, s.status,
                       s.created_at, s.updated_at, s.usage_json::text AS usage_json,
                       s.harvested_at,
                       (SELECT COUNT(*) FROM {SCHEMA}.obs_events e
                         WHERE e.platform = s.platform
                           AND e.native_session_id = s.native_id) AS event_count,
                       (SELECT COUNT(DISTINCT t.trace_id) FROM {SCHEMA}.trace_events t
                         WHERE t.platform_ref = s.native_id) AS lab_trace_count
                FROM {SCHEMA}.obs_sessions s"""
        params: dict[str, Any] = {"limit": limit}
        if platform:
            q += " WHERE s.platform = :platform"
            params["platform"] = platform
        # COALESCE, matching sqlite: created_at is nullable, and in Postgres a
        # NULL sorts FIRST under DESC — the nulls would push real sessions off
        # the end of the LIMIT.
        q += " ORDER BY COALESCE(s.created_at, '') DESC LIMIT :limit"
        return [dict(r) for r in self.client.execute(q, params)]

    def list_events(self, platform: str, native_session_id: str) -> list[dict[str, Any]]:
        """One session's harvested events, PAGED to fit the Data API's 1 MB cap.

        A busy session's raw payloads run to megabytes (2.43 MB measured), so a
        single SELECT fails with UnsupportedResultException — and it fails on
        exactly the sessions worth looking at. The page size is derived from the
        widest row in this session rather than guessed, because the rows vary by
        two orders of magnitude between platforms.
        """
        args = {"platform": platform, "sid": native_session_id}
        widest = self.client.execute(
            f"""SELECT COALESCE(MAX(LENGTH(raw_json::text)), 0) AS mx
                FROM {SCHEMA}.obs_events
                WHERE platform = :platform AND native_session_id = :sid""",
            args,
        )
        per_page = max(1, _DATA_API_RESULT_BUDGET // max(1, int(widest[0]["mx"] or 1)))

        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            rows = self.client.execute(
                f"""SELECT platform, native_session_id, event_id, event_type,
                           processed_at, summary, usage_json::text AS usage_json,
                           raw_json::text AS raw_json, harvested_at
                    FROM {SCHEMA}.obs_events
                    WHERE platform = :platform AND native_session_id = :sid
                    ORDER BY COALESCE(processed_at, ''), event_id
                    LIMIT :limit OFFSET :offset""",
                {**args, "limit": per_page, "offset": offset},
            )
            out.extend(dict(r) for r in rows)
            if len(rows) < per_page:
                return out
            offset += per_page

    def _riders(self, needle: str, sql_pattern: str) -> dict[str, str]:
        """(platform:native_id) -> rider value extracted from harvested events.

        The D27 rider is text INSIDE the platform's own recorded payload, so
        this is a text-level join: it survives hops where no header or metadata
        field does, which is the whole reason it exists.

        The extraction runs in SQL, unlike the sqlite twin which scans in
        Python. Not a style choice — the RDS Data API caps a result at **1 MB**
        and the matching `raw_json` payloads total ~3.6 MB, so pulling them
        back to regex locally fails outright with UnsupportedResultException.
        `substring(... from ...)` returns the first capture group, so only the
        short rider value crosses the wire and the result is one small row per
        session.

        MIN() rather than "whichever row came first": the sqlite version takes
        an arbitrary matching event, so this is at worst equally arbitrary and
        at best deterministic.
        """
        return {
            f"{r['platform']}:{r['native_session_id']}": r["rider"]
            for r in self.client.execute(
                f"""SELECT platform, native_session_id,
                           MIN(substring(raw_json::text from :pattern)) AS rider
                    FROM {SCHEMA}.obs_events
                    WHERE raw_json::text LIKE :needle
                      AND substring(raw_json::text from :pattern) IS NOT NULL
                    GROUP BY platform, native_session_id""",
                {"needle": f"%{needle}%", "pattern": sql_pattern},
            )
            if r.get("rider")
        }

    def session_callers(self) -> dict[str, str]:
        return self._riders("caller-agent", CALLER_RIDER_SQL)

    def session_lab_traces(self) -> dict[str, str]:
        return self._riders("lab-trace", LAB_TRACE_RIDER_SQL)

    def lab_traces_for(self, native_id: str) -> list[str]:
        return [
            r["trace_id"]
            for r in self.client.execute(
                f"SELECT DISTINCT trace_id FROM {SCHEMA}.trace_events "
                "WHERE platform_ref = :native_id",
                {"native_id": native_id},
            )
        ]

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
