# The lab console, containerized for ECS Fargate (WS13).
#
# Same shape as deploy/bridge/Dockerfile — the console is a FastAPI app and
# runs here exactly as under `uv run python -m console`.
#
# **The console is not just code: it RENDERS the repo.** `/api/docs` serves
# `plan/*.md`, `docs/*.md`, `build-notes/**/*.md` and `README.md`; the Insights
# section reads `config/insights.yaml`; the Architecture section parses
# `plan/09-deployment-map.md`; the Lab Guide's corpus is repo prose. A minimal
# image that copies only `src/` starts healthy and then 404s every doc chip —
# a failure that looks like a broken UI and is a missing COPY. So the markdown
# trees ship WITH the image, which also means a doc edit needs a redeploy to
# appear: correct, since the docs are versioned with the code that cites them.
#
# Smoke-test locally before pushing:
#   docker build -f deploy/console/Dockerfile -t a2alab-console .
#   docker run -e A2ALAB_TOKEN=x -p 8200:8200 a2alab-console
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
# --extra aws: boto3 for the RDS Data API. The hosted console reads traces,
# observability rows, briefs and the credential snapshot from Aurora — with no
# local traces/ directory and no .a2alab, that is its ONLY data source.
RUN uv sync --frozen --no-install-project --no-dev --extra aws

# The console CALLS platform targets for the Run buttons, so it needs the same
# two auth libraries the bridge does, for the same reason: a ModuleNotFoundError
# at request time on one route, not a failure at start.
RUN uv pip install "azure-identity>=1.25.3" "google-auth>=2.35"

COPY src ./src
COPY config ./config
# The rendered corpus. Keep in step with the /api/docs whitelist in
# src/console/app.py — a path the whitelist serves but the image lacks is a
# chip that 404s, which CLAUDE.md calls out as worse than plain text.
COPY plan ./plan
COPY docs ./docs
COPY build-notes ./build-notes
COPY README.md CLAUDE.md ./

ENV PYTHONPATH=/app/src \
    PORT=8200 \
    A2ALAB_TRACE_SINK=postgres \
    A2ALAB_OBS_STORE=postgres

# uvicorn binds 0.0.0.0 so the ALB health check reaches it from outside the
# container's loopback.
CMD ["sh", "-c", "uv run python -m console --host 0.0.0.0 --port $PORT"]
