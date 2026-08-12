"""AWS Strands Agents SDK backend — THE KIRO DELIVERABLE.

Contract (full brief: plan/12-strands-kiro-handoff.md):

- ``StrandsSdkBackend.answer(req: AgentRequest) -> AgentResponse`` runs one
  turn of a Strands agent using STRANDS_RESEARCH_SYSTEM_PROMPT
  (platforms/strands/core.py) and the model from STRANDS_MODEL_ID (env),
  running on Amazon Bedrock via the default AWS credential chain.
- An ``ask_agentforce`` tool delegates Salesforce-side questions through
  ``platforms.agentforce.client.AgentforceClient.from_env()`` — credentials
  stay host-side (same boundary as the OpenAI and Claude agents).
- An ``ask_agentforce_a2a`` tool delegates via the A2A protocol through the
  lab's hosted shim — used when the routing block selects the a2a-shim channel.
- Every delegation goes through ``interop.delegation`` (D27).
- Every model run is wrapped in an ``interop.trace.Hop`` whose
  ``platform_ref`` carries the Bedrock request id from the AgentResult metrics
  when available (ADR D18).
- Budget: STRANDS_ANSWER_TIMEOUT_S (default 40) — the Path A chain allows
  ~45s at the bridge.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import time

from interop import delegation
from interop.models import AgentRequest, AgentResponse, new_trace_id
from interop.trace import Hop
from platforms.strands.core import STRANDS_RESEARCH_SYSTEM_PROMPT

DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"
DEFAULT_TIMEOUT_S = "40"
DEFAULT_AGENTFORCE_TOOL_TIMEOUT_S = "34"

# One process-lifetime client so the OAuth token survives across tool calls.
_agentforce_client = None

# Thread pool for running async code from synchronous Strands tools.
# Strands tool functions are synchronous; the agent's event loop calls them
# from the model's response processing. Async delegation calls (Agentforce
# client, af_channel) must run in a fresh event loop on a worker thread.
_tool_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="strands-tool"
)


def _run_async(coro):
    """Run an async coroutine from a synchronous Strands tool context.

    Strands tools are sync functions. When called during an agent turn, there
    may already be a running event loop (the Strands SDK's own, or pytest's).
    This helper runs the coroutine in a new event loop on a worker thread,
    which avoids the 'event loop already running' error.
    """
    future = _tool_executor.submit(asyncio.run, coro)
    return future.result()


def _get_agentforce_client():
    global _agentforce_client
    if _agentforce_client is None:
        from platforms.agentforce.client import AgentforceClient

        _agentforce_client = AgentforceClient.from_env()
        # D25: the Strands agent talks to its Strands-paired Agentforce twin.
        strands_paired = os.environ.get("SF_STRANDS_AGENT_ID")
        if strands_paired:
            _agentforce_client.agent_id = strands_paired
    return _agentforce_client


def _build_agentforce_tool(
    inbound_depth: int = 0,
    trace_id: str | None = None,
    user_context: dict | None = None,
    user_token: str | None = None,
):
    """Build the direct Agentforce tool for one request, closed over the
    delegation depth and effective run trace id."""
    from strands import tool

    @tool(name="ask_agentforce")
    def ask_agentforce(question: str) -> str:
        """Ask the Salesforce Agentforce agent a question. Use for accounts, opportunities, cases, or org data."""  # noqa: E501
        if inbound_depth >= delegation.max_depth():
            return delegation.refusal("ask_agentforce")
        message, meta = delegation.delegate(
            question,
            caller="strands-sdk-agent",
            platform="strands",
            inbound_depth=inbound_depth,
            trace_id=trace_id,
            user_context=user_context,
            user_token=user_token,
        )
        try:
            timeout = float(
                os.environ.get(
                    "STRANDS_AGENTFORCE_TOOL_TIMEOUT_S", DEFAULT_AGENTFORCE_TOOL_TIMEOUT_S
                )
            )
            resp = _run_async(
                asyncio.wait_for(
                    _get_agentforce_client().ask(
                        AgentRequest(message=message, metadata=meta, trace_id=trace_id)
                    ),
                    timeout,
                )
            )
        except Exception as exc:  # noqa: BLE001
            return f"CRM lookup failed via Agentforce: {type(exc).__name__}: {exc}"
        return resp.text

    return ask_agentforce


def _build_agentforce_a2a_tool(
    inbound_depth: int = 0,
    trace_id: str | None = None,
    user_context: dict | None = None,
    user_token: str | None = None,
):
    """The channel twin: same Agentforce agent, over A2A via the hosted shim."""
    from strands import tool

    @tool(name="ask_agentforce_a2a")
    def ask_agentforce_a2a(question: str) -> str:
        """Ask the Salesforce Agentforce agent a question over the A2A protocol (via the lab's hosted shim). Use ONLY when the request's [A2A-LAB ROUTING] block selects the a2a-shim channel; otherwise prefer ask_agentforce."""  # noqa: E501
        from interop import af_channel

        if inbound_depth >= delegation.max_depth():
            return delegation.refusal("ask_agentforce_a2a")
        message, meta = delegation.delegate(
            question,
            caller="strands-sdk-agent",
            platform="strands",
            inbound_depth=inbound_depth,
            trace_id=trace_id,
            user_context=user_context,
            user_token=user_token,
        )
        try:
            timeout = float(
                os.environ.get(
                    "STRANDS_AGENTFORCE_TOOL_TIMEOUT_S", DEFAULT_AGENTFORCE_TOOL_TIMEOUT_S
                )
            )
            return _run_async(
                asyncio.wait_for(
                    af_channel.ask_via_shim(message, meta, trace_id=trace_id),
                    timeout,
                )
            )
        except Exception as exc:  # noqa: BLE001
            return f"A2A shim call failed: {type(exc).__name__}: {exc}"

    return ask_agentforce_a2a


# ---- cross-hyperscaler: Strands (AWS) -> Google ADK (Vertex) ----------------
# Two routes to the SAME Agent Engine agent, the operator picks per run — the
# cross-cloud twin of the ask_agentforce / ask_agentforce_a2a channel pair.
# What differs is the TRUST MODEL, and that is the whole point of the cell:
#
#   ask_google_adk        NATIVE-DIRECT. The container's own AWS role
#       (a2alab-agentcore) federates into a Google service account (workload
#       identity, no key) and calls Agent Engine's A2A endpoint straight. No
#       lab server in the path — so nothing lab-side sees or records the
#       cross-cloud payload; the trust is cloud-IAM end to end, least
#       privilege, and the blast radius is one impersonation binding.
#
#   ask_google_adk_bridge VIA-BRIDGE. The call goes to the lab bridge (a
#       Fargate task already federated into GCP as a2alab-bridge-task), which
#       forwards to the same ADK target and RECORDS both wire payloads. The
#       trust is now "trust the bridge": one more hop, one shared broker
#       identity, but full raw-payload capture — the lab's evidence ethos vs.
#       the tighter direct trust boundary, made switchable.
_adk_client = None


def _get_adk_client():
    """One process-lifetime A2A client to the ADK agent on Vertex AI Agent
    Engine. Auth is google-adc: on the AgentCore runtime that federates the
    container's AWS role into a Google SA (interop.cloud_auth, keyless);
    transport + version are pinned to match the target (the preview card route
    404s, and Agent Engine's managed handler is A2A 1.0-only)."""
    global _adk_client
    if _adk_client is None:
        from interop.clients.a2a import A2AClient

        endpoint = os.environ.get("ADK_A2A_ENDPOINT", "")
        if not endpoint:
            raise RuntimeError("ADK_A2A_ENDPOINT unset — see WS2/cross-hyperscaler setup")
        _adk_client = A2AClient(
            endpoint,
            auth={"scheme": "google-adc"},
            target_name="google-adk-a2a",
            source_name="strands",
            transport="http_json",
            protocol_version="1.0",
            timeout=float(os.environ.get("STRANDS_ADK_TIMEOUT_S", "65")),
        )
    return _adk_client


def _build_google_adk_tool(
    inbound_depth: int = 0,
    trace_id: str | None = None,
    user_context: dict | None = None,
    user_token: str | None = None,
):
    """NATIVE-DIRECT cross-hyperscaler tool: the Strands container calls the
    Google ADK agent over Agent Engine's own A2A endpoint, keyless-federated,
    no lab server in the path (D27-guarded like every delegation seam)."""
    from strands import tool

    @tool(name="ask_google_adk")
    def ask_google_adk(question: str) -> str:
        """Ask the Google ADK research agent (Gemini on Vertex AI Agent Engine) a question directly — a second research opinion from another hyperscaler's agent. Use when the request asks to consult the Google ADK/Gemini agent, UNLESS the [A2A-LAB ROUTING] block selects the bridge route (then use ask_google_adk_bridge)."""  # noqa: E501
        if inbound_depth >= delegation.max_depth():
            return delegation.refusal("ask_google_adk")
        message, meta = delegation.delegate(
            question,
            caller="strands-sdk-agent",
            platform="strands",
            inbound_depth=inbound_depth,
            trace_id=trace_id,
            user_context=user_context,
            user_token=user_token,
        )
        try:
            timeout = float(os.environ.get("STRANDS_ADK_TIMEOUT_S", "65"))
            resp = _run_async(
                asyncio.wait_for(
                    _get_adk_client().ask(
                        AgentRequest(message=message, metadata=meta, trace_id=trace_id)
                    ),
                    timeout,
                )
            )
        except Exception as exc:  # noqa: BLE001 - model-visible, not fatal
            return f"Google ADK consult failed (direct): {type(exc).__name__}: {exc}"
        return resp.text

    return ask_google_adk


def _build_google_adk_bridge_tool(
    inbound_depth: int = 0,
    trace_id: str | None = None,
    user_context: dict | None = None,
    user_token: str | None = None,
):
    """VIA-BRIDGE cross-hyperscaler twin: the same ADK agent, reached through
    the lab bridge so both wire payloads are captured. The bridge holds its own
    GCP federation (a2alab-bridge-task) and stamps the D27 rider itself, so this
    tool only forwards — the delegation guard still runs here first."""
    from strands import tool

    @tool(name="ask_google_adk_bridge")
    def ask_google_adk_bridge(question: str) -> str:
        """Ask the Google ADK research agent (Gemini on Vertex AI Agent Engine) THROUGH the lab bridge, which records the cross-cloud request and response. Use ONLY when the request's [A2A-LAB ROUTING] block selects the bridge route; otherwise prefer ask_google_adk."""  # noqa: E501
        import httpx

        if inbound_depth >= delegation.max_depth():
            return delegation.refusal("ask_google_adk_bridge")
        base = os.environ.get("A2ALAB_BRIDGE_URL", "")
        if not base:
            return "Google ADK consult failed (bridge): A2ALAB_BRIDGE_URL unset"
        # Strands -> bridge -> ADK is ONE logical delegation; the bridge is
        # transport, not an agent, and it is the seam that stamps the D27 rider
        # (delegation.delegate) on its forward. So we must NOT pre-stamp here —
        # doing so would arrive at the bridge already at depth 1, and the
        # bridge's own guard would refuse it (depth >= max_depth). We forward
        # the plain question at the inbound depth and carry only the user
        # channel in metadata, so the bridge stamps exactly once and the ADK
        # agent sees depth 1, symmetric with the direct route.
        meta: dict = {}
        if user_context:
            meta["user_context"] = user_context
        if user_token:
            meta["user_token"] = user_token
        url = base.rstrip("/") + "/invoke/google-adk-a2a"
        headers = {"x-trace-id": trace_id or ""}
        token = os.environ.get("BRIDGE_TOKEN")
        if token:
            headers["x-bridge-token"] = token
        body = AgentRequest(message=question, metadata=meta, trace_id=trace_id).to_dict()

        async def _post() -> str:
            timeout = float(os.environ.get("STRANDS_ADK_TIMEOUT_S", "65"))
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(url, json=body, headers=headers)
                r.raise_for_status()
                return AgentResponse.from_dict(r.json()).text

        try:
            return _run_async(_post())
        except Exception as exc:  # noqa: BLE001 - model-visible, not fatal
            return f"Google ADK consult failed (bridge): {type(exc).__name__}: {exc}"

    return ask_google_adk_bridge


def _extract_request_id(result) -> str | None:
    """Extract the Bedrock request id from AgentResult metrics if available."""
    metrics = getattr(result, "metrics", None)
    if metrics:
        # metrics.accumulated carries per-cycle data; the request id lives
        # on the last cycle's response metadata when the model is Bedrock.
        request_id = getattr(metrics, "request_id", None)
        if request_id:
            return str(request_id)
    # Fallback: walk the trace spans if present.
    state = getattr(result, "state", None)
    if state and isinstance(state, dict):
        rid = state.get("request_id") or state.get("bedrock_request_id")
        if rid:
            return str(rid)
    return None


class StrandsSdkBackend:
    backend_name = "strands-sdk"

    def __init__(self, model_id: str | None = None):
        self.model_id = model_id or os.environ.get("STRANDS_MODEL_ID", DEFAULT_MODEL_ID)

    def _make_agent(
        self,
        inbound_depth: int = 0,
        trace_id: str | None = None,
        user_context: dict | None = None,
        user_token: str | None = None,
    ):
        from strands import Agent
        from strands.models.bedrock import BedrockModel

        region = os.environ.get("AWS_REGION", "us-east-1")
        model = BedrockModel(
            model_id=self.model_id,
            region_name=region,
        )
        tools = [
            _build_agentforce_tool(inbound_depth, trace_id, user_context, user_token),
            _build_agentforce_a2a_tool(inbound_depth, trace_id, user_context, user_token),
            # Cross-hyperscaler: the same ADK agent by two routes (native-direct
            # federation vs the payload-capturing bridge), operator-selected.
            _build_google_adk_tool(inbound_depth, trace_id, user_context, user_token),
            _build_google_adk_bridge_tool(inbound_depth, trace_id, user_context, user_token),
        ]
        return Agent(
            model=model,
            system_prompt=STRANDS_RESEARCH_SYSTEM_PROMPT,
            tools=tools,
            callback_handler=None,
        )

    async def answer(self, req: AgentRequest) -> AgentResponse:
        trace_id = req.trace_id or new_trace_id()
        start = time.perf_counter()
        request_id: str | None = None
        final_text = ""
        with Hop(
            trace_id,
            source="strands-researcher",
            target="strands-platform",
            protocol="internal",
            transport_detail="strands-sdk invoke_async",
            request_payload=req.to_dict(),
        ) as hop:
            agent = self._make_agent(delegation.depth_of(req), trace_id, *delegation.user_of(req))
            result = await asyncio.wait_for(
                agent.invoke_async(req.message),
                float(os.environ.get("STRANDS_ANSWER_TIMEOUT_S", DEFAULT_TIMEOUT_S)),
            )
            final_text = str(result).strip()
            request_id = _extract_request_id(result)
            hop.platform_ref = request_id
            hop.response_payload = {"text": final_text, "request_id": request_id}
        return AgentResponse(
            text=final_text,
            session_id=req.session_id,
            latency_ms=int((time.perf_counter() - start) * 1000),
            raw={"request_id": request_id, "model": self.model_id, "backend": "strands-sdk"},
        )
