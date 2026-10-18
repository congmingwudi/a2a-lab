"""Run the WS27 memory arms side by side (plan/18 Phase 2b, D84/D92).

One protocol per arm, over the GA Agent API as the agent-caller identity:

  session 1   plant a run-unique fact and a preference, then end the session
  wait        extraction lag (~64 s measured 2026-10-14; default 120 s)
  session 2   a NEW session asks what the agent remembers
  read        the Data 360 tenant (GetContext for every candidate owner, as the
              arm's registered agent) and the AiParticipantInsight DMO

    uv run python scripts/memory_arms.py explicit native-flag native-action
    uv run python scripts/memory_arms.py native-action --wait 180 --owner 005...

Arms: `explicit` is concierge v1 (D83: hook -> A2ALabMemoryTurn -> bridge, owner =
the `memory_owner` session variable); `native-flag` and `native-action` are its
sibling agents (D92). The native routes key memory to a Salesforce principal, not
to `memory_owner`, so the planted fact is unique per run: a recall can't be a
leftover from an earlier run. Candidate owners read from the tenant are the
persona, the agent-caller run-as user (Id and username) and any `--owner`.

Every arm turn is a real Agent API session on the production org (each one is
deleted after use); the reads are read-only. Prints one JSON summary per arm.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

from setup_agentic_memory import AGENTS, DATA_SPACE, api, soql, tenant_session
from setup_jwt_eca import load_env

ARMS = {  # arm -> setup_agentic_memory.AGENTS key (the registered agent)
    "explicit": "concierge",
    "native-flag": "native-flag",
    "native-action": "native-action",
}
# A run-unique fact: a word pair chosen by the run id, so session 2's reply is
# checked for a name no earlier run planted.
_ADJ = ("amber", "cobalt", "saffron", "violet", "scarlet", "jade", "ochre", "silver")
_NOUN = ("heron", "lantern", "quokka", "glacier", "falcon", "cactus", "comet", "otter")


def planted(run: str) -> tuple[str, list[str]]:
    """(the fact turn, the words a real recall must contain) for this run."""
    n = int(run, 16)
    name = f"{_ADJ[n % len(_ADJ)].title()} {_NOUN[(n // len(_ADJ)) % len(_NOUN)].title()}"
    return f"My sailing dinghy is named {name}, and I race it every Sunday.", name.lower().split()


def recalled(reply: str, words: list[str]) -> bool:
    text = reply.lower()
    return all(w in text for w in words)


def candidate_owners(persona: str, extra: list[str]) -> list[str]:
    owners = [persona]
    caller = os.environ.get("SF_AGENT_CALLER_RUNAS_USER")
    if caller:
        owners.append(caller)
        owners += [r["Id"] for r in soql(f"SELECT Id FROM User WHERE Username = '{caller}'")]
    return owners + [o for o in extra if o not in owners]


def bot_id(developer_name: str) -> str:
    rows = soql(f"SELECT Id FROM BotDefinition WHERE DeveloperName = '{developer_name}'")
    if not rows:
        sys.exit(f"{developer_name} is not published (no BotDefinition) — publish it first")
    return rows[0]["Id"]


def insight_rows(since_iso: str) -> list[list]:
    """AiParticipantInsight rows created since `since_iso` (0 rows 2026-10-18)."""
    sql = ("SELECT InsightType__c, InsightPayloadTxt__c, IndividualId__c, UserId__c, "
           "AgentIdentifier__c, CreatedDate__c FROM AiParticipantInsight_std__dlm "
           f"WHERE CreatedDate__c >= '{since_iso}'")  # fmt: skip
    resp = api("POST", "/query-sql", {"sql": sql}, base="/services/data/v66.0/ssot")
    return resp.get("data", []) if isinstance(resp, dict) else [["query failed", str(resp)]]


def tenant_reads(agent_key: str, owners: list[str]) -> dict[str, str]:
    a = AGENTS[agent_key]
    out = {}
    with tenant_session(a) as http:
        for owner in owners:
            r = http.get("/api/v1/agentic/context",
                         params={"owner_id": owner, "owner_type": "individual",
                                 "agent_id": a.name,
                                 "conversation_id": f"a2alab-arms-read-{uuid.uuid4().hex[:8]}"},
                         headers={"ctx-agent-id": a.name,
                                  "ctx-dataspace-ds_name": DATA_SPACE})  # fmt: skip
            body = r.json() if r.status_code == 200 else {}
            # Recalled memory is `userContext` entries; `ontology` is {} even on a
            # read that recalls (trace f1f870e6, 2026-10-15), so it proves nothing.
            uc = body.get("userContext") or []
            out[owner] = f"{r.status_code} userContext {json.dumps(uc)[:600] if uc else 'NONE'}"
    return out


async def turns(client, run: str, messages: list[str]) -> list[str]:
    from interop.models import AgentRequest

    replies = []
    session = f"arms-{run}-{uuid.uuid4().hex[:6]}"
    try:
        for m in messages:
            resp = await client.ask(AgentRequest(message=m, session_id=session))
            replies.append(resp.text)
    finally:
        await client.end_session(session)
    return replies


async def run_arm(arm: str, run: str, wait: int, extra_owners: list[str]) -> dict:
    from platforms.agentforce.client import AgentforceClient

    agent = AGENTS[ARMS[arm]]
    persona = f"a2alab-arms-{run}"
    fact, words = planted(run)
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    client = AgentforceClient(
        my_domain=os.environ["SF_MY_DOMAIN"],
        client_id=os.environ["SF_CLIENT_ID_CLAUDE"],  # the agent-caller identity (D90)
        client_secret=os.environ["SF_CLIENT_SECRET_CLAUDE"],
        agent_id=bot_id(agent.name),
        source_name="memory-arms",
        # Only the explicit arm reads memory_owner; the native arms have no such variable.
        session_variables=[{"name": "memory_owner", "type": "Text", "value": persona}]
        if arm == "explicit" else None,
    )  # fmt: skip
    try:
        s1 = await turns(client, run, [fact, "Please always answer me in short bullet points."])
        print(f"[{arm}] session 1 done; waiting {wait}s for extraction", file=sys.stderr)
        await asyncio.sleep(wait)
        s2 = await turns(client, run, ["What do you remember about me from before?"])
    finally:
        await client.aclose()
    owners = candidate_owners(persona, extra_owners)
    return {
        "arm": arm, "agent": agent.name, "run": run, "persona": persona,
        "planted": fact, "session1": s1, "session2": s2[0],
        "recalled": recalled(s2[0], words),
        "tenant": tenant_reads(ARMS[arm], owners),
        "insight_rows": insight_rows(started),
    }  # fmt: skip


def main() -> None:
    load_env()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("arms", nargs="+", choices=sorted(ARMS))
    ap.add_argument("--wait", type=int, default=120, help="seconds between sessions 1 and 2")
    ap.add_argument("--owner", action="append", default=[], help="extra tenant owner id to read")
    args = ap.parse_args()
    t0 = time.time()
    for arm in args.arms:  # a run id (so a fact) per arm: no arm can recall another's plant
        run = uuid.uuid4().hex[:8]
        print(json.dumps(asyncio.run(run_arm(arm, run, args.wait, args.owner)), indent=2))
    print(f"{len(args.arms)} arm(s) in {time.time() - t0:.0f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
