"""Async account-brief runner (ADR D16) — the pattern Managed Agents is
actually designed for: a LONG-RUNNING research session, not a synchronous
request/response exchange.

Two entry points share this code:
- Console "Run" (ad-hoc): creates a session from the same brief agent the
  daily schedule uses and drives it to completion in the background.
- The daily scheduled deployment (Anthropic-side cron): sessions fire
  autonomously; `briefs.__main__ --watch` finds their runs and services
  them here.

The session does multi-source web research in the managed sandbox
(web_search / web_fetch — news, competitors, government relations,
geopolitics), then calls the `save_account_brief` custom tool. That tool is
executed HOST-SIDE by this module (briefs.salesforce.BriefWriter): insert
A2ALab_Account_Brief__c, log a Task on the Account, fire the in-app alert.
Salesforce credentials never enter the sandbox. Every hop lands in the
trace layer so the console shows the whole loop.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic

from briefs.salesforce import BriefWriter
from interop.trace import Hop, TraceEvent, get_recorder

STATE_FILE = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "brief.json"

# Long-running by design: generous cap so genuinely deep research finishes,
# while a wedged session still gets reaped. NOT the sync-path 100s budget.
BRIEF_TIMEOUT_S = float(os.environ.get("A2ALAB_BRIEF_TIMEOUT_S", "1800"))

SAVE_TOOL_NAME = "save_account_brief"

BRIEF_SYSTEM_PROMPT = (
    "You are the A2A Interop Lab's account-intelligence researcher, a "
    "long-running scheduled research agent. For each account you are given, "
    "produce a decision-ready daily intelligence brief for its Salesforce "
    "account team.\n\n"
    "Research broadly with web_search and web_fetch across MULTIPLE distinct "
    "queries and sources before writing. Cover, as separate markdown "
    "sections:\n"
    "1. Company news — funding, earnings, leadership, products, M&A.\n"
    "2. Competitor moves — what relevant competitors in the space are doing.\n"
    "3. Government & regulatory — policy, procurement, legal or regulatory "
    "developments that could affect the company.\n"
    "4. Geopolitics & international relations — trade, sanctions, supply "
    "chain, regional risk affecting the company or its markets.\n"
    "5. Implications for the account team — 3-5 concrete, actionable sales "
    "plays grounded in the sections above.\n\n"
    "Keep the brief under ~1200 words, cite sources inline as markdown "
    "links ONLY — never emit <cite> tags or other raw citation markup — "
    "and clearly flag anything speculative. If the account is a demo/"
    "fictional company with no real-world footprint, research the most "
    "plausible real-world interpretation of its name and industry, say you "
    "did so, and clearly label illustrative content.\n\n"
    "When (and only when) the brief is complete, call the "
    "save_account_brief tool EXACTLY ONCE per account with the full "
    "markdown. After the tool confirms the Salesforce record, reply with a "
    "2-3 sentence completion summary (record id, headline) and stop."
)

KICKOFF_TEMPLATE = (
    "Produce today's account intelligence brief for: {accounts}. "
    "Research first, then deliver via save_account_brief.{extra}"
)

SAVE_TOOL_DEF = {
    "type": "custom",
    "name": SAVE_TOOL_NAME,
    "description": (
        "Deliver a finished account intelligence brief to Salesforce. "
        "Inserts the brief as a record linked to the Account, logs an "
        "activity, and alerts the account team in-app. Call exactly once "
        "per account, only when the brief is complete."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "account_name": {
                "type": "string",
                "description": "The Salesforce account name, e.g. 'Omega, Inc.'",
            },
            "headline": {
                "type": "string",
                "description": "One-line summary of today's most important finding",
            },
            "brief_markdown": {
                "type": "string",
                "description": "The full brief in markdown (all five sections)",
            },
        },
        "required": ["account_name", "headline", "brief_markdown"],
    },
}


def load_brief_ids() -> dict:
    if not STATE_FILE.exists():
        raise RuntimeError(
            "Brief agent not provisioned. Run "
            "`uv run python scripts/setup_brief_agent.py` once (needs "
            "ANTHROPIC_API_KEY)."
        )
    return json.loads(STATE_FILE.read_text())


def _record_hop(trace_id: str, **kw) -> None:
    recorder = get_recorder()
    recorder.record(TraceEvent(trace_id=trace_id, hop_seq=recorder.next_hop_seq(trace_id), **kw))


class BriefRunner:
    """Drives one research session to completion, servicing tool calls."""

    def __init__(self, client: AsyncAnthropic | None = None):
        self._client = client or AsyncAnthropic()
        self._writer: BriefWriter | None = None

    def _get_writer(self) -> BriefWriter:
        if self._writer is None:
            self._writer = BriefWriter.from_env()
        return self._writer

    async def aclose(self) -> None:
        if self._writer is not None:
            await self._writer.aclose()

    async def run_adhoc(self, accounts: str, trace_id: str, extra_context: str = "") -> dict:
        """Console-triggered firing of the same job the daily schedule runs."""
        ids = load_brief_ids()
        with Hop(
            trace_id,
            source="brief-worker",
            target="brief-researcher",
            protocol="managed-agents-api",
            transport_detail="sessions.create (ad-hoc run of the daily job)",
            request_payload={"accounts": accounts, "agent_id": ids["agent_id"]},
        ) as hop:
            session = await self._client.beta.sessions.create(
                agent=ids["agent_id"],
                environment_id=ids["environment_id"],
                title=f"a2a-lab daily brief (ad-hoc): {accounts}",
            )
            hop.response_payload = {"session_id": session.id}

        extra = f"\nAdditional operator guidance: {extra_context}" if extra_context else ""
        kickoff = KICKOFF_TEMPLATE.format(accounts=accounts, extra=extra)
        return await self._drive(session.id, trace_id, kickoff=kickoff)

    async def service_scheduled_session(self, session_id: str, trace_id: str) -> dict:
        """Attach to a session fired by the scheduled deployment (its kickoff
        came from the deployment's initial_events) and drive it home."""
        _record_hop(
            trace_id,
            source="anthropic-scheduler",
            target="brief-researcher",
            protocol="managed-agents-api",
            transport_detail="scheduled deployment fired session (daily cron)",
            request_payload_raw={"session_id": session_id},
            response_payload_raw=None,
            status="ok",
            latency_ms=0,
        )
        return await self._drive(session_id, trace_id, kickoff=None)

    async def _drive(self, session_id: str, trace_id: str, *, kickoff: str | None) -> dict:
        start = time.perf_counter()
        texts: list[str] = []
        deliveries: list[dict] = []
        searches = 0
        deadline = start + BRIEF_TIMEOUT_S

        # Stream-first, then send the kickoff (ad-hoc only). For scheduled
        # sessions the kickoff already happened — consolidate history first
        # so tool calls emitted before we attached are not lost.
        stream = await self._client.beta.sessions.events.stream(session_id=session_id)
        try:
            handled: set[str] = set()
            if kickoff is not None:
                await self._client.beta.sessions.events.send(
                    session_id=session_id,
                    events=[
                        {
                            "type": "user.message",
                            "content": [{"type": "text", "text": kickoff}],
                        }
                    ],
                )
            else:
                answered = set()
                history = []
                events_page = await self._client.beta.sessions.events.list(session_id=session_id)
                async for ev in events_page:
                    history.append(ev)
                for ev in history:
                    if getattr(ev, "type", "") == "user.custom_tool_result":
                        answered.add(getattr(ev, "custom_tool_use_id", None))
                for ev in history:
                    done = await self._handle_event(
                        ev,
                        session_id,
                        trace_id,
                        texts,
                        deliveries,
                        handled,
                        skip_tool_ids=answered,
                    )
                    searches += done
                # If the session already idles awaiting nothing, we're done.
                session = await self._client.beta.sessions.retrieve(session_id)
                if getattr(session, "status", "") in ("terminated",):
                    return self._result(session_id, texts, deliveries, searches, start)

            async for event in stream:
                etype = getattr(event, "type", "")
                if etype == "session.status_idle":
                    stop = getattr(event, "stop_reason", None)
                    if getattr(stop, "type", None) != "requires_action":
                        break
                elif etype == "session.status_terminated":
                    break
                elif etype == "session.error":
                    raise RuntimeError(f"managed session error: {event}")
                else:
                    searches += await self._handle_event(
                        event, session_id, trace_id, texts, deliveries, handled
                    )
                if time.perf_counter() > deadline:
                    raise TimeoutError(
                        f"brief session exceeded A2ALAB_BRIEF_TIMEOUT_S="
                        f"{BRIEF_TIMEOUT_S:.0f}s (session {session_id})"
                    )
        finally:
            await stream.close()

        return self._result(session_id, texts, deliveries, searches, start)

    def _result(self, session_id, texts, deliveries, searches, start) -> dict:
        return {
            "session_id": session_id,
            "text": "\n".join(texts).strip(),
            "deliveries": deliveries,
            "web_lookups": searches,
            "elapsed_s": round(time.perf_counter() - start, 1),
        }

    async def _handle_event(
        self,
        event: Any,
        session_id: str,
        trace_id: str,
        texts: list[str],
        deliveries: list[dict],
        handled: set[str],
        *,
        skip_tool_ids: set | None = None,
    ) -> int:
        """Process one event. Returns 1 if it was a web research call."""
        etype = getattr(event, "type", "")
        event_id = getattr(event, "id", None)
        if event_id and event_id in handled:
            return 0
        if event_id:
            handled.add(event_id)

        if etype == "agent.message":
            for block in getattr(event, "content", []) or []:
                if getattr(block, "type", "") == "text":
                    texts.append(block.text)
            return 0

        if etype == "agent.tool_use":
            # Surface the multi-source research in the call path: one hop per
            # web_search / web_fetch the sandbox runs.
            name = getattr(event, "name", "")
            if name in ("web_search", "web_fetch"):
                tool_input = dict(getattr(event, "input", None) or {})
                _record_hop(
                    trace_id,
                    source="brief-researcher",
                    target="web",
                    protocol="internal",
                    transport_detail=f"{name}: "
                    + str(tool_input.get("query") or tool_input.get("url") or "")[:120],
                    request_payload_raw=tool_input,
                    response_payload_raw="(result stays in the managed sandbox)",
                    status="ok",
                    latency_ms=None,
                )
                return 1
            return 0

        if etype == "agent.custom_tool_use":
            if skip_tool_ids and event_id in skip_tool_ids:
                return 0
            if getattr(event, "name", "") != SAVE_TOOL_NAME:
                await self._client.beta.sessions.events.send(
                    session_id=session_id,
                    events=[
                        {
                            "type": "user.custom_tool_result",
                            "custom_tool_use_id": event.id,
                            "content": [{"type": "text", "text": f"Unknown tool: {event.name}"}],
                        }
                    ],
                )
                return 0
            tool_input = dict(getattr(event, "input", None) or {})
            try:
                delivery = await self._get_writer().save_brief(
                    account_name=str(tool_input.get("account_name", "")),
                    headline=str(tool_input.get("headline", ""))[:255],
                    brief_markdown=str(tool_input.get("brief_markdown", "")),
                    research_session_id=session_id,
                    trace_id=trace_id,
                )
                deliveries.append(delivery)
                result_text = (
                    f"Saved. Brief record {delivery['brief_id']} on account "
                    f"{delivery['account_name']} ({delivery['account_id']}); "
                    f"activity {delivery['task_id']}; in-app alert "
                    f"{'sent' if delivery['notified'] else 'skipped'}."
                )
            except Exception as exc:
                result_text = f"Salesforce delivery failed: {type(exc).__name__}: {exc}"
            await self._client.beta.sessions.events.send(
                session_id=session_id,
                events=[
                    {
                        "type": "user.custom_tool_result",
                        "custom_tool_use_id": event.id,
                        "content": [{"type": "text", "text": result_text}],
                    }
                ],
            )
            return 0

        return 0


async def run_brief(accounts: str, trace_id: str, extra_context: str = "") -> dict:
    """One-shot helper (console background task / --run-now)."""
    runner = BriefRunner()
    try:
        return await runner.run_adhoc(accounts, trace_id, extra_context)
    finally:
        await runner.aclose()
