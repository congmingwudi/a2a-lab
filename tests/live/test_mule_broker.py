"""WS10 SP1 proof (live). Calls the DEPLOYED MuleSoft broker over A2A and
asserts the lab trace shows the broker→face hop attributed to
mulesoft-omni-gateway. Deselected by default; run with `-m live` once the
broker is deployed and A2ALAB_MULE_BROKER_URL is set.

Attribution is read back from the CONSOLE API, not a local file: the
broker→face hop is recorded by the HOSTED face's wiretap into the hosted trace
store (Aurora), so only the console can see it. /api/traces/{id} is the
windowless lookup (no 6h window — see the traces-need-windowless-lookup note)."""

import os

import httpx
import pytest

from interop.models import AgentRequest, new_trace_id
from interop.registry import Registry

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_broker_consults_claude_face_and_attributes_the_hop():
    if not os.environ.get("A2ALAB_MULE_BROKER_URL"):
        pytest.skip("A2ALAB_MULE_BROKER_URL unset — broker not deployed")
    reg = Registry.load()
    client = reg.client_for("mule-broker-a2a")
    trace_id = new_trace_id()
    resp = await client.ask(
        AgentRequest(message="In one sentence, what is A2A?", trace_id=trace_id)
    )
    assert resp.text.strip()

    # The broker→face egress must appear in the hosted trace, attributed to the
    # gateway's machine identity (Task 4). Read it back via the console's
    # windowless per-trace lookup with a persona JWT.
    console = os.environ["A2ALAB_CONSOLE_BASE"]  # e.g. https://console-lab.agenticthings.com
    token = os.environ["A2ALAB_CONSOLE_JWT"]  # a persona JWT from /api/login
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.get(
            f"{console}/api/traces/{trace_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        payload = r.json()
        hops = payload["trace"]["hops"]
    assert any(h.get("source") == "mulesoft-omni-gateway" for h in hops), payload
