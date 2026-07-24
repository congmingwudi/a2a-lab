"""Lab Guide platform: corpus assembly, read tools, and the adapter loop
(Anthropic client faked — no network)."""

from __future__ import annotations


import pytest

from interop.models import AgentRequest
from interop.trace import Hop
from platforms.guide import corpus, tools
from platforms.guide.core import GuideAdapter

# ---- corpus ----------------------------------------------------------------


def test_system_prompt_stuffs_core_docs_and_adr_index():
    prompt = corpus.system_prompt()
    assert "honest" in prompt.lower()
    assert "===== plan/02-matrix.md =====" in prompt
    assert "ADR index" in prompt
    assert "- D27 (" in prompt  # index entries carry id + date


def test_get_decision_returns_markdown_and_rejects_unknown():
    d27 = corpus.get_decision("d27")  # case-insensitive
    assert "delegation" in d27.lower()
    with pytest.raises(ValueError, match="unknown decision"):
        corpus.get_decision("D999")


def test_read_doc_whitelist():
    assert "matrix" in corpus.read_doc("plan/02-matrix.md").lower()
    with pytest.raises(ValueError, match="unknown doc"):
        corpus.read_doc("../.env")


# ---- read tools ------------------------------------------------------------


def _write_sample_trace(trace_id: str) -> None:
    # The autouse fixture isolates the trace dir; Hop writes the jsonl.
    with Hop(
        trace_id,
        source="remote-caller",
        target="guide-rest",
        protocol="rest",
        transport_detail="test",
        request_payload={"message": "hi"},
    ) as hop:
        hop.response_payload = "x" * 2000  # exercises clipping


def test_list_recent_runs_and_get_trace_roundtrip():
    _write_sample_trace("guidetest0001")
    runs = tools.list_recent_runs()
    assert any(r["trace_id"] == "guidetest0001" for r in runs)
    assert tools.list_recent_runs(target_contains="nope-zzz") == []

    trace = tools.get_trace("guidetest0001")
    assert trace["hops"][0]["target"] == "guide-rest"
    assert "[clipped" in trace["hops"][0]["response"]

    missing = tools.get_trace("nope")
    assert missing["hops"] == [] and "no hops" in missing["note"]


def test_briefs_soft_fail_without_pg(monkeypatch):
    for var in ("A2ALAB_PG_CLUSTER_ARN", "A2ALAB_PG_DSN"):
        monkeypatch.delenv(var, raising=False)
    assert tools.list_briefs() == []
    assert "error" in tools.read_brief(1)


# ---- adapter loop (fake Anthropic client) ----------------------------------


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeStream:
    """Mimics client.messages.stream(): text_stream + get_final_message."""

    def __init__(self, texts, final):
        self._texts, self._final = texts, final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    async def text_stream(self):
        for t in self._texts:
            yield t

    async def get_final_message(self):
        return self._final


class _FakeMessages:
    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return self._rounds.pop(0)


class _FakeClient:
    def __init__(self, rounds):
        self.messages = _FakeMessages(rounds)


@pytest.mark.anyio
async def test_handle_runs_tool_round_then_answers():
    tool_round = _FakeStream(
        [],
        _Block(
            stop_reason="tool_use",
            content=[
                _Block(type="tool_use", id="tu1", name="get_decision", input={"decision_id": "D27"})
            ],
        ),
    )
    answer_round = _FakeStream(
        ["The delegation guard ", "stops loops (D27)."],
        _Block(stop_reason="end_turn", content=[]),
    )
    adapter = GuideAdapter(client=_FakeClient([tool_round, answer_round]))
    resp = await adapter.handle(AgentRequest(message="what stops loops?", trace_id="t1"))
    assert resp.text == "The delegation guard stops loops (D27)."
    assert resp.raw["backend"] == "lab-guide"
    # the tool result went back to the model as a tool_result block
    second_call = adapter._client.messages.calls[1]
    tool_results = second_call["messages"][-1]["content"]
    assert tool_results[0]["type"] == "tool_result"
    assert "delegation" in tool_results[0]["content"].lower()


@pytest.mark.anyio
async def test_answer_stream_yields_deltas_tools_done():
    tool_round = _FakeStream(
        [],
        _Block(
            stop_reason="tool_use",
            content=[_Block(type="tool_use", id="tu1", name="list_recent_runs", input={})],
        ),
    )
    answer_round = _FakeStream(["hi"], _Block(stop_reason="end_turn", content=[]))
    adapter = GuideAdapter(client=_FakeClient([tool_round, answer_round]))
    events = [e async for e in adapter.answer_stream("q", view={"type": "insight"})]
    kinds = [e["type"] for e in events]
    assert kinds == ["tool", "delta", "done"]
    # console view context rides as a second (uncached) system block
    system = adapter._client.messages.calls[0]["system"]
    assert len(system) == 2 and "CONSOLE CONTEXT" in system[1]["text"]


def test_mcp_extra_tools_are_registered():
    from interop.servers.mcp import create_mcp_server

    mcp = create_mcp_server(GuideAdapter(client=object()))
    names = {t.name for t in mcp._tool_manager.list_tools()}
    assert {
        "ask",
        "get_decision",
        "get_trace",
        "list_recent_runs",
        "list_briefs",
        "read_brief",
        "read_doc",
    } <= {n.replace("mcp_", "") for n in names}
