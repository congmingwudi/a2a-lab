# The nine protocol faces, containerized for ECS Fargate (WS13 item 2).
#
# One image, one process, eleven mounted ASGI apps — see src/faces/__init__.py
# for why that rather than nine Fargate services (cost, and ECS's five target
# groups per service).
#
# This image needs MORE than the bridge's or the console's: those two only ever
# CALL platforms, while these faces ARE the platforms. The Claude and Lab Guide
# faces answer in-process, so the agent SDKs have to be here.
#
# Smoke-test locally before pushing:
#   docker build -f deploy/faces/Dockerfile -t a2alab-faces .
#   docker run -e A2ALAB_TOKEN=x -p 8300:8300 a2alab-faces
#   curl localhost:8300/ ; curl localhost:8300/healthz
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
# --extra aws: boto3 for the Postgres trace sink (hops must leave the container)
# and for SigV4 against the AgentCore runtimes.
RUN uv sync --frozen --no-install-project --no-dev --extra aws

# The faces CALL Agentforce (both shims) and Foundry/Agent Engine is reachable
# from the adapters' tools, so the same two auth libraries the bridge carries,
# for the same reason: a ModuleNotFoundError at REQUEST time on one route
# rather than a failure at start.
RUN uv pip install "azure-identity>=1.25.3" "google-auth>=2.35"

# The OpenAI faces ARE the OpenAI platform, not a caller of it: with
# OPENAI_BACKEND=agents-sdk the adapter runs the Agents SDK in-process, so a
# missing `agents` module is a ModuleNotFoundError inside the tool call —
# the face starts healthy and fails only when asked to answer.
RUN uv pip install "openai-agents>=0.18.3,<1"

COPY src ./src
COPY config ./config
# The Lab Guide's corpus is repo prose — its three faces answer FROM these
# files, so an image with only src/ serves a guide that knows nothing.
COPY plan ./plan
COPY docs ./docs
COPY build-notes ./build-notes
COPY README.md CLAUDE.md ./

ENV PYTHONPATH=/app/src \
    PORT=8300 \
    A2ALAB_TRACE_SINK=postgres \
    A2ALAB_OBS_STORE=postgres

CMD ["sh", "-c", "uv run python -m faces --host 0.0.0.0 --port $PORT"]
