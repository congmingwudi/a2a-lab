"""Data 360 Agentic Memory and Context — the lab's one REST client (plan/18, D83).

Every lab seam that gives an agent memory goes through this module: the bridge's
`/memory/turn` route for Agentforce (Phase 2 — Apex cannot hold the tenant
credential, so it asks the bridge), and later the generic `MemoryAdapter` for
the platforms with no native SDK adapter (Phase 3). Same wire shapes as the
`agentic-context` SDK's AsyncSessionManager, so a turn written here and a turn
written by an SDK adapter land in the same conversation model.

Auth is the two-leg exchange the memory ECA was built for (plan/18 Phase 0,
`scripts/setup_jwt_eca.py` preset `memory`): an RS256 JWT-bearer grant as the
run-as user yields a core token, and `/services/a360/token` swaps it — bound to
the calling agent's API name (`aid`) — for a tenant token plus the tenant host.
Tokens are cached per governed person + agent pair until shortly before expiry.

Every call is a trace Hop with the raw request and response bodies, target
`agentic-memory`: memory is new wire, and the lab records wire (CLAUDE.md).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from interop.trace import Hop

DATA_SPACE_ENV = "A2ALAB_MEMORY_DATA_SPACE"
OWNER_TYPE = "individual"  # the SDK's default; open question 3 in plan/18
KEY_ENV = "A2ALAB_MEMORY_JWT_KEY"  # PEM text — the hosted bridge's secret carries it
KEY_FILE = "memory_jwt_private.pem"  # local: under A2ALAB_JWT_DIR (.a2alab)
TARGET = "agentic-memory"
GENERATION_CATEGORY = "sys_PROFILE_FACT"
GENERATION_RE = re.compile(r"^v1:(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z):([0-9a-f]{24})$")
GENERATION_TOKEN_RE = re.compile(r"v1:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z:[0-9a-f]{24}")


class MemoryCallError(RuntimeError):
    """A memory call refused; structured fields retain the wire refusal."""

    def __init__(self, message: str, *, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body

    @property
    def is_html_403(self) -> bool:
        text = self.body if isinstance(self.body, str) else ""
        return self.status == 403 and "<h1>403 Forbidden</h1>" in text and "<html" in text.lower()


class StaleMemoryGeneration(MemoryCallError):
    """A chat was created under an older customer-memory generation.

    ``error_code``/``http_status`` are its wire shape: the REST face answers 409
    with this code, so a caller behind a hop (the hosted console) can still tell
    a stale chat from a failure and offer to fork it (ws27-b2 R2-1, D107)."""

    error_code = "stale_memory_generation"
    http_status = 409


def data_space() -> str:
    """Configured Data 360 data space; never guess which environment owns memory."""
    value = os.environ.get(DATA_SPACE_ENV, "").strip()
    if not value:
        raise MemoryCallError(f"memory data space is not configured: set {DATA_SPACE_ENV}")
    return value


@dataclass(frozen=True)
class MemoryEntry:
    memory_id: str
    content: str


@dataclass(frozen=True)
class MemoryListing:
    entries: tuple[MemoryEntry, ...]
    total_count: int
    reported_total_count: int | None = None

    @property
    def complete(self) -> bool:
        return len(self.entries) == self.total_count


def _now(back_ms: int = 0) -> str:
    """The SDK's timestamp shape: millisecond precision, literal Z."""
    at = datetime.now(timezone.utc) - timedelta(milliseconds=back_ms)
    return at.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def owner_conversation(owner_id: str) -> str:
    """The owner's ONE long-lived conversation; each chat is a session inside it.

    GetContext summarises the conversation it is asked about and nothing else
    (2026-10-28, plan/03): a fresh conversation per chat — the shape until D94 —
    meant every new chat read an empty one. Keyed on the owner alone, never the
    calling agent, so every agent sharing memory reads the same summary (the
    internal demo kit's `conversationIdFor`, after its `Conversation_Prefix__c`).
    Hashed so a username never becomes a tenant id.
    """
    return f"a2alab-o-{hashlib.sha256(owner_id.encode()).hexdigest()[:24]}"


def case_conversation(owner_id: str, scenario: str, case_key: str) -> str:
    """One opaque episodic conversation for an owner's named experiment case.

    The tenant still receives the verified owner separately. Hashing all three
    inputs prevents a browser-selected case label (or username) from becoming a
    tenant identifier and keeps equal case labels isolated by owner/scenario.
    """
    if not owner_id or not scenario or not case_key.strip():
        raise ValueError("owner_id, scenario and a non-empty case_key are required")
    digest = hashlib.sha256(f"{owner_id}\0{scenario}\0{case_key.strip()}".encode()).hexdigest()
    return f"a2alab-c-{digest[:24]}"


