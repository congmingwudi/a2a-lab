"""Path A bridge: Agentforce's GA outbound is a REST callout, so the Apex
action always POSTs here; the bridge fans out to the target agent over
whatever protocol the registry says. Switching Path A from REST to MCP to
A2A is a targets.yaml edit — no Salesforce redeploy.

    uv run python -m bridge --port 8100

Auth: the Named/External Credential in Salesforce sends X-Bridge-Token; we
compare against BRIDGE_TOKEN (skipped when unset, for local dev).
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from interop import delegation
from interop.clients.base import RemoteAgentClient
from interop.models import AgentRequest, new_trace_id
from interop.registry import Registry
from interop.trace import Hop

TRACE_HEADER = "x-trace-id"
TOKEN_HEADER = "x-bridge-token"


def create_bridge_app(registry: Registry | None = None) -> FastAPI:
    # One long-lived client per target: AgentforceClient's OAuth token and
    # session caches (and every client's connection pool) must survive across
    # requests — a per-request client would create and orphan a prod-org
    # Agentforce session on every turn.
    clients: dict[str, RemoteAgentClient] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        for client in clients.values():
            await client.aclose()
        clients.clear()

    app = FastAPI(title="A2A lab bridge", lifespan=lifespan)
    state = {"registry": registry}

    def get_registry() -> Registry:
        if state["registry"] is None:
            state["registry"] = Registry.load()
        return state["registry"]

    def get_client(name: str) -> RemoteAgentClient:
        if name not in clients:
            clients[name] = get_registry().client_for(name)
        return clients[name]

    def check_auth(request: Request) -> None:
        expected = os.environ.get("BRIDGE_TOKEN")
        if expected and request.headers.get(TOKEN_HEADER) != expected:
            raise HTTPException(status_code=401, detail="bad or missing X-Bridge-Token")

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "service": "bridge"}

    # WS8 variant 3 (D61): the Agentforce fan-out orchestrator's DELEGATED
    # topology posts one Apex callout at `fanout:<scenario>`, and the bridge
    # runs that scenario's legs CONCURRENTLY off-platform — the parallelism
    # Agentforce's serial Apex budget cannot do on-platform. This is not a
    # target you can resolve in targets.yaml; it is a verb, so it is matched by
    # prefix before the registry lookup. The orchestrator only synthesises the
    # sections this returns.
    FANOUT_PREFIX = "fanout:"

    @app.post("/invoke/{target_name}")
    async def invoke(target_name: str, request: Request):
        check_auth(request)
        body = await request.json()
        req = AgentRequest.from_dict(body)
        req.trace_id = req.trace_id or request.headers.get(TRACE_HEADER) or new_trace_id()

        if target_name.startswith(FANOUT_PREFIX):
            return await _fanout(target_name[len(FANOUT_PREFIX) :], req, request)

        registry = get_registry()
        try:
            target = registry.get(target_name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

        # Runbook 4 (action-timeout measurement): inject an artificial delay
        # so the real Agentforce action timeout can be found empirically.
        delay = float(os.environ.get("A2ALAB_DELAY_S", "0") or 0)
        if delay:
            await asyncio.sleep(delay)

        # Delegation guard (D27): every bridge forward IS a delegation (the
        # Agentforce twin farming out through Apex). Refuse over-depth
        # requests with a clean wire-visible answer instead of letting a
        # circular chain die of stacked timeouts; otherwise stamp the
        # standard rider + metadata on what we forward.
        inbound_depth = delegation.depth_of(req)
        start = time.perf_counter()
        with Hop(
            req.trace_id,
            source="agentforce-apex" if TOKEN_HEADER in request.headers else "caller",
            target="bridge",
            protocol="rest",
            transport_detail=f"POST /invoke/{target_name}",
            request_payload=body,
        ) as hop:
            if not delegation.allowed(req):
                payload = {
                    "text": delegation.refusal("bridge"),
                    "session_id": req.session_id,
                    "delegation_refused": True,
                    "bridge": {"target": target_name, "protocol": target.protocol},
                }
                hop.response_payload = payload
                return payload
            user_ctx, user_token = delegation.user_of(req)
            req.message, meta = delegation.delegate(
                req.message,
                caller="agentforce-twin-via-bridge",
                platform="agentforce",
                inbound_depth=inbound_depth,
                trace_id=req.trace_id,
                user_context=user_ctx,
                user_token=user_token,
            )
            req.metadata = {**(req.metadata or {}), **meta}
            client = get_client(target_name)
            # WS11/D77: a target flagged bridge_dispatch: submit_poll is driven
            # fire-then-poll instead of a blocking ask() — the reverse Path A fix
            # for a remote behind a hard router timeout (Heroku's 30s H12). The
            # Apex callout cannot poll, so the bridge runs submit + poll here on
            # its behalf; each request is sub-second and the answer computes in
            # the remote's background, so no single bridge→remote request outlives
            # the ceiling. Bounded under the Apex callout's 110s so the bridge
            # returns first. Non-async-capable targets fall back to sync, honestly
            # recorded. Absent flag → the unchanged blocking path.
            if target.options.get("bridge_dispatch") == "submit_poll":
                from orchestration.runner import bridge_async_timeout_s, run_target_async

                text, ran_mode, polls = await run_target_async(
                    client,
                    req,
                    trace_id=req.trace_id,
                    timeout_s=bridge_async_timeout_s(),
                )
                payload = {
                    "text": text,
                    "session_id": req.session_id,
                    "bridge": {
                        "target": target_name,
                        "protocol": target.protocol,
                        "status": target.status,
                        "dispatch_mode": ran_mode,
                        "polls": polls,
                        "total_ms": int((time.perf_counter() - start) * 1000),
                    },
                }
                hop.response_payload = payload
                return payload
            resp = await client.ask(req)
            payload = resp.to_dict()
            payload["bridge"] = {
                "target": target_name,
                "protocol": target.protocol,
                "status": target.status,
                "total_ms": int((time.perf_counter() - start) * 1000),
            }
            hop.response_payload = payload
        return payload

    async def _fanout(scenario: str, req: AgentRequest, request: Request) -> dict:
        """Run a fan-out scenario's legs concurrently and return the rendered
        sections (WS8 variant 3, D61). The Agentforce orchestrator's delegated
        topology calls this ONCE; the parallelism it cannot do in serial Apex
        happens here, off-platform. Each leg is its own Hop under the same
        trace_id, exactly as the host-side CMA orchestrator produces — so the
        console groups the Agentforce-orchestrated run the same way.

        Same delegation contract as /invoke: an over-depth request is refused
        with a wire-visible answer rather than allowed to fan out further.
        """
        from orchestration import dispatch, legs_for

        from interop import af_channel

        # WS11: the DELEGATED fan-out can dispatch each leg the blocking way or
        # with the A2A fire-then-poll lifecycle. Agentforce cannot poll — its
        # one Apex callout is what is held open here — so the async loop runs on
        # the orchestrator's behalf right here, bounded by that callout rather
        # than by any API Gateway ceiling (the bridge is a long-lived Fargate
        # service, not a Lambda). The mode rides the situation text because the
        # Apex body carries no field for it; absent → sync, never an error.
        dispatch_mode = af_channel.read_dispatch_mode(req.message)
        # Strip lab routing blocks before the legs see the situation, so a
        # forwarded [A2A-LAB ROUTING] directive never leaks into a unit prompt.
        req.message = af_channel.strip_routing_blocks(req.message)

        inbound_depth = delegation.depth_of(req)
        start = time.perf_counter()
        with Hop(
            req.trace_id,
            source="agentforce-apex" if TOKEN_HEADER in request.headers else "caller",
            target="bridge",
            protocol="rest",
            transport_detail=f"POST /invoke/fanout:{scenario}",
            request_payload={"scenario": scenario, "message": req.message},
        ) as hop:
            try:
                legs_for(scenario)  # validate; raises KeyError for an unknown scenario
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            if not delegation.allowed(req):
                payload = {
                    "text": delegation.refusal("bridge"),
                    "session_id": req.session_id,
                    "delegation_refused": True,
                    "bridge": {"target": f"fanout:{scenario}", "protocol": "fan-out"},
                }
                hop.response_payload = payload
                return payload
            # The bridge is itself a delegating seam here, so the legs inherit
            # depth+1 — dispatch stamps the D27 rider on each leg from this
            # caller identity. caller_platform stays 'agentforce': the run
            # originated on-platform and the legs' logs should attribute it so.
            result = await dispatch(
                req.message,
                caller="agentforce-orchestrator-via-bridge",
                caller_platform="agentforce",
                scenario=scenario,
                trace_id=req.trace_id,
                inbound_depth=inbound_depth + 1,
                dispatch_mode=dispatch_mode,
            )
            payload = {
                # `text` is the field the Apex invocable reads back (askOne
                # parses parsed.get('text')). The orchestrator synthesises from
                # these rendered sections + the coverage line.
                "text": result.render(),
                "session_id": req.session_id,
                "bridge": {
                    "target": f"fanout:{scenario}",
                    "protocol": "fan-out",
                    "coverage": f"{result.ok_count}/{len(result.results)}",
                    # What was REQUESTED (dispatch_mode) vs what actually
                    # happened per leg (dispatch_summary names the async legs
                    # and any that fell back to sync). A stripped block reads as
                    # "sync" and an empty summary, honestly.
                    "dispatch_mode": dispatch_mode,
                    "dispatch": result.dispatch_summary,
                    "total_ms": int((time.perf_counter() - start) * 1000),
                },
            }
            hop.response_payload = payload
        return payload

    return app


def main() -> None:
    import argparse

    import uvicorn
    from dotenv import load_dotenv

    from interop.secret_env import load_secret_env_and_log

    load_dotenv()
    # Hosted (WS7 item 7): credentials live in Secrets Manager, not in the task
    # definition, and are loaded before anything reads os.environ — the
    # registry expands ${VAR} at Registry.load(), so a late load produces
    # empty endpoints that fail as network errors. A no-op locally, where
    # A2ALAB_RUNTIME_SECRET_ARN is unset and .env already holds everything.
    load_secret_env_and_log("bridge")
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    uvicorn.run(create_bridge_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
