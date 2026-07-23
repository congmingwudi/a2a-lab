"""OpenAI Agents SDK backend — THE CODEX DELIVERABLE.

Contract (full brief: plan/06-openai-codex-handoff.md):

- ``AgentsSdkBackend.answer(req: AgentRequest) -> AgentResponse`` runs one
  turn of an OpenAI Agents SDK agent using OPENAI_RESEARCH_SYSTEM_PROMPT
  (platforms/openai/core.py) and the model from OPENAI_MODEL (env).
- An ``ask_agentforce`` function tool delegates Salesforce-side questions
  through ``platforms.agentforce.client.AgentforceClient.from_env()`` —
  credentials stay host-side (mirror: platforms/claude/sdk_backend.py).
- Every model run is wrapped in an ``interop.trace.Hop`` whose
  ``platform_ref`` carries the OpenAI response/trace id at emit time, and
  ids are persisted to .a2alab/openai_responses.json — OpenAI has no
  read-back API for traces, so ids captured here are the ONLY join key the
  observability layer will ever have (ADR D18 / M9).
- Budget: OPENAI_ANSWER_TIMEOUT_S (default 40) — the Path A chain allows
  ~45s at the bridge.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from interop import delegation
from interop.models import AgentRequest, AgentResponse, new_trace_id
from interop.trace import Hop
from platforms.openai.core import OPENAI_RESEARCH_SYSTEM_PROMPT

DEFAULT_MODEL = "gpt-5-mini"
DEFAULT_TIMEOUT_S = "40"
DEFAULT_AGENTFORCE_TOOL_TIMEOUT_S = "34"
# Reasoning models spend "output" tokens on reasoning before any visible
# text: 400 starved the post-tool synthesis round into an empty answer.
DEFAULT_MAX_TOKENS = "2000"
DEFAULT_REASONING_EFFORT = "low"

# One process-lifetime client so the OAuth token survives across tool calls
# (a per-call client would re-authenticate and leak its connection pool on
# every mid-answer Agentforce question).
_agentforce_client = None
_responses_file_lock = threading.Lock()


def _get_agentforce_client():
    global _agentforce_client
    if _agentforce_client is None:
        from platforms.agentforce.client import AgentforceClient

        _agentforce_client = AgentforceClient.from_env()
        # D25: the OpenAI agent talks to its OpenAI-paired Agentforce twin
        # (whose external-research action targets openai-rest, not Claude),
        # keeping each cross-platform experiment a closed two-platform
        # system. Falls back to the shared SF_AGENT_ID when unset.
        openai_paired = os.environ.get("SF_OPENAI_AGENT_ID")
        if openai_paired:
            _agentforce_client.agent_id = openai_paired
    return _agentforce_client


def _responses_file() -> Path:
    return Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "openai_responses.json"


def _append_response_record(
    *,
    response_id: str | None,
    trace_id: str,
    session_id: str | None,
    ts: float,
) -> None:
    if not response_id:
        return
    path = _responses_file()
    record = {
        "response_id": response_id,
        "trace_id": trace_id,
        "session_id": session_id,
        "ts": ts,
    }
    with _responses_file_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            records = json.loads(path.read_text(encoding="utf-8") or "[]")
        except (OSError, ValueError, TypeError):
            records = []
        if not isinstance(records, list):
            records = []
        records.append(record)
        path.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _response_id_from_result(result: Any) -> str | None:
    response_id = getattr(result, "last_response_id", None)
    if response_id:
        return str(response_id)
    raw_responses = getattr(result, "raw_responses", None) or []
    if raw_responses:
        response_id = getattr(raw_responses[-1], "response_id", None)
        if response_id:
            return str(response_id)
    return None


def _build_agentforce_tool(inbound_depth: int = 0, trace_id: str | None = None):
    """Build the direct Agentforce tool for one request, closed over the
    delegation depth and effective run trace id."""
    from agents import function_tool

    @function_tool(
        name_override="ask_agentforce",
        description_override=(
            "Ask the Salesforce Agentforce agent a question. Use for "
            "accounts, opportunities, cases, or org data."
        ),
    )
    async def ask_agentforce(question: str) -> str:
        # Delegation guard (D27): a delegated-to agent doesn't delegate
        # onward — refuse in the tool result instead of looping back.
        if inbound_depth >= delegation.max_depth():
            return delegation.refusal("ask_agentforce")
        message, meta = delegation.delegate(
            question,
            caller="openai-agents-sdk-agent",
            platform="openai",
            inbound_depth=inbound_depth,
            trace_id=trace_id,
        )
        try:
            resp = await asyncio.wait_for(
                _get_agentforce_client().ask(
                    AgentRequest(message=message, metadata=meta, trace_id=trace_id)
                ),
                float(
                    os.environ.get(
                        "OPENAI_AGENTFORCE_TOOL_TIMEOUT_S",
                        DEFAULT_AGENTFORCE_TOOL_TIMEOUT_S,
                    )
                ),
            )
        except Exception as exc:  # noqa: BLE001 - tool failures are model-visible, not fatal
            return f"CRM lookup failed via Agentforce: {type(exc).__name__}: {exc}"
        return resp.text

    return ask_agentforce


def _build_agentforce_a2a_tool(inbound_depth: int = 0, trace_id: str | None = None):
    """The channel twin of ask_agentforce (D28): same Agentforce agent, but
    over the A2A protocol through the lab's hosted shim — used when the
    operator's routing block selects the a2a-shim channel."""
    from agents import function_tool

    @function_tool(
        name_override="ask_agentforce_a2a",
        description_override=(
            "Ask the Salesforce Agentforce agent a question over the A2A "
            "protocol (via the lab's hosted shim). Use ONLY when the "
            "request's [A2A-LAB ROUTING] block selects the a2a-shim "
            "channel; otherwise prefer ask_agentforce."
        ),
    )
    async def ask_agentforce_a2a(question: str) -> str:
        from interop import af_channel

        if inbound_depth >= delegation.max_depth():
            return delegation.refusal("ask_agentforce_a2a")
        message, meta = delegation.delegate(
            question,
            caller="openai-agents-sdk-agent",
            platform="openai",
            inbound_depth=inbound_depth,
            trace_id=trace_id,
        )
        try:
            return await asyncio.wait_for(
                af_channel.ask_via_shim(message, meta, trace_id=trace_id),
                float(
                    os.environ.get(
                        "OPENAI_AGENTFORCE_TOOL_TIMEOUT_S",
                        DEFAULT_AGENTFORCE_TOOL_TIMEOUT_S,
                    )
                ),
            )
        except Exception as exc:  # noqa: BLE001 - model-visible, not fatal
            return f"A2A shim call failed: {type(exc).__name__}: {exc}"

    return ask_agentforce_a2a


