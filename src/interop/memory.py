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
Tokens are cached per agent until shortly before expiry.

Every call is a trace Hop with the raw request and response bodies, target
`agentic-memory`: memory is new wire, and the lab records wire (CLAUDE.md).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from interop.trace import Hop

DATA_SPACE = "default"
OWNER_TYPE = "individual"  # the SDK's default; open question 3 in plan/18
KEY_ENV = "A2ALAB_MEMORY_JWT_KEY"  # PEM text — the hosted bridge's secret carries it
KEY_FILE = "memory_jwt_private.pem"  # local: under A2ALAB_JWT_DIR (.a2alab)
TARGET = "agentic-memory"


class MemoryCallError(RuntimeError):
    """A memory call refused; the message names the leg and the HTTP status."""


def _now(back_ms: int = 0) -> str:
    """The SDK's timestamp shape: millisecond precision, literal Z."""
    at = datetime.now(timezone.utc) - timedelta(milliseconds=back_ms)
    return at.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def entry_id(conversation_id: str, kind: str, text: str) -> str:
    """The same message gets the same id, so a re-send is a no-op.

    The tenant dedupes entries by id (two posts of one id: both 202, one entry
    stored, 2026-10-15), and the Agentforce hooks re-send: before_reasoning
    fires once per reasoning PASS, not per turn, so a multi-pass turn ingested
    its user message twice (2 of 14 turns, plan/03-results.md). The cost is
    that a user saying the identical thing twice in one conversation is
    stored once — harmless to what extraction learns from it.
    """
    digest = hashlib.sha256(f"{conversation_id}\0{kind}\0{text}".encode()).hexdigest()
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
        self._tokens: dict[str, _Token] = {}
        self.source = source

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------ auth

    async def _token(self, agent_id: str) -> _Token:
        cached = self._tokens.get(agent_id)
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
        self._tokens[agent_id] = tok
        return tok

    # ------------------------------------------------------------ wire

    async def _call(
        self,
        trace_id: str,
        agent_id: str,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        json_body: dict | None = None,
        params: dict | None = None,
        ok: tuple[int, ...] = (200, 201, 202),
        ref_key: str | None = None,
    ) -> tuple[int, Any]:
        tok = await self._token(agent_id)
        detail = f"{method} {path}"
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
                raise MemoryCallError(f"{detail} refused (HTTP {r.status_code}): {str(body)[:200]}")
        return r.status_code, body

    def _post_headers(self, owner_id: str, agent_id: str) -> dict[str, str]:
        return {"dataspace": DATA_SPACE, "owner_id": owner_id, "agent_id": agent_id}

    # ------------------------------------------------------------ verbs

    async def start_conversation(
        self, trace_id: str, *, agent_id: str, owner_id: str, conversation_id: str | None = None
    ) -> str:
        """Conversation + session + both participants, the SDK's lifecycle.

        One id for both the conversation and its session (as the SDK does), and
        409 is success, so a retried first turn is harmless.
        """
        cid = conversation_id or f"a2alab-{uuid.uuid4().hex[:12]}"
        h = self._post_headers(owner_id, agent_id)
        now = _now()
        base = "/api/v1/agentic/conversations"
        await self._call(trace_id, agent_id, "POST", base, headers=h, ok=(200, 201, 202, 409),
                         json_body={"conversationId": cid, "conversationOwnerId": owner_id,
                                    "ownerType": OWNER_TYPE, "name": cid,
                                    "channel": {"type": "web", "id": "a2alab"}})  # fmt: skip
        await self._call(trace_id, agent_id, "POST", f"{base}/{cid}/sessions", headers=h,
                         ok=(200, 201, 202, 409),
                         json_body={"sessionId": cid, "conversationOwnerId": owner_id,
                                    "ownerType": OWNER_TYPE,
                                    "participants": [{"id": f"{owner_id}-participant",
                                                      "type": OWNER_TYPE, "role": "owner",
                                                      "startTime": now, "orgId": "default"}]})  # fmt: skip
        await self._call(trace_id, agent_id, "POST",
                         f"{base}/{cid}/sessions/{cid}/participants", headers=h,
                         ok=(200, 201, 202, 409),
                         json_body={"participants": [{"id": f"{agent_id}-participant",
                                                      "type": "agent", "role": "participant",
                                                      "startTime": now}]})  # fmt: skip
        return cid

    async def ingest(
        self,
        trace_id: str,
        *,
        agent_id: str,
        owner_id: str,
        conversation_id: str,
        user_message: str | None = None,
        agent_response: str | None = None,
    ) -> None:
        """One turn's Message entries; either side may be absent.

        The reply goes FIRST, a millisecond earlier: beside a user message it
        is the reply that message was answering (D87). No Agentforce hook can
        supply it (D89), so today only out-of-band callers send one. Entry ids
        are content-derived (`entry_id`), so a hook firing per reasoning pass
        may re-send.
        """
        turn = uuid.uuid4().hex
        entries = []
        for kind, pid, text, back_ms in (
            ("agent_response", f"{agent_id}-participant", agent_response, 1),
            ("user_message", f"{owner_id}-participant", user_message, 0),
        ):
            if text:
                entries.append({"id": entry_id(conversation_id, kind, text),
                                "participantId": pid, "type": kind,
                                "timestamp": _now(back_ms), "category": "Message",
                                "interactionId": turn, "content": text})  # fmt: skip
        if not entries:
            return
        await self._call(trace_id, agent_id, "POST",
                         f"/api/v1/agentic/conversations/{conversation_id}/entries",
                         headers=self._post_headers(owner_id, agent_id),
                         json_body={"sessionId": conversation_id, "conversationOwnerId": owner_id,
                                    "entries": entries})  # fmt: skip

    async def get_context(
        self, trace_id: str, *, agent_id: str, owner_id: str, conversation_id: str
    ) -> dict:
        # ctx-agent-id is what the read authorizes on: without it every read
        # 403s with an empty body, the agent_id param notwithstanding. The SDK
        # omits it; the product's QA fit tests (a360-quality/jarlsberg-fit-tests,
        # AgenticContextApi) send it, and that is what works (2026-10-13).
        # The response's contextId is the service's id for this one read ("use
        # for tracing and debugging", the API reference), so it becomes the
        # hop's platform_ref: the handle to quote when a read comes back empty.
        _, body = await self._call(
            trace_id, agent_id, "GET", "/api/v1/agentic/context", ref_key="contextId",
            headers={"ctx-agent-id": agent_id, "ctx-dataspace-ds_name": DATA_SPACE},
            params={"conversation_id": conversation_id, "owner_id": owner_id,
                    "agent_id": agent_id, "owner_type": OWNER_TYPE},
        )  # fmt: skip
        return body if isinstance(body, dict) else {}

    async def close_session(
        self, trace_id: str, *, agent_id: str, owner_id: str, conversation_id: str
    ) -> None:
        """Close the session — the trigger for extraction (2026-10-27: facts
        landed within 6 min of a close; owners never closed had none). A closed
        session still takes entries and re-closes (202 both), so a seam with no
        end-of-chat signal closes after every turn."""
        await self._call(
            trace_id, agent_id, "POST",
            f"/api/v1/agentic/conversations/{conversation_id}/sessions/{conversation_id}/close",
            headers=self._post_headers(owner_id, agent_id), json_body={},
        )  # fmt: skip

    async def list_memories(
        self, trace_id: str, *, agent_id: str, owner_id: str, category: str
    ) -> list[str]:
        """Extracted memories of one category, as stored under ONE agent — the
        store is per agent, so a shared read asks each agent (`saved_memories`).
        This is the read that still answers while GetContext returns no
        `userContext` (2026-10-27, plan/03)."""
        _, body = await self._call(
            trace_id, agent_id, "GET", "/api/v1/agentic/memory",
            headers={"agent_id": agent_id},
            params={"profile_id": owner_id, "profile_type": OWNER_TYPE, "category": category},
        )  # fmt: skip
        memories = body.get("memories", []) if isinstance(body, dict) else []
        return [m["content"].strip() for m in memories if str(m.get("content") or "").strip()]


