"""LangGraph / LangSmith obs source (WS4 item 7, M11.2).

LangGraph is the agent framework; LangSmith is its framework-NATIVE
observability surface. A LangGraph/LangChain app auto-emits its run tree to
LangSmith with zero code when `LANGSMITH_API_KEY` + `LANGCHAIN_TRACING_V2=true`
are set (the Heroku dyno has both, D77). So — exactly like the ADK source reads
Vertex and the Strands source reads CloudWatch — this source observes LangGraph
through LangSmith's own API rather than inventing a lab-specific log store.

WHERE THIS DIFFERS from the runtime-rollup sources (strands/adk): LangSmith
exposes a PER-TURN run TREE, not a daily meter. Each turn is a `trace` whose
root run is a chain; its children are the LLM span (ChatAnthropic, carrying the
model + token counts) and any tool spans (`ask_agentforce`), plus internal
LangChain scaffolding (RunnableSequence, should_continue, prompt) that is NOT a
real step. So the honest mapping is:

  - one root run  → one obs SESSION (a turn): rolled-up tokens, latency, model,
    tool-call count, success/error — and the wire-trace join `lab_trace_id`
    which the backend stamps into `extra.metadata` (goes live after the next
    Heroku rebuild; older turns simply carry no join).
  - each LLM / tool child → one obs EVENT — the actual run tree, scaffolding
    chains dropped.

Auth: `LANGSMITH_API_KEY` (X-API-Key), the lab's own personal-account key
(the GCP/Azure pattern — external platforms use the congmingwudi@gmail.com
accounts; Salesforce provides AWS/the org/Heroku). Missing key → blocked, the
same honest degrade every source uses. The httpx.Client is injectable so tests
run against canned LangSmith payloads with no network.

OUT of scope here: the lab's own wire-trace persistence to Aurora (WS4 item 9)
— that is a separate sink, not this platform-obs column.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Any

import httpx

from observability.base import HarvestResult, PlatformLogSource
from observability.store import ObsStore

DEFAULT_ENDPOINT = "https://api.smith.langchain.com"
DEFAULT_PROJECT = "a2a-lab"
WINDOW_HOURS = 24
# LangSmith caps /runs/query at 100 rows per page (a 400 otherwise, verified
# live 2026-08-17). The lab's traffic is a handful of turns per window, so one
# page suffices; a busier project would page on the `cursors` field.
PAGE_LIMIT = 100

# LangChain scaffolding run names/types that are framework internals, not a step
# a reader cares about — dropped from the event stream. LLM and tool runs are
# kept; everything else (chain plumbing) is not.
_EVENT_RUN_TYPES = {"llm", "tool"}


def _meta(run: dict[str, Any]) -> dict[str, Any]:
    return ((run.get("extra") or {}).get("metadata")) or {}


def _latency_ms(start: Any, end: Any) -> int | None:
    """ISO8601 start/end → whole milliseconds, or None if either is unparseable."""
    try:
        t0 = dt.datetime.fromisoformat(str(start))
        t1 = dt.datetime.fromisoformat(str(end))
    except (TypeError, ValueError):
        return None
    return round((t1 - t0).total_seconds() * 1000)


def group_by_trace(runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Split a flat run list into {trace_id: {"root": run|None, "children": [...]}}.

    The root is the run with no parent (`parent_run_id` null); everything else
    under that trace_id is a child. A trace whose root is outside the window
    (children only) still groups, with root=None — the harvest skips it."""
    grouped: dict[str, dict[str, Any]] = {}
    for run in runs:
        tid = run.get("trace_id") or run.get("id")
        if tid is None:
            continue
        grp = grouped.setdefault(tid, {"root": None, "children": []})
        if run.get("parent_run_id") is None:
            grp["root"] = run
        else:
            grp["children"].append(run)
    return grouped


def _model_of(children: list[dict[str, Any]]) -> str | None:
    for c in children:
        if c.get("run_type") == "llm":
            model = _meta(c).get("ls_model_name")
            if model:
                return str(model)
    return None


