"""AWS Lambda entrypoint for the fan-out MCP server (WS7 item 4).

Handler: fanout_mcp/lambda_entry.handler. Unlike the obs server this one is NOT
stdlib-only — each tool call makes a real cross-cloud agent call, so the bundle
carries httpx, the a2a-sdk, google-auth and azure-identity (see
deploy/fanout/build_zip.sh).

Env it needs, and why each one:

    A2ALAB_FANOUT_MCP_TOKEN     bearer auth, matching the vault static_bearer
                                credential on the Anthropic side
    A2ALAB_TARGETS_PATH         the bundled targets.yaml
    ADK_*/FOUNDRY_*/OPENAI_*    the leg endpoints. Shipping a target by NAME
                                without the ${VAR}s its endpoint expands from
                                is the deploy-manifest bug that cost the ADK
                                orchestrator three legs (7f0f625): an unset var
                                expands to "" and an empty endpoint fails as a
                                network error, so the manifest bug reads as
                                connectivity.
    A2ALAB_GCP_WORKLOAD_*       AWS -> GCP federation (interop/cloud_auth.py)
    AZURE_*                     the Entra service principal, from Secrets Manager
    A2ALAB_TRACE_SINK=dynamodb  per-leg Hops must leave the function; the local
                                jsonl sink writes to a container that is about
                                to disappear

Timeouts: a leg is allowed 120s (orchestration.runner.LEG_TIMEOUT_S), so the
function timeout must exceed that or the tool call dies before the leg does and
the model is told nothing useful.
"""

from __future__ import annotations

from fanout_mcp import SERVER_INFO
from fanout_mcp.tools import auth_token, build_registry
from mcp_http.http import make_lambda_handler

# Built at cold start, deliberately: the registry construction reads
# config/targets.yaml and the leg agent definitions, and doing that per request
# would pay the parse on every tool call for a value that never changes.
handler = make_lambda_handler(build_registry(), auth_token(), SERVER_INFO)