def new_generation_marker() -> str:
    return f"v1:{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}:{uuid.uuid4().hex[:24]}"


def select_generation(listing: MemoryListing) -> tuple[str | None, bool]:
    """Return newest valid marker and whether malformed markers were present."""
    candidates = [entry.content for entry in listing.entries if entry.content.startswith("v1:")]
    valid = [content for content in candidates if GENERATION_RE.fullmatch(content)]
    malformed = len(valid) != len(candidates)
    return (max(valid) if valid else None), malformed


def is_generation_entry(entry: MemoryEntry) -> bool:
    """Reserved fact rows used only as experiment-control markers."""
    return entry.content.startswith("v1:")


def generation_conversation(owner_id: str, marker: str) -> str:
    if not GENERATION_RE.fullmatch(marker):
        raise ValueError("invalid customer-memory generation marker")
    return case_conversation(owner_id, "customer-memory", marker)


def _conversation_id(owner_id: str, conversation_id: str | None) -> str:
    return conversation_id or owner_conversation(owner_id)


# Order matters: tokens and card numbers before phone numbers, so a long digit
# run is not half-matched as a phone. Ported from the demo kit's
# AgentMemoryClient.REDACTIONS: what extraction never needs to learn.
REDACTIONS = [
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"), "[REDACTED_TOKEN]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [REDACTED_TOKEN]"),
    (
        re.compile(r"(?i)\b(password|passcode|pwd|api[ _-]?key|secret|token)(\s*(?:is|:|=)\s*)\S+"),
        r"\1\2[REDACTED]",
    ),
    (re.compile(r"\b[A-Za-z0-9_-]{32,}\b"), "[REDACTED_TOKEN]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    (re.compile(r"\b\d{4}([ -])\d{4}\1\d{4}\1\d{1,7}\b"), "[REDACTED_CARD]"),
    (re.compile(r"\b\d{4}([ -])\d{6}\1\d{5}\b"), "[REDACTED_CARD]"),
    (
        re.compile(r"(?i)\b(card|visa|mastercard|amex|credit|debit)(\D{0,20})\d{13,19}\b"),
        r"\1\2[REDACTED_CARD]",
    ),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
    (re.compile(r"(?:\+?1[ .-]?)?\(?\b\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b"), "[REDACTED_PHONE]"),
]


def redact(text: str) -> str:
    """Secrets and contact details out of a message before the tenant stores it:
    extraction would otherwise happily save a pasted token as a "fact"."""
    for pattern, repl in REDACTIONS:
        text = pattern.sub(repl, text)
    return text


URL_RE = re.compile(r"https?://([^\s/]+)(/[^\s]*)?", re.IGNORECASE)
API_PATH_RE = re.compile(r"(?:/api)?/v(\d+)/([A-Za-z0-9_.\-/]+)")


def normalize_ingest_content(text: str) -> str:
    """Readable retry form for the tenant edge's URL/path content refusal (D107)."""

    def url(match: re.Match[str]) -> str:
        path = (match.group(2) or "").strip("/`).,;:")
        return " ".join(part for part in (match.group(1), path.replace("/", " ")) if part)

    text = URL_RE.sub(url, text)
    return API_PATH_RE.sub(
        lambda m: f"API endpoint v{m.group(1)} {m.group(2).replace('/', ' ')}", text
    )


def entry_id(session_id: str, kind: str, text: str) -> str:
    """The same message gets the same id, so a re-send is a no-op.

    The tenant dedupes entries by id (two posts of one id: both 202, one entry
    stored, 2026-10-15), and the Agentforce hooks re-send: before_reasoning
    fires once per reasoning PASS, not per turn, so a multi-pass turn ingested
    its user message twice (2 of 14 turns, plan/03-results.md). The cost is
    that a user saying the identical thing twice in one chat is stored once —
    harmless to what extraction learns from it. Keyed on the SESSION, not the
    owner's conversation: the same question in two chats is two entries.
    """
    digest = hashlib.sha256(f"{session_id}\0{kind}\0{text}".encode()).hexdigest()
    return f"{kind[0]}-{digest[:32]}"


def _signing_key() -> str:
    pem = os.environ.get(KEY_ENV)
    if pem:
        return pem.replace("\\n", "\n")
    path = Path(os.environ.get("A2ALAB_JWT_DIR", ".a2alab")) / KEY_FILE
    if not path.exists():
        raise MemoryCallError(f"no memory signing key: set {KEY_ENV} or create {path}")
    return path.read_text()


@dataclass
class _Token:
    access_token: str
    base_url: str
    expires_at: float


class MemoryClient:
    """Async client for the tenant's `/api/v1/agentic/*` runtime APIs."""

    def __init__(self, http: httpx.AsyncClient | None = None, *, source: str = "bridge"):
        self._http = http or httpx.AsyncClient(timeout=30)
        self._tokens: dict[tuple[str, str], _Token] = {}
        self.source = source

    async def aclose(self) -> None:
        await self._http.aclose()

    async def preflight_agent(self, agent_id: str, owner_id: str) -> None:
        """Prove this runtime can mint a Data 360 token for ``agent_id``.

        Supplier memory calls this inside the hosted console task before the
        three paid specialist legs, so a missing task secret or unregistered
        aid is visible before the business run starts (D102).
        """
        await self._token(agent_id, owner_id)

    # ------------------------------------------------------------ auth

    async def _token(self, agent_id: str, owner_id: str) -> _Token:
        if not owner_id:
            raise MemoryCallError("a governed memory token requires a non-empty owner xid")
        cache_key = (owner_id, agent_id)
        cached = self._tokens.get(cache_key)
        if cached and cached.expires_at > time.time() + 60:
            return cached
        import jwt as pyjwt

        client_id = os.environ.get("SF_CLIENT_ID_MEMORY")
        run_as = os.environ.get("SF_MEMORY_RUNAS_USER")
        domain = os.environ.get("SF_MY_DOMAIN", "").rstrip("/")
        if not (client_id and run_as and domain):
            raise MemoryCallError(
                "SF_CLIENT_ID_MEMORY / SF_MEMORY_RUNAS_USER / SF_MY_DOMAIN not set"
            )
        domain = domain if domain.startswith("https://") else f"https://{domain}"
        assertion = pyjwt.encode(
            {
                "iss": client_id,
                "sub": run_as,
                "aud": os.environ.get("SF_LOGIN_URL", "https://login.salesforce.com"),
                "exp": int(time.time()) + 180,
            },
            _signing_key(),
            algorithm="RS256",
        )
        r = await self._http.post(
            f"{domain}/services/oauth2/token",
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                  "assertion": assertion},
        )  # fmt: skip
        if r.status_code != 200:
            raise MemoryCallError(
                f"JWT-bearer grant refused (HTTP {r.status_code}): {r.text[:200]}"
            )
        core = r.json()
        r = await self._http.post(
            f"{core['instance_url']}/services/a360/token",
            data={
                "grant_type": "urn:salesforce:grant-type:external:cdp",
                "subject_token": core["access_token"],
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                # a360 refuses an UNREGISTERED aid with access_denied (plan/18)
                "aid": agent_id,
                # The governance guide says reads fail closed unless the token is
                # bound to the end user (Individual id, or User id for employees).
                # Measured 2026-11-06 (D115): for our is-internal caller the Memory
                # API does NOT check xid against profile_id, so the owner we send
                # is the real isolation boundary. Keep xid = owner anyway.
                "xid": owner_id,
            },
        )
        cdp = r.json() if r.status_code == 200 else {}
        if not cdp.get("access_token"):
            raise MemoryCallError(
                f"a360 CDP exchange refused (HTTP {r.status_code}): {r.text[:200]}"
            )
        host = cdp["instance_url"]
        tok = _Token(
            access_token=cdp["access_token"],
            base_url=host if host.startswith("https://") else f"https://{host}",
            expires_at=time.time() + int(cdp.get("expires_in") or 3600),
        )
        self._tokens[cache_key] = tok
        return tok

    # ------------------------------------------------------------ wire

    async def _call(
        self,
        trace_id: str,
        agent_id: str,
        method: str,
        path: str,
        *,
        owner_id: str,
        headers: dict[str, str],
        json_body: dict | None = None,
        params: dict | None = None,
        ok: tuple[int, ...] = (200, 201, 202),
        ref_key: str | None = None,
    ) -> tuple[int, Any]:
        tok = await self._token(agent_id, owner_id)
        detail = f"{method} {path}"
        # Saved facts/preferences are read once per category per sharing agent.
        # Put both dimensions in the hop label so the trace does not present
        # four semantically distinct reads as anonymous duplicates.
        if path == "/api/v1/agentic/memory" and params:
            detail += f" · {params.get('category', 'unknown')} · {agent_id}"
        with Hop(
            trace_id,
            source=self.source,
            target=TARGET,
            protocol="rest",
            transport_detail=detail,
            request_payload={"params": params, "body": json_body} if params else json_body,
        ) as hop:
            r = await self._http.request(
                method,
                f"{tok.base_url}{path}",
                headers={"authorization": f"Bearer {tok.access_token}", **headers},
                json=json_body,
                params=params,
            )
            body: Any = (
                r.json() if r.content and "json" in r.headers.get("content-type", "") else r.text
            )
            hop.response_payload = {"status": r.status_code, "body": body}
            if ref_key and isinstance(body, dict) and body.get(ref_key):
                hop.platform_ref = str(body[ref_key])
            if r.status_code not in ok:
                raise MemoryCallError(
                    f"{detail} refused (HTTP {r.status_code}): {str(body)[:200]}",
                    status=r.status_code,
                    body=body,
                )
        return r.status_code, body

    def _post_headers(self, owner_id: str, agent_id: str) -> dict[str, str]:
        return {"dataspace": data_space(), "owner_id": owner_id, "agent_id": agent_id}

    # ------------------------------------------------------------ verbs

    async def start_conversation(
        self,
        trace_id: str,
        *,
        agent_id: str,
        owner_id: str,
        session_id: str | None = None,
        conversation_id: str | None = None,
    ) -> str:
        """The owner's conversation (if new) + this chat's session + both
        participants: the SDK's lifecycle, minus its one-id-for-both (D94).

        409 is success at every step, so an owner's second chat and a retried
        first turn are both harmless. Returns the session id.
        """
        cid = _conversation_id(owner_id, conversation_id)
        sid = session_id or f"a2alab-{uuid.uuid4().hex[:12]}"
        h = self._post_headers(owner_id, agent_id)
        now = _now()
        base = "/api/v1/agentic/conversations"
        await self._call(trace_id, agent_id, "POST", base, owner_id=owner_id,
                         headers=h, ok=(200, 201, 202, 409),
                         json_body={"conversationId": cid, "conversationOwnerId": owner_id,
                                    "ownerType": OWNER_TYPE, "name": cid,
                                    "channel": {"type": "web", "id": "a2alab"}})  # fmt: skip
        await self._call(trace_id, agent_id, "POST", f"{base}/{cid}/sessions",
                         owner_id=owner_id, headers=h,
                         ok=(200, 201, 202, 409),
                         json_body={"sessionId": sid, "conversationOwnerId": owner_id,
                                    "ownerType": OWNER_TYPE,
                                    "participants": [{"id": f"{owner_id}-participant",
                                                      "type": OWNER_TYPE, "role": "owner",
                                                      "startTime": now, "orgId": "default"}]})  # fmt: skip
        await self._call(trace_id, agent_id, "POST",
                         f"{base}/{cid}/sessions/{sid}/participants", headers=h,
                         owner_id=owner_id,
                         ok=(200, 201, 202, 409),
                         json_body={"participants": [{"id": f"{agent_id}-participant",
                                                      "type": "agent", "role": "participant",
                                                      "startTime": now}]})  # fmt: skip
        return sid

    async def ingest(
        self,
        trace_id: str,
        *,
        agent_id: str,
        owner_id: str,
        session_id: str,
        user_message: str | None = None,
        agent_response: str | None = None,
        conversation_id: str | None = None,
    ) -> bool:
        """One turn's Message entries; either side may be absent.

        The reply goes FIRST, a millisecond earlier: beside a user message it
        is the reply that message was answering (D87). No Agentforce hook can
        supply it (D89), so today only out-of-band callers send one. Entry ids
        are content-derived (`entry_id`), so a hook firing per reasoning pass
        may re-send. Both sides are `redact`ed first.
        """
        turn = uuid.uuid4().hex
        entries = []
        for kind, pid, text, back_ms in (
            ("agent_response", f"{agent_id}-participant", agent_response, 1),
            ("user_message", f"{owner_id}-participant", user_message, 0),
        ):
            if text:
                text = redact(text)
                entries.append({"id": entry_id(session_id, kind, text),
                                "participantId": pid, "type": kind,
                                "timestamp": _now(back_ms), "category": "Message",
                                "interactionId": turn, "content": text})  # fmt: skip
        if not entries:
            return False
        cid = _conversation_id(owner_id, conversation_id)
        body = {"sessionId": session_id, "conversationOwnerId": owner_id, "entries": entries}
        try:
            await self._call(trace_id, agent_id, "POST",
                             f"/api/v1/agentic/conversations/{cid}/entries",
                             owner_id=owner_id,
                             headers=self._post_headers(owner_id, agent_id), json_body=body)  # fmt: skip
            return False
        except MemoryCallError as exc:
            if not exc.is_html_403:
                raise
        normalized = []
        for entry in entries:
            content = normalize_ingest_content(entry["content"])
            normalized.append({**entry, "content": content,
                               "id": entry_id(session_id, entry["type"], content)})  # fmt: skip
        await self._call(trace_id, agent_id, "POST",
                         f"/api/v1/agentic/conversations/{cid}/entries",
                         owner_id=owner_id, headers=self._post_headers(owner_id, agent_id),
                         json_body={**body, "entries": normalized})  # fmt: skip
        return True

    async def get_context(
        self,
        trace_id: str,
        *,
        agent_id: str,
        owner_id: str,
        conversation_id: str | None = None,
    ) -> dict:
        # ctx-agent-id is what the read authorizes on: without it every read
        # 403s with an empty body, the agent_id param notwithstanding. The SDK
        # omits it; the product's QA fit tests (a360-quality/jarlsberg-fit-tests,
        # AgenticContextApi) send it, and that is what works (2026-10-13).
        # The response's contextId is the service's id for this one read ("use
        # for tracing and debugging", the API reference), so it becomes the
        # hop's platform_ref: the handle to quote when a read comes back empty.
        # It reads the OWNER's conversation — the summary of every earlier chat
        # (D94); asked about a conversation with no history it is 200 + empty.
        _, body = await self._call(
            trace_id, agent_id, "GET", "/api/v1/agentic/context", owner_id=owner_id,
            ref_key="contextId",
            headers={"ctx-agent-id": agent_id, "ctx-dataspace-ds_name": data_space()},
            params={"conversation_id": _conversation_id(owner_id, conversation_id),
                    "owner_id": owner_id,
                    "agent_id": agent_id, "owner_type": OWNER_TYPE},
        )  # fmt: skip
        return body if isinstance(body, dict) else {}

    async def close_session(
        self,
        trace_id: str,
        *,
        agent_id: str,
        owner_id: str,
        session_id: str,
        conversation_id: str | None = None,
    ) -> None:
        """Close the session — the trigger for extraction (2026-10-27: facts
        landed within 6 min of a close; owners never closed had none). A closed
        session still takes entries and re-closes (202 both), so a seam with no
        end-of-chat signal closes after every turn."""
        await self._call(
            trace_id, agent_id, "POST",
            f"/api/v1/agentic/conversations/{_conversation_id(owner_id, conversation_id)}"
            f"/sessions/{session_id}/close",
            owner_id=owner_id,
            headers=self._post_headers(owner_id, agent_id), json_body={},
        )  # fmt: skip

    async def list_memories(
        self, trace_id: str, *, agent_id: str, owner_id: str, category: str
    ) -> MemoryListing:
        """Read one customer-scoped semantic-memory category.

        ``agent_id`` identifies the registered memory service used for this
        governed request; it is not a platform-specific storage partition.
        """
        _, body = await self._call(
            trace_id, agent_id, "GET", "/api/v1/agentic/memory", owner_id=owner_id,
            headers={"agent_id": agent_id},
            params={"profile_id": owner_id, "profile_type": OWNER_TYPE, "category": category},
        )  # fmt: skip
        memories = body.get("memories", []) if isinstance(body, dict) else []
        # Keep blank-content rows: they still have governed memory ids and must
        # remain selectable/deletable by the management saga. Prompt rendering
        # filters their empty content separately.
        entries = tuple(
            MemoryEntry(str(m.get("memoryId") or ""), str(m.get("content") or "").strip())
            for m in memories
            if str(m.get("memoryId") or "")
        )
        # The published Read Memories contract returns ``memories[]`` and does
        # not define pagination or ``totalCount``. The tenant did include that
        # extra field during D107 acceptance, where it matched the row count,
        # but later returned 1 beside 22 facts and 0 beside one preference.
        # Treat an under-count as non-contract metadata: every returned id is
        # still governed and deletable. A value GREATER than the returned rows
        # remains a useful conservative signal and keeps destructive reset
        # disabled in case a future tenant version introduces paging.
        reported = None
        try:
            if isinstance(body, dict) and body.get("totalCount") is not None:
                reported = max(0, int(body["totalCount"]))
        except (TypeError, ValueError):
            pass
        effective_total = max(len(entries), reported or 0)
        return MemoryListing(entries, effective_total, reported)

    async def create_memories(
        self,
        trace_id: str,
        *,
        agent_id: str,
        owner_id: str,
        category: str,
        contents: list[str],
    ) -> MemoryListing:
        if not contents:
            return MemoryListing((), 0)
        _, body = await self._call(
            trace_id,
            agent_id,
            "POST",
            "/api/v1/agentic/memory",
            owner_id=owner_id,
            headers={"agent_id": agent_id},
            params={"profile_id": owner_id, "profile_type": OWNER_TYPE, "category": category},
            json_body={"memories": [{"content": value} for value in contents]},
        )
        memories = body.get("memories", []) if isinstance(body, dict) else []
        entries = tuple(MemoryEntry(str(m.get("memoryId") or ""), str(m.get("content") or ""))
                        for m in memories)  # fmt: skip
        return MemoryListing(entries, len(entries))

    async def delete_memories(
        self,
        trace_id: str,
        *,
        agent_id: str,
        owner_id: str,
        category: str,
        memory_ids: list[str],
    ) -> tuple[str, ...]:
        if not memory_ids:
            return ()
        _, body = await self._call(
            trace_id,
            agent_id,
            "DELETE",
            "/api/v1/agentic/memory",
            owner_id=owner_id,
            headers={"agent_id": agent_id},
            params={"profile_id": owner_id, "profile_type": OWNER_TYPE, "category": category},
            json_body={"memories": [{"memoryId": value} for value in memory_ids]},
        )
        memories = body.get("memories", []) if isinstance(body, dict) else []
        return tuple(str(m.get("memoryId")) for m in memories if m.get("memoryId"))


