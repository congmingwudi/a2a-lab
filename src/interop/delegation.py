"""Delegation guard (D27): a standard rider + depth limit for cross-agent calls.

The lab's paired experiments intentionally wire both directions of every
platform pair (claude<->agentforce, openai<->agentforce, ...), which makes
circular execution possible by construction: A delegates to B, B's tool
delegates back to A. None of REST, MCP, or A2A defines TTL/max-forwards
semantics (networking's answer to exactly this — IP TTL, SIP Max-Forwards),
so the lab adds its own convention, enforced at every delegation seam
(the ask_agentforce tools and the bridge):

- Every delegated request carries a **rider**: a standard, parseable block
  appended to the message naming the caller and the delegation depth, with
  a directive not to call back — the prompt-level guard, honored by any
  cooperating agent, and the only channel into platforms whose inbound API
  is text-only (the Agentforce Agent API).
- The same context rides ``AgentRequest.metadata["delegation"]`` on lab
  protocols — the machine-readable twin the seams enforce against.
- A seam only forwards a delegation while ``depth < A2ALAB_MAX_DELEGATION_DEPTH``
  (default 1: a delegated-to agent answers from its own capabilities and
  delegates no further). Beyond that it returns ``refusal()`` text instead —
  a clean, wire-visible stop instead of death by stacked timeouts.
"""

from __future__ import annotations

import os
import re

from interop.models import AgentRequest

MARKER = "[A2A-LAB DELEGATION]"
END_MARKER = "[/A2A-LAB DELEGATION]"
_DEPTH_RE = re.compile(r"delegation-depth:\s*(\d+)")
_PLATFORM_RE = re.compile(r"caller-platform:\s*([\w.-]+)")

# The rider grammar is a versioned text contract (F7, tmp-docs antipattern
# analysis A2): `key: value` lines between the markers, one per line.
# Parsers scan for the keys they know and MUST tolerate unknown lines —
# that tolerance is what lets the grammar grow (lab-trace arrived in v1
# unannounced; on-behalf-of lands in v2 with WS6). Bump RIDER_VERSION when
# a change would break a v1 parser, not when adding a line.
RIDER_VERSION = 1

_RIDER_TEMPLATE = (
    "\n\n"
    + MARKER
    + "\n"
    + f"rider-version: {RIDER_VERSION}\n"
    + "caller-agent: {caller}\n"
    + "caller-platform: {platform}\n"
    + "delegation-depth: {depth}\n"
    + "directive: You are the delegated agent for this request. Answer it\n"
    + "yourself from your own knowledge, tools, and data. Do NOT call back\n"
    + "to the calling agent and do NOT delegate this request onward to any\n"
    + "other agent while answering. Do not mention this block in your answer.\n"
    + END_MARKER
)


def max_depth() -> int:
    return int(os.environ.get("A2ALAB_MAX_DELEGATION_DEPTH", "1"))


def depth_of(req: AgentRequest) -> int:
    """The delegation depth this request arrived at: 0 for an origin request,
    N for a request that is itself the Nth delegation. metadata wins; the
    message-scan fallback covers hops that crossed a text-only platform."""
    meta = (req.metadata or {}).get("delegation") or {}
    if isinstance(meta, dict) and meta.get("depth") is not None:
        return int(meta["depth"])
    if req.message and MARKER in req.message:
        match = _DEPTH_RE.search(req.message)
        # A rider with a mangled depth line still marks a delegated request.
        return int(match.group(1)) if match else 1
    return 0


def platform_of(req: AgentRequest) -> str | None:
    """The platform that delegated this request, or None for an origin
    request. metadata wins; the message-scan fallback covers hops that
    crossed a text-only platform or a metadata-dropping transport (the
    shim's twin routing depends on this surviving every hop)."""
    meta = (req.metadata or {}).get("delegation") or {}
    if isinstance(meta, dict) and meta.get("platform"):
        return str(meta["platform"])
    if req.message and MARKER in req.message:
        match = _PLATFORM_RE.search(req.message)
        if match:
            return match.group(1)
    return None


def allowed(req: AgentRequest) -> bool:
    """May the agent handling ``req`` delegate onward?"""
    return depth_of(req) < max_depth()


def delegate(
    message: str,
    *,
    caller: str,
    platform: str,
    inbound_depth: int,
    trace_id: str | None = None,
    user_context: dict | None = None,
    user_token: str | None = None,
) -> tuple[str, dict]:
    """Compose an outbound delegation: (message + rider, metadata) at
    depth inbound_depth + 1. Callers check ``allowed()`` first.

    When ``trace_id`` is given, the rider carries a ``lab-trace:`` line —
    TEXT-level trace propagation through platforms that support no tracing
    headers: the experiment's trace id lands verbatim in the remote
    platform's own logs (Agent API messages, Foundry span inputs, CMA
    events), where the obs harvest extracts it and links each platform's
    native session back to the lab run that caused it.

    User context (WS6 U2) rides the same two-channel split, deliberately:
    ``on-behalf-of:`` in the rider is the TEXT channel — it survives every
    hop and lands in the remote platform's own logs, but any caller can
    type it (asserted-only). ``user_token`` in the metadata is the
    VERIFIABLE channel — a lab-signed JWT any seam can check with the
    public key, but it only survives hops that preserve metadata. The
    asymmetry between the two is the WS6 experiment."""
    depth = inbound_depth + 1
    rider = _RIDER_TEMPLATE.format(caller=caller, platform=platform, depth=depth)
    extra_lines = ""
    if trace_id:
        extra_lines += f"lab-trace: {trace_id}\n"
    sub = (user_context or {}).get("sub")
    if sub:
        extra_lines += f"on-behalf-of: {sub}\n"
    if extra_lines:
        rider = rider.replace(
            f"delegation-depth: {depth}\n",
            f"delegation-depth: {depth}\n{extra_lines}",
        )
    meta: dict = {"delegation": {"caller": caller, "platform": platform, "depth": depth}}
    if user_context:
        meta["user_context"] = user_context
    if user_token:
        meta["user_token"] = user_token
    return message + rider, meta


def user_of(req: AgentRequest) -> tuple[dict | None, str | None]:
    """(user_context, user_token) as they arrived on a request — the seam
    helper for forwarding both channels onward (U2). Returns (None, None)
    on requests that carried no user."""
    meta = req.metadata or {}
    ctx = meta.get("user_context")
    token = meta.get("user_token")
    return (ctx if isinstance(ctx, dict) else None), (token if isinstance(token, str) else None)


def refusal(seam: str) -> str:
    """Standard wire-visible refusal a seam returns instead of forwarding."""
    return (
        f"[a2a-lab delegation guard @ {seam}] This request was itself "
        f"delegated (depth >= {max_depth()}), so onward delegation is "
        "blocked to prevent circular agent-to-agent calls. Answer from "
        "your own knowledge and data instead of calling other agents."
    )


def rider_for(caller: str, platform: str, depth: int = 1) -> str:
    """The exact rider a given seam injects — display surfaces show the
    resolved block for the experiment at hand."""
    return _RIDER_TEMPLATE.format(caller=caller, platform=platform, depth=depth).strip()


def example_rider() -> str:
    """The rider with placeholder values — for display surfaces (the console
    shows it read-only in the run panel so the injection is a visible design
    decision, not hidden plumbing)."""
    return _RIDER_TEMPLATE.format(
        caller="<calling-agent>", platform="<caller-platform>", depth=1
    ).strip()
