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
from interop.memory import (
    GENERATION_CATEGORY,
    MemoryCallError,
    MemoryClient,
    MemoryListing,
    case_conversation,
    entry_id,
    owner_conversation,
    redact,
    normalize_ingest_content,
    new_generation_marker,
    render_context,
    saved_memories,
    select_generation,
    write_turn,
)
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
        self.memory_total_counts: dict[tuple[str, str], object] = {}
        self.refuse_memory: set[str] = set()
        self.html_403_entries = 0
        self.json_403_entries = 0

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
            if request.method == "POST":
                values = json.loads(request.content)["memories"]
                return httpx.Response(200, json={"statusCode": "created", "memories": [
                    {"memoryId": f"m-{i}", "content": item["content"]}
                    for i, item in enumerate(values)
                ]})
            if request.method == "DELETE":
                values = json.loads(request.content)["memories"]
                return httpx.Response(200, json={"statusCode": "deleted", "memories": values})
            contents = self.memories.get(key, [])
            return httpx.Response(200, json={"memories": [
                {"memoryId": f"m-{i}", "content": c} for i, c in enumerate(contents)
            ], "totalCount": self.memory_total_counts.get(key, len(contents))})
        if path.endswith("/entries") and self.html_403_entries:
            self.html_403_entries -= 1
            return httpx.Response(403, text="<html><h1>403 Forbidden</h1></html>")
        if path.endswith("/entries") and self.json_403_entries:
            self.json_403_entries -= 1
            return httpx.Response(403, json={"error": "forbidden"})
        return httpx.Response(202, json={})

    def client(self) -> MemoryClient:
        return MemoryClient(httpx.AsyncClient(transport=httpx.MockTransport(self.handler)))

    def bodies(self, suffix: str) -> list[dict]:
        return [json.loads(r.content) for r in self.calls if r.url.path.endswith(suffix)]


IDS = {"agent_id": "A2ALab_Memory_Concierge", "owner_id": "persona-ana"}


async def test_start_conversation_opens_a_session_in_the_owners_conversation():
    fake = FakeTenant()
    sid = await fake.client().start_conversation("t1", **IDS)
    cid = owner_conversation("persona-ana")
    paths = [r.url.path for r in fake.calls if r.url.host == "tenant.example"]
    assert paths == [
        "/api/v1/agentic/conversations",
        f"/api/v1/agentic/conversations/{cid}/sessions",
        f"/api/v1/agentic/conversations/{cid}/sessions/{sid}/participants",
    ]
    assert fake.bodies("/conversations")[0]["conversationId"] == cid
    session = fake.bodies("/sessions")[0]
    assert session["sessionId"] == sid != cid  # D94: a chat is a session, not a conversation
    assert session["participants"][0]["id"] == "persona-ana-participant"
    agent = fake.bodies("/participants")[0]["participants"][0]
    assert agent == {**agent, "id": "A2ALab_Memory_Concierge-participant", "type": "agent"}
    post = next(r for r in fake.calls if r.url.path == "/api/v1/agentic/conversations")
    assert post.headers["dataspace"] == "default"
    assert post.headers["agent_id"] == "A2ALab_Memory_Concierge"
    # the exchange is bound to the calling agent: a360 refuses an unregistered aid
    assert fake.exchanges[0]["aid"] == "A2ALab_Memory_Concierge"
    assert fake.exchanges[0]["xid"] == "persona-ana"


async def test_existing_conversation_409_is_success():
    fake = FakeTenant()
    fake.conversation_status = 409
    assert await fake.client().start_conversation("t1", session_id="c-1", **IDS) == "c-1"


def test_owner_conversation_is_per_owner_never_per_agent():
    # every agent sharing memory must read the same summary (D94)
    assert owner_conversation("persona-ana") == owner_conversation("persona-ana")
    assert owner_conversation("persona-ana") != owner_conversation("persona-bo")
    assert "persona" not in owner_conversation("persona-ana")  # hashed, not the username


def test_case_conversation_is_opaque_and_separated():
    cid = case_conversation("persona-ana", "supplier-disruption", "Rotterdam day one")
    assert cid == case_conversation("persona-ana", "supplier-disruption", "Rotterdam day one")
    assert cid != case_conversation("persona-bo", "supplier-disruption", "Rotterdam day one")
    assert cid != case_conversation("persona-ana", "another-scenario", "Rotterdam day one")
    assert "persona" not in cid and "Rotterdam" not in cid