SAVED_CATEGORIES = {"sys_PROFILE_FACT": "saved fact", "sys_PROFILE_PREFERENCES": "saved preference"}
MAX_SAVED = 20


async def saved_memories(
    mem: MemoryClient, trace_id: str, *, agent_id: str, owner_id: str
) -> tuple[str, list[str]]:
    """Customer facts/preferences from one shared memory service, fail-open."""
    asks = [(agent_id, category) for category in SAVED_CATEGORIES]
    results = await asyncio.gather(
        *(mem.list_memories(trace_id, agent_id=a, owner_id=owner_id, category=c) for a, c in asks),
        return_exceptions=True,
    )
    lines: list[str] = []
    errors: list[str] = []
    seen: set[str] = set()
    for (_, cat), got in zip(asks, results):
        if isinstance(got, MemoryCallError):
            errors.append(str(got))
            continue
        if isinstance(got, BaseException):
            raise got
        entries = got.entries if isinstance(got, MemoryListing) else got
        for item in entries:
            text = item.content if isinstance(item, MemoryEntry) else str(item)
            if not text.strip() or text.startswith("v1:"):
                continue
            if text.lower() not in seen and len(lines) < MAX_SAVED:
                seen.add(text.lower())
                lines.append(f"- {SAVED_CATEGORIES[cat]}: {text}")
    return "\n".join(lines), errors


