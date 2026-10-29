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
sibling agents (D92), and `employee-flag` / `employee-action` the same two as
Employee agents (D95). An Employee agent runs as the session user, which no
Integration-licensed identity can be, so those arms run as the dedicated employee
lab user (SF_EMPLOYEE_RUNAS_USER, JWT bearer through a2a_lab_employee) with
bypassUser false; `--preview` runs them through `sf agent preview` as the sf CLI's
user instead, whose local planner traces show the manageUserMemory outputs. The native routes key memory to a Salesforce principal, not
to `memory_owner`, so the planted fact is unique per run: a recall can't be a
leftover from an earlier run. Candidate owners read from the tenant are the
persona, the agent-caller run-as user (Id and username) and any `--owner`.

Every arm turn is a real Agent API (or preview) session on the production org (each one is
deleted after use); the reads are read-only. Prints one JSON summary per arm.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from interop.memory import owner_conversation
from setup_agentic_memory import AGENTS, DATA_SPACE, api, sf_json, soql, tenant_session
from setup_jwt_eca import PRESETS, load_env, mint_core_token, target_org

ARMS = {  # arm -> setup_agentic_memory.AGENTS key (the registered agent)
    "explicit": "concierge",
    "native-flag": "native-flag",
    "native-action": "native-action",
    # D95: the native arms as Employee agents. An Employee agent runs as the session
    # user, which the Integration-licensed agent-caller cannot be (the licence refuses
    # EinsteinCopilotUser), so these run as the employee lab user.
    "employee-flag": "employee-flag",
    "employee-action": "employee-action",
}
EMPLOYEE_ARMS = {"employee-flag", "employee-action"}
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


def tenant_reads(agent_key: str, owners: list[str], conversations: list[str]) -> dict[str, str]:
    """GetContext for every (owner, conversation) pair. GetContext summarises only
    the conversation it is asked about (D94), so a fresh id proves nothing: the
    candidates are the conversations the arm could have written — the owner's
    derived one (explicit, D94) and the Agent API session ids (native)."""
    a = AGENTS[agent_key]
    out = {}
    with tenant_session(a) as http:
        for owner in owners:
            for cid in conversations or [owner_conversation(owner)]:
                r = http.get("/api/v1/agentic/context",
                             params={"owner_id": owner, "owner_type": "individual",
                                     "agent_id": a.name, "conversation_id": cid},
                             headers={"ctx-agent-id": a.name,
                                      "ctx-dataspace-ds_name": DATA_SPACE})  # fmt: skip
                body = r.json() if r.status_code == 200 else {}
                # Recalled memory is `userContext` entries; `ontology` is {} even on a
                # read that recalls (trace f1f870e6, 2026-10-15), so it proves nothing.
                uc = body.get("userContext") or []
                out[f"{owner} @ {cid}"] = (
                    f"{r.status_code} userContext {json.dumps(uc)[:600] if uc else 'NONE'}"
                )
    return out


async def turns(client, run: str, messages: list[str]) -> tuple[list[str], str | None]:
    """(the replies, the Agent API session id they ran in)."""
    from interop.models import AgentRequest

    replies, sf_session = [], None
    session = f"arms-{run}-{uuid.uuid4().hex[:6]}"
    try:
        for m in messages:
            resp = await client.ask(AgentRequest(message=m, session_id=session))
            replies.append(resp.text)
            sf_session = resp.metadata.get("agent_session_id") or sf_session
    finally:
        await client.end_session(session)
    return replies, sf_session


SF_PROJECT = Path(__file__).resolve().parents[1] / "salesforce"  # preview needs a project dir


def _sf(*args: str) -> dict:
    cmd = ["sf", "agent", "preview", *args, "-o", target_org(), "--json"]
    proc = subprocess.run(cmd, cwd=SF_PROJECT, capture_output=True, text=True, check=False)
    out = json.loads(proc.stdout or "{}")
    if out.get("status") != 0:
        sys.exit(f"sf agent preview {args[0]} failed: {out.get('message') or proc.stderr}")
    return out["result"]


def native_memory(traces_path: str) -> list[dict]:
    """The manageUserMemory outputs in a preview session's local planner traces."""
    found = []
    for f in sorted(Path(traces_path, "traces").glob("*.json")):
        text = f.read_text()
        for m in re.finditer(r'"userMemoryEnabled": (true|false)', text):
            window = text[m.start() : m.start() + 1200]
            msg = re.search(r'"resultMessage": "([^"]*)"', window)
            mem = re.search(r'"userMemories": "((?:[^"\\]|\\.)*)"', window)
            found.append({"userMemoryEnabled": m.group(1) == "true",
                          "resultMessage": msg.group(1) if msg else None,
                          "userMemories": mem.group(1)[:300] if mem else None})  # fmt: skip
    return found


