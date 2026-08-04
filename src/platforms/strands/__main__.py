"""Run the AWS Strands research agent over a chosen protocol.

uv run python -m platforms.strands --protocol rest --port 8041
uv run python -m platforms.strands --protocol mcp  --port 8042
uv run python -m platforms.strands --protocol a2a  --port 8043
STRANDS_BACKEND=strands-sdk uv run python -m platforms.strands --protocol rest
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from interop.adapter import serve
from interop.secret_env import load_secret_env_and_log
from platforms.strands.core import make_adapter

DEFAULT_PORTS = {"rest": 8041, "mcp": 8042, "a2a": 8043}


def main() -> None:
    load_dotenv()
    # F1: hosted (AgentCore) runs get their credentials from Secrets Manager
    # before the adapter reads os.environ; a no-op locally, where .env rules.
    load_secret_env_and_log("strands")
    parser = argparse.ArgumentParser(description="AWS Strands research agent server")
    parser.add_argument("--protocol", choices=["rest", "mcp", "a2a"], default="rest")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--backend", choices=["stub", "strands-sdk"], default=None)
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
