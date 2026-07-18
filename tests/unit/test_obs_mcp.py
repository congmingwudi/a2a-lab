"""obs_mcp: hand-rolled MCP Streamable HTTP transport (ADR D23)."""

import base64
import json

import pytest
from starlette.testclient import TestClient

from obs_mcp import ToolDef, ToolRegistry, create_local_app, handle_message, make_lambda_handler


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDef(
            name="echo",
            description="Echo the text argument back.",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            fn=lambda args: f"echo: {args.get('text', '')}",
        )
    )

    def boom(args: dict) -> str:
        raise ValueError("kaput")

    registry.register(
        ToolDef(name="boom", description="Always fails.", input_schema={"type": "object"}, fn=boom)
    )
    return registry


def rpc(method: str, params: dict | None = None, req_id: object = 1) -> dict:
    body: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


# --- core.handle_message ---------------------------------------------------


@pytest.mark.parametrize("version", ["2025-06-18", "2025-03-26", "2024-11-05"])
def test_initialize_echoes_known_protocol_versions(version):
    reply = handle_message(rpc("initialize", {"protocolVersion": version}), make_registry())
    assert reply["id"] == 1
    assert reply["result"]["protocolVersion"] == version
    assert reply["result"]["capabilities"] == {"tools": {}}
    assert reply["result"]["serverInfo"] == {"name": "a2alab-obs-mcp", "version": "0.1.0"}


@pytest.mark.parametrize("params", [{"protocolVersion": "1999-01-01"}, {}, None])
def test_initialize_falls_back_to_latest_on_unknown_version(params):
    reply = handle_message(rpc("initialize", params), make_registry())
    assert reply["result"]["protocolVersion"] == "2025-06-18"


@pytest.mark.parametrize("method", ["notifications/initialized", "notifications/cancelled"])
def test_notifications_return_none(method):
    assert handle_message({"jsonrpc": "2.0", "method": method}, make_registry()) is None


def test_ping():
    reply = handle_message(rpc("ping"), make_registry())
    assert reply == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_tools_list_shape():
    reply = handle_message(rpc("tools/list"), make_registry())
    tools = reply["result"]["tools"]
    assert [t["name"] for t in tools] == ["echo", "boom"]
    echo = tools[0]
    assert set(echo) == {"name", "description", "inputSchema"}  # camelCase, no fn leaked
    assert echo["inputSchema"]["type"] == "object"


def test_tools_call_success():
    reply = handle_message(
        rpc("tools/call", {"name": "echo", "arguments": {"text": "hi"}}), make_registry()
    )
    assert reply["result"] == {
        "content": [{"type": "text", "text": "echo: hi"}],
        "isError": False,
    }


def test_tools_call_tool_exception_is_error_result_not_rpc_error():
    reply = handle_message(rpc("tools/call", {"name": "boom", "arguments": {}}), make_registry())
    assert "error" not in reply
    assert reply["result"]["isError"] is True
    assert reply["result"]["content"] == [{"type": "text", "text": "ValueError: kaput"}]


def test_tools_call_unknown_tool_is_invalid_params():
    reply = handle_message(rpc("tools/call", {"name": "nope", "arguments": {}}), make_registry())
    assert reply["error"]["code"] == -32602


def test_unknown_method_is_method_not_found():
    reply = handle_message(rpc("resources/list"), make_registry())
    assert reply["error"]["code"] == -32601


def test_request_without_id_gets_null_id_response():
    reply = handle_message({"jsonrpc": "2.0", "method": "ping"}, make_registry())
    assert reply == {"jsonrpc": "2.0", "id": None, "result": {}}


# --- http.create_local_app -------------------------------------------------


def make_client(token: str | None = "sekrit") -> TestClient:
    return TestClient(create_local_app(make_registry(), token))


@pytest.mark.parametrize("path", ["/", "/mcp"])
def test_local_app_dispatches_on_both_paths(path):
    client = make_client()
    resp = client.post(path, json=rpc("ping"), headers={"authorization": "Bearer sekrit"})
    assert resp.status_code == 200
    assert resp.json()["result"] == {}


