"""A2A 0.3-dialect compatibility for the lab's 1.x JSON-RPC servers.

The A2A version spectrum, measured at the wire (WS2/WS3): Vertex AI Agent
Engine rejects requests without ``a2a-version: 1.0``, while Microsoft
Foundry's A2A tool speaks the 0.3-era dialect — JSON-RPC method
``message/send``, ``kind``-discriminated parts, lowercase task states —
and the a2a-sdk 1.x server it calls registers ``SendMessage`` with
proto-JSON shapes, answering ``-32601 Method not found``. Neither side
negotiates down. This middleware makes every lab A2A server bilingual:
0.3-shaped requests are translated to the 1.x envelope on the way in and
the completed Task is translated back to 0.3 shape on the way out. 1.x
traffic passes through untouched. Pure ASGI with single-shot body replay,
so it works under Mangum (the hosted shim) as well as uvicorn.

Served alongside the card's 0.3-era compatibility fields
(url/protocolVersion/preferredTransport — see servers/a2a.py); together
they are what "supports 0.3 clients" actually takes.
"""

from __future__ import annotations

import json
from typing import Any

_STATE_TO_03 = {
    "TASK_STATE_SUBMITTED": "submitted",
    "TASK_STATE_WORKING": "working",
    "TASK_STATE_INPUT_REQUIRED": "input-required",
    "TASK_STATE_COMPLETED": "completed",
    "TASK_STATE_CANCELLED": "canceled",
    "TASK_STATE_FAILED": "failed",
    "TASK_STATE_REJECTED": "rejected",
    "TASK_STATE_AUTH_REQUIRED": "auth-required",
}
_ROLE_TO_1X = {"user": "ROLE_USER", "agent": "ROLE_AGENT"}
_ROLE_TO_03 = {v: k for k, v in _ROLE_TO_1X.items()}


def _part_to_1x(part: dict) -> dict:
    if part.get("kind") == "text" or "text" in part:
        return {"text": part.get("text", "")}
    return {k: v for k, v in part.items() if k != "kind"}


def _part_to_03(part: dict) -> dict:
    if "text" in part:
        return {"kind": "text", "text": part["text"]}
    return part


def _message_to_1x(message: dict) -> dict:
    out: dict[str, Any] = {
        "messageId": message.get("messageId", ""),
        "role": _ROLE_TO_1X.get(message.get("role", "user"), "ROLE_USER"),
        "parts": [_part_to_1x(p) for p in message.get("parts", [])],
    }
    for key in ("contextId", "taskId", "metadata"):
        if message.get(key):
            out[key] = message[key]
    return out


def _message_to_03(message: dict) -> dict:
    out = dict(message)
    out["kind"] = "message"
    out["role"] = _ROLE_TO_03.get(message.get("role", ""), "user")
    out["parts"] = [_part_to_03(p) for p in message.get("parts", [])]
    return out


def translate_03_request(payload: dict) -> dict | None:
    """A 0.3 ``message/send`` JSON-RPC payload → the 1.x ``SendMessage``
    envelope, or None when the payload is not the 0.3 dialect."""
    if payload.get("method") != "message/send":
        return None
    params = payload.get("params") or {}
    message = params.get("message") or {}
    return {
        "jsonrpc": "2.0",
        "id": payload.get("id"),
        "method": "SendMessage",
        # Only known keys cross: proto-JSON parsing rejects unknown fields.
        "params": {"message": _message_to_1x(message), "configuration": {}},
    }


def translate_1x_response(payload: dict) -> dict:
    """A 1.x JSON-RPC response → the 0.3 shape (result is the Task object
    itself, ``kind``-discriminated, lowercase states). Errors and shapes we
    don't recognize pass through unchanged."""
    result = payload.get("result")
    if not isinstance(result, dict):
        return payload
    task = result.get("task")
    if not isinstance(task, dict):
        return payload
    out_task: dict[str, Any] = {
        "id": task.get("id", ""),
        "contextId": task.get("contextId", ""),
        "kind": "task",
        "status": {
            "state": _STATE_TO_03.get((task.get("status") or {}).get("state", ""), "completed"),
        },
    }
    if (task.get("status") or {}).get("timestamp"):
        out_task["status"]["timestamp"] = task["status"]["timestamp"]
    if (task.get("status") or {}).get("message"):
        out_task["status"]["message"] = _message_to_03(task["status"]["message"])
    if task.get("artifacts"):
        out_task["artifacts"] = [
            {**a, "parts": [_part_to_03(p) for p in a.get("parts", [])]} for a in task["artifacts"]
        ]
    if task.get("history"):
        out_task["history"] = [_message_to_03(m) for m in task["history"]]
    return {"jsonrpc": "2.0", "id": payload.get("id"), "result": out_task}


class A2A03CompatMiddleware:
    """Detects 0.3-dialect JSON-RPC POSTs, rewrites them to 1.x for the
    wrapped app, and rewrites the response back. Everything else passes
    through byte-for-byte."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        while True:
            event = await receive()
            if event["type"] != "http.request":
                await self.app(scope, _replay(b"".join(chunks)), send)
                return
            chunks.append(event.get("body", b""))
            if not event.get("more_body"):
                break
        body = b"".join(chunks)

        translated: bytes | None = None
        try:
            payload = json.loads(body)
            rewritten = translate_03_request(payload) if isinstance(payload, dict) else None
            if rewritten is not None:
                translated = json.dumps(rewritten).encode()
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        if translated is None:
            await self.app(scope, _replay(body), send)
            return

        new_scope = dict(scope)
        new_scope["headers"] = [
            (k, v)
            for k, v in scope.get("headers", [])
            if k.lower() not in (b"content-length", b"a2a-version")
        ] + [
            (b"content-length", str(len(translated)).encode()),
            # The 1.x server validates the version header the same way
            # Agent Engine does — a 0.3 client won't have sent it.
            (b"a2a-version", b"1.0"),
        ]

        response_start: dict | None = None
        response_chunks: list[bytes] = []

        async def capture(event):
            nonlocal response_start
            if event["type"] == "http.response.start":
                response_start = event
            elif event["type"] == "http.response.body":
                response_chunks.append(event.get("body", b""))
                if not event.get("more_body"):
                    await _flush()

        async def _flush():
            raw = b"".join(response_chunks)
            out = raw
            try:
                out = json.dumps(translate_1x_response(json.loads(raw))).encode()
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            start = dict(response_start or {"type": "http.response.start", "status": 500})
            start["headers"] = [
                (k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"
            ] + [(b"content-length", str(len(out)).encode())]
            await send(start)
            await send({"type": "http.response.body", "body": out, "more_body": False})

        await self.app(new_scope, _replay(translated), capture)


def _replay(body: bytes):
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive
