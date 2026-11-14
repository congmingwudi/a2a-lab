import json

import httpx
import pytest

from interop import identity
from interop.memory import StaleMemoryGeneration
from interop.models import AgentRequest
from interop.registry import Target
from platforms.agentforce.client import AgentforceClient


class FakeAgentAPI:
    """httpx.MockTransport handler simulating OAuth + the Agent API."""

    def __init__(self):
        self.token_calls = 0
        self.session_calls = 0
        self.messages: list[dict] = []
        self.deleted: list[str] = []
        self.session_bodies: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/services/oauth2/token"):
            self.token_calls += 1
            return httpx.Response(200, json={"access_token": f"tok-{self.token_calls}"})
        if path.endswith("/sessions") and "/agents/" in path:
            self.session_calls += 1
            self.session_bodies.append(json.loads(request.content))
            assert request.headers["authorization"].startswith("Bearer tok-")
            return httpx.Response(200, json={"sessionId": f"sf-sess-{self.session_calls}"})
        if "/sessions/" in path and path.endswith("/messages"):
            body = json.loads(request.content)
            self.messages.append({"path": path, "body": body})
            return httpx.Response(
                200,
                json={"messages": [{"type": "Inform", "message": f"answer #{len(self.messages)}"}]},
            )
        if request.method == "DELETE" and "/sessions/" in path:
            self.deleted.append(path.rsplit("/", 1)[-1])
            return httpx.Response(204)
        return httpx.Response(404, text=f"unexpected {request.method} {path}")


@pytest.fixture
def fake_api():
    return FakeAgentAPI()


@pytest.fixture
def client(fake_api):
    c = AgentforceClient(
        my_domain="test.my.salesforce.com",
        client_id="cid",
        client_secret="csecret",
        agent_id="0XxTEST",
    )
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(fake_api.handler))
    return c


async def test_round_trip(client, fake_api):
    resp = await client.ask(AgentRequest(message="hello", trace_id="t1"))
    assert resp.text == "answer #1"
    assert resp.raw["messages"][0]["type"] == "Inform"
    assert fake_api.messages[0]["body"]["message"]["text"] == "hello"
    assert fake_api.messages[0]["body"]["message"]["sequenceId"] == 1


async def test_session_variables_sent_only_when_set(client, fake_api):
    """An agent reading an External variable (the memory concierge's memory_owner)
    gets it at session create; a client without variables sends no key at all."""
    await client.ask(AgentRequest(message="hi", trace_id="t1"))
    assert "variables" not in fake_api.session_bodies[0]
    client.session_variables = [{"name": "memory_owner", "type": "Text", "value": "a2alab-x"}]
    await client.ask(AgentRequest(message="hi", trace_id="t2"))
    assert fake_api.session_bodies[1]["variables"] == client.session_variables


async def test_token_provider_runs_as_that_user(client, fake_api):
    """An Employee agent runs as the token's user (D95): the client takes that
    user's token from the provider, never the client-credentials grant, and
    starts the session without bypassUser."""
    await client.ask(AgentRequest(message="hi", trace_id="t1"))
    assert fake_api.session_bodies[0]["bypassUser"] is True
    client.token_provider = lambda: {"access_token": "tok-employee", "expires_in": 7200}
    client.bypass_user = False
    client._token = None
    await client.ask(AgentRequest(message="hi", trace_id="t2"))
    assert fake_api.token_calls == 1  # only the first, client-credentials, ask
    assert fake_api.session_bodies[1]["bypassUser"] is False


async def test_session_reuse_and_sequence(client, fake_api):
    await client.ask(AgentRequest(message="q1", session_id="lab-1"))
    await client.ask(AgentRequest(message="q2", session_id="lab-1"))
    assert fake_api.session_calls == 1  # reused
    assert fake_api.messages[1]["body"]["message"]["sequenceId"] == 2
    assert "sf-sess-1" in fake_api.messages[1]["path"]


async def test_oneshot_sessions_not_reused_and_deleted(client, fake_api):
    await client.ask(AgentRequest(message="q1"))
    await client.ask(AgentRequest(message="q2"))
    assert fake_api.session_calls == 2
    # One-shot sessions must not accumulate on the org: create -> message -> DELETE.
    assert fake_api.deleted == ["sf-sess-1", "sf-sess-2"]


async def test_token_cached(client, fake_api):
    await client.ask(AgentRequest(message="q1", session_id="s"))
    await client.ask(AgentRequest(message="q2", session_id="s"))
    assert fake_api.token_calls == 1


async def test_concurrent_ensure_session_creates_exactly_one(client):
    """A11/F02: two coroutines racing ensure_session on ONE lab session_id must
    create exactly one Agentforce session, not one per racer. The pre-fix
    check-then-create had an await between the check and the store, so both saw
    'absent' and each started a session. `start_session` is stubbed to sleep so
    the interleave is deterministic, not transport-timing-dependent."""
    import asyncio

    calls = 0

    async def slow_start(trace_id):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)  # force both coroutines past the check together
        return f"sf-{calls}"

    client.start_session = slow_start
    await asyncio.gather(
        client.ensure_session("lab-race", "t"),
        client.ensure_session("lab-race", "t"),
    )
    assert calls == 1


