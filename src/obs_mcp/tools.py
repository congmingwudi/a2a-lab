"""The obs MCP server's two tools (ADR D23).

query_obs_store — the remote successor to the analyst's host-side custom
tool: read-only SQL against the Aurora store, executed under the lab_reader
role (the DB grant is the real guard; the prefix check here just fails
fast). save_brief — the delivery path: the finished brief lands in
lab.obs_briefs, keeping the analyst observable by the thing it analyzes
(each tool call is also recorded as a trace hop, writer credentials
permitting).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from obs_mcp.core import ToolDef, ToolRegistry
from observability.pg import CLUSTER_ARN_ENV, SCHEMA, PgClient

WRITER_SECRET_ENV = "A2ALAB_PG_WRITER_SECRET_ARN"

MAX_ROWS = 200
MAX_RESULT_CHARS = 60_000

QUERY_DESCRIPTION = (
    "Run one read-only SQL SELECT against the lab's observability store "
    f"(Aurora Postgres, schema `{SCHEMA}`). Tables: {SCHEMA}.trace_events "
    "(the lab's own wire hops: trace_id, hop_seq, ts epoch seconds, ts_at "
    "timestamptz, source, target, protocol, status, latency_ms, platform_ref, "
    "request/response_payload_raw jsonb), "
    f"{SCHEMA}.obs_sessions (harvested platform sessions: platform, native_id, "
    "lab_session_id, title, status, created_at, updated_at, usage_json jsonb), "
    f"{SCHEMA}.obs_events (platform-interior events: platform, "
    "native_session_id, event_type, processed_at, summary, usage_json jsonb), "
    f"{SCHEMA}.obs_harvest (per-platform harvest status), and "
    f"{SCHEMA}.obs_briefs (your past findings briefs). "
    "Join wire↔platform views on trace_events.platform_ref = "
    "obs_sessions.native_id. Explore the schema via information_schema. "
    f"Results are capped at {MAX_ROWS} rows — aggregate in SQL rather than "
    "fetching raw rows; use jsonb operators (->, ->>) for usage fields."
)

SAVE_BRIEF_DESCRIPTION = (
    "Save the finished findings brief (markdown) to the lab's obs store. "
    "Call exactly once, at the end, with the complete brief. Optionally "
    "report how many queries you ran."
)


class ObsTools:
    """Holds the two PgClients and implements the tool bodies. The reader
    client comes from the standard env (the Lambda's A2ALAB_PG_SECRET_ARN is
    the lab_reader secret); the writer client uses A2ALAB_PG_WRITER_SECRET_ARN
    when present, else falls back to the reader config (local DSN case,
    where the DSN user holds write grants)."""

    def __init__(self, reader: PgClient | None = None, writer: PgClient | None = None):
        self._reader = reader
        self._writer = writer

    @property
    def reader(self) -> PgClient:
        if self._reader is None:
            self._reader = PgClient.from_env()
        return self._reader

    @property
    def writer(self) -> PgClient:
        if self._writer is None:
            cluster = os.environ.get(CLUSTER_ARN_ENV)
            writer_secret = os.environ.get(WRITER_SECRET_ENV)
            if cluster and writer_secret:
                self._writer = PgClient(cluster_arn=cluster, secret_arn=writer_secret)
            else:
                self._writer = self.reader
        return self._writer

    # ---- tools ------------------------------------------------------------

    def query_obs_store(self, args: dict[str, Any]) -> str:
        sql = str(args.get("sql") or "").strip().rstrip(";").strip()
        head = sql.lstrip("(").lower()
        if not (head.startswith("select") or head.startswith("with")):
            result = json.dumps({"error": "only single SELECT/WITH statements are allowed"})
        elif ";" in sql:
            result = json.dumps({"error": "multiple statements are not allowed"})
        else:
            try:
                rows = self.reader.execute(sql)
                capped = rows[:MAX_ROWS]
                result = json.dumps(
                    {"rows": capped, "row_count": len(capped), "capped_at": MAX_ROWS},
                    default=str,
                    ensure_ascii=False,
                )
                if len(result) > MAX_RESULT_CHARS:
                    result = json.dumps(
                        {
                            "error": f"result too large ({len(result)} chars) — "
                            "aggregate in SQL or select fewer columns",
                            "row_count": len(capped),
                        }
                    )
            except Exception as exc:  # noqa: BLE001 - errors go back for self-correction
                result = json.dumps({"error": f"{type(exc).__name__}: {exc}"})
        self._record_hop("tools/call query_obs_store", {"sql": sql}, result)
        return result

    def save_brief(self, args: dict[str, Any]) -> str:
        brief_md = str(args.get("brief_md") or "").strip()
        if not brief_md:
            return json.dumps({"error": "brief_md is required"})
        queries_run = args.get("queries_run")
        self.writer.execute(
            f"""INSERT INTO {SCHEMA}.obs_briefs (brief_date, session_id, queries_run, brief_md)
                VALUES (CURRENT_DATE, :session_id, :queries_run, :brief_md)""",
            {
                "session_id": args.get("session_id"),
                "queries_run": int(queries_run) if queries_run is not None else None,
                "brief_md": brief_md,
            },
        )
        result = json.dumps({"saved": True, "chars": len(brief_md)})
        self._record_hop("tools/call save_brief", {"chars": len(brief_md)}, result)
        return result

    def _record_hop(self, detail: str, request: Any, response: Any) -> None:
        """Each tool call is itself a wire hop (D7 ethos) — best-effort,
        never allowed to fail the tool."""
        try:
            self.writer.execute(
                f"""INSERT INTO {SCHEMA}.trace_events
                    (trace_id, hop_seq, ts, ts_at, source, target, protocol,
                     transport_detail, status, request_payload_raw, response_payload_raw)
                    VALUES (:trace_id, 0, :ts, to_timestamp(:ts), 'obs-analyst', 'obs-store',
                            'mcp', :detail, 'ok', CAST(:request AS jsonb),
                            CAST(:response AS jsonb))
                    ON CONFLICT DO NOTHING""",
                {
                    "trace_id": "obs-analysis-" + time.strftime("%Y%m%d"),
                    "ts": time.time(),
                    "detail": detail,
                    "request": json.dumps(request, default=str, ensure_ascii=False),
                    "response": json.dumps(response, default=str, ensure_ascii=False)[:2000],
                },
            )
        except Exception:  # noqa: BLE001, S110 - tracing must not break the tool
            pass


def build_registry(tools: ObsTools | None = None) -> ToolRegistry:
    tools = tools or ObsTools()
    registry = ToolRegistry()
    registry.register(
        ToolDef(
            name="query_obs_store",
            description=QUERY_DESCRIPTION,
            input_schema={
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "A single SELECT statement."}
                },
                "required": ["sql"],
            },
            fn=tools.query_obs_store,
        )
    )
    registry.register(
        ToolDef(
            name="save_brief",
            description=SAVE_BRIEF_DESCRIPTION,
            input_schema={
                "type": "object",
                "properties": {
                    "brief_md": {
                        "type": "string",
                        "description": "The complete findings brief, markdown.",
                    },
                    "queries_run": {
                        "type": "integer",
                        "description": "How many query_obs_store calls you made.",
                    },
                },
                "required": ["brief_md"],
            },
            fn=tools.save_brief,
        )
    )
    return registry
