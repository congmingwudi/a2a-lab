#!/usr/bin/env bash
# Build the a2alab-af-shim Lambda bundle (D28): the A2A shim app + vendored
# deps for linux/arm64 py3.12 (matches the obs Lambdas' platform).
#
#   deploy/shim/build_zip.sh   ->  deploy/shim/dist/a2alab-af-shim.zip
set -euo pipefail
cd "$(dirname "$0")/../.."

DIST=deploy/shim/dist
STAGE="$DIST/stage"
rm -rf "$STAGE" "$DIST/a2alab-af-shim.zip"
mkdir -p "$STAGE"

# Vendored deps — platform wheels only, so compiled packages (pydantic-core)
# match the Lambda runtime.
uv pip install --target "$STAGE" \
  --python-platform aarch64-manylinux2014 --python-version 3.12 \
  --only-binary :all: \
  "a2a-sdk[http-server]>=1.1,<2" "fastapi>=0.115" "mangum>=0.19" \
  "httpx>=0.28,<1" "pyyaml>=6.0" "python-dotenv>=1.0" >/dev/null

# Lab code: interop + the agentforce platform package + observability (the
# postgres trace sink — shim hops go to the Aurora store, D23/D28; the Data
# API path needs only boto3, which the Lambda runtime provides).
cp -R src/interop "$STAGE/interop"
mkdir -p "$STAGE/platforms"
touch "$STAGE/platforms/__init__.py"
cp -R src/platforms/agentforce "$STAGE/platforms/agentforce"
cp -R src/observability "$STAGE/observability"
cp deploy/shim/handler.py "$STAGE/handler.py"
find "$STAGE" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

(cd "$STAGE" && zip -qr "../a2alab-af-shim.zip" .)
rm -rf "$STAGE"
ls -lh "$DIST/a2alab-af-shim.zip"