SAVED_CATEGORIES = {"sys_PROFILE_FACT": "saved fact", "sys_PROFILE_PREFERENCES": "saved preference"}
SHARED_AGENTS_ENV = "A2ALAB_MEMORY_SHARED_AGENTS"  # comma-separated registration names
SHARED_AGENTS = ("A2ALab_Memory_Concierge", "A2ALab_Memory_Claude")  # the explicit-seam agents
MAX_SAVED = 20


def shared_agents(agent_id: str) -> list[str]:
    """The calling agent first, then every other agent whose memories it shares."""
    names = os.environ.get(SHARED_AGENTS_ENV)
    others = [n.strip() for n in names.split(",")] if names else list(SHARED_AGENTS)
    return list(dict.fromkeys([agent_id, *(n for n in others if n)]))


async def saved_memories(
    mem: MemoryClient, trace_id: str, *, agent_id: str, owner_id: str
) -> tuple[str, list[str]]:
    """Every shared agent's saved facts and preferences as prompt lines, fail-open.

    The cross-agent half of recall: GetContext serves the conversation summary
    for the conversation you pass, but facts are stored per agent (plan/18,
    after the governed-mcp-demo-kit's `memoryContextFor`). One refused agent
    costs only its own lines.
    """
    asks = [(a, c) for a in shared_agents(agent_id) for c in SAVED_CATEGORIES]
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
        for text in got:
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
    conversation_id: str | None = None,
    start: bool | None = None,
    read: bool = True,
) -> tuple[str | None, str, list[str]]:
    """The read half of a turn, fail-open: (conversation_id, context text, errors).

    Opens the conversation first when `start` (default: only when there is no
    id yet — a caller with a derived id passes start=True, and 409 is success),
    then reads its context. A refusal is an entry in `errors`, never an
    exception: every seam that calls this (the bridge's /memory/turn,
    `MemoryAdapter`) degrades a memory outage to a no-memory turn instead of
    failing the user's turn.
    """
    ids = {"agent_id": agent_id, "owner_id": owner_id}
    errors: list[str] = []
    cid = conversation_id
    try:
        if not cid if start is None else start:
            cid = await mem.start_conversation(trace_id, conversation_id=cid, **ids)
    except MemoryCallError as exc:
        errors.append(str(exc))
    context = ""
    if cid and read:
        try:
            context = render_context(await mem.get_context(trace_id, conversation_id=cid, **ids))
        except MemoryCallError as exc:
            errors.append(str(exc))
    if read:
        # Owner-keyed, so it needs no conversation — it answers even when opening one failed.
        saved, saved_errors = await saved_memories(mem, trace_id, **ids)
        errors += saved_errors
        context = "\n".join(part for part in (context, saved) if part)
    return cid, context, errors


