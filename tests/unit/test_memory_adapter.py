"""interop.memory_adapter (plan/18 Phase 3, WS27 item 11): the generic memory
decorator over the inbound seam, against a fake memory client."""

from __future__ import annotations

import asyncio

import pytest

from interop import identity
from interop import memory_adapter as ma
from interop.memory import MemoryCallError, MemoryEntry, MemoryListing, generation_conversation, owner_conversation
from interop.memory_adapter import MemoryAdapter, owner_of, with_memory
from interop.models import AgentRequest, AgentResponse

USERS = {
    "ana": {"name": "Ana the Analyst", "role": "viewer"},
    "bridge-bot": {"name": "Bridge", "role": "machine"},
}
AGENT = "A2ALab_Memory_Case"
ANA_XID = "unified-profile-ana"
VIC_XID = "unified-profile-vic"
MAYA_XID = "unified-profile-maya"


@pytest.fixture(autouse=True)
def keypair_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(identity.KEY_DIR_ENV, str(tmp_path / "keys"))
    monkeypatch.setenv(ma.OWNER_MAP_ENV, '{"ana":"unified-profile-ana",'
                                        '"vic":"unified-profile-vic"}')
    monkeypatch.setenv(ma.CUSTOMER_MAP_ENV, '{"maya":"unified-profile-maya"}')