async def test_explicit_case_conversation_threads_every_runtime_call():
    fake = FakeTenant()
    mem = fake.client()
    cid = case_conversation("persona-ana", "supplier-disruption", "case-7")
    sid = await mem.start_conversation("t1", conversation_id=cid, **IDS)
    await mem.ingest(
        "t1", session_id=sid, conversation_id=cid, agent_response="candidate", **IDS
    )
    await mem.get_context("t1", conversation_id=cid, **IDS)
    await mem.close_session("t1", session_id=sid, conversation_id=cid, **IDS)
    tenant = [r for r in fake.calls if r.url.host == "tenant.example"]
    assert fake.bodies("/conversations")[0]["conversationId"] == cid
    assert any(r.url.path == f"/api/v1/agentic/conversations/{cid}/entries" for r in tenant)
    context = next(r for r in tenant if r.url.path == "/api/v1/agentic/context")
    assert context.url.params["conversation_id"] == cid
    assert tenant[-1].url.path == f"/api/v1/agentic/conversations/{cid}/sessions/{sid}/close"


async def test_case_read_can_exclude_owner_profile_memories():
    fake = FakeTenant(context={"summary": "case-only observation"})
    fake.memories[(IDS["agent_id"], "sys_PROFILE_FACT")] = ["unrelated owner fact"]
    _, context, errors = await memory_mod.open_and_read(
        fake.client(),
        "t1",
        conversation_id="a2alab-c-example",
        include_saved_memories=False,
        **IDS,
    )
    assert context == "- summary: case-only observation"
    assert not errors
    assert not any(r.url.path == "/api/v1/agentic/memory" for r in fake.calls)


async def test_ingest_sends_only_the_sides_it_has_and_caches_the_token():
    fake = FakeTenant()
    mem = fake.client()
    await mem.ingest("t1", session_id="c-1", user_message="I prefer aisle seats", **IDS)
    await mem.ingest("t1", session_id="c-1", user_message=None, **IDS)  # no-op
    (body,) = fake.bodies("/entries")
    (entry,) = body["entries"]
    assert entry["type"] == "user_message"
    assert entry["participantId"] == "persona-ana-participant"
    assert entry["timestamp"].endswith("Z") and len(entry["timestamp"]) == 24
    assert len(fake.exchanges) == 1


async def test_tokens_are_cached_per_owner_and_agent():
    fake = FakeTenant()
    mem = fake.client()
    await mem.get_context("t1", **IDS)
    await mem.get_context("t2", agent_id=IDS["agent_id"], owner_id="persona-bo")
    assert [e["xid"] for e in fake.exchanges] == ["persona-ana", "persona-bo"]


async def test_ingest_ids_are_content_derived_and_the_reply_comes_first():
    fake = FakeTenant()
    mem = fake.client()
    for _ in range(2):  # a hook firing twice in one turn re-sends the same ids
        await mem.ingest("t1", session_id="c-1", user_message="yes",
                         agent_response="Window or aisle?", **IDS)  # fmt: skip
    first, second = fake.bodies("/entries")
    assert [e["id"] for e in first["entries"]] == [e["id"] for e in second["entries"]]
    reply, user = first["entries"]
    assert (reply["type"], user["type"]) == ("agent_response", "user_message")
    assert reply["timestamp"] < user["timestamp"]
    assert reply["participantId"] == f"{IDS['agent_id']}-participant"
    assert entry_id("c-2", "user_message", "yes") != user["id"]  # same words, another chat


async def test_ingest_redacts_before_the_tenant_stores_it():
    fake = FakeTenant()
    await fake.client().ingest(
        "t1", session_id="c-1", user_message="my password is hunter2, mail ana@example.com", **IDS
    )
    (entry,) = fake.bodies("/entries")[0]["entries"]
    assert entry["content"] == "my password is [REDACTED] mail [REDACTED_EMAIL]"
    assert fake.calls[-1].url.path.endswith(f"/{owner_conversation('persona-ana')}/entries")


def test_redact_keeps_ordinary_facts():
    assert redact("My canoe is Indigo Lark 9c03, race on Sunday at 10") == (
        "My canoe is Indigo Lark 9c03, race on Sunday at 10"
    )
    assert redact("Bearer abcdefgh12345") == "Bearer [REDACTED_TOKEN]"
    assert redact("card 4111 1111 1111 1111") == "card [REDACTED_CARD]"


def test_generation_control_fact_is_validated_and_never_rendered_to_the_model():
    marker = new_generation_marker()
    listing = MemoryListing((
        memory_mod.MemoryEntry("fact", "Keeps bees."),
        memory_mod.MemoryEntry("control", marker),
    ), 2)
    assert GENERATION_CATEGORY == "sys_PROFILE_FACT"
    assert select_generation(listing) == (marker, False)
    assert marker not in render_context({"userContext": f"Keeps bees. {marker}"})
    malformed = MemoryListing((memory_mod.MemoryEntry("bad", "v1:caller-chosen"),), 1)
    assert select_generation(malformed) == (None, True)


