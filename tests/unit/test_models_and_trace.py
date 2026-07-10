import json

from interop.models import AgentRequest, AgentResponse, new_trace_id
from interop.trace import Hop, get_recorder


def test_request_round_trip():
    req = AgentRequest(message="hi", session_id="s1", trace_id="t1", metadata={"k": "v"})
    assert AgentRequest.from_dict(req.to_dict()) == req


def test_request_defaults():
    req = AgentRequest.from_dict({"message": "hi"})
    assert req.session_id is None and req.metadata == {}


def test_response_round_trip():
    resp = AgentResponse(text="answer", session_id="s1", latency_ms=12, raw={"a": 1})
    assert AgentResponse.from_dict(resp.to_dict()) == resp


def test_hop_records_jsonl(isolated_traces):
    trace_id = new_trace_id()
    with Hop(
        trace_id,
        source="a",
        target="b",
        protocol="rest",
        transport_detail="POST /invoke",
        request_payload={"message": "hi"},
    ) as hop:
        hop.response_payload = {"text": "yo"}

    files = list(isolated_traces.glob("*.jsonl"))
    assert len(files) == 1
    event = json.loads(files[0].read_text().strip())
    assert event["trace_id"] == trace_id
    assert event["status"] == "ok"
    assert event["request_payload_raw"] == {"message": "hi"}
    assert event["response_payload_raw"] == {"text": "yo"}
    assert event["latency_ms"] >= 0


def test_hop_records_error(isolated_traces):
    trace_id = new_trace_id()
    try:
        with Hop(
            trace_id,
            source="a",
            target="b",
            protocol="mcp",
            transport_detail="tools/call",
            request_payload="raw",
        ):
            raise ValueError("boom")
    except ValueError:
        pass
    event = json.loads(list(isolated_traces.glob("*.jsonl"))[0].read_text().strip())
    assert event["status"] == "error"
    assert "boom" in event["response_payload_raw"]


def test_hop_seq_increments_per_trace(isolated_traces):
    recorder = get_recorder()
    assert recorder.next_hop_seq("t1") == 0
    assert recorder.next_hop_seq("t1") == 1
    assert recorder.next_hop_seq("t2") == 0


def test_payload_clipping(isolated_traces):
    trace_id = new_trace_id()
    with Hop(
        trace_id,
        source="a",
        target="b",
        protocol="rest",
        transport_detail="x",
        request_payload="x" * 200_000,
    ) as hop:
        hop.response_payload = "ok"
    event = json.loads(list(isolated_traces.glob("*.jsonl"))[0].read_text().strip())
    assert "clipped" in event["request_payload_raw"]
    assert len(event["request_payload_raw"]) < 200_000
