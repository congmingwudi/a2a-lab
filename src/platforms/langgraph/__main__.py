"""Run the LangGraph research agent over a chosen protocol.

uv run python -m platforms.langgraph --protocol rest --port 8051
uv run python -m platforms.langgraph --protocol mcp  --port 8052
uv run python -m platforms.langgraph --protocol a2a  --port 8053
LANGGRAPH_BACKEND=langgraph uv run python -m platforms.langgraph --protocol rest
"""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

from interop.adapter import serve
from interop.secret_env import load_secret_env_and_log
from platforms.langgraph.core import make_adapter

DEFAULT_PORTS = {"rest": 8051, "mcp": 8052, "a2a": 8053, "all": 8050}

# The three langgraph faces, mounted behind ONE port for the Heroku host (one
# web dyno = one $PORT). Prefixes match the langgraph-*-hosted targets.yaml
# endpoints (${A2ALAB_LANGGRAPH_BASE}/langgraph-rest, /langgraph-mcp/mcp,
# /langgraph-a2a) — same shape as the Fargate faces twins.
LANGGRAPH_FACES = (
    ("langgraph-rest", "langgraph", "rest"),
    ("langgraph-mcp", "langgraph", "mcp"),
    ("langgraph-a2a", "langgraph", "a2a"),
)


def main() -> None:
    load_dotenv()
    # Hosted runs (Heroku) get their credentials from Secrets Manager before
    # the adapter reads os.environ; a no-op locally, where .env rules.
    load_secret_env_and_log("langgraph")
    parser = argparse.ArgumentParser(description="LangGraph research agent server")
    parser.add_argument("--protocol", choices=["rest", "mcp", "a2a", "all"], default="rest")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--backend", choices=["stub", "langgraph"], default=None)
    parser.add_argument(
        "--public-url",
        default=None,
        help="a2a only: URL the AgentCard advertises",
    )
    args = parser.parse_args()

    if args.backend:
        os.environ["LANGGRAPH_BACKEND"] = args.backend

    # Heroku assigns the listen port at runtime via $PORT; honour it when no
    # explicit --port is given so the same entry point works on Heroku (one
    # web dyno = one $PORT) and locally (fixed DEFAULT_PORTS).
    env_port = os.environ.get("PORT")
    port = args.port or (int(env_port) if env_port else DEFAULT_PORTS[args.protocol])

    if args.protocol == "all":
        # One dyno serves all three langgraph protocols behind one port — the
        # Heroku host shape (deploy/heroku). Reuses the faces multiplexer so the
        # MCP-lifespan and A2A-card-URL handling is the SAME code the Fargate
        # faces task uses. public_base is the Heroku app origin.
        import uvicorn

        from faces import build_faces_app

        public_base = args.public_url or os.environ.get("A2ALAB_LANGGRAPH_BASE")
        app = build_faces_app(public_base=public_base, faces=LANGGRAPH_FACES)
        uvicorn.run(app, host=args.host, port=port)
        return

    adapter = make_adapter(args.backend)
    serve(adapter, args.protocol, port=port, host=args.host, public_url=args.public_url)


if __name__ == "__main__":
    main()
