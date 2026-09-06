"""Invoke authorization at the protocol faces (WS25 A2, D80/F01).

The console gates its spend-incurring routes by role (D36). The protocol
faces did not: TokenAuthMiddleware verified a lab JWT and admitted it whatever
its role, so a viewer refused on /api/run could invoke any face directly.

One policy function, three call sites: the REST handler (which has the
request), and the WireTap for MCP and A2A — which already buffers the body
exactly once and can read the JSON-RPC method from it, so no second body
read exists anywhere. Role is resolved from the directory
(identity.load_users), never from the token claim, so a stale claim cannot
escalate — the console's rule.

Policy:
- viewer  → denied on invoking requests, allowed on discovery/health/reads.
- machine → allowed. The Agent Fabric gateway (WS10 SP1) fronts EVERY lab face
  (plan/07 WS10: one gateway API per face), so an attributed machine caller is
  a legitimate invoker of any face; it is the CONSOLE that denies it.
- operator/owner, and the header-borne shared token (no persona) → allowed.
- Streaming methods are NOT in the deny set: the lab advertises streaming but
  does not exercise it (CLAUDE.md), and a policy on a path that never runs
  would imply coverage that does not exist. Add them when a route exists.
"""

from __future__ import annotations

import json
from typing import Any

REST_INVOKE_PATHS = ("/invoke", "/invocations")
INVOKE_METHODS: dict[str, frozenset[str]] = {
    "mcp": frozenset({"tools/call"}),
    "a2a": frozenset({"SendMessage", "message/send"}),  # v1 and the 0.3 spelling
}
DENIED_ROLE = "viewer"


def _rpc_methods(body: bytes | None) -> set[str]:
    """Every JSON-RPC method named in the body — one for a single request, all
    of them for a batch (Codex ws25-b1 round 2: inspecting only the first
    member let `[tools/list, tools/call]` through). Malformed or empty bodies
    name no method and are therefore not invoking; the server's own parse
    error answers them."""
    if not body:
        return set()
    try:
        payload = json.loads(body)
    except ValueError:
        return set()
    members = payload if isinstance(payload, list) else [payload]
    return {
        m["method"] for m in members if isinstance(m, dict) and isinstance(m.get("method"), str)
    }


def is_invoke(protocol: str, scope: dict[str, Any], body: bytes | None) -> bool:
    """Does this request make an agent DO work (as opposed to being read)?"""
    if scope.get("method") != "POST":
        return False
    if protocol == "rest":
        path = scope.get("path", "")
        return any(path == p or path.endswith(p) for p in REST_INVOKE_PATHS)
    methods = INVOKE_METHODS.get(protocol)
    if not methods:
        return False
    return not methods.isdisjoint(_rpc_methods(body))


def role_of(scope: dict[str, Any]) -> tuple[str | None, str | None]:
    """(subject, directory role) of the verified persona, or (None, None) for
    the shared service token — which identifies no one."""
    claims = (scope.get("state") or {}).get("lab_user")
    if not claims:
        return None, None
    from interop import identity

    sub = claims.get("sub")
    role = identity.load_users().get(sub, {}).get("role")
    return sub, role


def invoke_denial(protocol: str, scope: dict[str, Any], body: bytes | None) -> str | None:
    """The 403 detail if this request must be refused, else None."""
    if not is_invoke(protocol, scope, body):
        return None
    _sub, role = role_of(scope)
    if role == DENIED_ROLE:
        return f"'invoke {protocol}' is operator-only (D36 role model)"
    return None
