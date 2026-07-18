# openai-agent (agents-sdk backend) for Bedrock AgentCore Runtime (D4/M9).
# One protocol mode per deployment, AgentCore's port conventions:
#   HTTP :8080 POST /invocations + GET /ping  (PROTOCOL=rest PORT=8080)
#   MCP  :8000 /mcp                           (PROTOCOL=mcp  PORT=8000)
#   A2A  :9000 /                              (PROTOCOL=a2a  PORT=9000)
# No Node needed (unlike the Claude sdk image) — the OpenAI Agents SDK is
# pure Python. Smoke-test locally before pushing:
#   docker build -f deploy/agentcore/openai.Dockerfile -t a2alab-openai .
#   docker run -e OPENAI_API_KEY -e PROTOCOL=rest -e PORT=8080 -p 8080:8080 a2alab-openai
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
# --extra openai lands once the agents-sdk backend adds the dependency
# (plan/06-openai-codex-handoff.md §5).
RUN uv sync --frozen --no-install-project --no-dev --extra openai || \
    uv sync --frozen --no-install-project --no-dev
COPY src ./src
COPY config ./config

ENV PYTHONPATH=/app/src \
    OPENAI_BACKEND=agents-sdk \
    PROTOCOL=rest \
    PORT=8080

CMD ["sh", "-c", "uv run python -m platforms.openai --protocol $PROTOCOL --port $PORT"]
