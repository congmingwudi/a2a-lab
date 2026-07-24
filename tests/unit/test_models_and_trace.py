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


# ---- credential scrub (F2) --------------------------------------------------


def test_redact_scrubs_credentials_everywhere():
    from interop.trace import redact

    payload = {
        "message": "call me",
        "access_token": "00Dxx0000001!AQEAQfake.session.token",
        "nested": {"client_secret": "shhh", "Authorization": "Bearer abc123def456ghi"},
        "text": (
            "header was Authorization: Bearer abcdefgh12345678 and the key "
            "sk-ant-api03-aaaaaaaaaaaaaaaaaaaaaaaaaa plus jwt "
            "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.c2lnbmF0dXJlLXBhcnQ "
            "and form access_token=00Dfaketoken&other=1"
        ),
    }
    out = redact(payload)
    flat = str(out)
    assert out["access_token"] == "[REDACTED]"
    assert out["nested"]["client_secret"] == "[REDACTED]"
    assert out["nested"]["Authorization"] == "[REDACTED]"
    assert "Bearer [REDACTED]" in out["text"]
    assert "[REDACTED-KEY]" in out["text"]
    assert "[REDACTED-JWT]" in out["text"]
    assert "access_token=[REDACTED]" in out["text"]
    for secret in ("shhh", "sk-ant", "eyJhbGciOiJSUzI1NiJ9.", "00Dfaketoken", "abcdefgh12345678"):
        assert secret not in flat
    # non-secret content survives verbatim — raw-evidence ethos
    assert out["message"] == "call me"


def test_redact_leaves_prose_slugs_alone():
    # Found live in harvested content: a TechCrunch URL slug starting
    # "sk-hynix-…" must NOT be treated as an API key (raw-evidence ethos).
    from interop.trace import redact

    url = "https://techcrunch.com/2026/07/06/access-to-sk-hynix-another-memory-maker-riding-the-ai-wave"
    assert redact(url) == url


def test_trace_event_writes_are_redacted():
    import os
    from pathlib import Path

    from interop.trace import TRACE_DIR_ENV, Hop

    trace_dir = Path(os.environ[TRACE_DIR_ENV])  # conftest's isolated dir
    with Hop(
        "redacttest1",
        source="a",
        target="b",
        protocol="rest",
        transport_detail="t",
        request_payload={"q": "hi", "access_token": "supersecret123"},
    ) as hop:
        hop.response_payload = "the token was Bearer zzzyyyxxx111222 ok"
    written = "".join(p.read_text() for p in trace_dir.glob("*.jsonl"))
    assert "redacttest1" in written
    assert "supersecret123" not in written
    assert "zzzyyyxxx111222" not in written
    assert "Bearer [REDACTED]" in written


def test_obs_store_writes_are_redacted(tmp_path):
    from observability.store import ObsStore

    store = ObsStore(db_path=tmp_path / "lab.db")
    store.upsert_event(
        "claude",
        "s1",
        "e1",
        raw={
            "input": "auth: Bearer abcd1234efgh5678",
            "api_key": "sk-proj-1234567890abcdef1234567890ab",
        },
    )
    row = store.list_events("claude", "s1")[0]
    assert "abcd1234efgh5678" not in row["raw_json"]
    assert "sk-proj" not in row["raw_json"]
    assert "[REDACTED]" in row["raw_json"]
    store.close()
