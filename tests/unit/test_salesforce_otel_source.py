"""Unit tests for the Agentforce Session Trace OTel source (WS23/D73).

No network: an httpx.MockTransport answers the token POST and the per-session
OTel GET. Session ids are pinned via A2ALAB_OTEL_SESSION_IDS so the DMO
enumerator is not exercised here — the mapping is what these tests pin.
"""

from __future__ import annotations

import httpx

from observability.salesforce_otel_source import SalesforceOtelSource
from observability.store import ObsStore

# A minimal but structurally faithful OTLP/JSON ResourceSpans document: one
# resource, two spans (a root turn + a child LLM call), attributes as the
# KeyValue/AnyValue oneof shape the wire actually uses.
OTEL_DOC = {
    "resourceSpans": [
        {
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": "Coral Cloud Concierge"}},
                ]
            },
            "scopeSpans": [
                {
                    "scope": {"name": "einstein.agent", "version": "1.0"},
                    "spans": [
                        {
                            "spanId": "aaaa",
                            "name": "agent.turn",
                            "startTimeUnixNano": "1784400000000000000",
                            "endTimeUnixNano": "1784400005000000000",
                            "attributes": [],
                            "status": {"message": "OK"},
                        },
                        {
                            "spanId": "bbbb",
                            "parentSpanId": "aaaa",
                            "name": "llm.call",
                            "startTimeUnixNano": "1784400001000000000",
                            "endTimeUnixNano": "1784400004000000000",
                            "attributes": [
                                {"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}},
                                {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-5"}},
                                {"key": "gen_ai.usage.total_tokens", "value": {"intValue": 321}},
                            ],
                            "status": {},
                        },
                    ],
                }
            ],
        }
    ]
}


def _transport():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/services/oauth2/token"):
            return httpx.Response(200, json={"access_token": "tok"})
        if request.url.path.endswith("/einstein/audit/otel/sess-1"):
            return httpx.Response(200, json=OTEL_DOC)
        if request.url.path.endswith("/einstein/audit/otel/sess-404"):
            return httpx.Response(404, json={"error": "unknown session"})
        return httpx.Response(500, text=f"unexpected {request.url}")

    return httpx.MockTransport(handler)


def _env(monkeypatch, session_ids="sess-1"):
    monkeypatch.setenv("SF_MY_DOMAIN", "https://example.my.salesforce.com")
    monkeypatch.setenv("SF_CLIENT_ID", "cid")
    monkeypatch.setenv("SF_CLIENT_SECRET", "csec")
    monkeypatch.setenv("A2ALAB_OTEL_SESSION_IDS", session_ids)


def _source():
    return SalesforceOtelSource(http=httpx.Client(transport=_transport()))


def test_blocked_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("SF_MY_DOMAIN", raising=False)
    monkeypatch.delenv("SF_CLIENT_ID", raising=False)
    result = _source().harvest(ObsStore(tmp_path / "lab.db"))
    assert result.status == "blocked"


def test_maps_otlp_spans_to_session_and_events(tmp_path, monkeypatch):
    _env(monkeypatch)
    store = ObsStore(tmp_path / "lab.db")
    result = _source().harvest(store)

    assert result.status == "ok"
    assert result.sessions == 1 and result.events == 2

    # Writes under its OWN platform name, not the live `salesforce` rows.
    (session,) = store.list_sessions("salesforce-otel")
    assert session["native_id"] == "sess-1"
    assert session["title"] == "Coral Cloud Concierge"
    assert store.list_sessions("salesforce") == []

    events = store.list_events("salesforce-otel", "sess-1")
    types = {e["event_type"] for e in events}
    # Semantic attribute wins for the LLM span; span name for the turn.
    assert types == {"otel.agent.turn", "otel.chat"}
    llm = next(e for e in events if e["event_type"] == "otel.chat")
    assert "gpt-5" in (llm["summary"] or "")
    assert "321" in (llm["summary"] or "")


def test_404_session_is_reported_not_raised(tmp_path, monkeypatch):
    _env(monkeypatch, session_ids="sess-1,sess-404")
    store = ObsStore(tmp_path / "lab.db")
    result = _source().harvest(store)
    # The known session still ingests; the 404 is counted, run survives.
    assert result.status == "ok"
    assert result.sessions == 1
    assert "not found" in result.detail


# -- the public live-read surface the console Session Trace tab uses (WS23) ---


def test_list_session_ids_from_explicit_env(monkeypatch):
    _env(monkeypatch, session_ids="sess-1,sess-9")
    assert _source().list_session_ids() == ["sess-1", "sess-9"]


def test_fetch_trace_returns_doc(monkeypatch):
    _env(monkeypatch)
    assert _source().fetch_trace("sess-1") == OTEL_DOC


def test_fetch_trace_404_is_none(monkeypatch):
    _env(monkeypatch)
    # A 404 is an unknown / aged-out session, reported as None — the console
    # renders "no trace", never a 500.
    assert _source().fetch_trace("sess-404") is None
