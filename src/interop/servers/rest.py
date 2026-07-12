"""REST protocol server: POST /invoke with AgentRequest JSON in, AgentResponse
JSON out. The simplest cell of the matrix and the baseline for comparison."""

from __future__ import annotations

import time
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from interop.adapter import AgentAdapter
from interop.models import AgentRequest, new_trace_id
from interop.trace import TraceEvent, get_recorder

TRACE_HEADER = "x-trace-id"


def create_rest_app(adapter: AgentAdapter) -> FastAPI:
    app = FastAPI(title=f"{adapter.name} (REST)", description=adapter.description)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "agent": adapter.name}

    # Bedrock AgentCore HTTP contract (GET /ping, POST /invocations on :8080)
    # — aliases so the same app deploys unchanged to AgentCore in M8.
    @app.get("/ping")
    async def ping():
        return {"status": "healthy"}

    @app.post("/invoke")
    @app.post("/invocations")
    async def invoke(request: Request):
        body = await request.json()
        req = AgentRequest.from_dict(body)
        req.trace_id = req.trace_id or request.headers.get(TRACE_HEADER) or new_trace_id()

        recorder = get_recorder()
        start = time.perf_counter()
        try:
            resp = await adapter.handle(req)
            status = "ok"
        except Exception as exc:
            # A structured 500: the error type/message travel in the body so
            # the caller's trace hop (and the console) can show WHAT failed,
            # not just that a 500 happened. Full stack still goes to stderr.
            error = f"{type(exc).__name__}: {exc}"
            recorder.record(
                TraceEvent(
                    trace_id=req.trace_id,
                    source="remote-caller",
                    target=adapter.name,
                    protocol="rest",
                    transport_detail="POST /invoke",
                    request_payload_raw=body,
                    response_payload_raw={"error": error, "trace_id": req.trace_id},
                    status="error",
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    hop_seq=recorder.next_hop_seq(req.trace_id),
                )
            )
            traceback.print_exc()
            return JSONResponse(
                status_code=500, content={"error": error, "trace_id": req.trace_id}
            )
        resp.latency_ms = resp.latency_ms or int((time.perf_counter() - start) * 1000)
        payload = resp.to_dict()
        recorder.record(
            TraceEvent(
                trace_id=req.trace_id,
                source="remote-caller",
                target=adapter.name,
                protocol="rest",
                transport_detail="POST /invoke",
                request_payload_raw=body,
                response_payload_raw=payload,
                status=status,
                latency_ms=int((time.perf_counter() - start) * 1000),
                hop_seq=recorder.next_hop_seq(req.trace_id),
            )
        )
        return payload

    return app
