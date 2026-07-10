import importlib
import json

from fastapi.testclient import TestClient


def make_app(trace_dir, monkeypatch):
    monkeypatch.setenv("A2ALAB_TRACE_DIR", str(trace_dir))
    import console.app as console_app

    importlib.reload(console_app)
    return console_app.create_console_app()


def test_traces_grouped_and_sorted(tmp_path, monkeypatch):
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    events = [
        {"trace_id": "t1", "ts": 100.0, "hop_seq": 0, "protocol": "rest"},
        {"trace_id": "t2", "ts": 200.0, "hop_seq": 0, "protocol": "mcp"},
        {"trace_id": "t1", "ts": 101.0, "hop_seq": 1, "protocol": "a2a"},
    ]
    (trace_dir / "2026-07-09.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n"
    )
    app = make_app(trace_dir, monkeypatch)
    client = TestClient(app)
    data = client.get("/api/traces").json()["traces"]
    assert [t["trace_id"] for t in data] == ["t2", "t1"]  # newest first
    t1 = data[1]
    assert len(t1["hops"]) == 2
    assert t1["protocols"] == ["a2a", "rest"]
    assert [h["hop_seq"] for h in t1["hops"]] == [0, 1]


def test_index_served(tmp_path, monkeypatch):
    app = make_app(tmp_path / "traces", monkeypatch)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "A2A Interop Lab" in r.text
