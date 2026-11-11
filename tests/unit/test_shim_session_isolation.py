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

D119 (ws25-b4) follow-up: b3's key joined `platform` and the verified subject
with `-`, and the platform is caller-controlled, so a shared-token caller with
`platform="foundry-alice"` landed in verified alice's `foundry` session. The key
is now an injective, presence-preserving encoding of `(platform, subject)`; the
helper-level tests pin that encoding and the executor-chain tests reproduce the
collisions it closes (`build-notes/claude/ws25-b4-plan.md`).
"""

from __future__ import annotations

import asyncio
import types
from urllib.parse import unquote

import pytest
from google.protobuf import struct_pb2

from a2a.types import Message

from interop import delegation
from interop.models import AgentResponse
from interop.servers import a2a as a2a_mod
from interop.servers import auth as auth_mod
from interop.servers.a2a import AdapterExecutor
from platforms.agentforce import proxy as proxy_mod
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


def _ctx(metadata: dict, ctx_id: str = "ctx-generated-by-sdk", text: str = "hi"):
    """A minimal RequestContext: the SDK always populates context_id, which is
    the whole point — reuse mode must OVERRIDE it, not defer to `is None`.
    `text` is the user input, so a test can carry a delegation rider in it."""
    msg = Message()
    struct = struct_pb2.Struct()
    struct.update(metadata)
    msg.metadata.CopyFrom(struct)
    return types.SimpleNamespace(
        get_user_input=lambda: text,
        message=msg,
        context_id=ctx_id,
        task_id="task-1",
    )


@pytest.fixture(autouse=True)
def _fake_updater(monkeypatch):
    monkeypatch.setattr(a2a_mod, "TaskUpdater", _FakeUpdater)


@pytest.fixture(autouse=True)
def _no_twin_routing(monkeypatch):
    """Pin every request to the ONE recording client. Twin routing runs before
    the session cache, and each AgentforceClient caches its own sessions, so a
    key collision only shares a session when both platform values resolve to the
    same client — the default one whenever the platform's twin env is unset
    (and always for unmapped platforms). A shell that sourced .env would set
    SF_FOUNDRY_AGENT_ID and send `platform="foundry"` to a REAL
    AgentforceClient.from_env() twin instead of exercising the collision."""
    for var in set(proxy_mod.TWIN_ENV_BY_PLATFORM.values()):
        monkeypatch.delenv(var, raising=False)


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
    assert client.session_keys == ["shim:sub:alice:none", "shim:sub:bob:none"]


async def test_same_subject_reuses_one_session():
    executor, client = _executor()
    await _run_as_subject(executor, "alice")
    await _run_as_subject(executor, "alice")
    assert client.session_keys == ["shim:sub:alice:none", "shim:sub:alice:none"]


async def test_no_verified_subject_takes_the_platform_only_fallback():
    """The legacy shared service token verifies no subject, so it lands on the
    platform-only key — not on some other caller's per-subject session."""
    executor, client = _executor()
    await _run_as_subject(executor, None)
    assert client.session_keys == ["shim:shared:none"]


async def test_sdk_context_id_is_overridden_in_reuse_mode():
    """The dead-guard fix: even though the SDK populated context_id, reuse mode
    overrides it with the reuse key rather than keying by the SDK's id."""
    executor, client = _executor()
    await _run_as_subject(executor, "alice")
    assert client.session_keys[0] != "ctx-generated-by-sdk"
    assert client.session_keys[0] == "shim:sub:alice:none"


async def test_caller_asserted_verified_subject_is_overwritten():
    """A caller putting `verified_subject` in the message body must not steal
    another user's session — the executor overwrites it with the verified one."""
    executor, client = _executor()
    await _run_as_subject(executor, "alice", metadata={"verified_subject": "attacker"})
    assert client.session_keys == ["shim:sub:alice:none"]


async def test_caller_asserted_subject_dropped_when_none_verified():
    """With no verified subject, a caller-asserted one is DROPPED, not trusted —
    the request falls back to the platform-only key."""
    executor, client = _executor()
    await _run_as_subject(executor, None, metadata={"verified_subject": "attacker"})
    assert client.session_keys == ["shim:shared:none"]


# --- through the REAL TokenAuthMiddleware, and under concurrency -------------


