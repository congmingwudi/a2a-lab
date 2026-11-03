"""auth: {scheme: salesforce-cc} on the A2A client (D106): the Agentforce A2A pilot.

What is pinned: the client mints a client-credentials token at the org's My
Domain, sends it as a Bearer on every A2A request, reuses it while fresh, mints
again once on a 401, and refuses to start without its three settings.
"""

import json

import httpx
import pytest

from interop.clients.a2a import A2AClient

AUTH = {
    "scheme": "salesforce-cc",
    "my_domain": "https://lab.my.salesforce.example",
    "client_id": "key",
    "client_secret": "secret",
}


def _transport(log, agent_status=None):
    tokens = iter(["t1", "t2", "t3"])
    statuses = iter(agent_status or [])

    def handler(request: httpx.Request) -> httpx.Response:
        # A snapshot: the retry re-sends the SAME Request object with a new header.
        log.append(httpx.Request(request.method, request.url, headers=dict(request.headers),
                                 content=request.content))  # fmt: skip
        if request.url.path == "/services/oauth2/token":
            body = json.dumps({"access_token": next(tokens)}).encode()

            async def stream():  # an UNREAD async body, as the real async transport returns
                yield body

            return httpx.Response(200, content=stream())
        return httpx.Response(next(statuses, 200), json={})

    return httpx.MockTransport(handler)


async def _get(auth, transport, n=1):
    async with httpx.AsyncClient(transport=transport, auth=auth) as hc:
        return [await hc.get("https://api.example/a2a/BOT/v1/card") for _ in range(n)]


async def test_mints_once_and_sends_the_bearer_on_every_request():
    log = []
    auth = A2AClient("https://api.example/a2a/BOT", auth=AUTH)._httpx_auth()
    await _get(auth, _transport(log), n=2)
    token_calls = [r for r in log if r.url.path == "/services/oauth2/token"]
    assert len(token_calls) == 1
    body = token_calls[0].content.decode()
    assert "grant_type=client_credentials" in body and "client_id=key" in body
    agent_calls = [r for r in log if r.url.path != "/services/oauth2/token"]
    assert [r.headers["authorization"] for r in agent_calls] == ["Bearer t1", "Bearer t1"]


async def test_a_401_mints_a_fresh_token_and_retries_once():
    log = []
    auth = A2AClient("https://api.example/a2a/BOT", auth=AUTH)._httpx_auth()
    (resp,) = await _get(auth, _transport(log, agent_status=[401, 200]))
    assert resp.status_code == 200
    agent_calls = [r for r in log if r.url.path != "/services/oauth2/token"]
    assert [r.headers["authorization"] for r in agent_calls] == ["Bearer t1", "Bearer t2"]


def test_the_scheme_sets_authorization_itself_so_no_static_header_is_added():
    client = A2AClient("https://api.example/a2a/BOT", auth=AUTH, protocol_version="1.0")
    headers = client._base_headers(client._httpx_auth())
    assert "authorization" not in {k.lower() for k in headers}
    assert headers["A2A-Version"] == "1.0"


async def test_a_bare_my_domain_host_gets_https():
    log = []
    auth = A2AClient(
        "https://api.example/a2a/BOT", auth={**AUTH, "my_domain": "lab.my.salesforce.example"}
    )._httpx_auth()
    await _get(auth, _transport(log))
    assert str(log[0].url) == "https://lab.my.salesforce.example/services/oauth2/token"


@pytest.mark.parametrize("missing", ["my_domain", "client_id", "client_secret"])
def test_refuses_to_start_without_its_settings(missing):
    auth = {k: v for k, v in AUTH.items() if k != missing}
    with pytest.raises(ValueError, match=missing):
        A2AClient("https://api.example/a2a/BOT", auth=auth)._httpx_auth()


def test_the_flagged_welcome_part_is_not_part_of_the_answer():
    # The first SendMessage of a context, as captured from the pilot 2026-11-03.
    from google.protobuf import json_format

    from a2a.types import Message
    from interop.clients.a2a import WELCOME_PART_KEY, _texts_from_parts

    msg = json_format.ParseDict(
        {
            "messageId": "m",
            "role": "ROLE_AGENT",
            "parts": [
                {"text": "Hi, I'm the assistant.", "metadata": {WELCOME_PART_KEY: True}},
                {"text": "pong"},
            ],
        },
        Message(),
    )
    assert _texts_from_parts(msg.parts) == ["pong"]


async def test_a_working_task_is_polled_until_it_finishes(monkeypatch):
    """WS29 item 14: the pilot hands back a WORKING task for a turn over 5 s.
    ask()'s Task fallback polls tasks/get instead of reading the empty
    artifacts as 'completed with no answer'."""
    from a2a.types import Artifact, Part, Task, TaskState, TaskStatus

    import interop.clients.a2a as a2a_mod

    monkeypatch.setattr(a2a_mod, "POLL_INTERVAL_S", 0)
    client = a2a_mod.A2AClient("https://example.invalid/a2a/x", timeout=5)
    working = Task(id="t1", status=TaskStatus(state=TaskState.TASK_STATE_WORKING))
    done = Task(
        id="t1",
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        artifacts=[Artifact(artifact_id="a", parts=[Part(text="the answer")])],
    )
    reads = iter([working, done])

    async def fake_get_task(task_id, trace_id, *, expect_transient):
        return next(reads)

    monkeypatch.setattr(client, "_get_task", fake_get_task)
    import time

    task = await client._await_task(working, "trace", time.perf_counter())
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert a2a_mod._texts_from_parts(task.artifacts[0].parts) == ["the answer"]