async def open_and_read(
    mem: MemoryClient,
    trace_id: str,
    *,
    agent_id: str,
    owner_id: str,
    session_id: str | None = None,
    start: bool | None = None,
    read: bool = True,
    conversation_id: str | None = None,
    include_saved_memories: bool = True,
    include_profile_context: bool = True,
) -> tuple[str | None, str, list[str]]:
    """The read half of a turn, fail-open: (session_id, context text, errors).

    Opens the session first when `start` (default: only when there is no id
    yet — a caller with a derived id passes start=True, and 409 is success),
    then reads the owner's context. A refusal is an entry in `errors`, never an
    exception: every seam that calls this (the bridge's /memory/turn,
    `MemoryAdapter`) degrades a memory outage to a no-memory turn instead of
    failing the user's turn.
    """
    ids = {"agent_id": agent_id, "owner_id": owner_id}
    errors: list[str] = []
    sid = session_id
    try:
        if not sid if start is None else start:
            sid = await mem.start_conversation(
                trace_id, session_id=sid, conversation_id=conversation_id, **ids
            )
    except MemoryCallError as exc:
        errors.append(str(exc))
    context = ""
    profile_ids: set[str] = set()
    if sid and read:
        try:
            body = await mem.get_context(trace_id, conversation_id=conversation_id, **ids)
            profile_ids = graph_profile_ids(body)
            context = render_context(body, include_profile=include_profile_context)
        except MemoryCallError as exc:
            errors.append(str(exc))
    if read and include_saved_memories:
        # Owner-keyed, so it needs no session — it answers even when opening one failed.
        saved, saved_errors = await saved_memories(mem, trace_id, **ids)
        errors += saved_errors
        context = "\n".join(part for part in (context, saved) if part)
    # Last, over the combined text: a saved memory can quote the ids as well as the graph.
    return sid, mask_profile_ids(mask_owner(context, owner_id), profile_ids), errors


