"""R2-1 (ws27-b2): a typed refusal must survive the hosted REST hop.

In-process, the console recognises StaleMemoryGeneration by class. Hosted, the
face is behind a REST hop, so the class is lost unless the error code travels on
the wire: the server answers 409 + ``error_code``, and the client keeps it.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from interop.adapter import AgentAdapter
from interop.clients.base import RemoteAgentError
from interop.clients.rest import RestClient
from interop.memory import StaleMemoryGeneration
from interop.models import AgentRequest
from interop.servers.rest import create_rest_app


class _StaleAdapter(AgentAdapter):
    name = "stale-face"
    description = "raises a typed refusal"

    async def handle(self, req):
        raise StaleMemoryGeneration("customer memory changed after this chat started")


class _BoomAdapter(AgentAdapter):
    name = "boom-face"
    description = "raises an untyped failure"

    async def handle(self, req):
        raise ValueError("kaboom")


def _invoke(adapter):
    return TestClient(create_rest_app(adapter)).post(
        "/invoke", json=AgentRequest(message="hi", trace_id="t-r21").to_dict()
    )


def test_rest_server_answers_a_typed_refusal_with_409_and_its_code():
    r = _invoke(_StaleAdapter())
    assert r.status_code == 409
    body = r.json()
    assert body["error_code"] == "stale_memory_generation"
    assert body["trace_id"] == "t-r21"
    assert "StaleMemoryGeneration" in body["error"]


def test_rest_server_keeps_untyped_failures_as_structured_500():
    r = _invoke(_BoomAdapter())
    assert r.status_code == 500
    assert "error_code" not in r.json()
    assert "kaboom" in r.json()["error"]


def _client_answering(status, payload):
    client = RestClient("https://face.example", target_name="claude-rest-hosted")
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=payload))
    )
    return client


@pytest.mark.asyncio
async def test_rest_client_keeps_the_wire_error_code():
    client = _client_answering(
        409,
        {
            "error": "StaleMemoryGeneration: changed",
            "error_code": "stale_memory_generation",
            "trace_id": "t-r21",
        },
    )
    with pytest.raises(RemoteAgentError) as info:
        await client.ask(AgentRequest(message="hi", trace_id="t-r21"))
    assert info.value.status == 409
    assert info.value.error_code == "stale_memory_generation"
    assert "server said" in str(info.value)  # the operator-facing text is unchanged


@pytest.mark.asyncio
async def test_rest_client_error_without_a_code_is_still_a_runtime_error():
    client = _client_answering(500, {"error": "ValueError: kaboom"})
    with pytest.raises(RuntimeError) as info:
        await client.ask(AgentRequest(message="hi"))
    assert isinstance(info.value, RemoteAgentError)
    assert info.value.error_code is None


def test_stale_generation_declares_its_wire_shape():
    assert StaleMemoryGeneration.error_code == "stale_memory_generation"
    assert StaleMemoryGeneration.http_status == 409