async def test_generation_control_fact_is_excluded_from_saved_prompt_material():
    marker = new_generation_marker()
    fake = FakeTenant()
    fake.memories[(IDS["agent_id"], "sys_PROFILE_FACT")] = [marker, "Keeps bees."]
    text, errors = await saved_memories(fake.client(), "t1", **IDS)
    assert errors == [] and "Keeps bees." in text and marker not in text


def test_ingest_normalization_keeps_meaning_without_url_shapes():
    got = normalize_ingest_content(
        "See https://runbooks.example.invalid/supplier-api, `/v1/orders`, and /api/v1/inventory."
    )
    assert "https://" not in got and "/v1/" not in got
    assert "runbooks.example.invalid supplier-api" in got
    assert "API endpoint v1 orders" in got
    assert "/api/v1/" not in got and "API endpoint v1 inventory" in got


async def test_ingest_retries_one_html_403_with_normalized_content():
    fake = FakeTenant()
    fake.html_403_entries = 1
    normalized = await fake.client().ingest(
        "t1", session_id="c-1", user_message="See https://probe.example.invalid/x /v1/orders", **IDS
    )
    assert normalized is True
    first, second = fake.bodies("/entries")
    assert "https://" in first["entries"][0]["content"]
    assert "https://" not in second["entries"][0]["content"]


async def test_ingest_does_not_retry_a_json_403():
    fake = FakeTenant()
    fake.json_403_entries = 1
    with pytest.raises(MemoryCallError, match="HTTP 403"):
        await fake.client().ingest(
            "t1", session_id="c-1", user_message="See https://probe.example.invalid/x", **IDS
        )
    assert len(fake.bodies("/entries")) == 1


async def test_get_context_uses_the_context_header_and_records_a_hop(isolated_traces):
    fake = FakeTenant(context={"contextId": "ctx-7", "summary": "likes aisle seats"})
    ctx = await fake.client().get_context("t-ctx", **IDS)
    assert ctx["summary"] == "likes aisle seats"
    req = next(r for r in fake.calls if r.url.path == "/api/v1/agentic/context")
    assert req.headers["ctx-dataspace-ds_name"] == "default"
    # the read 403s without it (2026-10-13), whatever the agent_id param says
    assert req.headers["ctx-agent-id"] == "A2ALab_Memory_Concierge"
    assert req.url.params["owner_type"] == "individual"
    # the OWNER's conversation: the one with every earlier chat in it (D94)
    assert req.url.params["conversation_id"] == owner_conversation("persona-ana")
    hops = [json.loads(line) for f in isolated_traces.glob("*.jsonl")
            for line in f.read_text().splitlines()]  # fmt: skip
    hops = [h for h in hops if h["trace_id"] == "t-ctx"]
    assert [h["target"] for h in hops] == ["agentic-memory"]
    assert hops[0]["response_payload_raw"]["status"] == 200
    assert hops[0]["platform_ref"] == "ctx-7"  # the service's id for this read


async def test_hops_without_a_context_id_carry_no_platform_ref(isolated_traces):
    await FakeTenant().client().ingest("t-in", session_id="c-1", user_message="x", **IDS)
    hops = [json.loads(line) for f in isolated_traces.glob("*.jsonl")
            for line in f.read_text().splitlines()]  # fmt: skip
    assert [h.get("platform_ref") for h in hops if h["trace_id"] == "t-in"] == [None]


async def test_refusal_raises_with_the_status():
    fake = FakeTenant(context={}, context_status=403)
    with pytest.raises(MemoryCallError, match="HTTP 403"):
        await fake.client().get_context("t1", **IDS)


async def test_close_session_closes_the_chats_session_in_the_owners_conversation():
    fake = FakeTenant()
    await fake.client().close_session("t1", session_id="c-1", **IDS)
    (req,) = [r for r in fake.calls if r.url.host == "tenant.example"]
    assert (req.method, req.url.path) == (
        "POST", f"/api/v1/agentic/conversations/{owner_conversation('persona-ana')}/sessions/c-1/close"
    )  # fmt: skip


async def test_a_second_write_to_the_same_closed_session_is_still_accepted():
    fake = FakeTenant()
    first = await write_turn(
        fake.client(), "t1", session_id="c-1", user_message="first", **IDS
    )
    second = await write_turn(
        fake.client(), "t2", session_id="c-1", user_message="second", **IDS
    )
    assert first == second == ([], False)
    tenant_paths = [r.url.path for r in fake.calls if r.url.host == "tenant.example"]
    assert sum(path.endswith("/entries") for path in tenant_paths) == 2
    assert sum(path.endswith("/close") for path in tenant_paths) == 2