def _jwt(monkeypatch, tmp_path, username: str) -> str:
    from interop import identity

    monkeypatch.setenv(identity.KEY_DIR_ENV, str(tmp_path / "keys"))
    return identity.issue_token(username, users={username: {"name": username, "role": "viewer"}})


async def _through_middleware(
    executor, supplied_token: str, metadata: dict | None = None, text: str = "hi"
):
    """The full chain: TokenAuthMiddleware verifies the token and its downstream
    app runs the real executor within the request's contextvar scope."""

    async def downstream(scope, receive, send):
        await executor.execute(_ctx(metadata or {}, text=text), _FakeQueue())

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
    assert client.session_keys == ["shim:sub:alice:none"]


async def test_shared_token_through_the_middleware_takes_fallback(tmp_path, monkeypatch):
    executor, client = _executor()
    await _through_middleware(executor, "shared-secret")
    assert client.session_keys == ["shim:shared:none"]


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
    assert client_a.session_keys == ["shim:sub:alice:none"]
    assert client_b.session_keys == ["shim:sub:bob:none"]


async def test_the_subject_is_reset_after_each_request(tmp_path, monkeypatch):
    """After the middleware returns, the contextvar is back to None — the next
    request served on this task must not inherit the last caller's subject."""
    executor, _ = _executor()
    token = _jwt(monkeypatch, tmp_path, "alice")
    assert auth_mod.verified_subject() is None
    await _through_middleware(executor, token)
    assert auth_mod.verified_subject() is None


def _http_scope(bearer: str) -> dict:
    return {
        "type": "http",
        "path": "/",
        "headers": [(b"authorization", f"Bearer {bearer}".encode())],
        "query_string": b"",
    }


async def _noop_receive():
    return {"type": "http.request", "body": b"", "more_body": False}


async def _noop_send(message):
    pass


async def test_background_task_reads_subject_from_its_snapshot(tmp_path, monkeypatch):
    """The a2a-sdk may dispatch the executor on a BACKGROUND task rather than
    awaiting it inline. `asyncio.create_task` snapshots the request's context —
    including the verified subject — so the child keys by the right caller even
    though the middleware has since reset the subject in the parent scope. The
    test creates the task inside the request scope but does NOT await it, lets the
    middleware unwind (asserting the parent is reset AND the child has not run
    yet), then runs the child: a process-global would read None here and mis-key
    to the platform-only fallback."""
    executor, client = _executor()
    token = _jwt(monkeypatch, tmp_path, "alice")
    holder: dict = {}

    async def downstream(scope, receive, send):
        holder["task"] = asyncio.create_task(executor.execute(_ctx({}), _FakeQueue()))

    mw = auth_mod.TokenAuthMiddleware(downstream, token="shared-secret")
    await mw(_http_scope(token), _noop_receive, _noop_send)

    assert auth_mod.verified_subject() is None  # the middleware reset the parent
    assert not holder["task"].done()  # the child has not read the subject yet
    await holder["task"]  # now it runs, against its own context snapshot
    assert client.session_keys == ["shim:sub:alice:none"]


async def test_jwt_and_shared_token_do_not_cross_concurrently(tmp_path, monkeypatch):
    """Concurrency across the two auth kinds: a verified JWT (subject alice) and
    the legacy shared service token (no subject) in flight simultaneously. A
    process-global subject would let the JWT's subject bleed into the shared
    caller's session key; the per-request contextvar keeps them apart."""
    exec_jwt, client_jwt = _executor()
    exec_shared, client_shared = _executor()
    tok = _jwt(monkeypatch, tmp_path, "alice")

    barrier = asyncio.Barrier(2)
    orig_jwt = exec_jwt.adapter.handle
    orig_shared = exec_shared.adapter.handle

    async def gated(orig, req):
        await barrier.wait()  # both requests parked here, mid-flight, at once
        return await orig(req)

    exec_jwt.adapter.handle = lambda req: gated(orig_jwt, req)
    exec_shared.adapter.handle = lambda req: gated(orig_shared, req)

    await asyncio.gather(
        _through_middleware(exec_jwt, tok),
        _through_middleware(exec_shared, "shared-secret"),
    )
    assert client_jwt.session_keys == ["shim:sub:alice:none"]
    assert client_shared.session_keys == ["shim:shared:none"]


