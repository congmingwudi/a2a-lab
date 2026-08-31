"""WS10 SP1: the A2A wiretap attributes an inbound hop to the verified lab
caller when the auth middleware stashed one on the ASGI scope."""

import asyncio
import json

from interop.servers.wiretap import WireTapMiddleware

_ENVELOPE = json.dumps(
    {
        "jsonrpc": "2.0",
        "method": "message/send",
        "params": {
            "message": {
                "metadata": {"trace_id": "t-attr"},
                "parts": [{"text": "hello"}],
            }
        },
    }
).encode()


async def _ok_app(scope, receive, send):
    while True:
        m = await receive()
        if not m.get("more_body"):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"{}"})


def _drive(scope):
    async def receive():
        return {"type": "http.request", "body": _ENVELOPE, "more_body": False}

    async def send(_m):
        pass

    mw = WireTapMiddleware(_ok_app, protocol="a2a", service="claude-a2a")
    asyncio.run(mw(scope, receive, send))


def _recorded_source(isolated_traces, trace_id):
    events = [
        json.loads(line)
        for f in isolated_traces.glob("*.jsonl")
        for line in f.read_text().splitlines()
    ]
    return [e for e in events if e["trace_id"] == trace_id][0]["source"]


def test_verified_lab_user_becomes_trace_source(isolated_traces):
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [],
        "state": {"lab_user": {"sub": "mulesoft-omni-gateway", "role": "machine"}},
    }
    _drive(scope)
    assert _recorded_source(isolated_traces, "t-attr") == "mulesoft-omni-gateway"


def test_no_lab_user_falls_back_to_remote_caller(isolated_traces):
    scope = {"type": "http", "method": "POST", "path": "/", "headers": []}
    _drive(scope)
    # No verified caller and no delegation rider in the body → unchanged behaviour.
    assert _recorded_source(isolated_traces, "t-attr") == "remote-caller"
