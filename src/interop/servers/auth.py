"""Shared-secret auth for every app the tunnel can expose.

The tunnel publishes the protocol servers and the console on public
hostnames, so each app must enforce auth itself: when A2ALAB_TOKEN is set,
requests need the token in X-Lab-Token, `Authorization: Bearer <token>`, or
(where enabled, for the console's EventSource which can't set headers) a
`?token=` query parameter. Unset token = pass-through for local dev, same
semantics as the bridge's BRIDGE_TOKEN.

Discovery and health endpoints stay open: A2A clients must be able to fetch
the agent card anonymously, and AgentCore/uptime checks hit /ping and
/healthz.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qs

TOKEN_ENV = "A2ALAB_TOKEN"
TOKEN_HEADER = "x-lab-token"
EXEMPT_PATHS = ("/healthz", "/ping", "/.well-known/agent-card.json")

# Discovery suffixes that must stay open even when the app is MOUNTED under a
# prefix. The A2A spec (and every well-behaved client, incl. the MuleSoft Omni
# Gateway) fetches the agent card ANONYMOUSLY before it ever sends a message —
# credential injection applies to the invoke, not to card discovery. A mounted
# face sees scope["path"] as the full "/claude-a2a/.well-known/agent-card.json"
# (modern Starlette reports the prefix in root_path, not by stripping path), so
# the exact-match EXEMPT_PATHS above never fires for it and the face 401s its
# own public card. Suffix-matching these keeps discovery open regardless of the
# mount prefix WITHOUT widening the health-path exemption (a per-face /healthz
# stays gated — only the unwrapped top-level ALB /healthz is open). The legacy
# /.well-known/agent.json is included for pre-0.3 A2A clients.
DISCOVERY_SUFFIXES = ("/.well-known/agent-card.json", "/.well-known/agent.json")


def _looks_like_jwt(value: str) -> bool:
    from interop.identity import looks_like_jwt

    return looks_like_jwt(value)


def _verify_lab_jwt(token: str):
    """Deferred import + never-raise: auth must not depend on the identity
    stack being configured — a missing keypair just means no JWT auth."""
    try:
        from interop.identity import verify_token

        return verify_token(token)
    except Exception:  # noqa: BLE001 - fail closed to shared-token path
        return None


class TokenAuthMiddleware:
    """Pure ASGI middleware (works under Starlette/FastAPI and wrapped apps).

    The token is resolved per request (constructor arg wins, else
    A2ALAB_TOKEN) so process start order and test monkeypatching don't
    freeze it at import time.
    """

    def __init__(
        self,
        app,
        *,
        token: str | None = None,
        allow_query_param: bool = False,
        exempt_paths: tuple[str, ...] = EXEMPT_PATHS,
        exempt_prefixes: tuple[str, ...] = (),
    ):
        self.app = app
        self._token = token
        self.allow_query_param = allow_query_param
        self.exempt_paths = exempt_paths
        self.exempt_prefixes = exempt_prefixes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        expected = self._token if self._token is not None else os.environ.get(TOKEN_ENV)
        path = scope.get("path", "")
        if (
            not expected
            or path in self.exempt_paths
            or path.endswith(DISCOVERY_SUFFIXES)
            or any(path.startswith(p) for p in self.exempt_prefixes)
        ):
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])
        }
        supplied = headers.get(TOKEN_HEADER)
        if not supplied:
            authz = headers.get("authorization", "")
            if authz.startswith("Bearer "):
                supplied = authz[len("Bearer ") :]
        if not supplied and self.allow_query_param:
            qs = parse_qs(scope.get("query_string", b"").decode("latin-1"))
            supplied = (qs.get("token") or [None])[0]

        # Lab user JWT (WS6 U1): a signed per-user credential is accepted
        # wherever the shared token is — and, unlike it, carries WHO. The
        # verified claims land in scope["state"] for the app to stamp onto
        # runs and traces. The shared token stays valid as the legacy /
        # service credential.
        if supplied and supplied != expected and _looks_like_jwt(supplied):
            claims = _verify_lab_jwt(supplied)
            if claims is not None:
                scope.setdefault("state", {})["lab_user"] = claims
                await self.app(scope, receive, send)
                return

        if supplied != expected:
            body = b'{"detail": "bad or missing X-Lab-Token"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
