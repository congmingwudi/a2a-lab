"""TraceSink fan-out (ADR D13): recorder → sinks, env selection, DynamoDB
item shape, and the never-break-the-hop containment guarantee."""

import json

import pytest

from interop.trace import (
    DynamoDbSink,
    JsonlFileSink,
    TraceEvent,
    TraceRecorder,
    sinks_from_env,
)


def make_event(**overrides):
    base = dict(
        trace_id="t1",
        source="src",
        target="dst",
        protocol="rest",
        transport_detail="POST /ask",
        request_payload_raw={"message": "hi"},
        response_payload_raw={"text": "hello"},
        latency_ms=42,
        ts=1767912345.5,
    )
    base.update(overrides)
    return TraceEvent(**base)


class CollectingSink:
    def __init__(self):
        self.events = []

    def emit(self, event_dict):
        self.events.append(event_dict)


class ExplodingSink:
    def emit(self, event_dict):
        raise RuntimeError("sink down")


def test_recorder_fans_out_to_all_sinks():
    a, b = CollectingSink(), CollectingSink()
    rec = TraceRecorder(sinks=[a, b])
    rec.record(make_event())
    assert len(a.events) == 1 and len(b.events) == 1
    assert a.events[0]["trace_id"] == "t1"


def test_sink_failure_never_breaks_the_hop(capsys):
    ok = CollectingSink()
    rec = TraceRecorder(sinks=[ExplodingSink(), ok])
    rec.record(make_event())  # must not raise
    assert len(ok.events) == 1
    assert "ExplodingSink failed" in capsys.readouterr().err


def test_trace_dir_arg_forces_single_jsonl_sink(tmp_path):
    rec = TraceRecorder(tmp_path / "tr")
    assert len(rec.sinks) == 1 and isinstance(rec.sinks[0], JsonlFileSink)
    rec.record(make_event())
    files = list((tmp_path / "tr").glob("*.jsonl"))
    assert len(files) == 1
    line = json.loads(files[0].read_text().strip())
    assert line["trace_id"] == "t1"


def test_sinks_from_env_default_and_list(monkeypatch):
    monkeypatch.delenv("A2ALAB_TRACE_SINK", raising=False)
    assert [type(s) for s in sinks_from_env()] == [JsonlFileSink]

    fake_table = FakeTable()
    monkeypatch.setattr(
        "interop.trace.DynamoDbSink.__init__",
        lambda self, *a, **k: setattr(self, "_table", fake_table) or setattr(self, "ttl_days", 14),
    )
    monkeypatch.setenv("A2ALAB_TRACE_SINK", "jsonl, dynamodb")
    kinds = [type(s).__name__ for s in sinks_from_env()]
    assert kinds == ["JsonlFileSink", "DynamoDbSink"]


def test_sinks_from_env_rejects_typos(monkeypatch):
    monkeypatch.setenv("A2ALAB_TRACE_SINK", "jsnol")
    with pytest.raises(ValueError, match="unknown trace sink"):
        sinks_from_env()


class FakeTable:
    def __init__(self):
        self.items = []

    def put_item(self, Item):
        self.items.append(Item)


def test_dynamodb_item_shape():
    table = FakeTable()
    sink = DynamoDbSink(table=table, ttl_days=1)
    sink.emit(make_event().to_dict())

    assert len(table.items) == 1
    item = table.items[0]
    assert item["trace_id"] == "t1"
    # sort key orders hops chronologically then by seq
    assert item["sk"].endswith("#0000") and item["sk"].split("#")[0].replace(".", "").isdigit()
    assert item["day"].count("-") == 2
    assert item["latency_ms"] == 42
    assert item["expires_at"] == int(1767912345.5 + 86400)
    # payloads stored as JSON strings (DynamoDB rejects floats/empty strings
    # inside documents; Data 360 maps scalars cleanly)
    assert json.loads(item["request_payload_raw"]) == {"message": "hi"}
    assert json.loads(item["response_payload_raw"]) == {"text": "hello"}


def test_dynamodb_sort_keys_order_hops():
    table = FakeTable()
    sink = DynamoDbSink(table=table, ttl_days=1)
    sink.emit(make_event(ts=1767912345.5, hop_seq=0).to_dict())
    sink.emit(make_event(ts=1767912345.5, hop_seq=1).to_dict())
    sink.emit(make_event(ts=1767912400.0, hop_seq=0).to_dict())
    keys = [i["sk"] for i in table.items]
    assert keys == sorted(keys)
