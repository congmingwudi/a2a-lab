"""The lab's hosted observability MCP server (ADR D23).

The analyst agent reads the obs store only through this server: read-only SQL
against Aurora plus a delivery tool for the finished brief. It runs as a Lambda
behind API Gateway, so the nightly deployment needs no process attached.

The transport moved to `mcp_http/` when the fan-out server (WS7 item 4) became
the second thing to need it — nothing in it was observability-specific. What
remains here is the part that is: the two tools and their store access.
"""

SERVER_INFO = {"name": "a2alab-obs-mcp", "version": "0.1.0"}

# No `from obs_mcp.tools import ...` here: tools pulls in the Postgres client,
# so importing it from the package root would make `import obs_mcp` cost a
# database dependency for callers that only wanted the server name.
__all__ = ["SERVER_INFO"]
