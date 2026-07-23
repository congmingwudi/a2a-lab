"""Microsoft Foundry log source (WS3, M11) — Application Insights via KQL.

The richest platform column so far, and the only AGENT-SEMANTIC one: with
App Insights connected to the Foundry project, every agent run emits
OpenTelemetry gen_ai spans into AppDependencies — ``invoke_agent`` (the
turn), ``chat <model>`` (each model call, WITH
``gen_ai.usage.input/output/cached_tokens`` and full input/output
messages), and ``execute_tool`` (the platform's own record of calling the
lab's A2A shim, duration included). Contrast: Salesforce = queryable
session/step DMOs, Anthropic = deep per-session events, OpenAI =
write-only, GCP = request-level logs + billing meters. Foundry lands
agent-semantic AND queryable (KQL) — the column WS3 hoped for.

Sessions: one per turn, keyed by ``gen_ai.response.id`` — the same id the
lab's FoundryClient records as ``platform_ref``, so the store's
trace_events↔obs_sessions join works out of the box. Spans grouped by
OperationId land as events; token usage aggregates onto the session
(the coverage tile's token count picks it up generically).

Auth rides the same Entra ADC the data plane uses; queries go through
azure-monitor-query (``AZURE_LOGS_WORKSPACE_ID`` = the Log Analytics
workspace customerId behind the connected App Insights resource).
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from typing import Any

from observability.base import HarvestResult, PlatformLogSource
from observability.store import ObsStore

WINDOW_HOURS = 24
MAX_ROWS = 1000

_KQL = f"""
AppDependencies
| where TimeGenerated > ago({WINDOW_HOURS}h)
| extend props = todynamic(Properties)
| where isnotempty(props["gen_ai.operation.name"])
| project TimeGenerated, Id, Name, DurationMs, Success, OperationId,
          operation = tostring(props["gen_ai.operation.name"]),
          response_id = tostring(props["gen_ai.response.id"]),
          agent = tostring(props["gen_ai.agent.id"]),
          model = tostring(props["gen_ai.response.model"]),
          input_tokens = toint(props["gen_ai.usage.input_tokens"]),
          output_tokens = toint(props["gen_ai.usage.output_tokens"]),
          cached_tokens = toint(props["gen_ai.usage.cached_tokens"])
| order by TimeGenerated asc
| take {MAX_ROWS}
"""


class FoundrySource(PlatformLogSource):
    name = "foundry"

    def harvest(self, store: ObsStore) -> HarvestResult:
        workspace = os.environ.get("AZURE_LOGS_WORKSPACE_ID")
        if not workspace:
            result = HarvestResult(
                platform=self.name,
                status="blocked",
                detail="AZURE_LOGS_WORKSPACE_ID unset — attach App Insights (WS3) first",
            )
            store.set_harvest_status(self.name, result.status, result.detail)
            return result

        try:
            from azure.identity import DefaultAzureCredential
            from azure.monitor.query import LogsQueryClient

            client = LogsQueryClient(DefaultAzureCredential())
            response = client.query_workspace(
                workspace, _KQL, timespan=timedelta(hours=WINDOW_HOURS)
            )
            table = response.tables[0]
            columns = [str(c) for c in table.columns]
            rows = [dict(zip(columns, row)) for row in table.rows]
        except Exception as exc:
            result = HarvestResult(
                platform=self.name, status="error", detail=f"{type(exc).__name__}: {exc}"
            )
            store.set_harvest_status(self.name, result.status, result.detail)
            return result

        result = HarvestResult(platform=self.name, status="ok")
        # One obs session per turn (response id = the lab's platform_ref —
        # the trace_events join key); spans land as its events.
        by_turn: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            key = row.get("response_id") or f"op-{row.get('OperationId', '?')}"
            by_turn.setdefault(key, []).append(row)

        for response_id, spans in by_turn.items():
            tokens_in = sum(int(s.get("input_tokens") or 0) for s in spans)
            tokens_out = sum(int(s.get("output_tokens") or 0) for s in spans)
            tokens_cached = sum(int(s.get("cached_tokens") or 0) for s in spans)
            invoke = next((s for s in spans if s.get("operation") == "invoke_agent"), spans[0])
            store.upsert_session(
                self.name,
                response_id,
                title=f"{invoke.get('agent') or 'foundry agent'} · {invoke.get('Name', '')}",
                status="completed" if all(s.get("Success") for s in spans) else "failed",
                created_at=str(invoke.get("TimeGenerated") or ""),
                usage={
                    "input_tokens": tokens_in,
                    "output_tokens": tokens_out,
                    "cache_read_input_tokens": tokens_cached,
                },
                raw={
                    "operation_id": invoke.get("OperationId"),
                    "spans": len(spans),
                    "model": next((s.get("model") for s in spans if s.get("model")), None),
                },
            )
            result.sessions += 1
            for span in spans:
                store.upsert_event(
                    self.name,
                    response_id,
                    str(span.get("Id") or ""),
                    event_type=str(span.get("operation") or "span"),
                    processed_at=str(span.get("TimeGenerated") or ""),
                    summary=(
                        f"{span.get('Name', '')} · {float(span.get('DurationMs') or 0):.0f}ms"
                        f" · {'ok' if span.get('Success') else 'FAILED'}"
                        + (
                            f" · in {span.get('input_tokens')} / out {span.get('output_tokens')} tokens"
                            if span.get("input_tokens")
                            else ""
                        )
                    ),
                    raw=json.loads(json.dumps(span, default=str)),
                )
                result.events += 1

        result.detail = (
            f"{result.sessions} turns / {result.events} gen_ai spans (last "
            f"{WINDOW_HOURS}h) — agent-semantic OTel via App Insights KQL: "
            "invoke_agent + chat (token usage) + execute_tool (the platform's "
            "own record of calling the lab's A2A shim)"
        )
        store.set_harvest_status(self.name, result.status, result.detail)
        return result
