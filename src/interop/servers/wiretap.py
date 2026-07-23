"""ASGI middleware that records raw wire bytes for framework-managed
protocols (MCP's and A2A's JSON-RPC envelopes travel inside the framework,
so a handler-level hook can't see them — this middleware can).

It tees the request body and the response body (bounded), digs the trace_id
out of the envelope when present (MCP: params.arguments.trace_id;
A2A: message.metadata.trace_id), and records one TraceEvent per exchange
with the actual bytes on the wire.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from interop.models import new_trace_id
from interop.trace import TraceEvent, get_recorder

_MAX_CAPTURE = 200_000
_RIDER_PLATFORM_RE = re.compile(r"caller-platform:\s*([\w.-]+)")


def _extract_caller(body: bytes) -> str | None:
    """Name the hop's source from the delegation context when the envelope
    carries it (metadata.delegation.platform, else the rider text in a
    message part) — inbound envelopes at the shim then read
    foundry→shim / adk→shim instead of the anonymous remote-caller."""
    try:
        envelope = json.loads(body)
    except Exception:
        return None
    params = envelope.get("params") if isinstance(envelope, dict) else None
    message = params.get("message") if isinstance(params, dict) else None
    if not isinstance(message, dict):
        return None
    meta = message.get("metadata")
    if isinstance(meta, dict):
        delegation = meta.get("delegation")
        if isinstance(delegation, dict) and delegation.get("platform"):
            return str(delegation["platform"])
    for part in message.get("parts") or []:
        text = part.get("text") if isinstance(part, dict) else None
        if text and "[A2A-LAB DELEGATION]" in text:
            match = _RIDER_PLATFORM_RE.search(text)
            if match:
                return match.group(1)
    return None


def _extract_trace_id(body: bytes) -> str | None:
    try:
        envelope = json.loads(body)
    except Exception:
        return None
    if not isinstance(envelope, dict):
        return None
    params = envelope.get("params")
    if isinstance(params, dict):
        # MCP tools/call: params.arguments.trace_id
        args = params.get("arguments")
        if isinstance(args, dict) and args.get("trace_id"):
            return str(args["trace_id"])
        # A2A message/send: params.message.metadata.trace_id (JSON mapping)
        msg = params.get("message")
        if isinstance(msg, dict):
            meta = msg.get("metadata")
            if isinstance(meta, dict) and meta.get("trace_id"):
                return str(meta["trace_id"])
    return None


def _decode(raw: bytes) -> Any:
    text = raw[:_MAX_CAPTURE].decode("utf-8", errors="replace")
    return text


class WireTapMiddleware:
    """Pure ASGI middleware (works under Starlette and FastAPI)."""

    def __init__(self, app, *, protocol: str, service: str):
        self.app = app
        self.protocol = protocol
        self.service = service

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")
        resp_chunks: list[bytes] = []
        resp_status: dict[str, Any] = {}
        start = time.perf_counter()

        # Buffer the request body up front, then replay it once to the
        # inner app. Passively teeing receive() hangs under Mangum (its
        # single-shot receive never yields again when the framework polls
        # for disconnect) — buffer-and-replay works under both Mangum and
        # uvicorn (same technique as A2A03CompatMiddleware), which is what
        # lets the hosted shim run the wiretap. Lab payloads are small
        # JSON envelopes; buffering them whole is fine.
        req_chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            req_chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        buffered = b"".join(req_chunks)
        replayed = False

        async def replay_receive():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": buffered, "more_body": False}
            # After the replay: under Mangum (aws.event in scope) the real
            # channel never yields again — fabricate the disconnect (no
            # streaming exists on Lambda). Under uvicorn, delegate to the
            # real channel: SSE servers (MCP streamable-http) long-poll it
            # for client disconnect, and a fabricated instant disconnect
            # kills their stream mid-response.
            if "aws.event" in scope:
                return {"type": "http.disconnect"}
            return await receive()

        async def tee_send(message):
            if message["type"] == "http.response.start":
                resp_status["status"] = message["status"]
            elif message["type"] == "http.response.body":
                if sum(len(c) for c in resp_chunks) < _MAX_CAPTURE:
                    resp_chunks.append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, replay_receive, tee_send)
        finally:
            body = b"".join(req_chunks)
            # Only record exchanges that carry a payload (skips GETs for
            # agent cards? No — those are interesting too when POST-less).
            if method == "POST" or body:
                trace_id = _extract_trace_id(body) or new_trace_id()
                recorder = get_recorder()
                status_code = resp_status.get("status", 0)
                recorder.record(
                    TraceEvent(
                        trace_id=trace_id,
                        source=_extract_caller(body) or "remote-caller",
                        target=self.service,
                        protocol=self.protocol,
                        transport_detail=f"{method} {path}",
                        request_payload_raw=_decode(body),
                        response_payload_raw=_decode(b"".join(resp_chunks)),
                        status="ok" if 200 <= status_code < 300 else "error",
                        latency_ms=int((time.perf_counter() - start) * 1000),
                        hop_seq=recorder.next_hop_seq(trace_id),
                    )
                )
