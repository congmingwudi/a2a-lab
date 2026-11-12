#!/usr/bin/env python3
"""D115 governance test: is curated memory customer-scoped or registration-scoped, and does a
mismatched identity fail closed?

The governance guide (developer.salesforce.com .../agentic-memory-governance) says curated
memory "is scoped to the customer (`xid`) and is available to any agent serving that
customer", and that "a missing or mismatched identity returns no data". D115 point 3 measured
facts as registration-scoped. This probe tests both claims with two SYNTHETIC owners (never a
demo customer):

  T1  save one fact for owner P through registration A (A2ALab_Memory_Case)
  T2  read P's facts through A                       -> expect the fact (baseline)
  T3  read P's facts through registration B          -> doc: the fact; D115: nothing
  T4  read P's facts through A with a token bound to owner Q (xid mismatch) -> expect nothing
  T5  GetContext for P through A with Q's token       -> expect nothing / refusal
  T6  token exchange for an unregistered aid          -> expect access_denied
  T7  delete the fact through A (cleanup), then confirm it is gone
  T8  read P's facts with a client token (xid=P, no aid) and no agent_id header
      -> public contract: clients can call without agent_id; no agent-level filter

T2/T3 poll for up to --wait seconds, because writes land through the real-time layer.
Results go to traces/governance/ (gitignored). Writes to Data 360: one fact, deleted again.

    uv run python scripts/memory_governance_probe.py [--wait 240] [--other A2ALab_Memory_Case_Graph_Ind]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from interop.memory import OWNER_TYPE, MemoryCallError, MemoryClient, data_space  # noqa: E402
from interop.models import new_trace_id  # noqa: E402
from setup_jwt_eca import PRESETS, exchange_cdp, mint_core_token  # noqa: E402

CATEGORY = "sys_PROFILE_FACT"
OUT = ROOT / "traces" / "governance"


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%SZ")


async def raw_read(
    mem: MemoryClient, *, token_agent: str, token_owner: str, path: str, params: dict, headers: dict
) -> tuple[int | str, object]:
    """One GET with a token minted for (token_agent, token_owner) — lets xid differ from the
    owner being read, which MemoryClient._call never does on purpose."""
    try:
        tok = await mem._token(token_agent, token_owner)
    except MemoryCallError as e:
        return "token-refused", str(e)[:300]
    r = await mem._http.get(
        f"{tok.base_url}{path}",
        params=params,
        headers={"authorization": f"Bearer {tok.access_token}", **headers},
    )
    body = r.json() if r.content and "json" in r.headers.get("content-type", "") else r.text
    return r.status_code, body


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--agent", default="A2ALab_Memory_Case")
    ap.add_argument("--other", default="A2ALab_Memory_Case_Graph_Ind")
    ap.add_argument(
        "--category",
        choices=("sys_PROFILE_FACT", "sys_PROFILE_PREFERENCES"),
        default=CATEGORY,
    )
    ap.add_argument("--wait", type=int, default=240)
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    run = uuid.uuid4().hex[:10]
    p_owner, q_owner = f"a2alab-gov-p-{run}", f"a2alab-gov-q-{run}"
    marker = f"Governance probe {run}: prefers aisle seats ({args.category})"
    mem, t = MemoryClient(source="governance-probe"), new_trace_id()
    res: dict = {
        "run": run,
        "owner_p": p_owner,
        "owner_q": q_owner,
        "agent": args.agent,
        "other": args.other,
        "category": args.category,
        "trace_id": t,
        "tests": {},
    }

    def rec(k: str, **v) -> None:
        res["tests"][k] = {"t": stamp(), **v}
        print(stamp(), k, json.dumps(v, default=str)[:300], flush=True)

    saved = await mem.create_memories(
        t, agent_id=args.agent, owner_id=p_owner, category=args.category, contents=[marker]
    )
    ids = [e.memory_id for e in saved.entries]
    rec("T1_save", memory_ids=ids)

    async def has_marker(agent: str) -> bool:
        listing = await mem.list_memories(
            t, agent_id=agent, owner_id=p_owner, category=args.category
        )
        return any(e.content == marker for e in listing.entries)

    t0, seen = time.time(), {"A": None, "B": None}
    while time.time() - t0 < args.wait and not all(seen.values()):
        for key, agent in (("A", args.agent), ("B", args.other)):
            if not seen[key] and await has_marker(agent):
                seen[key] = round(time.time() - t0, 1)
        if not all(seen.values()):
            await asyncio.sleep(15)
    rec("T2_same_registration", visible_after_s=seen["A"])
    rec(
        "T3_other_registration",
        visible_after_s=seen["B"],
        verdict="customer-scoped (doc)" if seen["B"] else "registration-scoped (D115)",
    )

    st, body = await raw_read(
        mem,
        token_agent=args.agent,
        token_owner=q_owner,
        path="/api/v1/agentic/memory",
        params={"profile_id": p_owner, "profile_type": OWNER_TYPE, "category": args.category},
        headers={"agent_id": args.agent},
    )
    leaked = marker in json.dumps(body)
    rec("T4_mismatched_xid_memory", status=st, leaked=leaked, body=body)

    st, body = await raw_read(
        mem,
        token_agent=args.agent,
        token_owner=q_owner,
        path="/api/v1/agentic/context",
        params={
            "conversation_id": f"{p_owner}",
            "owner_id": p_owner,
            "agent_id": args.agent,
            "owner_type": OWNER_TYPE,
        },
        headers={"ctx-agent-id": args.agent, "ctx-dataspace-ds_name": data_space()},
    )
    rec("T5_mismatched_xid_context", status=st, leaked=marker in json.dumps(body), body=body)

    st, body = await raw_read(
        mem,
        token_agent=f"A2ALab_Unregistered_{run}",
        token_owner=p_owner,
        path="/api/v1/agentic/memory",
        params={"profile_id": p_owner, "profile_type": OWNER_TYPE, "category": args.category},
        headers={"agent_id": f"A2ALab_Unregistered_{run}"},
    )
    rec("T6_unregistered_agent", status=st, body=body)

    # The published Memory API contract makes agent_id optional because a client
    # can call without an agent identity. Mint that exact JWT path: xid is P, aid
    # is absent, and the request sends no agent_id header. Record only the claim
    # keys and marker result; never retain the bearer token in the trace.
    core = mint_core_token(PRESETS["memory"])
    client_token = exchange_cdp(core, external_id=p_owner)
    import jwt as pyjwt

    claims = pyjwt.decode(client_token["access_token"], options={"verify_signature": False})
    attrs = claims.get("custom_attributes") or {}
    host = str(client_token["instance_url"])
    host = host if host.startswith("https://") else f"https://{host}"
    r = await mem._http.get(
        f"{host}/api/v1/agentic/memory",
        params={"profile_id": p_owner, "profile_type": OWNER_TYPE, "category": args.category},
        headers={"authorization": f"Bearer {client_token['access_token']}"},
    )
    client_body = (
        r.json() if r.content and "json" in r.headers.get("content-type", "") else r.text
    )
    rec(
        "T8_client_without_agent",
        status=r.status_code,
        marker_visible=marker in json.dumps(client_body),
        custom_attribute_keys=sorted(attrs),
        has_xid=bool(attrs.get("xid")),
        has_aid=bool(attrs.get("aid")),
        dataspace=attrs.get("dataspace"),
    )

    if ids:
        gone = await mem.delete_memories(
            t, agent_id=args.agent, owner_id=p_owner,
            category=args.category, memory_ids=ids,
        )
        rec("T7_cleanup", deleted=list(gone), still_visible=await has_marker(args.agent))
    await mem.aclose()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"governance-{run}.json").write_text(json.dumps(res, indent=1, default=str))
    print("wrote", OUT / f"governance-{run}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
