import json

import pytest

from interop.models import AgentRequest, AgentResponse, new_trace_id
from interop.trace import Hop, get_recorder


def test_request_round_trip():
    req = AgentRequest(message="hi", session_id="s1", trace_id="t1", metadata={"k": "v"})
    assert AgentRequest.from_dict(req.to_dict()) == req


def test_request_defaults():
    req = AgentRequest.from_dict({"message": "hi"})
    assert req.session_id is None and req.metadata == {}


# ---- from_dict input validation (F4) ---------------------------------------


def test_from_dict_rejects_non_string_message():
    with pytest.raises(ValueError, match="message is required and must be a string"):
        AgentRequest.from_dict({"message": 42})
    with pytest.raises(ValueError, match="message is required and must be a string"):
        AgentRequest.from_dict({})


def test_from_dict_rejects_non_dict_metadata():
    with pytest.raises(ValueError, match="metadata must be an object"):
        AgentRequest.from_dict({"message": "hi", "metadata": "not-a-dict"})
    # absent / null metadata both normalize to {}
    assert AgentRequest.from_dict({"message": "hi", "metadata": None}).metadata == {}


def test_from_dict_rejects_non_object_payload():
    with pytest.raises(ValueError, match="must be a JSON object"):
        AgentRequest.from_dict(["message"])


def test_from_dict_rejects_non_string_ids():
    with pytest.raises(ValueError, match="session_id must be a string"):
        AgentRequest.from_dict({"message": "hi", "session_id": 7})
    with pytest.raises(ValueError, match="trace_id must be a string"):
        AgentRequest.from_dict({"message": "hi", "trace_id": {"nope": 1}})


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


def test_hop_expected_exception_records_pending_not_error(isolated_traces):
    """An exception the caller EXPECTS (e.g. a not-yet-visible task 404 during
    the eventually-consistent window right after an async submit, WS11) records
    the hop as `pending`, not a red `error` — and is still raised so the
    caller's grace loop runs. This is what stops the console showing a string of
    ✗ failures for an async leg that in fact completes."""
    trace_id = new_trace_id()
    with pytest.raises(ValueError):
        with Hop(
            trace_id,
            source="orchestrator",
            target="google-adk-a2a",
            protocol="a2a",
            transport_detail="GetTask @ https://.../a2a",
            request_payload={"taskId": "t-1"},
            expected_exc=(ValueError,),
        ):
            raise ValueError("Resource not found: .../a2a/tasks/t-1")
    event = json.loads(list(isolated_traces.glob("*.jsonl"))[0].read_text().strip())
    assert event["status"] == "pending"
    # An UNEXPECTED exception type still records as error even with the flag set.
    trace_id2 = new_trace_id()
    with pytest.raises(KeyError):
        with Hop(
            trace_id2,
            source="orchestrator",
            target="google-adk-a2a",
            protocol="a2a",
            transport_detail="GetTask @ https://.../a2a",
            request_payload={"taskId": "t-2"},
            expected_exc=(ValueError,),
        ):
            raise KeyError("something genuinely wrong")
    events = [
        json.loads(line)
        for f in isolated_traces.glob("*.jsonl")
        for line in f.read_text().splitlines()
    ]
    err = [e for e in events if e["trace_id"] == trace_id2][0]
    assert err["status"] == "error"


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


# ---- WS25 A1: format-aware redaction of SERIALIZED payloads (F04, D80) ------
# The wiretap records raw wire bodies as strings, so a key-based scrub that only
# sees dicts leaves `{"client_secret": "…"}` intact. Ported from Codex's harness
# `regression.serialized-secret` / `regression.user-token`, plus the escaped,
# nested, truncated and case-variant shapes its critique asked for.


def _persisted_after_hop(trace_id: str, request_payload_raw) -> str:
    import os
    from pathlib import Path

    from interop.trace import TRACE_DIR_ENV, Hop

    with Hop(
        trace_id,
        source="client",
        target="claude-rest",
        protocol="rest",
        transport_detail="POST /invoke",
        request_payload=request_payload_raw,
    ) as hop:
        hop.response_payload = {"text": "Alternate supplier available"}
    trace_dir = Path(os.environ[TRACE_DIR_ENV])
    return "".join(p.read_text() for p in trace_dir.glob("*.jsonl"))


def test_serialized_json_secret_is_scrubbed_before_persistence():
    written = _persisted_after_hop("ws25a1-ser", '{"client_secret": "synthetic-secret-only"}')
    assert "synthetic-secret-only" not in written
    assert "client_secret" in written  # the KEY survives — evidence of what was sent


def test_structured_user_token_is_scrubbed_before_persistence():
    written = _persisted_after_hop("ws25a1-ut", {"user_token": "synthetic-opaque-token"})
    assert "synthetic-opaque-token" not in written


def test_serialized_secret_with_escaped_quote_is_fully_scrubbed():
    from interop.trace import redact

    out = redact('{"password": "abc\\"def-tail", "message": "hi"}')
    assert "abc" not in out and "def-tail" not in out
    assert '"message": "hi"' in out


def test_json_serialized_inside_a_json_string_is_scrubbed():
    import json

    from interop.trace import redact

    inner = json.dumps({"client_secret": "inner-secret-value"})
    outer = json.dumps({"body": inner, "message": "hello"})
    out = redact(outer)
    assert "inner-secret-value" not in out
    assert "hello" in out


def test_truncated_serialized_secret_is_scrubbed():
    from interop.trace import redact

    out = redact('{"access_token": "trunc-secret-value-that-never-clo')
    assert "trunc-secret-value" not in out


def test_serialized_secret_key_case_variant_is_scrubbed():
    from interop.trace import redact

    out = redact('{"Client_Secret": "CaseSecret123"}')
    assert "CaseSecret123" not in out


def test_serialized_non_secret_keys_survive_verbatim():
    from interop.trace import redact

    body = '{"message": "token talk", "tokens": 12, "author": "secretary"}'
    assert redact(body) == body


# Cross-review round (Codex, ws25-b1): shapes the first regex missed.


def test_unicode_escaped_secret_key_is_scrubbed():
    from interop.trace import redact

    out = redact('{"client\\u005fsecret":"probe-secret"}')
    assert "probe-secret" not in out


def test_array_valued_secret_is_scrubbed_and_json_stays_valid():
    import json

    from interop.trace import redact

    out = redact('{"password":["first","probe-secret"],"message":"hi"}')
    assert "probe-secret" not in out and "first" not in out
    assert json.loads(out)["message"] == "hi"


def test_object_valued_secret_is_scrubbed_and_json_stays_valid():
    import json

    from interop.trace import redact

    out = redact('{"password":{"nested":"probe-secret"},"message":"hi"}')
    assert "probe-secret" not in out
    assert json.loads(out)["message"] == "hi"


def test_nested_escaped_json_with_inner_escaped_quote_is_scrubbed():
    import json

    from interop.trace import redact

    inner = json.dumps({"client_secret": 'prefix"probe-secret'})
    out = redact(json.dumps({"body": inner}))
    assert "probe-secret" not in out


def test_valid_json_without_secrets_is_byte_identical():
    from interop.trace import redact

    body = '{"message":  "spaced",   "n": [1, 2,3], "author": "secretary"}'
    assert redact(body) == body


def test_clipped_body_with_secret_split_at_boundary_is_scrubbed():
    from interop.trace import redact

    out = redact('{"message": "hi", "refresh_token": "abcdef-probe-sec')
    assert "abcdef-probe-sec" not in out