async def test_list_memories_reads_one_shared_service_by_owner(isolated_traces):
    fake = FakeTenant()
    fake.memories[(IDS["agent_id"], "sys_PROFILE_FACT")] = ["Keeps bees.", "  "]
    got = await fake.client().list_memories("t1", category="sys_PROFILE_FACT", **IDS)
    assert isinstance(got, MemoryListing)
    assert [(entry.memory_id, entry.content) for entry in got.entries] == [
        ("m-0", "Keeps bees."), ("m-1", "")
    ]
    assert got.total_count == 2 and got.complete is True
    req = next(r for r in fake.calls if r.url.path == "/api/v1/agentic/memory")
    assert req.headers["agent_id"] == IDS["agent_id"]
    assert dict(req.url.params) == {"profile_id": "persona-ana", "profile_type": "individual",
                                    "category": "sys_PROFILE_FACT"}  # fmt: skip
    hops = [json.loads(line) for f in isolated_traces.glob("*.jsonl")
            for line in f.read_text().splitlines()]  # fmt: skip
    (hop,) = [h for h in hops if h["trace_id"] == "t1"]
    assert hop["transport_detail"] == (
        "GET /api/v1/agentic/memory · sys_PROFILE_FACT · A2ALab_Memory_Concierge"
    )


async def test_list_memories_ignores_undocumented_total_count_undercount():
    fake = FakeTenant()
    key = (IDS["agent_id"], "sys_PROFILE_FACT")
    fake.memories[key] = [f"Fact {i}" for i in range(22)]
    fake.memory_total_counts[key] = 1

    got = await fake.client().list_memories("t1", category="sys_PROFILE_FACT", **IDS)

    assert len(got.entries) == got.total_count == 22
    assert got.reported_total_count == 1
    assert got.complete is True


async def test_list_memories_still_refuses_a_reported_count_above_returned_rows():
    fake = FakeTenant()
    key = (IDS["agent_id"], "sys_PROFILE_FACT")
    fake.memories[key] = ["Only returned row"]
    fake.memory_total_counts[key] = 2

    got = await fake.client().list_memories("t1", category="sys_PROFILE_FACT", **IDS)

    assert got.total_count == got.reported_total_count == 2
    assert got.complete is False


async def test_create_and_delete_memories_use_profile_scope():
    fake = FakeTenant()
    mem = fake.client()
    created = await mem.create_memories(
        "t1", category="A2ALAB_EXPERIMENT_GENERATION", contents=["v1:marker"], **IDS
    )
    deleted = await mem.delete_memories(
        "t2", category="A2ALAB_EXPERIMENT_GENERATION", memory_ids=["m-0"], **IDS
    )
    assert created.entries[0].memory_id == "m-0" and deleted == ("m-0",)
    calls = [r for r in fake.calls if r.url.path == "/api/v1/agentic/memory"]
    assert [r.method for r in calls] == ["POST", "DELETE"]
    assert all(r.url.params["profile_id"] == IDS["owner_id"] for r in calls)


async def test_saved_memories_read_shared_service_categories_and_fail_open():
    fake = FakeTenant()
    fake.memories[("A2ALab_Memory_Concierge", "sys_PROFILE_FACT")] = [
        "Keeps bees.", "Has a dog."
    ]
    fake.memories[("A2ALab_Memory_Concierge", "sys_PROFILE_PREFERENCES")] = [
        "Answers in Portuguese."
    ]
    text, errors = await memory_mod.saved_memories(fake.client(), "t1", **IDS)
    assert text.splitlines() == [
        "- saved fact: Keeps bees.",
        "- saved fact: Has a dog.",
        "- saved preference: Answers in Portuguese.",
    ]
    assert errors == []
    reads = [r for r in fake.calls if r.url.path == "/api/v1/agentic/memory"]
    assert len(reads) == 2


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
    fake.saved[(IDS["agent_id"], "sys_PROFILE_FACT")] = [
        "Training for Porto."
    ]
    data = client.post(
        "/memory/turn", json={**IDS, "conversation_id": "c-1", "user_message": "hi"}
    ).json()
    assert data["context"] == "- summary: likes aisle seats\n- saved fact: Training for Porto."
    # One shared memory service is asked once per semantic category.
    assert {(k["agent_id"], k["owner_id"]) for k in fake.listed} == {
        ("A2ALab_Memory_Concierge", "persona-ana"),
    }  # fmt: skip
    assert {k["category"] for k in fake.listed} == set(memory_mod.SAVED_CATEGORIES)


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
