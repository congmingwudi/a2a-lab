"""Run the Lab Guide over a chosen protocol — the meta exhibit: the agent
that explains the lab, served through the lab's own inbound seam.

uv run python -m platforms.guide --protocol rest --port 8031
uv run python -m platforms.guide --protocol mcp  --port 8032
uv run python -m platforms.guide --protocol a2a  --port 8033
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from interop.adapter import serve
from platforms.guide.core import make_adapter

DEFAULT_PORTS = {"rest": 8031, "mcp": 8032, "a2a": 8033}


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Lab Guide server")
    parser.add_argument("--protocol", choices=["rest", "mcp", "a2a"], default="rest")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--public-url", default=None, help="a2a only: AgentCard URL")
    args = parser.parse_args()

    adapter = make_adapter()
    port = args.port or DEFAULT_PORTS[args.protocol]
    serve(adapter, args.protocol, port=port, host=args.host, public_url=args.public_url)


if __name__ == "__main__":
    main()
