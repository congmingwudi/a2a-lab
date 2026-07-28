"""Run every protocol face in one process (WS13 item 2).

    uv run python -m faces --port 8300

Hosted, this is the container that replaces the eleven `uv run python -m ...`
servers `scripts/run_local.sh` starts. Locally it is a quick way to bring the
whole board up on one port; `run_local.sh` still runs them separately, because
one-process-per-face is what makes a hung face obvious there.
"""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn
from dotenv import load_dotenv

from faces import PUBLIC_BASE_ENV, build_faces_app
from interop.secret_env import load_secret_env_and_log


def main() -> None:
    load_dotenv()
    # Hosted: credentials come from Secrets Manager before any adapter reads
    # os.environ. A no-op locally, where .env holds everything (D48).
    load_secret_env_and_log("faces")
    # Fail CLOSED, same rule as the console: these faces are public internet
    # behind an ALB, and TokenAuthMiddleware treats a missing A2ALAB_TOKEN as
    # "auth is off". A hosted container without its token must not serve.
    if os.environ.get("A2ALAB_RUNTIME_SECRET_ARN") and not os.environ.get("A2ALAB_TOKEN"):
        sys.exit(
            "faces: A2ALAB_RUNTIME_SECRET_ARN is set but A2ALAB_TOKEN is not — "
            "refusing to start with authentication disabled."
        )
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8300)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--public-base",
        default=None,
        help=f"externally reachable origin for the A2A cards (else {PUBLIC_BASE_ENV})",
    )
    args = parser.parse_args()
    uvicorn.run(build_faces_app(args.public_base), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
