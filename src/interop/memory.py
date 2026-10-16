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
        _, body = await self._call(
            trace_id, agent_id, "GET", "/api/v1/agentic/context",
            headers={"ctx-agent-id": agent_id, "ctx-dataspace-ds_name": DATA_SPACE},
            params={"conversation_id": conversation_id, "owner_id": owner_id,
                    "agent_id": agent_id, "owner_type": OWNER_TYPE},
        )  # fmt: skip
        return body if isinstance(body, dict) else {}


def render_context(ctx: dict, limit: int = 4000) -> str:
    """The context payload as prompt text. The payload's exact shape is not yet
    measured on this tenant (plan/18 Phase 2), so this is deliberately generic:
    every non-empty string leaf, labelled by its key path."""
    lines: list[str] = []

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
