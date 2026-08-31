"""WS10 SP1 walking-skeleton smoke: call the deployed MuleSoft broker over A2A
and print its answer + the attributed trace. Mirrors scripts/sf_smoke.py in
spirit (a go/no-go against a real deployment). Run:

    A2ALAB_MULE_BROKER_URL=https://<gateway-ingress>/... \\
      uv run python scripts/mule_broker_smoke.py "what is A2A?"
"""

from __future__ import annotations

import asyncio
import os
import sys

from interop.models import AgentRequest, new_trace_id
from interop.registry import Registry


async def main() -> int:
    if not os.environ.get("A2ALAB_MULE_BROKER_URL"):
        print("A2ALAB_MULE_BROKER_URL unset — deploy the broker first", file=sys.stderr)
        return 2
    question = sys.argv[1] if len(sys.argv) > 1 else "In one sentence, what is A2A?"
    reg = Registry.load()
    client = reg.client_for("mule-broker-a2a")
    trace_id = new_trace_id()
    resp = await client.ask(AgentRequest(message=question, trace_id=trace_id))
    print(f"trace_id: {trace_id}")
    print(f"answer:   {resp.text}")
    return 0 if resp.text.strip() else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