async def write_turn(
    mem: MemoryClient,
    trace_id: str,
    *,
    agent_id: str,
    owner_id: str,
    session_id: str,
    user_message: str | None = None,
    agent_response: str | None = None,
    conversation_id: str | None = None,
) -> tuple[list[str], bool]:
    """The write half of a turn, fail-open: the errors, if any.

    Independent of the read on purpose: GetContext 403'd for every
    conversation while writes 2xx'd (2026-10-13), and a refused read must not
    cost the turn its ingest, or memory never fills in. Every write ends in a
    close, because the close is what triggers extraction, and no seam the lab
    has gets an end-of-chat signal (Agent API, A2A and MCP turns just stop).
    """
    ids = {"agent_id": agent_id, "owner_id": owner_id, "session_id": session_id}
    try:
        normalized = await mem.ingest(trace_id, user_message=user_message,
                                      agent_response=agent_response,
                                      conversation_id=conversation_id, **ids)  # fmt: skip
        if user_message or agent_response:
            await mem.close_session(trace_id, conversation_id=conversation_id, **ids)
    except MemoryCallError as exc:
        return [str(exc)], False
    return [], bool(normalized)


# Response envelope, not memory: ids and a timestamp mean nothing to the model.
# contextId is kept on the trace hop instead (`get_context`).
ENVELOPE_KEYS = frozenset({"contextId", "sessionId", "createdAt"})


