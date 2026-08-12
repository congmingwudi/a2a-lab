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
# google-auth for the NATIVE-DIRECT cross-hyperscaler leg (WS5: Strands -> ADK).
# The direct route federates the container's AWS role into a Google SA via
# interop.cloud_auth -> google.auth.aws; google-auth is pulled ONLY by the `gcp`
# extra (google-adk et al.), which this image does not install, so it must be
# added explicitly — the same reason the fan-out Lambda's build_zip.sh installs
# it. Without it the direct route 500s on ModuleNotFoundError while the bridge
# route (plain httpx to the already-federated bridge) still works, which would
# read as "only one of the two routes is broken" — a confusing half-failure.
RUN uv pip install "google-auth>=2.35"
COPY src ./src
COPY config ./config

ENV PYTHONPATH=/app/src \
    STRANDS_BACKEND=strands-sdk \
    PROTOCOL=rest \
    PORT=8080

CMD ["sh", "-c", "uv run python -m platforms.strands --protocol $PROTOCOL --port $PORT"]
