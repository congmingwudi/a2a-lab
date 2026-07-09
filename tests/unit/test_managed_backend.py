"""ManagedBackend unit tests with a faked AsyncAnthropic client — verifies
session reuse, text assembly from the event stream, the idle-break gate, and
the ask_agentforce custom-tool round trip. No network."""

from types import SimpleNamespace

import pytest

from interop.models import AgentRequest, AgentResponse
from platforms.claude.managed_backend import ManagedBackend


def ev(**kwargs):
    return SimpleNamespace(**kwargs)


def text_block(text):
    return SimpleNamespace(type="text", text=text)


class FakeStream:
    def __init__(self, events):
        self._events = list(events)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)

    async def close(self):
        self.closed = True


class FakeAnthropic:
    """Just enough of client.beta.sessions.{create,events.{stream,send}}."""

    def __init__(self, scripted_events):
        self.scripted_events = scripted_events  # list per stream open
        self.created_sessions = []
        self.sent_events = []
        self.streams: list[FakeStream] = []
        outer = self

        class Events:
            async def stream(self, session_id):
                events = outer.scripted_events.pop(0)
                stream = FakeStream(events)
                outer.streams.append(stream)
                return stream

            async def send(self, session_id, events):
                outer.sent_events.append({"session_id": session_id, "events": events})

        class Sessions:
            def __init__(self):
                self.events = Events()

            async def create(self, agent, environment_id, title=None):
                sid = f"sesn_{len(outer.created_sessions) + 1}"
                outer.created_sessions.append(
                    {"agent": agent, "environment_id": environment_id, "title": title}
                )
                return SimpleNamespace(id=sid)

        self.beta = SimpleNamespace(sessions=Sessions())


@pytest.fixture
def provisioned(monkeypatch):
    monkeypatch.setenv("CLAUDE_MANAGED_AGENT_ID", "agent_test")
    monkeypatch.setenv("CLAUDE_MANAGED_ENV_ID", "env_test")


def terminal_idle():
    return ev(type="session.status_idle", stop_reason=SimpleNamespace(type="end_turn"))


async def test_answer_assembles_text(provisioned):
    fake = FakeAnthropic(
        [
            [
                ev(type="agent.thinking"),
                ev(type="agent.message", content=[text_block("Hello"), text_block("world")]),
                terminal_idle(),
            ]
        ]
    )
    backend = ManagedBackend(client=fake)
    resp = await backend.answer(AgentRequest(message="hi", trace_id="t1"))
    assert resp.text == "Hello\nworld"
    assert resp.raw["managed_session_id"] == "sesn_1"
    assert fake.created_sessions[0]["agent"] == "agent_test"
    # kickoff was a user.message
    assert fake.sent_events[0]["events"][0]["type"] == "user.message"
    assert fake.streams[0].closed


async def test_requires_action_idle_does_not_break(provisioned):
    fake = FakeAnthropic(
        [
            [
                ev(
                    type="session.status_idle",
                    stop_reason=SimpleNamespace(type="requires_action"),
                ),
                ev(type="agent.message", content=[text_block("late answer")]),
                terminal_idle(),
            ]
        ]
    )
    backend = ManagedBackend(client=fake)
    resp = await backend.answer(AgentRequest(message="hi"))
    assert resp.text == "late answer"


async def test_session_reuse_by_lab_session_id(provisioned):
    fake = FakeAnthropic(
        [
            [ev(type="agent.message", content=[text_block("a1")]), terminal_idle()],
            [ev(type="agent.message", content=[text_block("a2")]), terminal_idle()],
        ]
    )
    backend = ManagedBackend(client=fake)
    await backend.answer(AgentRequest(message="q1", session_id="lab-1"))
    await backend.answer(AgentRequest(message="q2", session_id="lab-1"))
    assert len(fake.created_sessions) == 1  # CMA session reused


async def test_custom_tool_round_trip(provisioned, monkeypatch):
    fake = FakeAnthropic(
        [
            [
                ev(
                    type="agent.custom_tool_use",
                    name="ask_agentforce",
                    id="sevt_1",
                    input={"question": "What is case 42?"},
                ),
                ev(type="agent.message", content=[text_block("Agentforce says: resolved")]),
                terminal_idle(),
            ]
        ]
    )
    backend = ManagedBackend(client=fake)

    class FakeAgentforce:
        def __init__(self):
            self.questions = []

        async def ask(self, req):
            self.questions.append(req.message)
            return AgentResponse(text="case 42 is resolved")

    fake_af = FakeAgentforce()
    backend._agentforce_client = fake_af

    resp = await backend.answer(AgentRequest(message="check case 42"))
    assert fake_af.questions == ["What is case 42?"]
    # the tool result was sent back to the session
    tool_results = [
        e
        for sent in fake.sent_events
        for e in sent["events"]
        if e["type"] == "user.custom_tool_result"
    ]
    assert len(tool_results) == 1
    assert tool_results[0]["custom_tool_use_id"] == "sevt_1"
    assert "case 42 is resolved" in tool_results[0]["content"][0]["text"]
    assert resp.text == "Agentforce says: resolved"


async def test_unprovisioned_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_MANAGED_AGENT_ID", raising=False)
    monkeypatch.delenv("CLAUDE_MANAGED_ENV_ID", raising=False)
    monkeypatch.setenv("A2ALAB_STATE_DIR", str(tmp_path / "nostate"))
    # STATE_FILE is resolved at import time; patch it for the test
    import platforms.claude.managed_backend as mb

    monkeypatch.setattr(mb, "STATE_FILE", tmp_path / "nostate" / "managed.json")
    backend = ManagedBackend(client=FakeAnthropic([[]]))
    with pytest.raises(RuntimeError, match="not provisioned"):
        await backend.answer(AgentRequest(message="hi"))
