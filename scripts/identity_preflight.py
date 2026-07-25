"""Prove every configured caller identity can still do its job (D37/F6).

Why this exists. On 2026-07-24 the lab split one shared Salesforce External
Client App into per-caller apps, and every signal said it worked: the deploy
scripts succeeded, the apps minted OAuth tokens, Salesforce login history
showed per-caller attribution, and three scenarios returned real CRM content.
All of it was true. None of it tested the thing that had broken — the new apps
were not linked to the agents, so every Agent API call would 404. The
scenarios passed because the containers were still holding the previous
credentials, and login history records token mints, not authorization.

The lesson is narrow and worth encoding: an identity is not verified by
authenticating, it is verified by DOING THE THING IT EXISTS TO DO. So this
script takes each configured identity and exercises its actual capability —
agent callers open an Agent API session, the harvest runs a Data Cloud query —
and fails loudly when one cannot.

    uv run python scripts/identity_preflight.py

Run it after any credential, scope, or connected-app change, and before a
demo. Exits non-zero if any configured identity cannot do its job, so it
works as a gate.
"""

from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv

AGENT_API = "https://api.salesforce.com/einstein/ai-agent/v1"

# Each identity, and the capability that proves it. `agent_env` names the twin
# an agent caller must be able to open a session against; `soql` marks the
# harvest identity, whose job is Data Cloud reads rather than the Agent API.
IDENTITIES = [
    {
        "app": "a2a_lab_app",
        "id_env": "SF_CLIENT_ID",
        "secret_env": "SF_CLIENT_SECRET",
        "agent_env": "SF_AGENT_ID",
        "used_by": "local development (bridge, local servers, local harvests)",
        "required": True,
    },
    {
        "app": "a2a_lab_claude",
        "id_env": "SF_CLIENT_ID_CLAUDE",
        "secret_env": "SF_CLIENT_SECRET_CLAUDE",
        "agent_env": "SF_AGENT_ID",
        "used_by": "Claude AgentCore runtime — ask_agentforce",
        "required": False,
    },
    {
        "app": "a2a_lab_openai",
        "id_env": "SF_CLIENT_ID_OPENAI",
        "secret_env": "SF_CLIENT_SECRET_OPENAI",
        "agent_env": "SF_OPENAI_AGENT_ID",
        "used_by": "OpenAI AgentCore runtime — ask_agentforce",
        "required": False,
    },
    {
        "app": "a2a_lab_shim",
        "id_env": "SF_CLIENT_ID_SHIM",
        "secret_env": "SF_CLIENT_SECRET_SHIM",
        "agent_env": "SF_FOUNDRY_AGENT_ID",
        "used_by": "hosted A2A shim — inbound from Foundry/ADK",
        "required": False,
    },
    {
        "app": "a2a_lab_obs",
        "id_env": "SF_CLIENT_ID_OBS",
        "secret_env": "SF_CLIENT_SECRET_OBS",
        "soql": "SELECT ssot__Id__c FROM ssot__AiAgentSession__dlm LIMIT 1",
        "used_by": "M11 harvest — Data Cloud DMO reads (needs the api scope)",
        "required": False,
    },
]

# A 404 from the Agent API is ambiguous: it means "no agent I can serve at
# that id", which covers BOTH "this app may not call that agent" and "that
# agent has no active version". Blaming the wrong one sends you into Setup to
# fix something that was never broken, so the agent's activation state is
# checked separately (SOQL) and the two are reported apart.
FIXES = {
    404: (
        "the agent is active, so the app is the problem: it authenticates but may not "
        "call this agent. In Agentforce Builder the Connections config is only editable "
        "on a DEACTIVATED version — deactivate, add the connected app, save, reactivate"
    ),
    403: "the app is linked but lacks the scope this call needs — check its OAuth scopes",
    401: "credentials rejected — the consumer secret in .env does not match the app",
}
INACTIVE_FIX = (
    "that agent has NO ACTIVE VERSION — this is not a credentials problem. "
    "Activate it (`sf agent activate --api-name <ApiName> -o <org> --json`) and re-run"
)


def _domain() -> str:
    dom = os.environ["SF_MY_DOMAIN"].rstrip("/")
    return dom if dom.startswith("https://") else f"https://{dom}"


def _token(http: httpx.Client, dom: str, cid: str, secret: str) -> tuple[int, str | None]:
    r = http.post(
        f"{dom}/services/oauth2/token",
        data={"grant_type": "client_credentials", "client_id": cid, "client_secret": secret},
    )
    return r.status_code, (r.json().get("access_token") if r.status_code == 200 else None)


