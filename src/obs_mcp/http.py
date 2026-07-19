"""HTTP adapters over obs_mcp.core: a local Starlette app and an AWS Lambda
Function URL handler (payload format 2.0).

The Lambda path is stdlib-only so the deployment zip needs no third-party
packages; starlette is imported lazily inside create_local_app.
"""

from __future__ import annotations

import base64
import hmac
import json
from collections.abc import Callable
from typing import Any

from obs_mcp.core import ToolRegistry, handle_message


def _parse_error_body() -> dict:
    return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}


def _auth_ok(authorization: str | None, token: str | None) -> bool:
    """Bearer check with a constant-time compare. Falsy expected token = auth off."""
    if not token:
        return True
    if not authorization or not authorization.startswith("Bearer "):
        return False
    return hmac.compare_digest(authorization[len("Bearer ") :], token)


def create_local_app(registry: ToolRegistry, token: str | None):
    """Starlette app: POST / and /mcp dispatch JSON-RPC, GET /healthz is open."""
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    async def mcp_endpoint(request: Request) -> Response:
        if not _auth_ok(request.headers.get("authorization"), token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body = json.loads(await request.body())
        except ValueError:
            return JSONResponse(_parse_error_body(), status_code=400)
        if not isinstance(body, dict):
            return JSONResponse(_parse_error_body(), status_code=400)
        reply = handle_message(body, registry)
        if reply is None:
            return Response(status_code=202)
        return JSONResponse(reply)

    async def healthz(request: Request) -> Response:
        return JSONResponse({"ok": True})

    return Starlette(
        routes=[
            Route("/", mcp_endpoint, methods=["POST"]),
            Route("/mcp", mcp_endpoint, methods=["POST"]),
            Route("/healthz", healthz, methods=["GET"]),
        ]
    )


def make_lambda_handler(registry: ToolRegistry, token: str | None) -> Callable[[dict, Any], dict]:
    """AWS Lambda Function URL handler (payload format 2.0), stdlib only."""

    def _response(status: int, payload: dict | None = None) -> dict:
        if payload is None:
            return {"statusCode": status, "headers": {}, "body": ""}
        return {
            "statusCode": status,
            "headers": {"content-type": "application/json"},
            "body": json.dumps(payload),
        }

    def handler(event: dict, context: Any) -> dict:
        http = (event.get("requestContext") or {}).get("http") or {}
        method = http.get("method", "")
        path = http.get("path", "/")
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}

        if method == "GET" and path == "/healthz":
            return _response(200, {"ok": True})
        if method != "POST":
            return _response(405, {"error": "method not allowed"})
        if not _auth_ok(headers.get("authorization"), token):
            return _response(401, {"error": "unauthorized"})

        raw = event.get("body") or ""
        try:
            if event.get("isBase64Encoded"):
                raw = base64.b64decode(raw).decode("utf-8")
            body = json.loads(raw)
        except ValueError:
            return _response(400, _parse_error_body())
        if not isinstance(body, dict):
            return _response(400, _parse_error_body())

        reply = handle_message(body, registry)
        if reply is None:
            return _response(202)
        return _response(200, reply)

    return handler