def summarize_session(root: dict[str, Any], children: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll one run tree up into the session usage block. Pure — no store, no
    network — so the arithmetic is testable against canned runs.

    Tokens come from the root's own rolled-up counts when present (LangSmith
    sums the tree onto the root), falling back to summing the LLM children."""
    llm = [c for c in children if c.get("run_type") == "llm"]
    tools = [c for c in children if c.get("run_type") == "tool"]

    def _tok(field: str) -> int:
        val = root.get(field)
        if val is not None:
            return int(val)
        return sum(int(c.get(field) or 0) for c in llm)

    return {
        "window_hours": WINDOW_HOURS,
        "model": _model_of(children),
        "prompt_tokens": _tok("prompt_tokens"),
        "completion_tokens": _tok("completion_tokens"),
        "total_tokens": _tok("total_tokens"),
        "latency_ms": _latency_ms(root.get("start_time"), root.get("end_time")),
        "llm_calls": len(llm),
        "tool_calls": len(tools),
        "lab_trace_id": _meta(root).get("lab_trace_id"),
        "status": root.get("status"),
    }


def _event_summary(run: dict[str, Any]) -> str:
    name = str(run.get("name") or run.get("run_type") or "span")
    if run.get("run_type") == "llm":
        bits = [name]
        model = _meta(run).get("ls_model_name")
        if model:
            bits.append(str(model))
        total = run.get("total_tokens")
        if total is not None:
            bits.append(f"{int(total)} tokens")
        return " · ".join(bits)
    return name


def event_rows(children: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The substantive child spans (LLM + tool) as event rows. LangChain
    scaffolding chains (RunnableSequence / should_continue / prompt) are dropped
    — they are framework plumbing, not steps a reader is looking for."""
    rows: list[dict[str, Any]] = []
    for run in children:
        rtype = run.get("run_type")
        if rtype not in _EVENT_RUN_TYPES:
            continue
        usage = None
        if rtype == "llm":
            usage = {
                "prompt_tokens": run.get("prompt_tokens"),
                "completion_tokens": run.get("completion_tokens"),
                "total_tokens": run.get("total_tokens"),
            }
        rows.append(
            {
                "id": run.get("id"),
                "event_type": rtype,
                "processed_at": run.get("start_time"),
                "summary": _event_summary(run),
                "usage": usage,
                "raw": run,
            }
        )
    return rows


class LangGraphSource(PlatformLogSource):
    name = "langgraph"

    def __init__(self, http: httpx.Client | None = None):
        self._http = http or httpx.Client(timeout=30)

    def _headers(self, key: str) -> dict[str, str]:
        return {"x-api-key": key, "content-type": "application/json"}

    def _project_id(self, endpoint: str, key: str, project: str) -> str | None:
        resp = self._http.get(
            f"{endpoint}/api/v1/sessions",
            params={"name": project},
            headers=self._headers(key),
        )
        resp.raise_for_status()
        data = resp.json()
        # /sessions?name= returns the matching projects as a list.
        for row in data if isinstance(data, list) else [data]:
            if isinstance(row, dict) and row.get("id"):
                return str(row["id"])
        return None

    def _query_runs(self, endpoint: str, key: str, project_id: str) -> list[dict[str, Any]]:
        start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=WINDOW_HOURS)
        resp = self._http.post(
            f"{endpoint}/api/v1/runs/query",
            headers=self._headers(key),
            json={
                "session": [project_id],
                "filter": f'gte(start_time, "{start.isoformat()}")',
                "limit": PAGE_LIMIT,
            },
        )
        resp.raise_for_status()
        return resp.json().get("runs", [])

    def harvest(self, store: ObsStore) -> HarvestResult:
        key = os.environ.get("LANGSMITH_API_KEY")
        if not key:
            result = HarvestResult(
                platform=self.name,
                status="blocked",
                detail="LANGSMITH_API_KEY unset — LangGraph's observability is "
                "LangSmith (set it + LANGCHAIN_TRACING_V2=true; the Heroku dyno "
                "has both, D77)",
            )
            store.set_harvest_status(self.name, result.status, result.detail)
            return result

        endpoint = os.environ.get("LANGSMITH_ENDPOINT", DEFAULT_ENDPOINT).rstrip("/")
        project = os.environ.get("LANGCHAIN_PROJECT", DEFAULT_PROJECT)
        result = HarvestResult(platform=self.name, status="ok")
        try:
            project_id = self._project_id(endpoint, key, project)
            if not project_id:
                result.detail = (
                    f"LangSmith project '{project}' has no runs yet — run a "
                    "langgraph turn (the agent traces on next invoke, D77)"
                )
                store.set_harvest_status(self.name, result.status, result.detail)
                return result

            runs = self._query_runs(endpoint, key, project_id)
            for tid, grp in group_by_trace(runs).items():
                root = grp["root"]
                if root is None:  # root outside the window — skip the partial tree
                    continue
                children = grp["children"]
                usage = summarize_session(root, children)
                store.upsert_session(
                    self.name,
                    str(root.get("id") or tid),
                    lab_session_id=usage.get("lab_trace_id"),
                    title=str(root.get("name") or "LangGraph turn"),
                    status=root.get("status"),
                    created_at=root.get("start_time"),
                    updated_at=root.get("end_time"),
                    usage=usage,
                    raw=root,
                )
                result.sessions += 1
                for ev in event_rows(children):
                    store.upsert_event(
                        self.name,
                        str(root.get("id") or tid),
                        str(ev["id"]),
                        event_type=ev["event_type"],
                        processed_at=ev["processed_at"],
                        summary=ev["summary"],
                        usage=ev["usage"],
                        raw=ev["raw"],
                    )
                    result.events += 1

            result.detail = (
                f"{result.sessions} turn(s) / {result.events} span(s) via LangSmith "
                f"(project '{project}', last {WINDOW_HOURS}h) — LangGraph's native obs"
            )
        except httpx.HTTPStatusError as exc:
            result.status = "error"
            result.detail = f"HTTP {exc.response.status_code}: {exc.response.text[:300]}"
        except Exception as exc:  # noqa: BLE001 - report, don't raise
            result.status = "error"
            result.detail = f"{type(exc).__name__}: {exc}"

        store.set_harvest_status(self.name, result.status, result.detail)
        return result