def _active_agents(http: httpx.Client, dom: str) -> dict[str, bool]:
    """Which lab agents have an Active version, keyed by BotDefinition id.

    Uses whichever configured identity carries the `api` scope; without one we
    return {} and simply don't make the active/inactive distinction rather than
    guessing at it.
    """
    for spec in IDENTITIES:
        cid, secret = os.environ.get(spec["id_env"]), os.environ.get(spec["secret_env"])
        if not cid or not secret:
            continue
        _, token = _token(http, dom, cid, secret)
        if not token:
            continue
        r = http.get(
            f"{dom}/services/data/v64.0/query",
            params={
                "q": "SELECT BotDefinitionId, Status FROM BotVersion "
                "WHERE BotDefinition.DeveloperName LIKE 'A2ALab%'"
            },
            headers={"authorization": f"Bearer {token}"},
        )
        if r.status_code != 200:
            continue
        active: dict[str, bool] = {}
        for rec in r.json().get("records", []):
            bid = str(rec.get("BotDefinitionId"))[:15]
            active[bid] = active.get(bid, False) or rec.get("Status") == "Active"
        return active
    return {}


def _check(http: httpx.Client, dom: str, spec: dict, active: dict[str, bool]) -> dict:
    cid, secret = os.environ.get(spec["id_env"]), os.environ.get(spec["secret_env"])
    if not cid or not secret:
        return {"state": "unset", "detail": f"{spec['id_env']} / {spec['secret_env']} not set"}

    code, token = _token(http, dom, cid, secret)
    if not token:
        return {"state": "FAIL", "detail": f"token mint failed (HTTP {code})"}

    headers = {"authorization": f"Bearer {token}", "content-type": "application/json"}
    if spec.get("soql"):
        r = http.get(
            f"{dom}/services/data/v64.0/query",
            params={"q": spec["soql"]},
            headers=headers,
        )
        capability = "Data Cloud query"
    else:
        agent_id = os.environ.get(spec["agent_env"])
        if not agent_id:
            return {"state": "unset", "detail": f"{spec['agent_env']} not set"}
        r = http.post(
            f"{AGENT_API}/agents/{agent_id}/sessions",
            headers=headers,
            json={
                "externalSessionKey": "identity-preflight",
                "instanceConfig": {"endpoint": dom},
                "streamingCapabilities": {"chunkTypes": ["Text"]},
                "bypassUser": True,
            },
        )
        capability = "Agent API session"

    if r.status_code < 400:
        # Close the session we opened; a leaked session is a licensing cost.
        if not spec.get("soql"):
            sid = (r.json() or {}).get("sessionId")
            if sid:
                http.delete(
                    f"{AGENT_API}/sessions/{sid}",
                    headers={**headers, "x-session-end-reason": "UserRequest"},
                )
        return {"state": "ok", "detail": f"{capability} OK"}
    # Separate "the agent is switched off" from "this app may not call it"
    # before handing out a fix — they produce the identical 404.
    if r.status_code == 404 and not spec.get("soql"):
        agent_id = str(os.environ.get(spec["agent_env"], ""))[:15]
        if active and not active.get(agent_id, False):
            return {"state": "FAIL", "detail": f"{capability} refused (HTTP 404) — {INACTIVE_FIX}"}
    return {
        "state": "FAIL",
        "detail": f"{capability} refused (HTTP {r.status_code}) — "
        f"{FIXES.get(r.status_code, 'see the response body')}",
    }


def main() -> int:
    load_dotenv()
    dom = _domain()
    print(f"Identity preflight against {dom}\n")
    failures = 0
    with httpx.Client(timeout=60) as http:
        active = _active_agents(http, dom)
        inactive = [a for a, is_on in active.items() if not is_on]
        if inactive:
            print(
                f"note: {len(inactive)} lab agent(s) have no active version — "
                "sessions against those will 404 regardless of credentials.\n"
            )
        for spec in IDENTITIES:
            result = _check(http, dom, spec, active)
            state = result["state"]
            mark = {"ok": "  ok  ", "FAIL": " FAIL ", "unset": "unset "}[state]
            print(f"[{mark}] {spec['app']:<16} {result['detail']}")
            print(f"           {spec['used_by']}")
            if state == "FAIL" or (state == "unset" and spec["required"]):
                failures += 1
    print()
    if failures:
        print(
            f"{failures} identity/identities cannot do their job — fix before deploying or demoing."
        )
        return 1
    print("every configured identity proved its capability.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
