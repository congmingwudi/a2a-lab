"""A11/F02 — the Agentforce shim must isolate conversations by the VERIFIED
caller, not collapse every caller on a platform onto one shared session.

The defect (D81): `AgentforceProxyAdapter.handle` only set the reuse key
`when req.session_id is None`, but the pinned a2a-sdk generates a context id for
every inbound message, so that guard was dead on the real server and one
`shim-shared-{platform}` session served all callers. The fix threads the JWT
subject the auth middleware verified — never a caller-asserted body field —
through the executor into the reuse key, and overrides the SDK context id in
reuse mode.

These exercise the REAL chain: TokenAuthMiddleware verifies the JWT and
publishes the subject, the REAL AdapterExecutor reads it and stamps the request,
and the REAL proxy builds the session key. Only the a2a-sdk JSON-RPC transport
envelope and the TaskUpdater plumbing are faked.
"""

from __future__ import annotations

import asyncio
import types

import pytest
from google.protobuf import struct_pb2

from a2a.types import Message

from interop.models import AgentResponse
from interop.servers import a2a as a2a_mod
from interop.servers import auth as auth_mod
from interop.servers.a2a import AdapterExecutor
from platforms.agentforce.proxy import AgentforceProxyAdapter


class RecordingClient:
    """Stands in for AgentforceClient: records the session key each ask used."""

    def __init__(self):
        self.agent_id = "0XxDEFAULT"
        self.source_name = "shim"
        self.session_keys: list[str | None] = []

    async def ask(self, req):
        self.session_keys.append(req.session_id)
        return AgentResponse(text="ok")


class _FakeUpdater:
    def __init__(self, *a, **k):
        pass

    async def start_work(self):
        pass

    async def add_artifact(self, *a, **k):
        pass

    async def complete(self):
        pass

    async def failed(self, *a, **k):
        pass

    def new_agent_message(self, *a, **k):
        return None


class _FakeQueue:
    async def enqueue_event(self, event):
        pass


def _ctx(metadata: dict, ctx_id: str = "ctx-generated-by-sdk"):
    """A minimal RequestContext: the SDK always populates context_id, which is
    the whole point — reuse mode must OVERRIDE it, not defer to `is None`."""
    msg = Message()
    struct = struct_pb2.Struct()
    struct.update(metadata)
    msg.metadata.CopyFrom(struct)
    return types.SimpleNamespace(
        get_user_input=lambda: "hi",
        message=msg,
        context_id=ctx_id,
        task_id="task-1",
    )


@pytest.fixture(autouse=True)
def _fake_updater(monkeypatch):
    monkeypatch.setattr(a2a_mod, "TaskUpdater", _FakeUpdater)


def _executor():
    client = RecordingClient()
    adapter = AgentforceProxyAdapter(client=client, session_reuse=True)
    return AdapterExecutor(adapter), client


async def _run_as_subject(executor, subject: str | None, metadata: dict | None = None):
    """Drive the executor with the verified subject published as the middleware
    would, then reset it — the request-bound contract the middleware guarantees."""
    token = auth_mod.VERIFIED_SUBJECT.set(subject)
    try:
        await executor.execute(_ctx(metadata or {}), _FakeQueue())
    finally:
        auth_mod.VERIFIED_SUBJECT.reset(token)


# --- the reuse key is per verified subject -----------------------------------


async def test_two_subjects_get_two_sessions():
    executor, client = _executor()
    await _run_as_subject(executor, "alice")
    await _run_as_subject(executor, "bob")
    assert client.session_keys == ["shim-shared-direct-alice", "shim-shared-direct-bob"]


async def test_same_subject_reuses_one_session():
    executor, client = _executor()
    await _run_as_subject(executor, "alice")
    await _run_as_subject(executor, "alice")
    assert client.session_keys == ["shim-shared-direct-alice", "shim-shared-direct-alice"]


async def test_no_verified_subject_takes_the_platform_only_fallback():
    """The legacy shared service token verifies no subject, so it lands on the
    platform-only key — not on some other caller's per-subject session."""
    executor, client = _executor()
    await _run_as_subject(executor, None)
    assert client.session_keys == ["shim-shared-direct"]


