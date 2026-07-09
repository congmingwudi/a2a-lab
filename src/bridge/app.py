"""Path A bridge: Agentforce's GA outbound is a REST callout, so the Apex
action always POSTs here; the bridge fans out to the target agent over
whatever protocol the registry says. Switching Path A from REST to MCP to
A2A is a targets.yaml edit — no Salesforce redeploy.

    uv run python -m bridge --port 8100

Auth: the Named/External Credential in Salesforce sends X-Bridge-Token; we
compare against BRIDGE_TOKEN (skipped when unset, for local dev).
"""

from __future__ import annotations

import os
import time

from fastapi import FastAPI, HTTPException, Request

from interop.models import AgentRequest, new_trace_id
from interop.registry import Registry
from interop.trace import TraceEvent, get_recorder

TRACE_HEADER = "x-trace-id"
TOKEN_HEADER = "x-bridge-token"


def create_bridge_app(registry: Registry | None = None) -> FastAPI:
    app = FastAPI(title="A2A lab bridge")
    state = {"registry": registry}

    def get_registry() -> Registry:
        if state["registry"] is None:
            state["registry"] = Registry.load()
        return state["registry"]

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

        recorder = get_recorder()
        start = time.perf_counter()
        recorder.record(
            TraceEvent(
                trace_id=req.trace_id,
                source="agentforce-apex" if TOKEN_HEADER in request.headers else "caller",
                target="bridge",
                protocol="rest",
                transport_detail=f"POST /invoke/{target_name}",
                request_payload_raw=body,
                status="pending",
                hop_seq=recorder.next_hop_seq(req.trace_id),
            )
        )

        client = registry.client_for(target_name)
        try:
            resp = await client.ask(req)
        finally:
            await client.aclose()

        payload = resp.to_dict()
        payload["bridge"] = {
            "target": target_name,
            "protocol": target.protocol,
            "status": target.status,
            "total_ms": int((time.perf_counter() - start) * 1000),
        }
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
