"""Run the obs MCP server locally: uv run python -m obs_mcp --port 8250.

Same registry as the Lambda; useful for wire-level poking (curl JSON-RPC)
and for fronting a local Postgres/DSN during development.
"""

from __future__ import annotations

import argparse
import os

import uvicorn
from dotenv import load_dotenv

from obs_mcp.http import create_local_app
from obs_mcp.tools import build_registry


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8250)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    app = create_local_app(build_registry(), os.environ.get("A2ALAB_OBS_MCP_TOKEN"))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