def test_local_app_auth_missing_and_wrong_and_correct():
    client = make_client()
    assert client.post("/", json=rpc("ping")).status_code == 401
    assert (
        client.post("/", json=rpc("ping"), headers={"authorization": "Bearer wrong"}).status_code
        == 401
    )
    assert (
        client.post("/", json=rpc("ping"), headers={"authorization": "sekrit"}).status_code == 401
    )  # not Bearer-shaped
    assert (
        client.post("/", json=rpc("ping"), headers={"authorization": "Bearer sekrit"}).status_code
        == 200
    )


def test_local_app_auth_disabled_when_token_falsy():
    for token in (None, ""):
        assert make_client(token).post("/", json=rpc("ping")).status_code == 200


def test_local_app_notification_is_202_empty():
    client = make_client()
    resp = client.post(
        "/",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"authorization": "Bearer sekrit"},
    )
    assert resp.status_code == 202
    assert resp.content == b""


def test_local_app_parse_error():
    resp = make_client().post(
        "/mcp", content=b"{not json", headers={"authorization": "Bearer sekrit"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == -32700


def test_local_app_get_is_405_and_healthz_is_open():
    client = make_client()
    assert client.get("/").status_code == 405
    assert client.get("/mcp").status_code == 405
    resp = client.get("/healthz")  # no auth header on purpose
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# --- http.make_lambda_handler ----------------------------------------------


def lambda_event(
    method: str = "POST",
    path: str = "/",
    body: dict | str | None = None,
    headers: dict | None = None,
    b64: bool = False,
) -> dict:
    raw = json.dumps(body) if isinstance(body, dict) else (body or "")
    if b64:
        raw = base64.b64encode(raw.encode()).decode()
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "headers": headers or {},
        "body": raw,
        "isBase64Encoded": b64,
    }


def make_handler(token: str | None = "sekrit"):
    return make_lambda_handler(make_registry(), token)


def test_lambda_dispatch_and_case_insensitive_headers():
    resp = make_handler()(
        lambda_event(
            body=rpc("tools/call", {"name": "echo", "arguments": {"text": "hi"}}),
            headers={"Authorization": "Bearer sekrit"},
        ),
        None,
    )
    assert resp["statusCode"] == 200
    assert resp["headers"] == {"content-type": "application/json"}
    result = json.loads(resp["body"])["result"]
    assert result["content"] == [{"type": "text", "text": "echo: hi"}]


def test_lambda_base64_body():
    resp = make_handler()(
        lambda_event(body=rpc("ping"), headers={"authorization": "Bearer sekrit"}, b64=True), None
    )
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["result"] == {}


def test_lambda_auth_missing_wrong_correct_disabled():
    handler = make_handler()
    assert handler(lambda_event(body=rpc("ping")), None)["statusCode"] == 401
    assert (
        handler(lambda_event(body=rpc("ping"), headers={"authorization": "Bearer wrong"}), None)[
            "statusCode"
        ]
        == 401
    )
    assert (
        handler(lambda_event(body=rpc("ping"), headers={"authorization": "Bearer sekrit"}), None)[
            "statusCode"
        ]
        == 200
    )
    open_handler = make_handler(token=None)
    assert open_handler(lambda_event(body=rpc("ping")), None)["statusCode"] == 200


def test_lambda_notification_is_202_empty():
    resp = make_handler()(
        lambda_event(
            body={"jsonrpc": "2.0", "method": "notifications/cancelled"},
            headers={"authorization": "Bearer sekrit"},
        ),
        None,
    )
    assert resp["statusCode"] == 202
    assert resp["body"] == ""


def test_lambda_parse_error():
    resp = make_handler()(
        lambda_event(body="{not json", headers={"authorization": "Bearer sekrit"}), None
    )
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"]["code"] == -32700


def test_lambda_healthz_and_bad_method():
    handler = make_handler()
    resp = handler(lambda_event(method="GET", path="/healthz"), None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == {"ok": True}
    assert handler(lambda_event(method="GET", path="/"), None)["statusCode"] == 405
