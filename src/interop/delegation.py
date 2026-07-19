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

_RIDER_TEMPLATE = (
    "\n\n"
    + MARKER
    + "\n"
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


def allowed(req: AgentRequest) -> bool:
    """May the agent handling ``req`` delegate onward?"""
    return depth_of(req) < max_depth()


def delegate(message: str, *, caller: str, platform: str, inbound_depth: int) -> tuple[str, dict]:
    """Compose an outbound delegation: (message + rider, metadata) at
    depth inbound_depth + 1. Callers check ``allowed()`` first."""
    depth = inbound_depth + 1
    rider = _RIDER_TEMPLATE.format(caller=caller, platform=platform, depth=depth)
    meta = {"delegation": {"caller": caller, "platform": platform, "depth": depth}}
    return message + rider, meta


def refusal(seam: str) -> str:
    """Standard wire-visible refusal a seam returns instead of forwarding."""
    return (
        f"[a2a-lab delegation guard @ {seam}] This request was itself "
        f"delegated (depth >= {max_depth()}), so onward delegation is "
        "blocked to prevent circular agent-to-agent calls. Answer from "
        "your own knowledge and data instead of calling other agents."
    )


def example_rider() -> str:
    """The rider with placeholder values — for display surfaces (the console
    shows it read-only in the run panel so the injection is a visible design
    decision, not hidden plumbing)."""
    return _RIDER_TEMPLATE.format(
        caller="<calling-agent>", platform="<caller-platform>", depth=1
    ).strip()
