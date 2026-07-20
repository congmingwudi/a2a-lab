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
from platforms.agentforce.proxy import AgentforceProxyAdapter  # noqa: E402

app = create_a2a_app(
    AgentforceProxyAdapter(session_reuse=True),
    public_url=os.environ.get("AF_SHIM_PUBLIC_URL", "https://unset.invalid/"),
    # Mangum's single-shot body receive hangs the WireTap middleware; the
    # adapter-level Hops still record (D28).
    wiretap=False,
)

handler = Mangum(app, lifespan="off")
