"""Unit tests for the OpenAI harvest source (M9) — injected fetch, no network."""

from __future__ import annotations

from observability.openai_source import OpenAISource
from observability.store import ObsStore

RESPONSE = {
    "id": "resp_abc",
    "created_at": 1784400000,
    "model": "gpt-5-mini",
    "status": "completed",
    "usage": {"input_tokens": 100, "output_tokens": 50},
    "output": [
        {"id": "rs_1", "type": "reasoning"},
        {
            "id": "fc_1",
            "type": "function_call",
            "name": "ask_agentforce",
            "arguments": '{"question": "Omega status"}',
        },
        {
            "id": "msg_1",
            "type": "message",
            "content": [{"type": "output_text", "text": "the answer"}],
        },
    ],
}


def make_store(tmp_path):
    return ObsStore(tmp_path / "lab.db")


def test_harvest_blocked_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = OpenAISource(fetch=lambda rid: RESPONSE).harvest(make_store(tmp_path))
    assert result.status == "blocked"


def test_harvest_fetches_ids_from_join_file(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    state = tmp_path / "state"
    state.mkdir()
    (state / "openai_responses.json").write_text(
        '[{"response_id": "resp_abc", "trace_id": "t1", "session_id": "lab-s1", "ts": 1.0}]'
    )
    monkeypatch.setenv("A2ALAB_STATE_DIR", str(state))
    store = make_store(tmp_path)
    result = OpenAISource(fetch=lambda rid: RESPONSE).harvest(store)
    assert result.status == "ok"
    assert result.sessions == 1 and result.events == 3
    (session,) = store.list_sessions("openai")
    assert session["native_id"] == "resp_abc"
    assert session["lab_session_id"] == "lab-s1"
    events = store.list_events("openai", "resp_abc")
    assert {e["event_type"] for e in events} == {"reasoning", "function_call", "message"}
    assert any("ask_agentforce" in (e["summary"] or "") for e in events)


def test_harvest_skips_cached_and_survives_expired(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    state = tmp_path / "state"
    state.mkdir()
    (state / "openai_responses.json").write_text(
        '[{"response_id": "resp_abc", "ts": 1.0}, {"response_id": "resp_gone", "ts": 2.0}]'
    )
    monkeypatch.setenv("A2ALAB_STATE_DIR", str(state))
    store = make_store(tmp_path)
    calls = []

    def fetch(rid):
        calls.append(rid)
        if rid == "resp_gone":
            raise RuntimeError("404 expired")
        return RESPONSE

    source = OpenAISource(fetch=fetch)
    first = source.harvest(store)
    assert first.sessions == 1 and len(first.errors) == 1  # expired id reported, run survives
    second = source.harvest(store)
    assert second.sessions == 1  # cached — counted without refetch
    assert calls.count("resp_abc") == 1  # immutable: fetched exactly once across runs