# D117: a context data graph attached to the registration (the real-time
# Customer360 graph) is served as a `sourceType: Profile` userContext entry —
# the same tag curated profile facts carry. The two differ in shape: curated
# facts are upper-case memory-category tables (`SYS_PROFILE_FACT[1]{CREATED,
# VALUE}`), a graph record is a TOON object rooted on the unified individual
# (`UnifiedLink…[n]:` / `Id: <unified id>`).
_GRAPH_RECORD_RE = re.compile(r"(?m)^(?:UnifiedLink\w*\[\d+\]:|Id: [0-9a-fA-F]{32}\s*$)")
_MEMORY_TABLE_RE = re.compile(r"(?m)^[A-Z][A-Z0-9_]*\[\d+\]\{")
# A record's TOON size is bounded by the graph's own node limits: the profile (~800
# chars) plus WS32's POS node, recency-capped at 10 orders of ~140 chars each once the
# decimals are compacted — about 2.2K. 4000 holds it whole with headroom for the
# email/link lists; cutting it mid-record would drop the record's tail, not its oldest data.
GRAPH_LIMIT = 4000
# Data 360 serialises NUMBER fields with 18-digit scale ("58.000000000000000000"). Pure
# noise to a model and ~20 chars per value; trimmed only where a field ends (comma,
# whitespace, end of entry), so timestamps such as "25.000Z" are untouched.
_PADDED_DECIMAL_RE = re.compile(r"(\d)(?:\.0+|(\.\d*?[1-9])0+)(?=[,\s]|$)")


