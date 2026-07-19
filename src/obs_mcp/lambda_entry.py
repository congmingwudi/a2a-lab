"""AWS Lambda entrypoint for the obs MCP server (Function URL, D23).

Handler: obs_mcp/lambda_entry.handler — stdlib + boto3 only (the Data API
backend), so the deployment zip is just this repo's source. Env:
A2ALAB_PG_CLUSTER_ARN, A2ALAB_PG_SECRET_ARN (lab_reader),
A2ALAB_PG_WRITER_SECRET_ARN (save_brief + trace hops),
A2ALAB_OBS_MCP_TOKEN (bearer auth — matches the vault static_bearer
credential on the Anthropic side).
"""

from __future__ import annotations

import os

from obs_mcp.http import make_lambda_handler
from obs_mcp.tools import build_registry

handler = make_lambda_handler(build_registry(), os.environ.get("A2ALAB_OBS_MCP_TOKEN"))
