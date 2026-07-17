"""M11.5: the observability analyst — a managed Claude agent that
*interprets* the harvested store.

Division of labor per D22: the pull is deterministic ETL (obs_harvest.py);
this agent sits one layer up and only does analysis — run/failure counts,
latency and token-spend anomalies, cross-platform comparison — via a single
host-side custom tool that executes READ-ONLY SQL against traces/lab.db.
No credentials and no database ever enter the sandbox; the agent sees only
query results delivered as tool results.

Control plane: scripts/setup_obs_analyst.py creates the agent once (IDs in
.a2alab/obs_analyst.json). Data plane: run_analysis() drives one session
(the same stream-first / custom-tool servicing loop as briefs.runner) and
writes the findings brief to traces/obs-briefs/YYYY-MM-DD.md.

The analyst's own CMA sessions are picked up by the next harvest — it is
observable by the thing it analyzes.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path

from anthropic import AsyncAnthropic

from interop.trace import Hop
from observability.store import default_db_path

STATE_FILE = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "obs_analyst.json"

QUERY_TOOL_NAME = "query_obs_store"
MAX_ROWS = 200
ANALYSIS_TIMEOUT_S = float(os.environ.get("A2ALAB_ANALYST_TIMEOUT_S", "600"))

QUERY_TOOL_DEF = {
    "type": "custom",
    "name": QUERY_TOOL_NAME,
    "description": (
        "Run a read-only SQL SELECT against the lab's observability store "
        "(SQLite). Tables: trace_events (the lab's own wire hops: trace_id, "
        "hop_seq, ts, source, target, protocol, status, latency_ms, "
        "platform_ref), obs_sessions (harvested platform sessions: platform, "
        "native_id, lab_session_id, title, status, created_at, usage_json), "
        "obs_events (platform-interior events: platform, native_session_id, "
        "event_type, processed_at, summary, usage_json), obs_harvest "
        "(per-platform harvest status). Explore the schema with "
        '"SELECT sql FROM sqlite_master". Results are capped at 200 rows — '
        "aggregate in SQL rather than fetching raw rows."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"sql": {"type": "string", "description": "A single SELECT statement."}},
        "required": ["sql"],
    },
}

ANALYST_SYSTEM_PROMPT = """You are the observability analyst for the A2A Interop Lab — a \
cross-platform agent-to-agent experiment rig (Salesforce Agentforce ↔ Claude, REST/MCP/A2A \
protocols compared side by side).

Your input is the lab's observability store, reachable only through the query_obs_store tool \
(read-only SQL). trace_events is the lab's own wire view of every hop; obs_sessions/obs_events \
are what each *platform* recorded internally about the same runs, joined via \
trace_events.platform_ref = obs_sessions.native_id.

Produce a findings brief in markdown. Investigate before writing: volumes and time span; \
error hops and their causes (status='error' — look at latency_ms near timeout budgets, e.g. \
the documented ~45s bridge cap and cold-start session provisioning); latency by protocol and \
target; token spend from usage_json (JSON — use SQLite json_extract) and where it \
concentrates; per-platform coverage gaps from obs_harvest. Compare platforms where the data \
allows it. Every claim must come from a query you actually ran — include the number. Flag \
anomalies worth a human's attention, and say plainly when the data is too thin to conclude \
something. Keep the brief under ~500 words; lead with the two or three findings that matter."""

KICKOFF = """Analyze the lab's observability store as of now and produce today's findings \
brief. Start by checking obs_harvest freshness and the row counts of each table so the brief \
states what window and volume it covers."""


def _run_readonly_sql(sql: str, db_path: Path) -> str:
    """Execute one SELECT against lab.db read-only; return JSON for the
    tool result. Errors return as text so the agent can correct itself."""
    if not sql.strip().lower().startswith("select"):
        return json.dumps({"error": "only SELECT statements are allowed"})
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in conn.execute(sql).fetchmany(MAX_ROWS)]
        finally:
            conn.close()
        return json.dumps(
            {"rows": rows, "row_count": len(rows), "capped_at": MAX_ROWS}, default=str
        )
    except sqlite3.Error as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})


def load_analyst_ids() -> dict:
    if not STATE_FILE.exists():
        raise RuntimeError(
            "Observability analyst is not provisioned — run "
            "`uv run python scripts/setup_obs_analyst.py` once."
        )
    return json.loads(STATE_FILE.read_text())


class ObsAnalyst:
    def __init__(self, client: AsyncAnthropic | None = None, db_path: Path | None = None):
        self._client = client or AsyncAnthropic()
        self.db_path = db_path or default_db_path()

    async def _drive(self, session_id: str, trace_id: str) -> tuple[list[str], int]:
        texts: list[str] = []
        queries = 0
        stream = await self._client.beta.sessions.events.stream(session_id=session_id)
        try:
            await self._client.beta.sessions.events.send(
                session_id=session_id,
                events=[{"type": "user.message", "content": [{"type": "text", "text": KICKOFF}]}],
            )
            async for event in stream:
                etype = getattr(event, "type", "")
                if etype == "agent.message":
                    for block in getattr(event, "content", []) or []:
                        if getattr(block, "type", "") == "text":
                            texts.append(block.text)
                elif etype == "agent.custom_tool_use" and event.name == QUERY_TOOL_NAME:
                    queries += 1
                    sql = str((dict(event.input or {})).get("sql", ""))
                    result = _run_readonly_sql(sql, self.db_path)
                    await self._client.beta.sessions.events.send(
                        session_id=session_id,
                        events=[
                            {
                                "type": "user.custom_tool_result",
                                "custom_tool_use_id": event.id,
                                "content": [{"type": "text", "text": result}],
                            }
                        ],
                    )
                elif etype == "session.status_idle":
                    stop = getattr(event, "stop_reason", None)
                    if getattr(stop, "type", None) != "requires_action":
                        break
                elif etype == "session.status_terminated":
                    break
                elif etype == "session.error":
                    raise RuntimeError(f"analyst session error: {event}")
        finally:
            await stream.close()
        return texts, queries

    async def run_analysis(self) -> dict:
        """One analysis session: create → drive → save the brief."""
        ids = load_analyst_ids()
        trace_id = f"obs-analysis-{time.strftime('%Y%m%d-%H%M%S')}"
        with Hop(
            trace_id,
            source="obs-analyst",
            target="anthropic-managed-agents",
            protocol="managed-agents-api",
            transport_detail="analysis session",
            request_payload={"kickoff": KICKOFF},
        ) as hop:
            session = await self._client.beta.sessions.create(
                agent=ids["agent_id"],
                environment_id=ids["environment_id"],
                title=f"a2a-lab obs analysis {time.strftime('%Y-%m-%d')}",
            )
            hop.platform_ref = session.id
            texts, queries = await asyncio.wait_for(
                self._drive(session.id, trace_id), ANALYSIS_TIMEOUT_S
            )
            brief = "\n".join(texts).strip()
            hop.response_payload = {"queries_run": queries, "brief_chars": len(brief)}

        out_dir = self.db_path.parent / "obs-briefs"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / (time.strftime("%Y-%m-%d") + ".md")
        out_path.write_text(brief + "\n")
        return {
            "session_id": session.id,
            "queries_run": queries,
            "brief_path": str(out_path),
            "brief": brief,
        }