class FakeMemory:
    def __init__(self, context: dict | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.context = context if context is not None else {"summary": "prefers aisle seats"}
        self.fail_read = False
        self.read_delay_s = 0.0
        self.generation: str | None = None

    async def start_conversation(self, trace_id, **kw):
        self.calls.append(("start", kw))
        return kw.get("session_id") or "c-fresh"

    async def get_context(self, trace_id, **kw):
        self.calls.append(("context", kw))
        await asyncio.sleep(self.read_delay_s)
        if self.fail_read:
            raise MemoryCallError("GET /api/v1/agentic/context refused (HTTP 403)")
        return self.context

    async def ingest(self, trace_id, **kw):
        self.calls.append(("ingest", kw))

    async def close_session(self, trace_id, **kw):
        self.calls.append(("close", kw))

    async def list_memories(self, trace_id, **kw):
        if self.generation:
            return MemoryListing((MemoryEntry("generation-1", self.generation),), 1)
        return MemoryListing((), 0)


class Inner:
    name = "inner-agent"
    description = "an inner agent"
    extra_mcp_tools = ["kept"]

    def __init__(self):
        self.seen: list[AgentRequest] = []

    async def handle(self, req: AgentRequest) -> AgentResponse:
        self.seen.append(req)
        return AgentResponse(text="Noted, aisle it is.", session_id=req.session_id or "s-new")


def _req(
    user: str | None = "ana",
    session_id: str | None = None,
    msg: str = "Book me a flight",
    memory: bool = True,
):
    meta: dict = {"memory": True} if memory else {}
    if user:
        meta["user_token"] = identity.issue_token(user, users=USERS)
    return AgentRequest(message=msg, session_id=session_id, trace_id="t1", metadata=meta)


def _adapter(fake: FakeMemory, **kw) -> tuple[MemoryAdapter, Inner]:
    inner = Inner()
    return MemoryAdapter(inner, AGENT, client=fake, **kw), inner


def test_owner_is_the_verified_user_and_never_a_machine_or_a_claim():
    assert owner_of(_req("ana")) == ANA_XID
    assert owner_of(_req("bridge-bot")) is None
    assert owner_of(_req(None)) is None
    forged = AgentRequest(message="x", metadata={"user_token": "eyJ.not.valid",
                                                 "user_context": {"sub": "ana"}})  # fmt: skip
    assert owner_of(forged) is None


def test_owner_mapping_is_required_and_must_be_valid_json(monkeypatch):
    monkeypatch.delenv(ma.OWNER_MAP_ENV)
    assert owner_of(_req("ana")) is None
    monkeypatch.setenv(ma.OWNER_MAP_ENV, "not-json")
    assert owner_of(_req("ana")) is None


def test_selected_customer_uses_the_customer_profile_map():
    req = _req("ana")
    req.metadata["memory_customer"] = "maya"
    assert owner_of(req) == MAYA_XID
    req.metadata["memory_customer"] = "not-configured"
    assert owner_of(req) is None


async def test_a_run_that_does_not_ask_for_memory_is_the_inner_agents():
    fake = FakeMemory()
    adapter, inner = _adapter(fake)
    req = _req(memory=False, session_id="s-1")
    resp = await adapter.handle(req)
    assert fake.calls == []
    assert inner.seen[0] is req
    assert resp.metadata is None
    # only a literal True turns it on — a truthy string from a careless caller does not
    loose = _req(memory=False)
    loose.metadata["memory"] = "yes"
    await adapter.handle(loose)
    assert fake.calls == []


async def test_asked_for_without_a_verified_user_answers_and_says_why():
    fake = FakeMemory()
    adapter, inner = _adapter(fake)
    resp = await adapter.handle(_req(None))
    assert fake.calls == []
    assert inner.seen[0].message == "Book me a flight"
    mem = resp.metadata["memory"]
    assert mem["ok"] is False and mem["owner_id"] is None and "profile" in mem["error"]


async def test_a_turn_reads_before_and_ingests_both_sides_after():
    fake = FakeMemory()
    adapter, inner = _adapter(fake)
    resp = await adapter.handle(_req(session_id="s-1"))

    assert [c[0] for c in fake.calls] == ["start", "context", "ingest", "close"]
    sent = inner.seen[0]
    assert "prefers aisle seats" in sent.message and sent.message.endswith("Book me a flight")
    ingest = fake.calls[2][1]
    # the ORIGINAL user text, not the memory-augmented prompt, plus the reply (D89)
    assert ingest["user_message"] == "Book me a flight"
    assert ingest["agent_response"] == "Noted, aisle it is."
    assert ingest["owner_id"] == ANA_XID and ingest["agent_id"] == AGENT
    mem = resp.metadata["memory"]
    assert mem["ok"] is True and mem["owner_id"] == ANA_XID
    assert mem["session_id"] == fake.calls[0][1]["session_id"]
    assert mem["conversation_id"] == owner_conversation(ANA_XID)  # the owner's, D94
    assert mem["context_chars"] > 0
    assert mem["recalled"] is True and mem["stored"] is True and mem["read_ok"] is True
    assert "prefers aisle seats" in mem["context_preview"]


async def test_the_memory_session_follows_the_lab_session_and_is_started_once():
    fake = FakeMemory()
    adapter, _ = _adapter(fake)
    first = await adapter.handle(_req(session_id="s-1"))
    second = await adapter.handle(_req(session_id="s-1", msg="And a window seat back"))
    assert [c[0] for c in fake.calls] == [
        "start",
        "context",
        "ingest",
        "close",
        "context",
        "ingest",
        "close",
    ]
    assert first.metadata["memory"]["session_id"] == second.metadata["memory"]["session_id"]
    # deterministic, so a restarted face resumes the same session (409 = ok)
    other, _ = _adapter(FakeMemory())
    again = await other.handle(_req(session_id="s-1"))
    assert again.metadata["memory"]["session_id"] == first.metadata["memory"]["session_id"]


async def test_customer_generation_changes_conversation_and_session_for_every_face():
    fake = FakeMemory()
    adapter, _ = _adapter(fake)
    req = _req(session_id="s-1")
    req.metadata["memory_customer"] = "maya"
    first = await adapter.handle(req)
    fake.generation = "v1:2026-11-03T20:00:00Z:0123456789abcdef01234567"
    second = await adapter.handle(req)
    assert second.metadata["memory"]["conversation_id"] == generation_conversation(
        MAYA_XID, fake.generation
    )
    assert second.metadata["memory"]["conversation_id"] != first.metadata["memory"]["conversation_id"]
    assert second.metadata["memory"]["session_id"] != first.metadata["memory"]["session_id"]


async def test_malformed_customer_generation_suppresses_old_memory():
    fake = FakeMemory()
    fake.generation = "caller-chosen-bad-value"
    adapter, inner = _adapter(fake)
    req = _req(session_id="s-1")
    req.metadata["memory_customer"] = "maya"
    resp = await adapter.handle(req)
    assert fake.calls == []
    assert inner.seen[0].message == req.message
    assert resp.metadata["memory"]["ok"] is False


async def test_a_first_turn_without_a_session_is_followed_by_the_reply_session():
    fake = FakeMemory()
    adapter, _ = _adapter(fake)
    first = await adapter.handle(_req())  # inner mints session "s-new"
    second = await adapter.handle(_req(session_id="s-new"))
    assert [c[0] for c in fake.calls].count("start") == 1
    assert second.metadata["memory"]["session_id"] == first.metadata["memory"]["session_id"]


async def test_owners_never_share_a_conversation_for_the_same_session():
    users = {**USERS, "vic": {"name": "Vic", "role": "viewer"}}
    fake = FakeMemory()
    adapter, _ = _adapter(fake)
    a = await adapter.handle(_req(session_id="s-1"))
    vic = AgentRequest(message="hi", session_id="s-1", trace_id="t2",
                       metadata={"memory": True,
                                 "user_token": identity.issue_token("vic", users=users)})  # fmt: skip
    b = await adapter.handle(vic)
    for key in ("session_id", "conversation_id"):
        assert a.metadata["memory"][key] != b.metadata["memory"][key]


async def test_an_empty_context_leaves_the_message_alone():
    adapter, inner = _adapter(FakeMemory(context={}))
    await adapter.handle(_req(session_id="s-1"))
    assert inner.seen[0].message == "Book me a flight"


async def test_a_refused_read_still_answers_and_still_ingests():
    fake = FakeMemory()
    fake.fail_read = True
    adapter, inner = _adapter(fake)
    resp = await adapter.handle(_req(session_id="s-1"))
    assert inner.seen[0].message == "Book me a flight"
    assert [c[0] for c in fake.calls] == ["start", "context", "ingest", "close"]
    assert resp.metadata["memory"]["ok"] is False
    assert resp.metadata["memory"]["read_ok"] is False
    assert resp.metadata["memory"]["stored"] is True
    assert "403" in resp.metadata["memory"]["error"]


async def test_a_slow_read_is_cut_off_at_the_budget():
    fake = FakeMemory()
    fake.read_delay_s = 1.0
    adapter, inner = _adapter(fake, budget_s=0.05)
    resp = await adapter.handle(_req(session_id="s-1"))
    assert inner.seen[0].message == "Book me a flight"
    mem = resp.metadata["memory"]
    assert mem["ok"] is False and "budget" in mem["error"]


async def test_existing_response_metadata_is_kept():
    class WithMeta(Inner):
        async def handle(self, req):
            return AgentResponse(text="ok", metadata={"agent_session_id": "AF-1"})

    adapter = MemoryAdapter(WithMeta(), AGENT, client=FakeMemory())
    resp = await adapter.handle(_req(session_id="s-1"))
    assert resp.metadata["agent_session_id"] == "AF-1" and "memory" in resp.metadata


def test_the_wrapper_keeps_the_inner_agents_face():
    adapter, inner = _adapter(FakeMemory())
    assert adapter.name == "inner-agent" and adapter.description == "an inner agent"
    assert adapter.extra_mcp_tools == ["kept"]


def test_with_memory_wraps_only_platforms_with_a_memory_agent():
    inner = Inner()
    wrapped = with_memory(inner, "claude")
    assert isinstance(wrapped, MemoryAdapter) and wrapped.agent_id == AGENT
    openai = with_memory(inner, "openai")
    assert isinstance(openai, MemoryAdapter)
    assert openai.agent_id == AGENT
    # no registration, no wrapper
    assert with_memory(inner, "adk") is inner


def test_the_memory_agent_names_match_the_registration_script():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import setup_agentic_memory

    assert set(ma.MEMORY_AGENTS.values()) == {AGENT}
    assert setup_agentic_memory.AGENTS["case-memory"].name == AGENT


def test_registration_delete_is_dry_run_by_default_and_verifies_apply(monkeypatch):
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import setup_agentic_memory as setup

    old_name = setup.AGENTS["legacy-claude"].name
    live = {old_name, setup.AGENTS["case-memory"].name}
    calls = []
    monkeypatch.setattr(setup, "agents", lambda: [{"name": name} for name in live])

    def fake_api(method, path, body=None, base=setup.API):
        calls.append((method, path, body, base))
        live.discard(path.rsplit("/", 1)[-1])
        return {}

    monkeypatch.setattr(setup, "api", fake_api)
    setup.cmd_delete(SimpleNamespace(agents=["legacy-claude"], apply=False))
    assert calls == [] and old_name in live

    setup.cmd_delete(SimpleNamespace(agents=["legacy-claude"], apply=True))
    assert calls == [("DELETE", f"/agent/{old_name}", {}, setup.API)]
    assert old_name not in live


def test_wrapped_adapters_share_one_client(monkeypatch):
    monkeypatch.setattr(ma, "_shared", None)
    shared = FakeMemory()
    monkeypatch.setattr(ma, "MemoryClient", lambda **_kw: shared)
    a, b = with_memory(Inner(), "claude"), with_memory(Inner(), "claude")
    assert a._client() is b._client() is shared
