"""Agent API go/no-go smoke test — run this the moment Salesforce credentials
exist (it is the earliest signal that the org's licensing/agent setup allows
the Agent API at all).

    uv run python scripts/sf_smoke.py

Checks, in order:
 1. OAuth client-credentials token from the org's External Client App
 2. Agent API session create for SF_AGENT_ID
 3. one message round-trip
 4. session delete

Exit code 0 = go. Any failure prints the exact HTTP response for diagnosis.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

from interop.models import AgentRequest, new_trace_id


async def main() -> None:
    load_dotenv()
    from platforms.agentforce.client import AgentforceClient

    try:
        client = AgentforceClient.from_env()
    except RuntimeError as exc:
        print(f"NOT CONFIGURED: {exc}")
        sys.exit(2)

    trace_id = new_trace_id()
    print(f"trace_id: {trace_id}")

    print("1) OAuth token ...", end=" ", flush=True)
    try:
        await client._get_token()
        print("OK")
    except Exception as exc:
        print(f"FAIL\n   {exc}")
        sys.exit(1)

    print("2) session create ...", end=" ", flush=True)
    try:
        await client.ensure_session("smoke", trace_id)
        print("OK")
    except Exception as exc:
        print(f"FAIL\n   {exc}")
        sys.exit(1)

    print("3) message round-trip ...", end=" ", flush=True)
    try:
        req = AgentRequest(
            message="Hello — connectivity smoke test.",
            session_id="smoke",
            trace_id=trace_id,
        )
        resp = await client.ask(req)
        print("OK")
        print(f"   agent said: {resp.text[:200]!r} ({resp.latency_ms} ms)")
    except Exception as exc:
        print(f"FAIL\n   {exc}")
        sys.exit(1)

    print("4) session delete ...", end=" ", flush=True)
    try:
        await client.end_session("smoke", trace_id)
        print("OK")
    except Exception as exc:
        print(f"WARN (non-fatal): {exc}")

    await client.aclose()
    print("\nGO: Agent API works for this org/agent.")


if __name__ == "__main__":
    asyncio.run(main())
