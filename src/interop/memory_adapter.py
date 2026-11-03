"""The generic memory decorator over the inbound seam (plan/18 Phase 3, WS27 item 11).

`MemoryAdapter(inner, agent_id)` is an `AgentAdapter` that wraps any other one:
it reads the owner's Data 360 Agentic Memory context before `inner.handle()`
and ingests the turn after it. So every agent the lab hosts gets memory without
per-platform code: this is the "REST at the lab seam" integration mode, the one
the recall matrix compares against the native SDK adapters and the Agentforce
Apex action (D83, D84).

Four choices carry the design:

- **Memory is a per-run choice, not a lab-wide switch.** The wrapper sits on
  every face whose platform has a registered memory agent, and is dormant: a
  turn only gets memory when its request says so (`metadata["memory"] = True`
  — the console's Memory toggle on an experiment, `config/scenarios.yaml`
  `memory_toggle`). Off, the turn is the inner agent's, byte for byte, so the
  same experiment runs with and without memory side by side.

- **The owner is the VERIFIED end user.** `owner_of` verifies the lab JWT riding
  `metadata["user_token"]` (WS6 U2 — every protocol carries it) and keys memory
  by its `sub`. Never a caller-asserted field (`user_context` is display-only),
  and never a `machine` identity: a shared service caller would pool every
  user into one owner. No verified user means no memory, so the turn passes
  through untouched.
- **This seam has the reply.** No Agentforce hook can hand over the agent's
  answer (D89), but a decorator around `handle()` holds both sides of the
  turn, so it ingests the user message AND the reply.
- **Fail-open and bounded.** Memory is a cross-region round trip (us-east-1 to
  the tenant); each half runs under `A2ALAB_MEMORY_BUDGET_S`, and any refusal or
  overrun degrades to a no-memory turn, recorded in `resp.metadata["memory"]`.

`MEMORY_AGENTS` names each platform's AgentApiName, registered by
`scripts/setup_agentic_memory.py register <platform>` — the a360 exchange refuses
an unregistered `aid`, so a platform with no entry is never wrapped. A run that
asks for memory before the registration exists is answered without it, and the
refusal is in `resp.metadata["memory"]`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import os
import time
from collections import OrderedDict
from typing import Any

from interop.adapter import AgentAdapter
from interop.memory import MemoryClient, open_and_read, owner_conversation, redact, write_turn
from interop.models import AgentRequest, AgentResponse

# Both platforms use one domain-level memory-service identity. Claude and
# OpenAI remain distinct execution/provenance participants, but curated profile
# facts/preferences belong to the customer and are not platform partitions.
# Kept in step with setup_agentic_memory.AGENTS["case-memory"].
CASE_MEMORY_AGENT = "A2ALab_Memory_Case"
MEMORY_AGENTS = {
    "claude": CASE_MEMORY_AGENT,
    "openai": CASE_MEMORY_AGENT,
}
REQUEST_FLAG = "memory"  # metadata key: True asks for memory on THIS turn
BUDGET_ENV = "A2ALAB_MEMORY_BUDGET_S"
OWNER_MAP_ENV = "A2ALAB_MEMORY_OWNER_MAP"
CUSTOMER_MAP_ENV = "A2ALAB_MEMORY_CUSTOMER_MAP"
DEFAULT_BUDGET_S = 8.0
_MAX_SESSIONS = 1024

_shared: MemoryClient | None = None


def owner_of(req: AgentRequest) -> str | None:
    """Return the verified persona's Data 360 profile identifier.

    Agentic Memory's ``xid`` is the end customer represented by the profile
    DMO, not a lab login name. The mapping is deployment configuration because
    profile identifiers are org-specific. Missing, malformed, or incomplete
    configuration fails closed so two users can never collapse into one owner.
    """
    from interop import identity

    token = (req.metadata or {}).get("user_token")
    if not isinstance(token, str):
        return None
    claims = identity.verify_token(token)
    if not claims or claims.get("role") == "machine" or not claims.get("sub"):
        return None
    customer_key = (req.metadata or {}).get("memory_customer")
    map_env = CUSTOMER_MAP_ENV if isinstance(customer_key, str) and customer_key else OWNER_MAP_ENV
    lookup_key = customer_key if map_env == CUSTOMER_MAP_ENV else str(claims["sub"])
    try:
        owner_map = json.loads(os.environ.get(map_env, "{}"))
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(owner_map, dict):
        return None
    owner = owner_map.get(lookup_key)
    return owner.strip() if isinstance(owner, str) and owner.strip() else None


def _derived_session(agent_id: str, owner_id: str, session_id: str) -> str:
    """One memory session per (agent, owner, lab session), inside the owner's
    one conversation (D94), stable across restarts — start_conversation treats
    409 as success, so a restarted face resumes it."""
    digest = hashlib.sha256(f"{agent_id}\0{owner_id}\0{session_id}".encode()).hexdigest()
    return f"a2alab-{digest[:24]}"


def _with_context(message: str, context: str) -> str:
    return (
        "[a2a-lab memory] What you already know about this user from earlier "
        "conversations (Data 360 Agentic Memory). Use it where it is relevant; "
        "do not recite it.\n"
        f"{context}\n"
        "[end memory]\n\n"
        f"{message}"
    )


class MemoryAdapter:
    """An AgentAdapter that gives `inner` the owner's memory, fail-open."""

    def __init__(
        self,
        inner: AgentAdapter,
        agent_id: str,
        *,
        client: Any = None,
        budget_s: float | None = None,
    ):
        self.inner = inner
        self.agent_id = agent_id
        self.name = inner.name
        self.description = inner.description
        self._explicit = client
        self.budget_s = (
            budget_s
            if budget_s is not None
            else float(os.environ.get(BUDGET_ENV, DEFAULT_BUDGET_S))
        )
        # (owner, lab session) -> an OPENED memory session
        self._sessions: OrderedDict[tuple[str, str], str] = OrderedDict()

    def __getattr__(self, attr: str):
        # Anything else the protocol servers look for on an adapter
        # (extra_mcp_tools, ...) is the inner agent's.
        return getattr(self.inner, attr)

    def _client(self):
        global _shared
        if self._explicit is not None:
            return self._explicit
        if _shared is None:
            _shared = MemoryClient(source="memory-adapter")
        return _shared

    def _remember(self, key: tuple[str, str], sid: str) -> None:
        self._sessions[key] = sid
        self._sessions.move_to_end(key)
        while len(self._sessions) > _MAX_SESSIONS:
            self._sessions.popitem(last=False)

    async def _bounded(self, coro, what: str) -> tuple[Any, list[str]]:
        try:
            return await asyncio.wait_for(coro, self.budget_s), []
        except asyncio.TimeoutError:
            return None, [f"memory {what} over its {self.budget_s:g}s budget"]

    async def handle(self, req: AgentRequest) -> AgentResponse:
        if (req.metadata or {}).get(REQUEST_FLAG) is not True:
            return await self.inner.handle(req)
        owner = owner_of(req)
        if owner is None:
            # Asked for, but nobody to remember: say so rather than look like
            # a turn that recalled nothing.
            resp = await self.inner.handle(req)
            meta = dict(resp.metadata or {})
            meta["memory"] = {"agent_id": self.agent_id, "owner_id": None, "ok": False,
                              "error": ("no Data 360 customer profile is mapped to this "
                                        "verified lab user")}
            return dataclasses.replace(resp, metadata=meta)
        mem = self._client()
        trace_id = req.trace_id or ""
        ids = {"agent_id": self.agent_id, "owner_id": owner}

        known = self._sessions.get((owner, req.session_id)) if req.session_id else None
        sid = known or (
            _derived_session(self.agent_id, owner, req.session_id) if req.session_id else None
        )
        t0 = time.perf_counter()
        opened, read_errors = await self._bounded(
            open_and_read(mem, trace_id, session_id=sid, start=not known, **ids), "read"
        )
        context = ""
        if opened is not None:
            sid, context, read_errors = opened
        read_ms = int((time.perf_counter() - t0) * 1000)
        if sid and req.session_id and not read_errors:
            self._remember((owner, req.session_id), sid)

        sent = (
            dataclasses.replace(req, message=_with_context(req.message, context))
            if context
            else req
        )
        resp = await self.inner.handle(sent)

        t1 = time.perf_counter()
        write_errors: list[str] = []
        if sid:
            written, overrun = await self._bounded(
                write_turn(
                    mem,
                    trace_id,
                    session_id=sid,
                    user_message=req.message,
                    agent_response=resp.text or None,
                    **ids,
                ),  # fmt: skip
                "ingest",
            )
            write_errors += overrun or written or []
        ingest_ms = int((time.perf_counter() - t1) * 1000)
        # A first turn with no session: the inner agent minted one, and the next
        # turn arrives carrying it — follow it to the session just opened.
        if sid and not req.session_id and resp.session_id and not (read_errors or write_errors):
            self._remember((owner, resp.session_id), sid)

        errors = [*read_errors, *write_errors]
        customer_key = (req.metadata or {}).get("memory_customer")
        meta = dict(resp.metadata or {})
        meta["memory"] = {
            "agent_id": self.agent_id,
            "owner_id": owner,
            "conversation_id": owner_conversation(owner),
            "session_id": sid,
            "context_chars": len(context),
            "context_preview": redact(context)[:360] if context else "",
            "recalled": bool(context),
            "read_ok": not read_errors,
            "read_ms": read_ms,
            "ingest_ms": ingest_ms,
            "stored": bool(sid) and not write_errors,
            "customer_key": customer_key if isinstance(customer_key, str) else None,
            "ok": not errors,
            **({"error": "; ".join(errors)} if errors else {}),
        }
        return dataclasses.replace(resp, metadata=meta)


def with_memory(adapter: AgentAdapter, platform: str) -> AgentAdapter:
    """Wrap `adapter` (dormant until a run asks) when the platform has a memory agent."""
    agent_id = MEMORY_AGENTS.get(platform)
    return MemoryAdapter(adapter, agent_id) if agent_id else adapter
