"""WS25 A2 (D80/F01): a viewer persona cannot INVOKE an agent through any
protocol face, though it can still discover and read. Enforced on the real
mounted surface — REST /invoke + /invocations, MCP tools/call, A2A v1
SendMessage and the 0.3 message/send spelling — via one policy function
(interop.authz) called from the REST handler and from the WireTap's
already-buffered body for MCP/A2A. Role comes from the directory, never the
token claim, so a stale claim cannot escalate (the console's rule, D36).
"""

import pytest
from starlette.testclient import TestClient

from interop.adapter import build_app
from interop.models import AgentRequest, AgentResponse


class EchoAdapter:
    name = "echo"
    description = "echo adapter for authz tests"

    async def handle(self, req: AgentRequest) -> AgentResponse:
        return AgentResponse(text=f"echo: {req.message}", session_id=req.session_id)


USERS = {
    "vic": {"name": "Vic", "role": "viewer"},
    "ana": {"name": "Ana", "role": "operator"},
    "gw": {"name": "Gateway", "role": "machine", "client_id_env": "GW_ID"},
}


@pytest.fixture
def directory(monkeypatch, tmp_path):
    from interop import identity

    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    monkeypatch.setenv(identity.KEY_DIR_ENV, str(tmp_path / "keys"))
    monkeypatch.setattr(identity, "load_users", lambda *_a, **_k: USERS)
    return identity


def _bearer(identity, user, users=USERS):
    return {"authorization": f"Bearer {identity.issue_token(user, users=users)}"}


def _rpc(method, params=None, id_=1):
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}


MCP_CALL = _rpc("tools/call", {"name": "ask", "arguments": {"message": "hi"}})
MCP_HEADERS = {"accept": "application/json, text/event-stream"}


def _a2a_send(method):
    # v1 (`SendMessage`) spells the role ROLE_USER; the 0.3 dialect
    # (`message/send`) spells it "user" and the compat layer translates.
    role = "user" if "/" in method else "ROLE_USER"
    return _rpc(
        method,
        {"message": {"role": role, "parts": [{"text": "hi"}], "messageId": "m1"}},
    )


# ---- REST ------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/invoke", "/invocations"])
def test_viewer_cannot_invoke_rest(directory, path):
    client = TestClient(build_app(EchoAdapter(), "rest"))
    r = client.post(path, json={"message": "hi"}, headers=_bearer(directory, "vic"))
    assert r.status_code == 403
    assert "operator-only" in r.json()["detail"]


def test_operator_machine_and_shared_token_invoke_rest(directory):
    client = TestClient(build_app(EchoAdapter(), "rest"))
    for headers in (
        _bearer(directory, "ana"),
        _bearer(directory, "gw"),
        {"x-lab-token": "sekrit"},
    ):
        r = client.post("/invoke", json={"message": "hi"}, headers=headers)
        assert r.status_code == 200, r.text


def test_stale_role_claim_does_not_escalate(directory):
    """Token minted when vic was 'operator'; the directory now says viewer."""
    client = TestClient(build_app(EchoAdapter(), "rest"))
    stale = {"vic": {"name": "Vic", "role": "operator"}}
    r = client.post("/invoke", json={"message": "hi"}, headers=_bearer(directory, "vic", stale))
    assert r.status_code == 403


def test_viewer_can_still_read_rest_health(directory):
    client = TestClient(build_app(EchoAdapter(), "rest"))
    assert client.get("/healthz", headers=_bearer(directory, "vic")).status_code == 200


# ---- MCP -------------------------------------------------------------------


def test_viewer_cannot_call_mcp_tool_but_can_list(directory):
    with TestClient(build_app(EchoAdapter(), "mcp")) as client:
        headers = {**_bearer(directory, "vic"), **MCP_HEADERS}
        assert client.post("/mcp", json=_rpc("tools/list"), headers=headers).status_code == 200
        r = client.post("/mcp", json=MCP_CALL, headers=headers)
        assert r.status_code == 403, r.text


def test_operator_calls_mcp_tool(directory):
    with TestClient(build_app(EchoAdapter(), "mcp")) as client:
        headers = {**_bearer(directory, "ana"), **MCP_HEADERS}
        r = client.post("/mcp", json=MCP_CALL, headers=headers)
        assert r.status_code == 200, r.text
        assert "echo: hi" in r.text


# ---- A2A -------------------------------------------------------------------


@pytest.mark.parametrize("method", ["SendMessage", "message/send"])
def test_viewer_cannot_send_a2a_message(directory, method):
    client = TestClient(build_app(EchoAdapter(), "a2a", public_url="http://t/"))
    r = client.post("/", json=_a2a_send(method), headers=_bearer(directory, "vic"))
    assert r.status_code == 403, r.text


def test_viewer_can_discover_and_poll_a2a(directory):
    client = TestClient(build_app(EchoAdapter(), "a2a", public_url="http://t/"))
    headers = _bearer(directory, "vic")
    assert client.get("/.well-known/agent-card.json", headers=headers).status_code == 200
    r = client.post("/", json=_rpc("GetTask", {"id": "nope"}), headers=headers)
    assert r.status_code != 403  # polling is reading — an unknown task is an A2A error, not a 403


def test_machine_sends_a2a_message(directory):
    client = TestClient(build_app(EchoAdapter(), "a2a", public_url="http://t/"))
    r = client.post("/", json=_a2a_send("SendMessage"), headers=_bearer(directory, "gw"))
    assert r.status_code == 200, r.text


def test_denied_invoke_is_recorded_as_an_error_hop(directory, isolated_traces):
    import os
    from pathlib import Path

    from interop.trace import TRACE_DIR_ENV

    client = TestClient(build_app(EchoAdapter(), "a2a", public_url="http://t/"))
    client.post("/", json=_a2a_send("SendMessage"), headers=_bearer(directory, "vic"))
    written = "".join(p.read_text() for p in Path(os.environ[TRACE_DIR_ENV]).glob("*.jsonl"))
    assert '"status": "error"' in written and "operator-only" in written
