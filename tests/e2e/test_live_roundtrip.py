"""Live cross-platform round-trip tests — the real thing, no mocks.

These hit the actual Anthropic Managed Agents API and the real Salesforce
org, so they are marked `live` and deselected by default:

    set -a; source .env; set +a
    uv run pytest -m live tests/e2e/test_live_roundtrip.py -v

Each test asserts on BOTH the answer and the wire trace (the isolated trace
dir from conftest), because the trace is the proof that the hop actually
crossed platforms rather than the model answering from its own knowledge.

- Path B:   Claude agent -> ask_agentforce -> Agentforce Agent API
- Path A:   caller (Apex-equivalent) -> bridge -> Claude REST server
- Full loop: caller -> bridge -> Claude -> Agentforce -> back, one trace
"""

from __future__ import annotations

import json
import os
import socket
import threading

import pytest
import uvicorn
from fastapi.testclient import TestClient

from bridge.app import create_bridge_app
from interop.adapter import build_app
from interop.models import AgentRequest
from interop.registry import Registry, Target

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY"
    ),
]

needs_salesforce = pytest.mark.skipif(
    not os.environ.get("SF_CLIENT_ID"), reason="needs SF_* credentials"
)

CONSULT_AGENTFORCE = (
    "Use your ask_agentforce tool to ask the Salesforce Agentforce agent what "
    "it can help with. Then answer with one sentence that summarizes its reply."
)


def read_trace(trace_dir, trace_id):
    events = []
    for f in trace_dir.glob("*.jsonl"):
        for line in f.read_bytes().split(b"\n"):
            if line.strip():
                ev = json.loads(line)
                if ev["trace_id"] == trace_id:
                    events.append(ev)
    return events


class LiveServer:
    """Run an ASGI app on an ephemeral local port in a background thread."""

    def __init__(self, app):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self):
        self._thread.start()
        import time

        for _ in range(100):
            if self._server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("server did not start")

    def __exit__(self, *exc):
        self._server.should_exit = True
        self._thread.join(timeout=5)


def apex_headers(trace_id: str) -> dict[str, str]:
    """Exactly what A2ALabInvokeRemoteAgent sends: trace id + bridge token."""
    headers = {"x-trace-id": trace_id}
    if os.environ.get("BRIDGE_TOKEN"):
        headers["x-bridge-token"] = os.environ["BRIDGE_TOKEN"]
    return headers


def claude_registry(port: int) -> Registry:
    token = os.environ.get("A2ALAB_TOKEN", "")
    return Registry(
        {
            "claude-rest": Target(
                name="claude-rest",
                platform="claude",
                protocol="rest",
                endpoint=f"http://127.0.0.1:{port}",
                status="native",
                auth={"header_name": "x-lab-token", "header_value": token},
            )
        }
    )


@needs_salesforce
async def test_path_b_claude_consults_agentforce(isolated_traces):
    """Claude (Managed Agents) -> ask_agentforce -> real Agent API round trip."""
    from platforms.claude.core import make_adapter

    adapter = make_adapter("managed")
    resp = await adapter.handle(
        AgentRequest(message=CONSULT_AGENTFORCE, trace_id="live-path-b")
    )

    assert resp.text.strip(), "Claude returned an empty answer"
    hops = read_trace(isolated_traces, "live-path-b")
    protocols = {h["protocol"] for h in hops}
    assert "managed-agents-api" in protocols, "no Claude reasoning hop recorded"
    assert "agentforce-api" in protocols, (
        "Claude never called Agentforce — the round trip did not cross platforms"
    )
    af_details = " ".join(
        h["transport_detail"] for h in hops if h["protocol"] == "agentforce-api"
    )
    # Full Agent API lifecycle on the same trace: create -> message -> delete.
    assert "/sessions" in af_details and "/messages" in af_details
    assert "DELETE" in af_details, "one-shot Agentforce session was not cleaned up"


def test_path_a_bridge_to_claude(isolated_traces):
    """Apex-equivalent caller -> bridge -> Claude REST server (real reasoning).

    This is Path A minus the Salesforce-internal leg (Apex deploy lands in M3
    with the tunnel); the caller sends exactly what A2ALabInvokeRemoteAgent
    sends.
    """
    from platforms.claude.core import make_adapter

    claude_app = build_app(make_adapter("managed"), "rest")
    with LiveServer(claude_app) as server:
        bridge = TestClient(create_bridge_app(claude_registry(server.port)))
        r = bridge.post(
            "/invoke/claude-rest",
            json={"message": "In one sentence: what is an A2A Agent Card?"},
            headers=apex_headers("live-path-a"),
        )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["text"].strip()
    assert data["bridge"]["target"] == "claude-rest"

    hops = read_trace(isolated_traces, "live-path-a")
    routes = {(h["source"], h["target"]) for h in hops}
    # Source label depends on whether the caller presented the bridge token
    # (with it, the bridge attributes the hop to the Apex action).
    assert routes & {("caller", "bridge"), ("agentforce-apex", "bridge")}, (
        "bridge inbound hop missing"
    )
    assert ("client", "claude-rest") in routes, "bridge outbound hop missing"
    assert ("remote-caller", "claude-researcher") in routes, "claude server hop missing"
    bridge_hop = next(h for h in hops if h["target"] == "bridge")
    assert bridge_hop["status"] == "ok" and bridge_hop["latency_ms"] is not None


@needs_salesforce
def test_full_bidirectional_loop(isolated_traces):
    """The showpiece: one trace crossing every system in both directions —
    caller -> bridge -> Claude -> Agentforce -> back up the stack."""
    from platforms.claude.core import make_adapter

    claude_app = build_app(make_adapter("managed"), "rest")
    with LiveServer(claude_app) as server:
        bridge = TestClient(create_bridge_app(claude_registry(server.port)))
        r = bridge.post(
            "/invoke/claude-rest",
            json={"message": CONSULT_AGENTFORCE},
            headers=apex_headers("live-full-loop"),
        )
    assert r.status_code == 200, r.text
    assert r.json()["text"].strip()

    hops = read_trace(isolated_traces, "live-full-loop")
    protocols = {h["protocol"] for h in hops}
    assert {"rest", "managed-agents-api", "agentforce-api"} <= protocols, (
        f"loop did not cross all systems; saw only {sorted(protocols)}"
    )
    participants = {h["source"] for h in hops} | {h["target"] for h in hops}
    assert {"bridge", "claude-researcher", "agentforce"} <= participants
