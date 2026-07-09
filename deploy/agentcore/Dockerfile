# claude-agent (sdk backend) for Bedrock AgentCore Runtime.
# One protocol mode per deployment:
#   HTTP :8080 POST /invocations + GET /ping  (PROTOCOL=rest PORT=8080)
#   MCP  :8000 /mcp                           (PROTOCOL=mcp  PORT=8000)
#   A2A  :9000 /                              (PROTOCOL=a2a  PORT=9000)
# The image bundles Node because claude-agent-sdk spawns the Claude Code CLI.
# Smoke-test locally before pushing:
#   docker build -f deploy/agentcore/Dockerfile -t a2alab-claude .
#   docker run -e ANTHROPIC_API_KEY -e PROTOCOL=rest -e PORT=8080 -p 8080:8080 a2alab-claude
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
COPY src ./src
COPY config ./config

ENV PYTHONPATH=/app/src \
    CLAUDE_BACKEND=sdk \
    PROTOCOL=rest \
    PORT=8080

CMD ["sh", "-c", "uv run python -m platforms.claude --protocol $PROTOCOL --port $PORT"]
