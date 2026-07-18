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
    parser.add_argument(
        "--public-url",
        default=None,
        help="URL this shim's AgentCard advertises (per server — don't share "
        "A2A_PUBLIC_URL with the claude a2a server or both cards advertise "
        "the same URL)",
    )
    args = parser.parse_args()
    serve(
        AgentforceProxyAdapter(),
        "a2a",
        port=args.port,
        host=args.host,
        public_url=args.public_url or f"http://localhost:{args.port}/",
    )


if __name__ == "__main__":
    main()
