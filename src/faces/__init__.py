"""The fourteen local protocol faces, hosted as ONE ASGI app (WS13 item 2).

**What this closes.** `config/targets.yaml` pointed a spread of cells at
`localhost:80xx` — the Claude, OpenAI and (since WS5) Strands REST/MCP/A2A
servers, the Lab Guide's three, and the two Agentforce shims. Inside a container
`localhost` is the container, so every one of them failed from the hosted
console and the lab still needed a laptop running `run_local.sh` to exercise a
protocol comparison. That was the last runtime dependency on the operator's
machine.

**Why one process rather than fourteen services.** Each face is an ASGI app that
`interop.adapter.build_app()` already returns without running a server, so
there is no reason to pay for fourteen Fargate tasks (~$125/month) to run
fourteen `uvicorn`s. One task runs all of them. It also sidesteps ECS's limit of
five target groups per service, which fourteen separately-addressed faces would
have hit.

**Why paths rather than fourteen hostnames.** Host-based routing is what the
console uses and would have worked, but every hostname is a DNS record somebody
creates by hand in Cloudflare. Fourteen records and fourteen listener rules,
versus one of each, for no behavioural difference: the faces are addressed as

    https://<faces host>/<face>/...

and mounted with Starlette so each sub-app keeps its own root. The A2A cards are
told their public URL explicitly — they advertise absolute URLs, and a mounted
app cannot infer its own prefix reliably.

The faces are deliberately the SAME objects the local stack serves: same
adapters, same `build_app`, same auth middleware. A face that behaved
differently hosted would make the protocol comparison meaningless, which is the
lab's whole subject.
"""

from __future__ import annotations

import contextlib
import os

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

# One entry per face: mount prefix -> (platform, protocol). The prefix is the
# target name in config/targets.yaml, so a reader can map a failing cell to a
# URL without a lookup table.
FACES: tuple[tuple[str, str, str], ...] = (
    ("claude-rest", "claude", "rest"),
    ("claude-mcp", "claude", "mcp"),
    ("claude-a2a", "claude", "a2a"),
    ("openai-rest", "openai", "rest"),
    ("openai-mcp", "openai", "mcp"),
    ("openai-a2a", "openai", "a2a"),
    # WS5: the strands faces serve the stub backend until Kiro delivers
    # strands-sdk (the stub needs no extra dep, so this is safe in the faces
    # image today; plan/12). Once the backend and its `strands` extra land,
    # the faces image gains the extra and these serve the real agent.
    ("strands-rest", "strands", "rest"),
    ("strands-mcp", "strands", "mcp"),
    ("strands-a2a", "strands", "a2a"),
    ("guide-rest", "guide", "rest"),
    ("guide-mcp", "guide", "mcp"),
    ("guide-a2a", "guide", "a2a"),
    ("agentforce-mcp", "agentforce", "mcp"),
    ("agentforce-a2a", "agentforce", "a2a"),
)

# The same variable config/targets.yaml expands for the hosted twins, so the
# address the cards ADVERTISE and the address clients are SENT to cannot
# drift apart.
PUBLIC_BASE_ENV = "A2ALAB_FACES_BASE"


def _adapter(platform: str):
    """Build the same adapter the standalone server would.

    Imported lazily and per face: pulling in every platform's backend at module
    import would make one missing optional dependency break all fourteen faces
    rather than the one that needs it.
    """
    if platform == "claude":
        from platforms.claude.core import make_adapter

        return make_adapter()
    if platform == "openai":
        from platforms.openai.core import make_adapter

        return make_adapter()
    if platform == "strands":
        from platforms.strands.core import make_adapter

        return make_adapter()
    if platform == "guide":
        from platforms.guide.core import make_adapter

        return make_adapter()
    if platform == "langgraph":
        # Not in the module-level FACES tuple (that is the Fargate faces task,
        # and langgraph is hosted on Heroku, WS4/D77) — but the dispatch is
        # shared so a langgraph-only faces app (build_faces_app(faces=...)) can
        # multiplex its three protocols behind one Heroku $PORT.
        from platforms.langgraph.core import make_adapter

        return make_adapter()
    if platform == "agentforce":
        from platforms.agentforce.proxy import AgentforceProxyAdapter

        return AgentforceProxyAdapter()
    raise ValueError(f"unknown platform for a face: {platform}")


