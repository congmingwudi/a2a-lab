#!/usr/bin/env bash
# Build the two hosted-obs Lambda zips (ADR D23) into deploy/obs/dist/.
#
#   deploy/obs/build_zips.sh
#
# a2alab-obs-mcp.zip     — the MCP server (stdlib + boto3-in-runtime only)
# a2alab-obs-harvest.zip — the harvest cron (bundles anthropic + httpx for
#                          linux/arm64 python3.12)
#
# Packaging shim: interop/__init__.py (and, for the MCP zip,
# observability/__init__.py) are replaced with empty-docstring versions so
# importing interop.trace / observability.pg doesn't drag pydantic+fastapi
# into the bundle. Only stdlib modules from those packages are shipped.
set -euo pipefail
cd "$(dirname "$0")/../.."
DIST=deploy/obs/dist
rm -rf "$DIST" && mkdir -p "$DIST/mcp" "$DIST/harvest"

# ---- MCP server zip ---------------------------------------------------------
cp -R src/obs_mcp "$DIST/mcp/obs_mcp"
mkdir -p "$DIST/mcp/observability" "$DIST/mcp/interop"
cp src/observability/pg.py src/observability/store.py "$DIST/mcp/observability/"
cp src/interop/trace.py "$DIST/mcp/interop/"
echo '"""packaging shim (deploy/obs/build_zips.sh)"""' > "$DIST/mcp/observability/__init__.py"
echo '"""packaging shim (deploy/obs/build_zips.sh)"""' > "$DIST/mcp/interop/__init__.py"
(cd "$DIST/mcp" && zip -qr ../a2alab-obs-mcp.zip . -x '*__pycache__*')

# ---- harvest zip ------------------------------------------------------------
uv pip install --target "$DIST/harvest" \
  --python-platform aarch64-manylinux2014 --python-version 3.12 \
  --only-binary :all: anthropic httpx -q
cp -R src/observability "$DIST/harvest/observability"
rm -f "$DIST/harvest/observability/analyst.py"
mkdir -p "$DIST/harvest/interop"
cp src/interop/trace.py "$DIST/harvest/interop/"
echo '"""packaging shim (deploy/obs/build_zips.sh)"""' > "$DIST/harvest/interop/__init__.py"
(cd "$DIST/harvest" && zip -qr ../a2alab-obs-harvest.zip . -x '*__pycache__*')

ls -lh "$DIST"/*.zip
