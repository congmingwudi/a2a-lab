"""Run the Claude research agent over a chosen protocol.

uv run python -m platforms.claude --protocol rest --port 8001
uv run python -m platforms.claude --protocol mcp  --port 8002
uv run python -m platforms.claude --protocol a2a  --port 8003
CLAUDE_BACKEND=sdk uv run python -m platforms.claude --protocol rest --port 8001
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from interop.adapter import serve
from platforms.claude.core import make_adapter

DEFAULT_PORTS = {"rest": 8001, "mcp": 8002, "a2a": 8003}


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Claude research agent server")
    parser.add_argument("--protocol", choices=["rest", "mcp", "a2a"], default="rest")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--backend", choices=["managed", "sdk"], default=None)
    parser.add_argument(
        "--public-url",
        default=None,
        help="a2a only: URL the AgentCard advertises (per server — a shared "
        "A2A_PUBLIC_URL env would make every card on this machine advertise "
        "the same URL)",
    )
    args = parser.parse_args()

    adapter = make_adapter(args.backend)
    port = args.port or DEFAULT_PORTS[args.protocol]
    serve(adapter, args.protocol, port=port, host=args.host, public_url=args.public_url)


if __name__ == "__main__":
    main()
