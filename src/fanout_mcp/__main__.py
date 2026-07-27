"""Run the fan-out MCP server locally: uv run python -m fanout_mcp --port 8251.

Same registry as the Lambda, so the tools reach the real business-unit agents
from here. Two uses: poking the wire by hand (curl JSON-RPC), and running the
MCP variant of the orchestrator against a local server through the tunnel while
the hosted one is still being provisioned.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fanout_mcp import SERVER_INFO  # noqa: E402
from fanout_mcp.tools import auth_token, build_registry  # noqa: E402
from mcp_http.http import create_local_app  # noqa: E402


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8251)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    token = auth_token()
    if not token:
        # Loud, because the failure it prevents is silent: an unauthenticated
        # server that works perfectly in testing and is then exposed through
        # the tunnel with every business-unit agent behind it.
        print("warning: A2ALAB_FANOUT_MCP_TOKEN unset — this server accepts anyone")
    app = create_local_app(build_registry(), token, SERVER_INFO)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
