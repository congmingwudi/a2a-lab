# The Path A bridge, containerized for ECS Fargate (WS7 item 7).
#
# Deliberately NOT a Lambda behind API Gateway, which is the pattern every
# other hosted lab component uses (D23, D28, D41). An HTTP API's integration
# timeout maxes at 30s and is not adjustable, and the bridge's client timeout
# is 45s (plan/01-architecture.md, Path A budget chain). Hosting it that way
# would silently cut Path A's sync research depth by 15s — adopting the very
# constraint D41 exists to document. An ALB's idle timeout is configurable, so
# the budget survives the move off the laptop.
#
# No code changes: the bridge is already a FastAPI app and runs here exactly as
# it does under `uv run python -m bridge`.
#
# Smoke-test locally before pushing:
#   docker build -f deploy/bridge/Dockerfile -t a2alab-bridge .
#   docker run -e BRIDGE_TOKEN=x -p 8100:8100 a2alab-bridge
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
# --extra aws: boto3, needed twice over — the Postgres TraceSink writes hops
# through the RDS Data API, and the AgentCore client signs SigV4 with the task
# role. Without it the bridge starts, answers, and silently records nothing.
RUN uv sync --frozen --no-install-project --no-dev --extra aws

# The bridge CALLS every platform, so it needs each destination's auth library
# — but only the auth library. Deliberately not `--extra azure`/`--extra gcp`:
# those carry azure-ai-projects, azure-monitor-query, google-adk and
# aiplatform, which exist to HOST a Foundry/ADK agent or harvest its logs.
# Nothing here does either.
#   azure-identity  -> the A2A client's `azure-ad` scheme (Foundry's inbound
#                      A2A is Entra-only) via interop.cloud_auth
#   google-auth     -> the `google-adc` scheme for Agent Engine, and the
#                      AWS->GCP federation this task uses instead of a key
# Missing either is a ModuleNotFoundError at REQUEST time, not at start: the
# container boots healthy and only that one route 500s.
RUN uv pip install "azure-identity>=1.25.3" "google-auth>=2.35"

COPY src ./src
COPY config ./config

ENV PYTHONPATH=/app/src \
    PORT=8100

# uvicorn binds 0.0.0.0 so the ALB health check can reach it from outside the
# container's loopback.
CMD ["sh", "-c", "uv run python -m bridge --host 0.0.0.0 --port $PORT"]
