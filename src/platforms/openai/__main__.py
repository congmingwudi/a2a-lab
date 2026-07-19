"""Run the OpenAI research agent over a chosen protocol.

uv run python -m platforms.openai --protocol rest --port 8011
uv run python -m platforms.openai --protocol mcp  --port 8012
uv run python -m platforms.openai --protocol a2a  --port 8013
OPENAI_BACKEND=agents-sdk uv run python -m platforms.openai --protocol rest
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from interop.adapter import serve
from platforms.openai.core import make_adapter

DEFAULT_PORTS = {"rest": 8011, "mcp": 8012, "a2a": 8013}


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="OpenAI research agent server")
    parser.add_argument("--protocol", choices=["rest", "mcp", "a2a"], default="rest")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--backend", choices=["stub", "agents-sdk"], default=None)
    parser.add_argument(
        "--public-url",
        default=None,
        help="a2a only: URL the AgentCard advertises",
    )
    args = parser.parse_args()

    adapter = make_adapter(args.backend)
    port = args.port or DEFAULT_PORTS[args.protocol]
    serve(adapter, args.protocol, port=port, host=args.host, public_url=args.public_url)


if __name__ == "__main__":
    main()
