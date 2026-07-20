"""Loopback e2e: serve a deterministic EchoAdapter over all three protocols
and drive each with its RemoteAgentClient. Proves every client×server
pairing (the protocol plumbing) with no LLM and no external platform, and
that trace events with raw payloads land for each hop.
"""

import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from interop.adapter import build_app
from interop.clients.a2a import A2AClient
from interop.clients.mcp import McpClient
from interop.clients.rest import RestClient
from interop.models import AgentRequest, AgentResponse


class EchoAdapter:
    name = "echo"
    description = "Deterministic echo agent for loopback protocol tests."

    async def handle(self, req: AgentRequest) -> AgentResponse:
        text = f"echo: {req.message}"
        # Nested metadata must arrive as plain dicts on every protocol
        # (isinstance guard: a protobuf Struct leaking through would skip
        # this branch and fail the delegation round-trip assertion).
        delegation_meta = req.metadata.get("delegation")
        if isinstance(delegation_meta, dict):
            text += f" [delegated-by {delegation_meta['platform']} depth {int(delegation_meta['depth'])}]"
        return AgentResponse(
            text=text,
            session_id=req.session_id,
            raw={"metadata": req.metadata},
        )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerThread:
    def __init__(self, app, port: int):
        self.config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self.server = uvicorn.Server(self.config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.port = port

    def start(self):
        self.thread.start()
        deadline = time.time() + 15
        while time.time() < deadline:
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError(f"server on :{self.port} did not start")

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)


@pytest.fixture(scope="module")
def echo_servers():
    adapter = EchoAdapter()
    ports = {proto: free_port() for proto in ("rest", "mcp", "a2a")}
    servers = []
    for proto, port in ports.items():
        kwargs = {"public_url": f"http://127.0.0.1:{port}/"} if proto == "a2a" else {}
        servers.append(ServerThread(build_app(adapter, proto, **kwargs), port).start())
    yield ports
    for s in servers:
        s.stop()


def read_trace_events(trace_dir):
    return [
        json.loads(line)
        for f in trace_dir.glob("*.jsonl")
        for line in f.read_text().splitlines()
        if line.strip()
    ]


async def test_rest_loopback(echo_servers, isolated_traces):
    client = RestClient(f"http://127.0.0.1:{echo_servers['rest']}")
    resp = await client.ask(AgentRequest(message="ping", session_id="s-rest", trace_id="t-rest"))
    await client.aclose()
    assert resp.text == "echo: ping"
    assert resp.session_id == "s-rest"
    events = read_trace_events(isolated_traces)
    protos = {e["protocol"] for e in events}
    assert "rest" in protos
    assert all(e["trace_id"] == "t-rest" for e in events)
    # client hop + server hop, correlated by the propagated trace id
    assert len(events) >= 2


async def test_rest_healthz_and_agentcore_aliases(echo_servers):
    base = f"http://127.0.0.1:{echo_servers['rest']}"
    async with httpx.AsyncClient() as hc:
        assert (await hc.get(f"{base}/healthz")).json()["agent"] == "echo"
        assert (await hc.get(f"{base}/ping")).json() == {"status": "healthy"}
        r = await hc.post(f"{base}/invocations", json={"message": "via agentcore alias"})
        assert r.json()["text"] == "echo: via agentcore alias"


async def test_mcp_loopback(echo_servers, isolated_traces):
    client = McpClient(f"http://127.0.0.1:{echo_servers['mcp']}/mcp")
    resp = await client.ask(AgentRequest(message="ping", session_id="s-mcp", trace_id="t-mcp"))
    assert resp.text == "echo: ping"
    assert resp.session_id == "s-mcp"  # round-trips through the JSON tool result
    events = read_trace_events(isolated_traces)
    mcp_events = [e for e in events if e["protocol"] == "mcp"]
    assert mcp_events, f"no mcp trace events in {[e['protocol'] for e in events]}"
    # The server-side wiretap captured the actual JSON-RPC envelope
    server_events = [e for e in mcp_events if e["target"] == "echo"]
    assert any("tools/call" in str(e["request_payload_raw"]) for e in server_events)
    assert any(e["trace_id"] == "t-mcp" for e in server_events)


async def test_a2a_loopback(echo_servers, isolated_traces):
    client = A2AClient(f"http://127.0.0.1:{echo_servers['a2a']}")
    resp = await client.ask(AgentRequest(message="ping", session_id="ctx-42", trace_id="t-a2a"))
    assert resp.text == "echo: ping"
    assert resp.session_id == "ctx-42"  # contextId round-trip
    assert resp.raw["state"] == "TASK_STATE_COMPLETED"
    events = read_trace_events(isolated_traces)
    a2a_events = [e for e in events if e["protocol"] == "a2a"]
    server_events = [e for e in a2a_events if e["target"] == "echo"]
    assert any('"method":"SendMessage"' in str(e["request_payload_raw"]) for e in server_events)
    assert any(e["trace_id"] == "t-a2a" for e in server_events)


async def test_a2a_delegation_metadata_round_trip(echo_servers):
    """metadata["delegation"] rides the A2A message and lands server-side as
    a plain dict — the shim's twin routing (D25/D28) depends on it."""
    client = A2AClient(f"http://127.0.0.1:{echo_servers['a2a']}")
    resp = await client.ask(
        AgentRequest(
            message="ping",
            metadata={"delegation": {"caller": "adk-gemini-agent", "platform": "adk", "depth": 1}},
        )
    )
    assert resp.text == "echo: ping [delegated-by adk depth 1]"


async def test_a2a_agent_card_published(echo_servers):
    async with httpx.AsyncClient() as hc:
        r = await hc.get(f"http://127.0.0.1:{echo_servers['a2a']}/.well-known/agent-card.json")
        assert r.status_code == 200
        card = r.json()
        assert card["name"] == "echo"
        skills = card.get("skills", [])
        assert skills and skills[0]["id"] == "ask"


async def test_concurrent_protocols(echo_servers):
    """All three protocol servers answer concurrently."""
    rest = RestClient(f"http://127.0.0.1:{echo_servers['rest']}")
    mcp = McpClient(f"http://127.0.0.1:{echo_servers['mcp']}/mcp")
    a2a = A2AClient(f"http://127.0.0.1:{echo_servers['a2a']}")
    try:
        results = await asyncio.gather(
            rest.ask(AgentRequest(message="r")),
            mcp.ask(AgentRequest(message="m")),
            a2a.ask(AgentRequest(message="a")),
        )
    finally:
        await rest.aclose()
    assert [r.text for r in results] == ["echo: r", "echo: m", "echo: a"]
