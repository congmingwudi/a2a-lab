"""Offline review probes: execute selected source definitions with fake boundaries.

Run with python3 -B build-notes/codex/codebase-review/readiness_probes.py.
No application imports, credentials, network, persistence, or third-party packages.
These demonstrate current behavior; they are not integration or release tests.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
from pathlib import Path
import re
import statistics
import sys
import threading
import time
from types import ModuleType, SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[3]


def selected(path, names, **context):
    tree = ast.parse((ROOT / path).read_text(), filename=path)
    nodes = []
    found = set()
    for node in tree.body:
        name = getattr(node, "name", None)
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", None)
        if name in names:
            found.add(name)
            nodes.append(node)
    assert found == set(names), (path, set(names) - found)
    future = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0
    )
    module = ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[]))
    namespace = dict(context)
    exec(compile(module, path, "exec"), namespace)
    return namespace


def redaction_probe():
    ns = selected("src/interop/trace.py", {
        "_SECRET_KEYS", "_SECRET_PATTERNS", "_redact_str", "redact"
    }, re=re)
    payload = {"password": "review-sentinel-secret", "client_secret": "opaque-value"}
    assert ns["redact"](payload)["password"] == "[REDACTED]"
    raw = json.dumps(payload)
    assert ns["redact"](raw) == raw
    print("CONFIRMED: credential fields scrubbed as dicts survive as raw JSON strings")


async def face_auth_probe():
    entered = []

    async def app(scope, receive, send):
        entered.append(scope["state"]["lab_user"]["role"])

    ns = selected("src/interop/servers/auth.py", {
        "TOKEN_ENV", "TOKEN_HEADER", "EXEMPT_PATHS", "DISCOVERY_SUFFIXES",
        "TokenAuthMiddleware"
    }, os=os, _looks_like_jwt=lambda _: True,
        _verify_lab_jwt=lambda _: {"sub": "review-viewer", "role": "viewer"})
    middleware = ns["TokenAuthMiddleware"](app, token="fake-service-token")
    await middleware({"type": "http", "path": "/claude-rest/invoke",
                      "headers": [(b"authorization", b"Bearer fake-user-token")]},
                     None, None)
    assert entered == ["viewer"]
    print("CONFIRMED: a JWT accepted as viewer is admitted to the protocol application")


def console_gate_probe():
    interop = ModuleType("interop")
    interop.identity = NS(load_users=lambda: {"v": {"role": "viewer"}},
                          is_operator_role=lambda role: role == "operator")
    sys.modules["interop"] = interop

    class Forbidden(Exception):
        def __init__(self, **kwargs):
            self.status_code = kwargs["status_code"]

    ns = selected("src/console/app.py", {"_OPERATOR_ONLY", "_viewer_forbidden"},
                  HTTPException=Forbidden)
    req = NS(url=NS(path="/api/traces"), method="DELETE",
             scope={"state": {"lab_user": {"sub": "v", "role": "viewer"}}})
    ns["_viewer_forbidden"](req)
    req.url.path = "/api/run"
    try:
        ns["_viewer_forbidden"](req)
    except Forbidden as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("control request should be denied")
    print("CONFIRMED: console viewer gate permits DELETE /api/traces but denies /api/run")


async def session_probe():
    keys = []

    async def ask(req):
        keys.append(req.session_id)

    ns = selected("src/platforms/agentforce/proxy.py",
                  {"TWIN_ENV_BY_PLATFORM", "AgentforceProxyAdapter"},
                  os=NS(environ={}), delegation=NS(platform_of=lambda req: None))
    adapter = ns["AgentforceProxyAdapter"](client=NS(ask=ask), session_reuse=True)
    for user in ("alice", "bob"):
        await adapter.handle(NS(session_id=None, metadata={"user_context": {"sub": user}}))
    assert keys == ["shim-shared-direct", "shim-shared-direct"]
    print("CONFIRMED: two distinct callers receive the same hosted-shim session key")


async def deadline_probe():
    ns = selected("src/orchestration/runner.py", {"_run_leg_async"},
                  asyncio=asyncio, time=time, _POLL_UNRETRIEVABLE=(),
                  poll_interval_s=lambda: 0, poll_not_found_grace_s=lambda: 0)

    async def submit(req):
        return NS(answered_immediately=False, task_id="fake-task")

    async def poll(*args, **kwargs):
        await asyncio.sleep(0.06)
        return NS(done=True, state="TASK_STATE_COMPLETED", text="late answer")

    start = time.monotonic()
    answer, _ = await ns["_run_leg_async"](
        NS(submit=submit, poll=poll), None, NS(), trace_id="fake-trace", timeout_s=0.02
    )
    elapsed = time.monotonic() - start
    assert answer == "late answer" and elapsed > 0.04
    print(f"CONFIRMED: 20ms async budget returned success after {elapsed * 1000:.0f}ms")


def duplicate_worker_probe():
    ns = selected("src/fanout_mcp/tasks.py", {"run_task"})
    barrier = threading.Barrier(2)
    calls = []

    class Store:
        def get(self, task_id):
            barrier.wait(timeout=2)
            return NS(done=False, unit="Logistics")

        def mark_working(self, task_id):
            pass

        def situation_of(self, task_id):
            return "synthetic situation"

        def finish(self, task_id, **kwargs):
            pass

    def runner(unit, situation):
        calls.append(unit)
        return "synthetic result"

    store = Store()
    threads = [threading.Thread(target=ns["run_task"], args=("same-task", store, runner))
               for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()
    assert calls == ["Logistics", "Logistics"]
    print("CONFIRMED: overlapping deliveries of one task both invoke the worker runner")


async def matrix_probe():
    ns = selected("scripts/matrix.py", {"_p95", "run_cell"},
                  time=time, statistics=statistics, math=__import__("math"),
                  QUESTION="synthetic question", AgentRequest=NS,
                  new_trace_id=lambda: "fake-trace")

    async def ask(req):
        return NS(text="")

    async def close():
        pass

    registry = NS(get=lambda name: NS(platform="fake", protocol="rest", status="native"),
                  client_for=lambda *args, **kwargs: NS(ask=ask, aclose=close))
    result = await ns["run_cell"](registry, "fake-target", 1)
    assert result["ok"] and result["snippet"] == ""
    print("CONFIRMED: matrix records PASS for an empty answer")


async def main():
    redaction_probe()
    await face_auth_probe()
    console_gate_probe()
    await session_probe()
    await deadline_probe()
    duplicate_worker_probe()
    await matrix_probe()
    print("7 offline behavior probes completed; no application integration tests executed")


if __name__ == "__main__":
    asyncio.run(main())
