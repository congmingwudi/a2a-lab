"""RemoteAgentClient for agents hosted on Bedrock AgentCore Runtime (D4/D24/D26).

Platform-agnostic: any lab platform containerized behind the REST
``POST /invocations`` + ``GET /ping`` contract (see deploy/agentcore/) is
reachable through this client — the target's ``auth.runtime_arn`` selects
the runtime, nothing else is platform-specific.

Bedrock AgentCore Runtime's data plane is IAM-authed (SigV4) — there is no
raw public HTTP endpoint — so the lab reaches the hosted agent through
boto3 ``invoke_agent_runtime``: the JSON payload lands on the container's
POST /invocations (the same handler the local rest servers serve), and the
response body is the canonical AgentResponse dict. App-level bearer auth is
intentionally OFF in the runtime (A2ALAB_TOKEN unset there): IAM already
gates the data plane, and invoke carries no custom headers.

Target config (config/targets.yaml):
    openai-agentcore:
      platform: openai
      protocol: agentcore-http
      auth: {runtime_arn: "${OPENAI_AGENTCORE_ARN}"}
    claude-agentcore:
      platform: claude
      protocol: agentcore-http
      auth: {runtime_arn: "${CLAUDE_AGENTCORE_ARN}"}
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from interop.clients.base import RemoteAgentClient
from interop.models import AgentRequest, AgentResponse
from interop.trace import Hop

DEFAULT_TIMEOUT = 65.0


class AgentCoreClient(RemoteAgentClient):
    protocol = "agentcore-http"

    def __init__(
        self,
        runtime_arn: str,
        *,
        auth: dict[str, Any] | None = None,
        target_name: str = "agentcore",
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.runtime_arn = runtime_arn
        self.target_name = target_name
        self.timeout = timeout
        self._client = None
        # AgentCore requires runtimeSessionId >= 33 chars; map lab session
        # ids to stable runtime ids so conversations stick to a container.
        self._session_ids: dict[str, str] = {}

    @classmethod
    def from_target(cls, target) -> "AgentCoreClient":
        auth = target.auth or {}
        runtime_arn = auth.get("runtime_arn") or ""
        if not runtime_arn:
            raise RuntimeError(
                f"target '{target.name}' has no runtime_arn — deploy the runtime with "
                f"deploy/agentcore/deploy.sh {target.platform}, then set the ARN env var "
                "referenced by this target in config/targets.yaml"
            )
        kwargs: dict[str, Any] = {"auth": auth, "target_name": target.name}
        if (target.options or {}).get("timeout"):
            kwargs["timeout"] = float(target.options["timeout"])
        return cls(runtime_arn, **kwargs)

    def _boto(self):
        if self._client is None:
            import boto3

            region = self.runtime_arn.split(":")[3]
            self._client = boto3.client("bedrock-agentcore", region_name=region)
        return self._client

    def _runtime_session_id(self, lab_session_id: str | None) -> str:
        # Sessionless requests get a FRESH runtime session every time:
        # AgentCore session ids carry instance affinity, so a cached one
        # keeps routing to a pre-update warm instance (stale env/image)
        # after a runtime deploy — observed as tool failures only the
        # console could reproduce.
        if lab_session_id is None:
            return f"a2alab-adhoc-{uuid.uuid4().hex}"[:100]
        if lab_session_id not in self._session_ids:
            self._session_ids[lab_session_id] = f"a2alab-{lab_session_id}-{uuid.uuid4().hex}"[:100]
        return self._session_ids[lab_session_id]

    def _invoke_sync(self, payload: dict[str, Any], runtime_session_id: str) -> dict[str, Any]:
        resp = self._boto().invoke_agent_runtime(
            agentRuntimeArn=self.runtime_arn,
            runtimeSessionId=runtime_session_id,
            payload=json.dumps(payload).encode("utf-8"),
        )
        body = resp["response"].read()
        return json.loads(body)

    async def ask(self, req: AgentRequest) -> AgentResponse:
        payload = req.to_dict()
        runtime_session_id = self._runtime_session_id(req.session_id)
        with Hop(
            req.trace_id or "untraced",
            source="remote-caller",
            target=self.target_name,
            protocol=self.protocol,
            transport_detail="invoke_agent_runtime POST /invocations",
            request_payload=payload,
        ) as hop:
            hop.platform_ref = runtime_session_id
            data = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, self._invoke_sync, payload, runtime_session_id
                ),
                self.timeout,
            )
            hop.response_payload = data
        return AgentResponse.from_dict(data)
