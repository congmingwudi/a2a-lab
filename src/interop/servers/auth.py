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
    ):
        self.app = app
        self._token = token
        self.allow_query_param = allow_query_param
        self.exempt_paths = exempt_paths

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        expected = self._token if self._token is not None else os.environ.get(TOKEN_ENV)
        if not expected or scope.get("path", "") in self.exempt_paths:
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