async def test_end_session(client, fake_api):
    await client.ask(AgentRequest(message="q1", session_id="lab-9"))
    await client.end_session("lab-9")
    assert fake_api.deleted == ["sf-sess-1"]


async def test_aclose_ends_cached_sessions(client, fake_api):
    await client.ask(AgentRequest(message="q1", session_id="lab-1"))
    await client.ask(AgentRequest(message="q2", session_id="lab-2"))
    await client.aclose()
    assert sorted(fake_api.deleted) == ["sf-sess-1", "sf-sess-2"]


async def test_traces_recorded(client, fake_api, isolated_traces):
    await client.ask(AgentRequest(message="q", trace_id="trace-x"))
    lines = [
        json.loads(line)
        for f in isolated_traces.glob("*.jsonl")
        for line in f.read_text().splitlines()
    ]
    protocols = {e["protocol"] for e in lines}
    assert protocols == {"agentforce-api"}
    assert all(e["trace_id"] == "trace-x" for e in lines)
    # oneshot: session create + message + delete = 3 hops
    assert len(lines) == 3


def test_from_env_missing(monkeypatch):
    for var in ("SF_MY_DOMAIN", "SF_CLIENT_ID", "SF_CLIENT_SECRET", "SF_AGENT_ID"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError, match="missing env var"):
        AgentforceClient.from_env()


@pytest.fixture
def memory_client(fake_api, tmp_path, monkeypatch):
    monkeypatch.setenv(identity.KEY_DIR_ENV, str(tmp_path / "keys"))
    monkeypatch.setenv(
        "A2ALAB_MEMORY_CUSTOMER_MAP",
        '{"maya":"003000000000001AAA","jordan":"003000000000002AAA"}',
    )
    c = AgentforceClient(
        my_domain="test.my.salesforce.com",
        client_id="cid",
        client_secret="csecret",
        agent_id="0XxMEMORY",
        memory_customer_binding=True,
    )
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(fake_api.handler))
    return c


def _memory_request(customer="maya", generation="v1:2026-11-13T00:00:00Z:0123456789abcdef01234567"):
    users = {"ana": {"name": "Ana", "role": "operator"}}
    return AgentRequest(
        message="What do you remember?",
        trace_id="trace-memory",
        session_id="lab-memory",
        metadata={
            "user_token": identity.issue_token("ana", users=users),
            "memory_customer": customer,
            "memory_generation": generation,
        },
    )


async def test_memory_target_binds_owner_generation_and_correlation_at_session_create(
    memory_client, fake_api
):
    resp = await memory_client.ask(_memory_request())
    variables = {item["name"]: item["value"] for item in fake_api.session_bodies[0]["variables"]}
    assert variables == {
        "memory_owner": "003000000000001AAA",
        "memory_generation": "v1:2026-11-13T00:00:00Z:0123456789abcdef01234567",
        "memory_correlation": "lab-memory",
    }
    assert resp.metadata["memory"]["customer_key"] == "maya"
    assert resp.metadata["memory"]["generation"].startswith("v1:")
    assert resp.metadata["memory"]["evidence_pending"] is True
    assert resp.metadata["memory"]["correlation_id"] == "lab-memory"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda req: req.metadata.pop("user_token"),
        lambda req: req.metadata.pop("memory_customer"),
        lambda req: req.metadata.update(memory_customer="unknown"),
    ],
)
async def test_memory_target_refuses_unverified_or_unmapped_requests_before_agent_api(
    memory_client, fake_api, mutate
):
    req = _memory_request()
    mutate(req)
    with pytest.raises(RuntimeError, match="customer memory"):
        await memory_client.ask(req)
    assert fake_api.session_calls == 0
    assert fake_api.messages == []


async def test_memory_target_refuses_owner_switch_before_message(memory_client, fake_api):
    await memory_client.ask(_memory_request("maya"))
    with pytest.raises(RuntimeError, match="different customer"):
        await memory_client.ask(_memory_request("jordan"))
    assert len(fake_api.messages) == 1


async def test_memory_target_ends_stale_generation_before_message(memory_client, fake_api):
    await memory_client.ask(_memory_request())
    req = _memory_request(generation="v1:2026-11-13T00:01:00Z:abcdef0123456789abcdef01")
    with pytest.raises(StaleMemoryGeneration) as exc:
        await memory_client.ask(req)
    assert exc.value.error_code == "stale_memory_generation"
    assert fake_api.deleted == ["sf-sess-1"]
    assert len(fake_api.messages) == 1


def test_from_target_rejects_empty_agent_id_and_enables_memory_binding():
    target = Target(
        name="agentforce-memory-rest",
        platform="agentforce",
        protocol="agentforce-api",
        auth={
            "my_domain": "example.my.salesforce.com",
            "client_id": "cid",
            "client_secret": "secret",
            "agent_id": "",
        },
        options={"memory_customer_binding": True},
    )
    with pytest.raises(RuntimeError, match="agent_id"):
        AgentforceClient.from_target(target)
