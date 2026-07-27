#!/usr/bin/env bash
# Build the a2alab-fanout-mcp Lambda bundle (WS7 item 4).
#
#   deploy/fanout/build_zip.sh   ->  deploy/fanout/dist/a2alab-fanout-mcp.zip
#
# Unlike the obs MCP zip this one is NOT stdlib-only: every tool call makes a
# real cross-cloud agent call, so the whole outbound client stack ships —
# a2a-sdk for the two A2A legs, google-auth for the AWS->GCP federation,
# azure-identity for the Entra service principal, and boto3 comes from the
# runtime for the AgentCore leg. Measured ~40MB unpacked, well inside Lambda's
# 250MB limit.
set -euo pipefail
cd "$(dirname "$0")/../.."

DIST=deploy/fanout/dist
STAGE="$DIST/stage"
rm -rf "$STAGE" "$DIST/a2alab-fanout-mcp.zip"
mkdir -p "$STAGE"

# Platform wheels only, so compiled packages (pydantic-core, cryptography)
# match the Lambda runtime rather than this laptop's arm64 macOS.
uv pip install --target "$STAGE" \
  --python-platform aarch64-manylinux2014 --python-version 3.12 \
  --only-binary :all: \
  "a2a-sdk>=1.1,<2" "httpx>=0.28,<1" "pyyaml>=6.0" "python-dotenv>=1.0" \
  "google-auth>=2.35" "azure-identity>=1.19" >/dev/null

# Lab code. mcp_http is the transport, fanout_mcp the tools, orchestration the
# leg definitions and the single-leg runner, interop the clients/registry/trace.
cp -R src/mcp_http "$STAGE/mcp_http"
cp -R src/fanout_mcp "$STAGE/fanout_mcp"
cp -R src/orchestration "$STAGE/orchestration"
cp -R src/interop "$STAGE/interop"
cp -R src/observability "$STAGE/observability"
rm -f "$STAGE/observability/analyst.py"

# The targets file, bundled. Shipping a target by NAME without the ${VAR}s its
# endpoint expands from is the deploy-manifest bug that cost the ADK
# orchestrator all three legs (7f0f625): an unset var expands to "" and an
# empty endpoint fails as a network error, so the manifest bug reads as
# connectivity. deploy_fanout.sh sets those vars on the function.
mkdir -p "$STAGE/labconfig"
cp config/targets.yaml "$STAGE/labconfig/targets.yaml"

find "$STAGE" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
(cd "$STAGE" && zip -qr "../a2alab-fanout-mcp.zip" .)
rm -rf "$STAGE"
ls -lh "$DIST/a2alab-fanout-mcp.zip"
