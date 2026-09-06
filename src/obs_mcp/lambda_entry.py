"""AWS Lambda entrypoint for the obs MCP server (D23).

Handler: obs_mcp/lambda_entry.handler — stdlib + boto3 only (the Data API
backend), so the deployment zip is just this repo's source. Env:
A2ALAB_PG_CLUSTER_ARN, A2ALAB_PG_SECRET_ARN (lab_reader),
A2ALAB_PG_WRITER_SECRET_ARN (save_brief + trace hops),
A2ALAB_OBS_MCP_TOKEN (bearer auth — matches the vault static_bearer
credential on the Anthropic side).
"""

from __future__ import annotations

import os

from interop.secret_env import load_secret_env_and_log
from mcp_http.http import make_lambda_handler
from obs_mcp import SERVER_INFO
from obs_mcp.tools import build_registry

# A no-op unless A2ALAB_RUNTIME_SECRET_ARN is set (the token rides this
# function's own env today); done first so a future move of the token into the
# runtime secret needs no code change. make_lambda_handler then refuses to
# build with an empty token on a hosted runtime (WS25 A3, D80/F06).
load_secret_env_and_log("obs-mcp")

handler = make_lambda_handler(build_registry(), os.environ.get("A2ALAB_OBS_MCP_TOKEN"), SERVER_INFO)