def compact_graph_entry(entry: str) -> str:
    """A data-graph record with its padded decimals trimmed (58.000… → 58, 1.50… → 1.5)."""
    return _PADDED_DECIMAL_RE.sub(lambda m: m.group(1) + (m.group(2) or ""), entry)


def is_graph_record(item: Any) -> bool:
    """True for a userContext entry that is a data-graph record (D117), not a memory."""
    if not isinstance(item, dict) or str(item.get("sourceType") or "").casefold() != "profile":
        return False
    entry = item.get("entry")
    return (
        isinstance(entry, str)
        and bool(_GRAPH_RECORD_RE.search(entry))
        and not _MEMORY_TABLE_RE.search(entry)
    )


# WS32 item 12: the owner is the customer's CRM Contact id (D117), and the graph record
# repeats it (identity link, Individual, every POS row, ContactPointEmail, the
# transcript's participant label). It identifies nothing a model needs: the record's
# rows are already this customer's, and the console never sends the id to the browser
# (D118). Shown it, a model quotes it (hosted proof, plan/03-results.md).
OWNER_MASK = "this customer"
_SF_ID_RE = re.compile(r"[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?")


def mask_owner(text: str, owner_id: str) -> str:
    """`text` with a Salesforce-record owner id (and its 15-character form)
    replaced by `OWNER_MASK`. Whole tokens only, so another record's id that merely
    shares characters is untouched. Other owners — the supplier fan-out's lab
    persona (D102) — pass through: they are not a customer and carry no CRM id."""
    if not text or not _SF_ID_RE.fullmatch(owner_id or ""):
        return text
    forms = sorted({owner_id, owner_id[:15]}, key=len, reverse=True)
    pattern = r"(?<![A-Za-z0-9])(?:" + "|".join(map(re.escape, forms)) + r")(?![A-Za-z0-9])"
    return re.sub(pattern, OWNER_MASK, text)


