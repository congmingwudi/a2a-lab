"""F4 (A7/A8): the MCP `ask` tool publishes a declared output schema and a
versioned structured result — no more opaque string blob. Exercises the
FastMCP server in-process (no transport)."""

import json

from interop.models import AgentRequest, AgentResponse
from interop.servers.mcp import ASK_CONTRACT_VERSION, create_mcp_server


class EchoAdapter:
    name = "echo"
    description = "echo adapter for contract tests"

    async def handle(self, req: AgentRequest) -> AgentResponse:
        return AgentResponse(text=f"echo: {req.message}", session_id=req.session_id)


async def test_ask_declares_output_schema():
    mcp = create_mcp_server(EchoAdapter())
    tools = {t.name: t for t in await mcp.list_tools()}
    schema = tools["ask"].outputSchema
    assert schema is not None, "ask must publish an outputSchema (F4)"
    props = schema["properties"]
    assert set(props) == {"contract_version", "text", "session_id", "latency_ms", "raw"}
    assert props["contract_version"]["type"] == "integer"
    assert props["text"]["type"] == "string"
    # the contract version is documented in the tool description too
    assert "contract v1" in tools["ask"].description


async def test_ask_returns_versioned_structured_content_and_json_text():
    mcp = create_mcp_server(EchoAdapter())
    content, structured = await mcp.call_tool("ask", {"message": "hi", "session_id": "s9"})
    # structuredContent carries the contract
    assert structured["contract_version"] == ASK_CONTRACT_VERSION
    assert structured["text"] == "echo: hi"
    assert structured["session_id"] == "s9"
    # legacy clients still get the same JSON as text content
    text_blocks = [c.text for c in content if getattr(c, "type", "") == "text"]
    assert text_blocks, "JSON text content must be preserved for legacy clients"
    data = json.loads(text_blocks[0])
    assert data["contract_version"] == ASK_CONTRACT_VERSION
    assert data["text"] == "echo: hi"