def build_faces_app(
    public_base: str | None = None,
    faces: tuple[tuple[str, str, str], ...] | None = None,
) -> Starlette:
    """Mount every face under its own prefix on one ASGI app.

    `public_base` is the externally reachable origin (e.g.
    `https://faces-lab.example.com`). It is only needed by the A2A faces, whose
    AgentCard advertises an absolute URL that a client then calls back — get it
    wrong and the card points somewhere unreachable, which is the one failure a
    smoke test of the mount itself would not catch.

    `faces` selects which faces to mount, defaulting to the full FACES tuple
    (the Fargate faces task). A subset lets one host serve one platform's
    protocols behind a single port — e.g. the langgraph-only app on Heroku
    (WS4), which passes its own public_base (${A2ALAB_LANGGRAPH_BASE}).
    """
    from interop.adapter import build_app

    faces = faces or FACES
    base = (public_base or os.environ.get(PUBLIC_BASE_ENV) or "").rstrip("/")
    routes: list = []
    mounted: list[str] = []
    sub_apps: list = []
    for prefix, platform, protocol in faces:
        kwargs = {}
        if protocol == "a2a":
            # No base configured is a local smoke test; the card then advertises
            # a localhost URL, which is honest about what it is.
            kwargs["public_url"] = (
                f"{base}/{prefix}/" if base else f"http://localhost:8300/{prefix}/"
            )
        try:
            app = build_app(_adapter(platform), protocol, **kwargs)
        except Exception as exc:  # noqa: BLE001
            # One face that cannot build (a missing key, an optional dep) must
            # not take down the other ten — the lab's point is comparing cells,
            # and losing the whole board to one of them is the worse outcome.
            app = _broken_face(prefix, exc)
        routes.append(Mount(f"/{prefix}", app=app))
        mounted.append(prefix)
        inner = _lifespan_owner(app)
        if inner is not None:
            sub_apps.append(inner)

    async def index(_request):
        return JSONResponse({"faces": mounted, "public_base": base or None})

    async def healthz(_request):
        # Unauthenticated on purpose, like the console's: the ALB health check
        # carries no credentials and a gated health path never stabilises.
        return JSONResponse({"status": "healthy", "app": "faces", "faces": len(mounted)})

    routes.extend([Route("/", index), Route("/healthz", healthz)])

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        """Run every mounted sub-app's lifespan.

        Starlette's `Mount` does NOT propagate lifespan to the app it mounts,
        and the MCP faces need theirs: FastMCP's streamable-HTTP transport
        starts its session manager there, so a mounted-but-unstarted MCP app
        answers every call with `RuntimeError: Task group is not initialized`.
        That is the one failure that a smoke test of the mount alone would
        miss, because the route resolves perfectly — it is the transport
        underneath that never came up.
        """
        async with contextlib.AsyncExitStack() as stack:
            for sub in sub_apps:
                await stack.enter_async_context(sub.router.lifespan_context(sub))
            yield

    return Starlette(routes=routes, lifespan=lifespan)


def _lifespan_owner(app):
    """The innermost app with a lifespan, unwrapping our ASGI middlewares.

    `build_app` returns TokenAuthMiddleware(WireTapMiddleware(app)) for MCP, and
    both keep the wrapped app on `.app` — so the object that owns the lifespan
    is two layers down and invisible to the parent router.
    """
    seen = 0
    while app is not None and seen < 8:
        router = getattr(app, "router", None)
        if router is not None and hasattr(router, "lifespan_context"):
            return app
        app = getattr(app, "app", None)
        seen += 1
    return None


def _broken_face(prefix: str, exc: Exception):
    detail = f"{type(exc).__name__}: {exc}"

    async def app(scope, receive, send):
        response = JSONResponse(
            {"error": f"face '{prefix}' failed to start", "detail": detail}, status_code=503
        )
        await response(scope, receive, send)

    return app