async def write_turn(
    mem: MemoryClient,
    trace_id: str,
    *,
    agent_id: str,
    owner_id: str,
    conversation_id: str,
    user_message: str | None = None,
    agent_response: str | None = None,
) -> list[str]:
    """The write half of a turn, fail-open: the errors, if any.

    Independent of the read on purpose: GetContext 403'd for every
    conversation while writes 2xx'd (2026-10-13), and a refused read must not
    cost the turn its ingest, or memory never fills in. Every write ends in a
    close, because the close is what triggers extraction.
    """
    ids = {"agent_id": agent_id, "owner_id": owner_id, "conversation_id": conversation_id}
    try:
        await mem.ingest(trace_id, user_message=user_message,
                         agent_response=agent_response, **ids)  # fmt: skip
        if user_message or agent_response:
            await mem.close_session(trace_id, **ids)
    except MemoryCallError as exc:
        return [str(exc)]
    return []


# Response envelope, not memory: ids and a timestamp mean nothing to the model.
# contextId is kept on the trace hop instead (`get_context`).
ENVELOPE_KEYS = frozenset({"contextId", "sessionId", "createdAt"})


def render_context(ctx: dict, limit: int = 4000) -> str:
    """The context payload as prompt text: every non-empty string leaf,
    labelled by its key path, minus the top-level envelope (`ENVELOPE_KEYS`).
    Deliberately generic, because the service has added fields before."""
    lines: list[str] = []
    ctx = {k: v for k, v in ctx.items() if k not in ENVELOPE_KEYS}

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for item in node:
                walk(item, path)
        elif isinstance(node, str) and node.strip():
            lines.append(f"- {path}: {node.strip()}")

    walk(ctx, "")
    return "\n".join(lines)[:limit]