class AgentsSdkBackend:
    backend_name = "agents-sdk"

    def __init__(self, model: str | None = None):
        self.model = model or os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)

    def _agent(self, inbound_depth: int = 0, trace_id: str | None = None):
        from agents import Agent, ModelSettings

        settings_kwargs: dict[str, Any] = {
            "max_tokens": int(os.environ.get("OPENAI_MAX_TOKENS", DEFAULT_MAX_TOKENS)),
            "verbosity": "low",
        }
        try:
            # Low reasoning effort keeps the two model rounds inside the
            # 40s budget. Guarded import: unit tests fake the `agents`
            # module and may run without the openai extra installed.
            from openai.types.shared import Reasoning

            settings_kwargs["reasoning"] = Reasoning(
                effort=os.environ.get("OPENAI_REASONING_EFFORT", DEFAULT_REASONING_EFFORT)
            )
        except ImportError:
            pass
        return Agent(
            name="OpenAI Researcher",
            instructions=OPENAI_RESEARCH_SYSTEM_PROMPT,
            model=self.model,
            model_settings=ModelSettings(**settings_kwargs),
            # No stop_on_first_tool: after ask_agentforce returns, the model
            # gets a synthesis round so it can attribute the CRM portion
            # ("From the CRM (via Agentforce): ...") and add its own
            # research — the Path C collaboration contract. Budget still
            # fits: tool leg capped at 34s inside the 40s run cap.
            tools=[
                _build_agentforce_tool(inbound_depth, trace_id),
                _build_agentforce_a2a_tool(inbound_depth, trace_id),
            ],
        )

    async def _run(self, req: AgentRequest, trace_id: str):
        from agents import Runner

        return await Runner.run(
            self._agent(delegation.depth_of(req), trace_id),
            req.message,
            max_turns=int(os.environ.get("OPENAI_MAX_TURNS", "3")),
        )

    async def answer(self, req: AgentRequest) -> AgentResponse:
        trace_id = req.trace_id or new_trace_id()
        start = time.perf_counter()
        response_id: str | None = None
        final_text = ""
        with Hop(
            trace_id,
            source="openai-researcher",
            target="openai-platform",
            protocol="internal",
            transport_detail="agents-sdk run",
            request_payload=req.to_dict(),
        ) as hop:
            result = await asyncio.wait_for(
                self._run(req, trace_id),
                float(os.environ.get("OPENAI_ANSWER_TIMEOUT_S", DEFAULT_TIMEOUT_S)),
            )
            final_text = str(getattr(result, "final_output", "") or "").strip()
            response_id = _response_id_from_result(result)
            hop.platform_ref = response_id
            hop.response_payload = {"text": final_text, "response_id": response_id}
        _append_response_record(
            response_id=response_id,
            trace_id=trace_id,
            session_id=req.session_id,
            ts=time.time(),
        )
        return AgentResponse(
            text=final_text,
            session_id=req.session_id,
            latency_ms=int((time.perf_counter() - start) * 1000),
            raw={"response_id": response_id, "model": self.model},
        )
