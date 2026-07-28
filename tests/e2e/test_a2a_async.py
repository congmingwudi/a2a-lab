"""WS11 — A2A fire-then-poll, proved against a deliberately slow adapter.

The measurement that matters is a COMPARISON, not a state name: the same work,
submitted asynchronously, must return an HTTP response in far less time than the
work itself takes. A test that only asserted `state == SUBMITTED` would pass on a
server that blocked for the full duration and merely reported an early state.

`SLOW_S` is 2.0s against a submit budget of 0.75s. That gap is wide enough to
survive a loaded CI box and still fail loudly if `message/send` ever goes back to
waiting for the adapter — which is the regression this file exists to catch,
because everything above it keeps working when it does. It just gets slow.
"""

import asyncio
import time

import pytest

from interop.adapter import build_app
from interop.clients.a2a import A2AClient
from interop.models import AgentRequest, AgentResponse

from .test_loopback import ServerThread, free_port

SLOW_S = 2.0
SUBMIT_BUDGET_S = 0.75


class SlowAdapter:
    name = "slow"
    description = "Sleeps, so that 'returned before the work finished' is measurable."

    def __init__(self, delay: float = SLOW_S):
        self.delay = delay
        self.started = 0
        self.finished = 0

    async def handle(self, req: AgentRequest) -> AgentResponse:
        self.started += 1
        await asyncio.sleep(self.delay)
        self.finished += 1
        return AgentResponse(text=f"slow answer: {req.message}", session_id=req.session_id)


@pytest.fixture
def slow_server():
    adapter = SlowAdapter()
    port = free_port()
    server = ServerThread(
        build_app(adapter, "a2a", public_url=f"http://127.0.0.1:{port}/"), port
    ).start()
    yield adapter, f"http://127.0.0.1:{port}"
    server.stop()


async def test_submit_returns_before_the_work_finishes(slow_server, isolated_traces):
    """The gateway-ceiling claim, as an assertion: the HTTP request is over
    while the agent is still running."""
    adapter, base = slow_server
    client = A2AClient(base, target_name="slow")

    start = time.perf_counter()
    handle = await client.submit(AgentRequest(message="ping", trace_id="t-async"))
    submit_s = time.perf_counter() - start

    assert submit_s < SUBMIT_BUDGET_S, (
        f"submit took {submit_s:.2f}s against a {SLOW_S}s adapter — "
        "message/send is blocking on the work again"
    )
    assert handle.task_id
    assert not handle.answered_immediately
    # The adapter is still mid-sleep: work started, no answer yet.
    assert adapter.started == 1
    assert adapter.finished == 0


async def test_poll_walks_the_task_to_completion(slow_server, isolated_traces):
    """The completion arrives AFTER the originating request is gone — the
    a2a-sdk's background consumer keeps draining the queue into the task store,
    which is the half that had to be verified rather than assumed."""
    adapter, base = slow_server
    client = A2AClient(base, target_name="slow")

    handle = await client.submit(AgentRequest(message="ping", trace_id="t-poll"))

    seen = []
    deadline = time.time() + SLOW_S + 8
    snapshot = None
    while time.time() < deadline:
        snapshot = await client.poll(handle.task_id, trace_id="t-poll")
        seen.append(snapshot.state)
        if snapshot.done:
            break
        await asyncio.sleep(0.1)

    assert snapshot is not None and snapshot.done, f"never reached a terminal state: {seen}"
    assert snapshot.state == "TASK_STATE_COMPLETED"
    assert snapshot.text == "slow answer: ping"
    # It was genuinely in flight — at least one reading before the terminal one.
    assert len(seen) > 1, f"completed on the first poll; the adapter did not stay slow: {seen}"
    assert adapter.finished == 1


async def test_polling_is_traced_per_call(slow_server, isolated_traces):
    """Every hop lands with raw payloads (core requirement) — including the
    polls, so a trace shows how many times the caller checked back. For the
    fan-out orchestrator that count IS the finding: does the model poll
    sensibly or busy-wait."""
    from .test_loopback import read_trace_events

    _, base = slow_server
    client = A2AClient(base, target_name="slow")
    handle = await client.submit(AgentRequest(message="ping", trace_id="t-traced"))
    await client.poll(handle.task_id, trace_id="t-traced")
    await client.poll(handle.task_id, trace_id="t-traced")

    events = read_trace_events(isolated_traces)
    ours = [e for e in events if e["trace_id"] == "t-traced"]
    submits = [e for e in ours if "SendMessage" in str(e.get("transport_detail"))]
    polls = [e for e in ours if "GetTask" in str(e.get("transport_detail"))]
    assert len(submits) == 1, f"expected one submit hop, got {len(submits)}"
    assert len(polls) == 2, f"expected two poll hops, got {len(polls)}"
    assert all(e["protocol"] == "a2a" for e in ours)


async def test_blocking_ask_still_waits(slow_server, isolated_traces):
    """The async half is additive. `ask()` must keep blocking, or every
    existing leg and every matrix latency number silently changes meaning."""
    adapter, base = slow_server
    client = A2AClient(base, target_name="slow")

    start = time.perf_counter()
    resp = await client.ask(AgentRequest(message="ping", trace_id="t-blocking"))
    elapsed = time.perf_counter() - start

    assert resp.text == "slow answer: ping"
    assert elapsed >= SLOW_S * 0.9, f"ask() returned in {elapsed:.2f}s — it stopped waiting"
    assert adapter.finished == 1
