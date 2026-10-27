"""interop.memory (plan/18, D83) against a fake core org + Data 360 tenant, and the
bridge's /memory/turn route that Agentforce's before_reasoning hook calls."""

import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from bridge.app import create_bridge_app
from interop import memory as memory_mod
from interop.memory import MemoryCallError, MemoryClient, entry_id, render_context
from interop.registry import Registry

CORE = "https://core.example"
TENANT = "https://tenant.example"


@pytest.fixture(autouse=True)
def memory_env(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    monkeypatch.setenv(memory_mod.KEY_ENV, pem)
    monkeypatch.setenv("SF_CLIENT_ID_MEMORY", "ck")
    monkeypatch.setenv("SF_MEMORY_RUNAS_USER", "runas@example")
    monkeypatch.setenv("SF_MY_DOMAIN", CORE)


class FakeTenant:
    def __init__(self, context: dict | None = None, context_status: int = 200):
        self.calls: list[httpx.Request] = []
        self.exchanges: list[dict] = []
        self.context = context if context is not None else {"summary": "likes aisle seats"}
        self.context_status = context_status
        self.conversation_status = 201
        self.memories: dict[tuple[str, str], list[str]] = {}
        self.refuse_memory: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path == "/services/oauth2/token":
            return httpx.Response(200, json={"access_token": "core", "instance_url": CORE})
        if path == "/services/a360/token":
            self.exchanges.append(dict(httpx.QueryParams(request.content.decode())))
            return httpx.Response(
                200, json={"access_token": "cdp", "instance_url": "tenant.example",
                           "expires_in": 3600}
            )  # fmt: skip
        assert request.headers["authorization"] == "Bearer cdp"
        if path == "/api/v1/agentic/context":
            return httpx.Response(self.context_status, json=self.context)
        if path == "/api/v1/agentic/conversations":
            return httpx.Response(self.conversation_status, json={})
        if path == "/api/v1/agentic/memory":
            key = (request.headers["agent_id"], request.url.params["category"])
            if request.headers["agent_id"] in self.refuse_memory:
                return httpx.Response(500, json={"error": "Failed to load agent configuration"})
            contents = self.memories.get(key, [])
            return httpx.Response(200, json={"memories": [{"content": c} for c in contents]})
        return httpx.Response(202, json={})

    def client(self) -> MemoryClient:
        return MemoryClient(httpx.AsyncClient(transport=httpx.MockTransport(self.handler)))

    def bodies(self, suffix: str) -> list[dict]:
        return [json.loads(r.content) for r in self.calls if r.url.path.endswith(suffix)]


IDS = {"agent_id": "A2ALab_Memory_Concierge", "owner_id": "persona-ana"}


async def test_start_conversation_runs_the_sdk_lifecycle():
    fake = FakeTenant()
    cid = await fake.client().start_conversation("t1", **IDS)
    paths = [r.url.path for r in fake.calls if r.url.host == "tenant.example"]
    assert paths == [
        "/api/v1/agentic/conversations",
        f"/api/v1/agentic/conversations/{cid}/sessions",
        f"/api/v1/agentic/conversations/{cid}/sessions/{cid}/participants",
    ]
    session = fake.bodies("/sessions")[0]
    assert session["sessionId"] == cid  # one id for both, as the SDK does
    assert session["participants"][0]["id"] == "persona-ana-participant"
    agent = fake.bodies("/participants")[0]["participants"][0]
    assert agent == {**agent, "id": "A2ALab_Memory_Concierge-participant", "type": "agent"}
    post = next(r for r in fake.calls if r.url.path == "/api/v1/agentic/conversations")
    assert post.headers["dataspace"] == "default"
    assert post.headers["agent_id"] == "A2ALab_Memory_Concierge"
    # the exchange is bound to the calling agent: a360 refuses an unregistered aid
    assert fake.exchanges[0]["aid"] == "A2ALab_Memory_Concierge"


async def test_existing_conversation_409_is_success():
    fake = FakeTenant()
    fake.conversation_status = 409
    assert await fake.client().start_conversation("t1", conversation_id="c-1", **IDS) == "c-1"


async def test_ingest_sends_only_the_sides_it_has_and_caches_the_token():
    fake = FakeTenant()
    mem = fake.client()
    await mem.ingest("t1", conversation_id="c-1", user_message="I prefer aisle seats", **IDS)
    await mem.ingest("t1", conversation_id="c-1", user_message=None, **IDS)  # no-op
    (body,) = fake.bodies("/entries")
    (entry,) = body["entries"]
    assert entry["type"] == "user_message"
    assert entry["participantId"] == "persona-ana-participant"
    assert entry["timestamp"].endswith("Z") and len(entry["timestamp"]) == 24
    assert len(fake.exchanges) == 1


async def test_ingest_ids_are_content_derived_and_the_reply_comes_first():
    fake = FakeTenant()
    mem = fake.client()
    for _ in range(2):  # a hook firing twice in one turn re-sends the same ids
        await mem.ingest("t1", conversation_id="c-1", user_message="yes",
                         agent_response="Window or aisle?", **IDS)  # fmt: skip
    first, second = fake.bodies("/entries")
    assert [e["id"] for e in first["entries"]] == [e["id"] for e in second["entries"]]
    reply, user = first["entries"]
    assert (reply["type"], user["type"]) == ("agent_response", "user_message")
    assert reply["timestamp"] < user["timestamp"]
    assert reply["participantId"] == f"{IDS['agent_id']}-participant"
    assert entry_id("c-2", "user_message", "yes") != user["id"]


async def test_get_context_uses_the_context_header_and_records_a_hop(isolated_traces):
    fake = FakeTenant(context={"contextId": "ctx-7", "summary": "likes aisle seats"})
    ctx = await fake.client().get_context("t-ctx", conversation_id="c-1", **IDS)
    assert ctx["summary"] == "likes aisle seats"
    req = next(r for r in fake.calls if r.url.path == "/api/v1/agentic/context")
    assert req.headers["ctx-dataspace-ds_name"] == "default"
    # the read 403s without it (2026-10-13), whatever the agent_id param says
    assert req.headers["ctx-agent-id"] == "A2ALab_Memory_Concierge"
    assert req.url.params["owner_type"] == "individual"
    hops = [json.loads(line) for f in isolated_traces.glob("*.jsonl")
            for line in f.read_text().splitlines()]  # fmt: skip
    hops = [h for h in hops if h["trace_id"] == "t-ctx"]
    assert [h["target"] for h in hops] == ["agentic-memory"]
    assert hops[0]["response_payload_raw"]["status"] == 200
    assert hops[0]["platform_ref"] == "ctx-7"  # the service's id for this read


async def test_hops_without_a_context_id_carry_no_platform_ref(isolated_traces):
    await FakeTenant().client().ingest("t-in", conversation_id="c-1", user_message="x", **IDS)
    hops = [json.loads(line) for f in isolated_traces.glob("*.jsonl")
            for line in f.read_text().splitlines()]  # fmt: skip
    assert [h.get("platform_ref") for h in hops if h["trace_id"] == "t-in"] == [None]


async def test_refusal_raises_with_the_status():
    fake = FakeTenant(context={}, context_status=403)
    with pytest.raises(MemoryCallError, match="HTTP 403"):
        await fake.client().get_context("t1", conversation_id="c-1", **IDS)


async def test_close_session_posts_the_close_for_the_conversation_session():
    fake = FakeTenant()
    await fake.client().close_session("t1", conversation_id="c-1", **IDS)
    (req,) = [r for r in fake.calls if r.url.host == "tenant.example"]
    assert (req.method, req.url.path) == (
        "POST", "/api/v1/agentic/conversations/c-1/sessions/c-1/close"
    )  # fmt: skip


async def test_list_memories_reads_one_agents_store_by_owner():
    fake = FakeTenant()
    fake.memories[(IDS["agent_id"], "sys_PROFILE_FACT")] = ["Keeps bees.", "  "]
    got = await fake.client().list_memories("t1", category="sys_PROFILE_FACT", **IDS)
    assert got == ["Keeps bees."]
    req = next(r for r in fake.calls if r.url.path == "/api/v1/agentic/memory")
    assert req.headers["agent_id"] == IDS["agent_id"]
    assert dict(req.url.params) == {"profile_id": "persona-ana", "profile_type": "individual",
                                    "category": "sys_PROFILE_FACT"}  # fmt: skip


async def test_saved_memories_merge_every_shared_agent_and_fail_open(monkeypatch):
    monkeypatch.setenv(memory_mod.SHARED_AGENTS_ENV, "A2ALab_Memory_Claude, Gone_Agent")
    fake = FakeTenant()
    fake.memories[("A2ALab_Memory_Concierge", "sys_PROFILE_FACT")] = ["Keeps bees."]
    fake.memories[("A2ALab_Memory_Claude", "sys_PROFILE_FACT")] = ["keeps bees.", "Has a dog."]
    fake.memories[("A2ALab_Memory_Claude", "sys_PROFILE_PREFERENCES")] = ["Answers in Portuguese."]
    fake.refuse_memory.add("Gone_Agent")
    text, errors = await memory_mod.saved_memories(fake.client(), "t1", **IDS)
    # the calling agent's lines first, a case-insensitive duplicate dropped
    assert text.splitlines() == [
        "- saved fact: Keeps bees.",
        "- saved fact: Has a dog.",
        "- saved preference: Answers in Portuguese.",
    ]
    assert len(errors) == 2 and all("HTTP 500" in e for e in errors)  # one per category


def test_render_context_labels_string_leaves():
    text = render_context({"summary": "Lisbon trip", "facts": [{"value": "aisle"}, {"n": 3}]})
    assert text == "- summary: Lisbon trip\n- facts.value: aisle"


def test_render_context_drops_the_envelope():
    # the live shape (2026-10-13): ids and timestamps are not memory
    ctx = {"contextId": "f0fa", "sessionId": "s", "createdAt": "2026-10-13T17:32:27Z",
           "userContext": [{"entry": "Prefers aisle seats.", "sourceType": "Profile"}],
           "ontology": {}}  # fmt: skip
    assert render_context(ctx) == (
        "- userContext.entry: Prefers aisle seats.\n- userContext.sourceType: Profile"
    )


# ---------------------------------------------------------------- bridge route


class FakeMemory:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[tuple] = []
        self.listed: list[dict] = []
        self.saved: dict[tuple[str, str], list[str]] = {}

    async def start_conversation(self, trace_id, **kw):
        self.calls.append(("start", kw))
        return "c-new"

    async def get_context(self, trace_id, **kw):
        self.calls.append(("context", kw))
        if self.fail:
            raise MemoryCallError("GET /api/v1/agentic/context refused (HTTP 403)")
        return {"summary": "likes aisle seats"}

    async def ingest(self, trace_id, **kw):
        self.calls.append(("ingest", kw))

    async def close_session(self, trace_id, **kw):
        self.calls.append(("close", kw))

    async def list_memories(self, trace_id, **kw):
        self.listed.append(kw)
        return self.saved.get((kw["agent_id"], kw["category"]), [])

    async def aclose(self):
        pass


@pytest.fixture
def bridge(monkeypatch):
    fake = FakeMemory()
    monkeypatch.setattr(memory_mod, "MemoryClient", lambda **_: fake)
    return TestClient(create_bridge_app(Registry({}))), fake


def test_memory_turn_first_turn_starts_reads_then_ingests(bridge):
    client, fake = bridge
    r = client.post("/memory/turn", json={**IDS, "user_message": "hi"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True and data["conversation_id"] == "c-new"
    assert data["context"] == "- summary: likes aisle seats"
    assert [c[0] for c in fake.calls] == ["start", "context", "ingest", "close"]
    assert fake.calls[2][1]["user_message"] == "hi"


def test_memory_turn_context_carries_saved_memories(bridge):
    client, fake = bridge
    fake.saved[("A2ALab_Memory_Claude", "sys_PROFILE_FACT")] = ["Training for Porto."]
    data = client.post(
        "/memory/turn", json={**IDS, "conversation_id": "c-1", "user_message": "hi"}
    ).json()
    assert data["context"] == "- summary: likes aisle seats\n- saved fact: Training for Porto."
    # the store is per agent: every shared agent is asked, by owner
    assert {(k["agent_id"], k["owner_id"]) for k in fake.listed} == {
        ("A2ALab_Memory_Concierge", "persona-ana"), ("A2ALab_Memory_Claude", "persona-ana")
    }  # fmt: skip


def test_memory_turn_reuses_the_conversation(bridge):
    client, fake = bridge
    client.post("/memory/turn", json={**IDS, "conversation_id": "c-1", "user_message": "x"})
    assert [c[0] for c in fake.calls] == ["context", "ingest", "close"]


def test_memory_turn_fails_open(bridge):
    client, fake = bridge
    fake.fail = True
    data = client.post(
        "/memory/turn", json={**IDS, "conversation_id": "c-1", "user_message": "hi"}
    ).json()
    assert data["ok"] is False and "403" in data["error"] and data["context"] == ""
    # a refused read must not cost the turn its ingest
    assert [c[0] for c in fake.calls] == ["context", "ingest", "close"]


def test_memory_turn_reply_only_ingests_without_reading(bridge):
    client, fake = bridge
    data = client.post(
        "/memory/turn", json={**IDS, "conversation_id": "c-1", "agent_response": "Noted."}
    ).json()
    assert data["ok"] is True and data["context"] == ""
    assert [c[0] for c in fake.calls] == ["ingest", "close"]
    assert fake.calls[0][1]["agent_response"] == "Noted."


def test_memory_turn_requires_ids(bridge):
    client, _ = bridge
    assert client.post("/memory/turn", json={"agent_id": "a"}).status_code == 400


def test_memory_turn_checks_the_bridge_token(bridge, monkeypatch):
    client, _ = bridge
    monkeypatch.setenv("BRIDGE_TOKEN", "secret")
    assert client.post("/memory/turn", json=IDS).status_code == 401