async def test_sdk_context_id_is_overridden_in_reuse_mode():
    """The dead-guard fix: even though the SDK populated context_id, reuse mode
    overrides it with the reuse key rather than keying by the SDK's id."""
    executor, client = _executor()
    await _run_as_subject(executor, "alice")
    assert client.session_keys[0] != "ctx-generated-by-sdk"
    assert client.session_keys[0] == "shim-shared-direct-alice"


async def test_caller_asserted_verified_subject_is_overwritten():
    """A caller putting `verified_subject` in the message body must not steal
    another user's session — the executor overwrites it with the verified one."""
    executor, client = _executor()
    await _run_as_subject(executor, "alice", metadata={"verified_subject": "attacker"})
    assert client.session_keys == ["shim-shared-direct-alice"]


async def test_caller_asserted_subject_dropped_when_none_verified():
    """With no verified subject, a caller-asserted one is DROPPED, not trusted —
    the request falls back to the platform-only key."""
    executor, client = _executor()
    await _run_as_subject(executor, None, metadata={"verified_subject": "attacker"})
    assert client.session_keys == ["shim-shared-direct"]


# --- through the REAL TokenAuthMiddleware, and under concurrency -------------


def _jwt(monkeypatch, tmp_path, username: str) -> str:
    from interop import identity

    monkeypatch.setenv(identity.KEY_DIR_ENV, str(tmp_path / "keys"))
    return identity.issue_token(username, users={username: {"name": username, "role": "viewer"}})


async def _through_middleware(executor, supplied_token: str, metadata: dict | None = None):
    """The full chain: TokenAuthMiddleware verifies the token and its downstream
    app runs the real executor within the request's contextvar scope."""

    async def downstream(scope, receive, send):
        await executor.execute(_ctx(metadata or {}), _FakeQueue())

    mw = auth_mod.TokenAuthMiddleware(downstream, token="shared-secret")
    scope = {
        "type": "http",
        "path": "/",
        "headers": [(b"authorization", f"Bearer {supplied_token}".encode())],
        "query_string": b"",
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    await mw(scope, receive, send)


async def test_verified_jwt_flows_through_the_middleware(tmp_path, monkeypatch):
    executor, client = _executor()
    token = _jwt(monkeypatch, tmp_path, "alice")
    await _through_middleware(executor, token)
    assert client.session_keys == ["shim-shared-direct-alice"]


async def test_shared_token_through_the_middleware_takes_fallback(tmp_path, monkeypatch):
    executor, client = _executor()
    await _through_middleware(executor, "shared-secret")
    assert client.session_keys == ["shim-shared-direct"]


async def test_subject_does_not_leak_across_concurrent_requests(tmp_path, monkeypatch):
    """The isolation gate: two overlapping requests — different JWT subjects,
    forced to be in-flight simultaneously — must each key by their OWN subject.
    A process-global (rather than request-bound) subject would cross them."""
    exec_a, client_a = _executor()
    exec_b, client_b = _executor()
    tok_a = _jwt(monkeypatch, tmp_path, "alice")
    tok_b = _jwt(monkeypatch, tmp_path, "bob")

    barrier = asyncio.Barrier(2)

    # Make the executor's downstream pause mid-request so both are simultaneously
    # "inside" the middleware's contextvar scope before either finishes.
    orig_handle_a = exec_a.adapter.handle
    orig_handle_b = exec_b.adapter.handle

    async def gated(orig, req):
        await barrier.wait()
        return await orig(req)

    exec_a.adapter.handle = lambda req: gated(orig_handle_a, req)
    exec_b.adapter.handle = lambda req: gated(orig_handle_b, req)

    await asyncio.gather(
        _through_middleware(exec_a, tok_a),
        _through_middleware(exec_b, tok_b),
    )
    assert client_a.session_keys == ["shim-shared-direct-alice"]
    assert client_b.session_keys == ["shim-shared-direct-bob"]


async def test_the_subject_is_reset_after_each_request(tmp_path, monkeypatch):
    """After the middleware returns, the contextvar is back to None — the next
    request served on this task must not inherit the last caller's subject."""
    executor, _ = _executor()
    token = _jwt(monkeypatch, tmp_path, "alice")
    assert auth_mod.verified_subject() is None
    await _through_middleware(executor, token)
    assert auth_mod.verified_subject() is None