# The record's unified-profile id — its root `Id:` and each link's `UnifiedRecordId` — is
# the other identifier a model offers when asked for "my id" (plan/03-results.md,
# 2026-11-10). Read off the graph record itself, so only ids the record names are masked
# (a trace id is also 32 hex); the trace layer keeps the raw wire bytes either way.
PROFILE_MASK = "this customer's profile"
_PROFILE_ID_RE = re.compile(
    r"(?m)(?:^Id: ([0-9a-fA-F]{32})\s*$|\bUnifiedRecordId: ([0-9A-Za-z]{16,64})\b)"
)


def graph_profile_ids(ctx: Any) -> set[str]:
    """The unified-profile ids named by the data-graph records in a GetContext body."""
    items = ctx.get("userContext") if isinstance(ctx, dict) else None
    return {
        a or b
        for item in (items if isinstance(items, list) else [])
        if is_graph_record(item)
        for a, b in _PROFILE_ID_RE.findall(str(item["entry"]))
    }


def mask_profile_ids(text: str, ids: set[str]) -> str:
    """`text` with each of `ids` (whole tokens) replaced by `PROFILE_MASK`."""
    if not text or not ids:
        return text
    alt = "|".join(map(re.escape, sorted(ids, key=len, reverse=True)))
    return re.sub(rf"(?<![A-Za-z0-9])(?:{alt})(?![A-Za-z0-9])", PROFILE_MASK, text)


# Masking alone is not enough: asked "what is my id?", a model quotes the mask label back
# as the id (hosted, 2026-11-11, plan/03-results.md). So the prompt that carries a masked
# context also says why the ids are missing and how to refer to the customer instead.
IDS_WITHHELD_NOTE = (
    "The customer's record ids (CRM Contact id, unified profile id) are withheld from you "
    'and appear as "this customer". Never present a placeholder as an id: if asked for '
    "an id, say you don't have access to it, and refer to the customer by first name when "
    'the context gives one, otherwise as "this customer".'
)


def withheld_note(context: str) -> str:
    """`IDS_WITHHELD_NOTE` plus a newline when `context` carries a mask, else ""."""
    return f"{IDS_WITHHELD_NOTE}\n" if context and OWNER_MASK in context else ""


def render_context(
    ctx: dict,
    limit: int = 4000,
    *,
    include_profile: bool = True,
    graph_limit: int = GRAPH_LIMIT,
) -> str:
    """The context payload as prompt text: every non-empty string leaf,
    labelled by its key path, minus the top-level envelope (`ENVELOPE_KEYS`).
    Deliberately generic, because the service has added fields before.

    Data-graph records (D117) render first, as `dataGraph`, on their own
    `graph_limit` budget — a long conversation history cannot crowd the
    customer's system-of-record out — and survive `include_profile=False`:
    they are read from Data 360 on every call, not re-materialized memory."""
    lines: list[str] = []
    ctx = {k: v for k, v in ctx.items() if k not in ENVELOPE_KEYS}
    graph: list[str] = []
    if isinstance(ctx.get("userContext"), list):
        graph = [
            compact_graph_entry(str(i["entry"]).strip())
            for i in ctx["userContext"]
            if is_graph_record(i)
        ]
        ctx["userContext"] = [i for i in ctx["userContext"] if not is_graph_record(i)]
    if not include_profile and isinstance(ctx.get("userContext"), list):
        # A reset rotates the conversation, but Data 360 can immediately
        # re-materialize profile facts from retained pre-reset history. Their
        # CREATED timestamps are new, so a timestamp comparison is not a safe
        # generation boundary. Keep this generation's Conversation context and
        # quarantine owner-global Profile rows instead.
        ctx["userContext"] = [
            item
            for item in ctx["userContext"]
            if not (
                isinstance(item, dict) and str(item.get("sourceType") or "").casefold() == "profile"
            )
        ]

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for item in node:
                walk(item, path)
        elif isinstance(node, str) and node.strip():
            # The tenant's supported Memory API categories are fixed. D107's
            # generation marker therefore lives in sys_PROFILE_FACT, but is
            # control state, never model context. Strip exact marker tokens
            # from GetContext as well as from saved_memories above.
            clean = GENERATION_TOKEN_RE.sub("", node).strip(" ,;:-")
            if clean:
                lines.append(f"- {path}: {clean}")

    walk(ctx, "")
    head = "\n".join(f"- dataGraph: {g}" for g in graph if g)[:graph_limit]
    body = "\n".join(lines)[:limit]
    return "\n".join(part for part in (head, body) if part)