# --- D119 (ws25-b4): the reuse key is injective over (platform, subject) ------
#
# `platform` is caller-controlled (delegation.platform_of returns metadata
# verbatim, or the rider-text match), so no character of it may act as a
# separator, and an absent platform must not equal any present one. The helper
# is read through the module at call time, not imported by name, so the
# executor-chain reds below stay visible before the helper exists.


def _shim_session_key(platform, subject):
    return proxy_mod._shim_session_key(platform, subject)


def _decode(key: str) -> tuple[str | None, str | None]:
    """Test-local LEFT INVERSE of the proxy's key: key -> (platform, subject).

    A function with a left inverse is injective, so if this round-trips every
    tuple, no two tuples share a key. Encoded components never contain `:`, so
    the four shapes are told apart by the tag and the field count."""

    def dec(field: str) -> str:
        return unquote(field, errors="surrogatepass")

    match key.split(":"):
        case ["shim", "shared", "none"]:
            return None, None
        case ["shim", "shared", "value", p]:
            return dec(p), None
        case ["shim", "sub", s, "none"]:
            return None, dec(s)
        case ["shim", "sub", s, "value", p]:
            return dec(p), dec(s)
    raise ValueError(f"not a shim session key shape: {key!r}")


# Every value here is an admissible platform (and, for the helper, subject):
# separators, the key's own tags, percent escapes and double escapes, the old
# `-` joiner, non-ASCII, and a lone surrogate (JSON can carry one).
_HOSTILE = [
    "",
    "direct",
    "none",
    "value",
    "shared",
    "sub",
    "shim",
    ":",
    "::",
    "%",
    "%3A",
    "%253A",
    "a-b",
    "b-c",
    "a:b",
    "a:value:b",
    "b:value:c",
    "x:sub:alice",
    "é",
    "\ud800",
]


def test_key_keeps_absent_platform_distinct_from_literal_direct():
    """H1 — an absent platform and the literal string "direct" are different
    inputs, so they get different keys (b3/rev 1 flattened both to "direct")."""
    assert _shim_session_key(None, "alice") != _shim_session_key("direct", "alice")
    assert _shim_session_key(None, None) != _shim_session_key("direct", None)


def test_key_pins_the_encoding_of_both_components():
    """H2 — exact literals. Leaving either component unencoded, encoding `:` but
    not `%`, widening the safe set, or collapsing presence fails a string."""
    assert _shim_session_key("p:%/x", "s:%/y") == "shim:sub:s%3A%25%2Fy:value:p%3A%25%2Fx"
    assert _shim_session_key("p:%/x", None) == "shim:shared:value:p%3A%25%2Fx"
    assert _shim_session_key(None, "alice") == "shim:sub:alice:none"
    assert _shim_session_key(None, None) == "shim:shared:none"


def test_literal_escape_does_not_alias_an_encoded_separator():
    """H3 — a literal "%3A" must not collide with an encoded ":", checked in each
    component separately (an encoder that rewrites `:` but passes `%` through
    would alias them)."""
    assert _shim_session_key("p", "a:b") != _shim_session_key("p", "a%3Ab")
    assert _shim_session_key("a:b", "s") != _shim_session_key("a%3Ab", "s")
    assert _shim_session_key("a:b", None) != _shim_session_key("a%3Ab", None)


def test_key_has_a_left_inverse_over_a_hostile_corpus():
    """H4 — injectivity and totality, checked rather than argued: every one of
    the (|C|+1)^2 = 441 (platform, subject) tuples over C ∪ {None} encodes
    without raising (the lone surrogate included) and decodes back to itself."""
    domain = [None, *_HOSTILE]
    pairs = [(p, s) for p in domain for s in domain]
    assert len(pairs) == 441
    keys = {}
    mismatches = []
    for p, s in pairs:
        key = _shim_session_key(p, s)
        keys[(p, s)] = key
        try:
            decoded = _decode(key)
        except ValueError as exc:
            decoded = exc
        if decoded != (p, s):
            mismatches.append(((p, s), key, decoded))
    assert not mismatches, f"{len(mismatches)} tuples do not round-trip: {mismatches[:5]}"
    assert len(set(keys.values())) == len(pairs)


