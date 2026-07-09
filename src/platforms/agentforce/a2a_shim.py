"""A2A shim for Agentforce (Path B via-shim cell).

    uv run python -m platforms.agentforce.a2a_shim --port 8023
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from interop.adapter import serve
from platforms.agentforce.proxy import AgentforceProxyAdapter


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8023)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    serve(AgentforceProxyAdapter(), "a2a", port=args.port, host=args.host)


if __name__ == "__main__":
    main()
