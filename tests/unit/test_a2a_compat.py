"""A2A 0.3-dialect compatibility (WS3): Foundry's A2A tool speaks
message/send + kind-discriminated parts + lowercase states; the lab's 1.x
servers speak SendMessage + proto-JSON. The translators must map both
directions without touching 1.x traffic."""

from interop.servers.a2a_compat import (
    translate_03_request,
    translate_1x_response,
)


def test_request_translation_03_to_1x():
    payload = {
        "jsonrpc": "2.0",
        "id": "42",
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "messageId": "m1",
                "contextId": "ctx",
                "role": "user",
                "parts": [{"kind": "text", "text": "hello"}],
                "metadata": {"trace_id": "t1"},
            }
        },
    }
    out = translate_03_request(payload)
    assert out["method"] == "SendMessage"
    assert out["id"] == "42"
    message = out["params"]["message"]
    assert message["role"] == "ROLE_USER"
    assert message["parts"] == [{"text": "hello"}]
    assert message["contextId"] == "ctx"
    assert message["metadata"] == {"trace_id": "t1"}
    assert "kind" not in message


def test_1x_requests_pass_through():
    assert translate_03_request({"method": "SendMessage", "params": {}}) is None
    assert translate_03_request({"method": "GetTask"}) is None


def test_response_translation_1x_to_03():
    payload = {
        "jsonrpc": "2.0",
        "id": "42",
        "result": {
            "task": {
                "id": "task-1",
                "contextId": "ctx",
                "status": {"state": "TASK_STATE_COMPLETED", "timestamp": "2026-07-22T00:00:00Z"},
                "artifacts": [{"artifactId": "a1", "name": "answer", "parts": [{"text": "hi"}]}],
                "history": [{"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "hello"}]}],
            }
        },
    }
    out = translate_1x_response(payload)
    task = out["result"]
    assert task["kind"] == "task" and task["id"] == "task-1"
    assert task["status"]["state"] == "completed"
    assert task["artifacts"][0]["parts"] == [{"kind": "text", "text": "hi"}]
    history = task["history"][0]
    assert history["role"] == "user" and history["kind"] == "message"
    assert history["parts"] == [{"kind": "text", "text": "hello"}]


def test_failed_state_and_error_pass_through():
    failed = {
        "id": "1",
        "result": {"task": {"id": "t", "status": {"state": "TASK_STATE_FAILED"}}},
    }
    assert translate_1x_response(failed)["result"]["status"]["state"] == "failed"
    error = {"jsonrpc": "2.0", "id": "1", "error": {"code": -32601, "message": "nope"}}
    assert translate_1x_response(error) == error
