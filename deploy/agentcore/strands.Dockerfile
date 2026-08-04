# strands-agent (strands-sdk backend) for Bedrock AgentCore Runtime (WS5/D66).
# One protocol mode per deployment, AgentCore's port conventions:
#   HTTP :8080 POST /invocations + GET /ping  (PROTOCOL=rest PORT=8080)
#   MCP  :8000 /mcp                           (PROTOCOL=mcp  PORT=8000)
#   A2A  :9000 /                              (PROTOCOL=a2a  PORT=9000)
# No Node needed (like the OpenAI image; unlike the Claude sdk image) — the
# Strands Agents SDK is pure Python. The model runs on Amazon Bedrock, so the
# container needs NO model API key: the runtime's IAM execution role calls
# bedrock:InvokeModel (grant added by deploy/agentcore/deploy.sh strands).
# Smoke-test locally before pushing:
#   docker build -f deploy/agentcore/strands.Dockerfile -t a2alab-strands .
#   docker run -e PROTOCOL=rest -e PORT=8080 -p 8080:8080 a2alab-strands   # stub backend
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
# strands = the strands-sdk backend (Kiro's deliverable; the extra is Kiro's
# to add to pyproject.toml/uv.lock, plan/12). aws = boto3 for the postgres
# TraceSink (Data API) AND the Bedrock model calls — without it the
# container's hops are silently contained-and-dropped ("[trace] PostgresSink
# failed: No module named 'boto3'"), same lesson as openai.Dockerfile.
RUN uv sync --frozen --no-install-project --no-dev --extra strands --extra aws
COPY src ./src
COPY config ./config

ENV PYTHONPATH=/app/src \
    STRANDS_BACKEND=strands-sdk \
    PROTOCOL=rest \
    PORT=8080

CMD ["sh", "-c", "uv run python -m platforms.strands --protocol $PROTOCOL --port $PORT"]