def preview_turns(api_name: str, messages: list[str]) -> tuple[list[str], str, list[dict]]:
    """(the replies, the session id, its manageUserMemory outputs) for one preview
    session of a published agent, run as the CLI's user (D95)."""
    sid = _sf("start", "-n", api_name)["sessionId"]
    replies = []
    try:
        for m in messages:
            r = _sf("send", "-n", api_name, "--session-id", sid, "-u", m)
            replies.append(" ".join(x.get("message", "") for x in r.get("messages", [])))
    finally:
        traces = _sf("end", "-n", api_name, "--session-id", sid).get("tracesPath", "")
    return replies, sid, native_memory(traces) if traces else []


def run_preview_arm(arm: str, run: str, wait: int, extra_owners: list[str]) -> dict:
    agent = AGENTS[ARMS[arm]]
    fact, words = planted(run)
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    s1, sf1, m1 = preview_turns(
        agent.name, [fact, "Please always answer me in short bullet points."]
    )
    print(f"[{arm}] session 1 done; waiting {wait}s for extraction", file=sys.stderr)
    time.sleep(wait)
    s2, sf2, m2 = preview_turns(agent.name, ["What do you remember about me from before?"])
    user = sf_json("org", "display")["username"]
    owners = [user] + [r["Id"] for r in soql(f"SELECT Id FROM User WHERE Username = '{user}'")]
    return {
        "arm": arm, "agent": agent.name, "run": run, "session_user": user,
        "planted": fact, "session1": s1, "session2": s2[0],
        "recalled": recalled(s2[0], words),
        "agent_sessions": [sf1, sf2],
        "native_memory": {"session1": m1, "session2": m2},
        "tenant": tenant_reads(ARMS[arm], owners + [o for o in extra_owners if o not in owners],
                               [sf1, sf2]),
        "insight_rows": insight_rows(started),
    }  # fmt: skip


async def run_arm(arm: str, run: str, wait: int, extra_owners: list[str]) -> dict:
    from platforms.agentforce.client import AgentforceClient

    agent = AGENTS[ARMS[arm]]
    persona = f"a2alab-arms-{run}"
    fact, words = planted(run)
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    employee = arm in EMPLOYEE_ARMS
    client = AgentforceClient(
        my_domain=os.environ["SF_MY_DOMAIN"],
        # The agent-caller identity (D90); an Employee arm authenticates as the
        # employee lab user instead, who is also the user its agent runs as (D95).
        client_id="" if employee else os.environ["SF_CLIENT_ID_CLAUDE"],
        client_secret="" if employee else os.environ["SF_CLIENT_SECRET_CLAUDE"],
        token_provider=(lambda: mint_core_token(PRESETS["employee"])) if employee else None,
        bypass_user=not employee,
        agent_id=bot_id(agent.name),
        source_name="memory-arms",
        # Only the explicit arm reads memory_owner; the native arms have no such variable.
        session_variables=[{"name": "memory_owner", "type": "Text", "value": persona}]
        if arm == "explicit" else None,
    )  # fmt: skip
    try:
        s1, sf1 = await turns(
            client, run, [fact, "Please always answer me in short bullet points."]
        )
        print(f"[{arm}] session 1 done; waiting {wait}s for extraction", file=sys.stderr)
        await asyncio.sleep(wait)
        s2, sf2 = await turns(client, run, ["What do you remember about me from before?"])
    finally:
        await client.aclose()
    owners = candidate_owners(persona, extra_owners)
    if employee:
        user = os.environ["SF_EMPLOYEE_RUNAS_USER"]
        owners = [user] + [r["Id"] for r in soql(f"SELECT Id FROM User WHERE Username = '{user}'")]
        owners += [o for o in extra_owners if o not in owners]
    return {
        "arm": arm, "agent": agent.name, "run": run, "persona": persona,
        **({"session_user": os.environ["SF_EMPLOYEE_RUNAS_USER"]} if employee else {}),
        "planted": fact, "session1": s1, "session2": s2[0],
        "recalled": recalled(s2[0], words),
        "agent_sessions": [sf1, sf2],
        "tenant": tenant_reads(ARMS[arm], owners,
                               [] if arm == "explicit" else [c for c in (sf1, sf2) if c]),
        "insight_rows": insight_rows(started),
    }  # fmt: skip


def main() -> None:
    load_env()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("arms", nargs="+", choices=sorted(ARMS))
    ap.add_argument("--wait", type=int, default=120, help="seconds between sessions 1 and 2")
    ap.add_argument("--owner", action="append", default=[], help="extra tenant owner id to read")
    ap.add_argument(
        "--preview",
        action="store_true",
        help="run the employee arms through sf agent preview as the CLI's user",
    )
    args = ap.parse_args()
    t0 = time.time()
    for arm in args.arms:  # a run id (so a fact) per arm: no arm can recall another's plant
        run = uuid.uuid4().hex[:8]
        result = (run_preview_arm(arm, run, args.wait, args.owner)
                  if args.preview and arm in EMPLOYEE_ARMS
                  else asyncio.run(run_arm(arm, run, args.wait, args.owner)))  # fmt: skip
        print(json.dumps(result, indent=2))
    print(f"{len(args.arms)} arm(s) in {time.time() - t0:.0f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