async def test_shared_token_cannot_forge_a_subject_key_via_platform(tmp_path, monkeypatch):
    """E1 — through the REAL middleware: verified alice on platform `foundry`,
    and a shared-token caller choosing `platform="foundry-alice"` in metadata.
    b3 gave both `shim-shared-foundry-alice`, so the shared-token caller entered
    alice's Agentforce session. Scope: this guards the sub/shared namespace
    split; the encoding is pinned by H2-H4 and E4."""
    executor, client = _executor()
    token = _jwt(monkeypatch, tmp_path, "alice")
    await _through_middleware(executor, token, metadata={"delegation": {"platform": "foundry"}})
    await _through_middleware(
        executor, "shared-secret", metadata={"delegation": {"platform": "foundry-alice"}}
    )
    alice_key, forged_key = client.session_keys
    assert alice_key != forged_key
    # And the chain really delivered a verified alice and an unverified forger.
    assert alice_key == "shim:sub:alice:value:foundry"
    assert forged_key == "shim:shared:value:foundry-alice"


async def test_rider_text_platform_cannot_forge_a_subject_key(tmp_path, monkeypatch):
    """E2 — E1 with the platform delivered ONLY as rider text (the Foundry path:
    its A2A tool cannot attach metadata, so platform_of scans the message, whose
    `[\\w.-]+` admits `-`). Same scope note as E1."""
    executor, client = _executor()
    token = _jwt(monkeypatch, tmp_path, "alice")
    alice_msg, _ = delegation.delegate(
        "q", caller="foundry-agent", platform="foundry", inbound_depth=0
    )
    forged_msg, _ = delegation.delegate(
        "q", caller="foundry-agent", platform="foundry-alice", inbound_depth=0
    )
    await _through_middleware(executor, token, text=alice_msg)
    await _through_middleware(executor, "shared-secret", text=forged_msg)
    alice_key, forged_key = client.session_keys
    assert alice_key != forged_key
    assert alice_key == "shim:sub:alice:value:foundry"
    assert forged_key == "shim:shared:value:foundry-alice"


async def test_verified_subjects_cannot_collide_across_platforms():
    """E3 — two VERIFIED callers: subject `c` on platform `a-b` vs subject `b-c`
    on platform `a` (b3: both `shim-shared-a-b-c`). Under the fix this passes on
    subject-first ordering alone, so it guards the ordering."""
    executor, client = _executor()
    await _run_as_subject(executor, "c", metadata={"delegation": {"platform": "a-b"}})
    await _run_as_subject(executor, "b-c", metadata={"delegation": {"platform": "a"}})
    first, second = client.session_keys
    assert first != second


@pytest.mark.parametrize(
    "one, other",
    [
        # Codex's pair: collides only for a variant with no `value:` prefix.
        (("a:b", "c"), ("a", "b:c")),
        # Collides for a tags-only, unencoded variant: `shim:sub:a:value:b:value:c`.
        (("a:value:b", "c"), ("a", "b:value:c")),
    ],
    ids=["codex-pair", "value-prefix-pair"],
)
async def test_verified_subjects_cannot_collide_through_colons_in_metadata(one, other):
    """E4 — verified <-> verified through colon-bearing values in
    metadata.delegation.platform (verbatim; the rider regex never applies).
    Green against b3 (its key has no `:` structure); red against a tags-only
    variant of the D119 format, which is the point: it requires the encoding."""
    executor, client = _executor()
    for subject, platform in (one, other):
        await _run_as_subject(executor, subject, metadata={"delegation": {"platform": platform}})
    first, second = client.session_keys
    assert first != second


async def test_absent_platform_is_not_the_literal_direct_platform():
    """E5 — H1 end to end, for a verified caller and for a shared-token caller:
    no delegation metadata vs `delegation.platform="direct"` are distinct keys
    (b3: `shim-shared-direct-alice` / `shim-shared-direct` for both)."""
    executor, client = _executor()
    await _run_as_subject(executor, "alice")
    await _run_as_subject(executor, "alice", metadata={"delegation": {"platform": "direct"}})
    await _run_as_subject(executor, None)
    await _run_as_subject(executor, None, metadata={"delegation": {"platform": "direct"}})
    alice_absent, alice_direct, shared_absent, shared_direct = client.session_keys
    assert alice_absent != alice_direct
    assert shared_absent != shared_direct
