"""Lambda handler for the AWS-hosted Agentforce A2A shim (D28).

The same shim that runs locally on :8023 — `create_a2a_app` over
`AgentforceProxyAdapter` — wrapped for Lambda with Mangum and exposed
through API Gateway (deploy_shim.sh; Function URLs are SCP-blocked in this
account, same story as the obs MCP endpoint, D23). Removes the laptop from
the a2a-shim channel entirely: any cloud container (AgentCore, Agent
Engine) can reach Agentforce over A2A through this endpoint.

App-layer auth: the shim enforces x-lab-token (A2ALAB_TOKEN env) like every
other lab server; the agent-card well-known path stays auth-exempt.
Interior trace hops write to /tmp (ephemeral — the caller's client hop is
the lab record, same honest posture as the other hosted runtimes).
"""

import os

os.environ.setdefault("A2ALAB_TRACE_DIR", "/tmp/traces")

from mangum import Mangum  # noqa: E402

from interop.servers.a2a import create_a2a_app  # noqa: E402
from interop.servers.auth import TokenAuthMiddleware  # noqa: E402
from platforms.agentforce.proxy import AgentforceProxyAdapter  # noqa: E402

app = create_a2a_app(
    AgentforceProxyAdapter(session_reuse=True),
    public_url=os.environ.get("AF_SHIM_PUBLIC_URL", "https://unset.invalid/"),
    # WireTap ON: since its buffer-and-replay rewrite it runs under Mangum,
    # so the shim captures the raw inbound A2A envelope (e.g. Foundry's 0.3
    # message/send) alongside the adapter-level Agent API hops — the
    # foundry→shim leg stops being dark at the server side.
    wiretap=True,
)

# App-layer bearer auth, explicitly: build_app() applies this wrapper for
# the locally-served apps, but this handler mounts create_a2a_app directly
# — without it the public API Gateway URL serves JSON-RPC unauthenticated
# (found live 2026-07-22; the card path stays exempt by design).
app = TokenAuthMiddleware(app)


class _HeaderLogger:
    """A2ALAB_DEBUG_HEADERS=1: log inbound header NAMES (+ whether the lab
    token header is present — never its value) to CloudWatch. For debugging
    third-party callers' auth behavior (e.g. what a Foundry project
    connection actually sends); off by default."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and os.environ.get("A2ALAB_DEBUG_HEADERS"):
            names = [k.decode("latin-1").lower() for k, v in scope.get("headers", [])]
            print(
                f"[hdr-debug] {scope.get('method')} {scope.get('path')} "
                f"headers={sorted(names)} x-lab-token={'x-lab-token' in names}"
            )
        await self.inner(scope, receive, send)


handler = Mangum(_HeaderLogger(app), lifespan="off")
