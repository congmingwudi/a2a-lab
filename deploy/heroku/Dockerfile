# The LangGraph agent (WS4), containerized for HEROKU (the operator's host
# choice, D77) — the lab's first non-AWS-hosted platform.
#
# One image, one web dyno, three mounted ASGI apps: `--protocol all` reuses the
# faces multiplexer (src/faces) so the langgraph REST/MCP/A2A faces sit behind
# ONE $PORT — Heroku gives a web dyno exactly one HTTP port. That is why the
# langgraph-*-hosted twins in config/targets.yaml are paths under one origin
# (${A2ALAB_LANGGRAPH_BASE}/langgraph-rest, /langgraph-mcp/mcp, /langgraph-a2a),
# the same shape as the Fargate faces twins.
#
# This image needs the `langgraph` extra (the agent IS LangGraph, in-process)
# and `aws` (boto3): traces leave the dyno to the shared Aurora store via the
# rds-data DATA API — an HTTPS AWS API reachable from off-VPC Heroku with a
# scoped AWS access key, so no VPC peering is required (A2ALAB_TRACE_SINK=
# postgres + A2ALAB_PG_CLUSTER_ARN/A2ALAB_PG_SECRET_ARN + AWS creds config vars).
#
# Smoke-test locally before pushing (stub backend, no keys needed):
#   docker build -f deploy/heroku/Dockerfile -t a2alab-langgraph .
#   docker run -e PORT=8050 -e A2ALAB_TOKEN=x -e LANGGRAPH_BACKEND=stub \
#     -e A2ALAB_TRACE_SINK=jsonl -p 8050:8050 a2alab-langgraph
#   curl localhost:8050/ ; curl localhost:8050/healthz
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
# langgraph: the in-process agent framework + langchain-anthropic brain.
# aws: boto3 for the Postgres/Data-API trace sink (hops must leave the dyno).
RUN uv sync --frozen --no-install-project --no-dev --extra langgraph --extra aws

COPY src ./src
COPY config ./config

ENV PYTHONPATH=/app/src \
    LANGGRAPH_BACKEND=langgraph \
    A2ALAB_TRACE_SINK=postgres

# Heroku assigns $PORT at runtime; --protocol all + $PORT bind all three faces.
CMD ["sh", "-c", "uv run python -m platforms.langgraph --protocol all --host 0.0.0.0 --port ${PORT:-8050}"]
