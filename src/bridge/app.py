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

    @app.post("/invoke/{target_name}")
    async def invoke(target_name: str, request: Request):
        check_auth(request)
        body = await request.json()
        req = AgentRequest.from_dict(body)
        req.trace_id = req.trace_id or request.headers.get(TRACE_HEADER) or new_trace_id()

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
            req.message, meta = delegation.delegate(
                req.message,
                caller="agentforce-twin-via-bridge",
                platform="agentforce",
                inbound_depth=inbound_depth,
            )
            req.metadata = {**(req.metadata or {}), **meta}
            resp = await get_client(target_name).ask(req)
            payload = resp.to_dict()
            payload["bridge"] = {
                "target": target_name,
                "protocol": target.protocol,
                "status": target.status,
                "total_ms": int((time.perf_counter() - start) * 1000),
            }
            hop.response_payload = payload
        return payload

    return app


def main() -> None:
    import argparse

    import uvicorn
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    uvicorn.run(create_bridge_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
