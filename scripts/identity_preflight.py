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
        # The hosted brief writer (src/briefs/salesforce.py) is this app's other
        # job, and its bundle is measured (D90): prove every read it makes and
        # that it MAY create/update what it writes, without writing anything.
        "briefs": True,
        "used_by": "local development + the brief writer's CRM writes (D90)",
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
        # The console's Session Trace tab reads the beta OTel API as this app too
        # (WS23); a session from the query above is fetched to prove it.
        "otel": True,
        "used_by": "M11 harvest + console Session Trace — DMO reads, OTel API (D90)",
        "required": False,
    },
    {
        # JWT-bearer, not client-credentials: no secret exists. Its job is the
        # Data 360 CDP token the Agentic Memory runtime APIs take (plan/18), so
        # the check runs BOTH legs — a core token alone proves nothing.
        "app": "a2a_lab_memory",
        "id_env": "SF_CLIENT_ID_MEMORY",
        "jwt_preset": "memory",
        "used_by": "Agentic Memory & Context — JWT-bearer → a360 CDP token (plan/18)",
        "required": False,
    },
    {
        # JWT-bearer as a UI-licensed user: its job is the frontdoor URL the
        # console's inline Tableau Next embed logs the browser in with (WS19).
        "app": "a2a_lab_tab_embed",
        "id_env": "SF_CLIENT_ID_TAB_EMBED",
        "jwt": {"run_as_env": "SF_TAB_EMBED_RUNAS_USER", "key_file": "tab_embed_jwt_private.pem"},
        "used_by": "console inline Tableau Next embed — JWT-bearer → /singleaccess (D90)",
        "required": False,
    },
]

# Every lab seam runs as a dedicated integration user, not the org admin (D86,
# D90): a token whose user is a System Administrator fails the identity even
# when its capability works, because that is the state D90 removed.
ADMIN_PROFILE = "System Administrator"

# What the brief writer must be allowed to do, by sObject describe flag.
BRIEF_WRITES = {"A2ALab_Account_Brief__c": ("createable", "updateable"),
                "Task": ("createable", "updateable")}  # fmt: skip
BRIEF_READS = (
    "SELECT Id, Name FROM Account LIMIT 1",
    "SELECT Id FROM A2ALab_Account_Brief__c LIMIT 1",
    "SELECT Id FROM Task WHERE A2ALab_Delivery_Key__c != null LIMIT 1",
    "SELECT Id FROM User WHERE IsActive = true LIMIT 1",
    # Tooling, as the writer reads it: /query refuses Setup objects without View Setup.
    "tooling:SELECT Id FROM CustomNotificationType WHERE DeveloperName = 'A2ALab_Brief_Alert'",
)

# A 404 from the Agent API is ambiguous: it means "no agent I can serve at
# that id", which covers BOTH "this app may not call that agent" and "that
# agent has no active version". Blaming the wrong one sends you into Setup to
# fix something that was never broken, so the agent's activation state is
# checked separately (SOQL) and the two are reported apart.
FIXES = {
    404: (
        "the agent is active, so the app is the problem. Check "
        "isNamedUserJwtEnabled=true on its ExtlClntAppGlobalOauthSettings — the Agent "
        "API refuses an app without JWT-based access tokens and says only 404. That "
        "was the cause on 2026-07-25; scopes, policies and security settings were all "
        "ruled out first. There is NO app-to-agent linking step (vendor guide)"
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


def _mint(http: httpx.Client, dom: str, cid: str, secret: str) -> tuple[int, dict]:
    r = http.post(
        f"{dom}/services/oauth2/token",
        data={"grant_type": "client_credentials", "client_id": cid, "client_secret": secret},
    )
    return r.status_code, (r.json() if r.status_code == 200 else {})


def _token(http: httpx.Client, dom: str, cid: str, secret: str) -> tuple[int, str | None]:
    code, body = _mint(http, dom, cid, secret)
    return code, body.get("access_token")


def _configured_agent_ids() -> set[str]:
    """The twins this preflight actually exercises, by 15-char BotDefinition id.

    Scoped deliberately to the agents named in IDENTITIES rather than to every
    `A2ALab%` bot in the org: the org also holds retired agents (the
    pre-Agent-Script original from D14 among them), and counting those made the
    "no active version" note fire on every clean run — a warning that is always
    on teaches you to ignore it, which is the opposite of what a preflight is
    for. An agent nothing calls being switched off is not a finding.
    """
    return {
        str(os.environ.get(spec["agent_env"], ""))[:15]
        for spec in IDENTITIES
        if spec.get("agent_env") and os.environ.get(spec["agent_env"])
    }


def _active_agents(http: httpx.Client, dom: str) -> dict[str, bool]:
    """Which lab agents have an Active version, keyed by BotDefinition id.

    Uses whichever configured identity carries the `api` scope; without one we
    return {} and simply don't make the active/inactive distinction rather than
    guessing at it.
    """
    configured = _configured_agent_ids()
    if not configured:
        return {}
    for spec in IDENTITIES:
        cid, secret = os.environ.get(spec["id_env"]), os.environ.get(spec.get("secret_env", ""))
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
            if bid not in configured:
                continue
            active[bid] = active.get(bid, False) or rec.get("Status") == "Active"
        return active
    return {}


def _check_jwt(spec: dict) -> dict:
    """A JWT-bearer identity: mint as the run-as user, then the CDP exchange."""
    from setup_jwt_eca import PRESETS, CredentialError, exchange_cdp, mint_core_token

    if not os.environ.get(spec["id_env"]):
        return {"state": "unset", "detail": f"{spec['id_env']} not set"}
    preset = PRESETS[spec["jwt_preset"]]
    try:
        core = mint_core_token(preset)
        if preset.cdp_exchange:
            exchange_cdp(core)
    except CredentialError as e:
        return {"state": "FAIL", "detail": str(e)}
    if preset.preauth_permission_set:
        # D86: a gated identity must not run as an admin. The JWT's own `sub` is
        # asked, through the token it just minted, so this checks what the
        # credential IS rather than what .env claims.
        profile = _own_profile(core)
        if profile == "System Administrator":
            return {"state": "FAIL", "detail": "runs as a System Administrator, not the "
                    "dedicated integration user (D86, plan/18 Phase 0b)"}  # fmt: skip
        return {"state": "ok", "detail": f"JWT-bearer + a360 CDP token OK, {_run_as_note(profile)}"}
    return {"state": "ok", "detail": "JWT-bearer + a360 CDP token OK"}


def _check_briefs(http: httpx.Client, dom: str, headers: dict, _r) -> str | None:
    """The brief writer's reads, and describe-level proof it may write (D90)."""
    base = f"{dom}/services/data/v64.0"
    for q in BRIEF_READS:
        path, q = ("tooling/query", q[8:]) if q.startswith("tooling:") else ("query", q)
        r = http.get(f"{base}/{path}", params={"q": q}, headers=headers)
        if r.status_code != 200:
            return f"brief writer read refused (HTTP {r.status_code}): {q[:60]} — {r.text[:160]}"
    for sobject, flags in BRIEF_WRITES.items():
        r = http.get(f"{base}/sobjects/{sobject}/describe", headers=headers)
        d = r.json() if r.status_code == 200 else {}
        missing = [f for f in flags if not d.get(f)]
        if missing:
            return f"brief writer may not {'/'.join(missing)} {sobject} (HTTP {r.status_code})"
    return None


def _check_otel(http: httpx.Client, dom: str, headers: dict, r: httpx.Response) -> str | None:
    """Fetch one session's OTel trace; 404 is an aged-out session, not a refusal."""
    recs = r.json().get("data") or r.json().get("records") or []
    sid = next((str(v) for rec in recs for v in (rec if isinstance(rec, list) else
                rec.values()) if isinstance(v, str) and len(v) >= 15), None)  # fmt: skip
    if not sid:
        return None  # no session to fetch; the DMO read already passed
    o = http.get(f"{dom}/services/data/v66.0/einstein/audit/otel/{sid}", headers=headers)
    if o.status_code in (200, 404):
        return None
    return f"OTel API refused (HTTP {o.status_code}): {o.text[:160]}"


def _check_frontdoor(spec: dict) -> dict:
    """JWT-bearer as the run-as user, then /singleaccess: what the embed does."""
    from setup_jwt_eca import CredentialError, Preset, mint_core_token

    j = spec["jwt"]
    if not (os.environ.get(spec["id_env"]) and os.environ.get(j["run_as_env"])):
        return {"state": "unset", "detail": f"{spec['id_env']} / {j['run_as_env']} not set"}
    preset = Preset(name=spec["app"], description="", scopes="", client_id_env=spec["id_env"],
                    run_as_env=j["run_as_env"], key_file=j["key_file"])  # fmt: skip
    try:
        core = mint_core_token(preset)
    except CredentialError as e:
        return {"state": "FAIL", "detail": str(e)}
    fd = httpx.post(f"{core['instance_url']}/services/oauth2/singleaccess", timeout=20,
                    headers={"authorization": f"Bearer {core['access_token']}",
                             "accept": "application/json"})  # fmt: skip
    if fd.status_code != 200 or not fd.json().get("frontdoor_uri"):
        return {"state": "FAIL", "detail": f"frontdoor refused (HTTP {fd.status_code}): "
                f"{fd.text[:160]}"}  # fmt: skip
    profile = _own_profile(core)
    if profile == ADMIN_PROFILE:
        return {"state": "FAIL", "detail": f"frontdoor OK, but runs as a {ADMIN_PROFILE} (D90)"}
    return {"state": "ok", "detail": f"JWT-bearer + frontdoor URL OK, {_run_as_note(profile)}"}


UNNAMED = "?"  # the token's scopes cannot name its user or read its profile


def _run_as_note(profile: str | None) -> str:
    """How the run-as reads, never claiming more than the token proved."""
    if profile == UNNAMED:
        # Chatbot-scoped Agent API tokens land here: nothing they can call names
        # the user, so the gate in the app's policy (D90) is the only guarantee.
        return "run-as not observable with this token's scopes"
    if profile is None:
        # An admin can always read its own profile; an API-only integration
        # profile cannot (no object access), so None is itself the non-admin signal.
        return "run-as profile not readable by the user (API-only, not admin)"
    return f"run-as profile {profile!r}"


def _own_profile(core: dict) -> str | None:
    """The token user's profile name; None if the user can't read it, UNNAMED
    if the token's scopes can't ask."""
    base, auth = core["instance_url"], {"authorization": f"Bearer {core['access_token']}"}
    try:
        uid = httpx.get(f"{base}/services/oauth2/userinfo", headers=auth, timeout=20).json()[
            "user_id"
        ]
    except (httpx.HTTPError, KeyError, ValueError):
        return UNNAMED
    try:
        r = httpx.get(f"{base}/services/data/v64.0/query", headers=auth, timeout=20,
                      params={"q": f"SELECT Profile.Name FROM User WHERE Id = '{uid}'"})  # fmt: skip
    except httpx.HTTPError:
        return None
    if r.status_code != 200 and "api" not in (core.get("scope") or "").split():
        # The token's scopes stop short of the query API (Chatbot-only Agent API
        # apps): that says nothing about the user, so it is not a non-admin signal.
        return UNNAMED
    if r.status_code != 200:
        return None  # an api-scoped token whose user may not read User: not an admin
    recs = r.json().get("records") or []
    return ((recs[0].get("Profile") or {}).get("Name")) if recs else None


def _check(http: httpx.Client, dom: str, spec: dict, active: dict[str, bool]) -> dict:
    if spec.get("jwt_preset"):
        return _check_jwt(spec)
    if spec.get("jwt"):
        return _check_frontdoor(spec)
    cid, secret = os.environ.get(spec["id_env"]), os.environ.get(spec["secret_env"])
    if not cid or not secret:
        return {"state": "unset", "detail": f"{spec['id_env']} / {spec['secret_env']} not set"}

    code, minted = _mint(http, dom, cid, secret)
    token = minted.get("access_token")
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
        done = [f"{capability} OK"]
        for extra in (_check_briefs if spec.get("briefs") else None,
                      _check_otel if spec.get("otel") else None):  # fmt: skip
            if extra:
                problem = extra(http, dom, headers, r)
                if problem:
                    return {"state": "FAIL", "detail": problem}
                done.append({_check_briefs: "brief writer access OK",
                             _check_otel: "OTel API OK"}[extra])  # fmt: skip
        profile = _own_profile({**minted, "instance_url": dom})
        if profile == ADMIN_PROFILE:
            return {"state": "FAIL", "detail": f"{', '.join(done)}, but runs as a "
                    f"{ADMIN_PROFILE} — move it to its integration user (D90)"}  # fmt: skip
        return {"state": "ok", "detail": f"{', '.join(done)}, {_run_as_note(profile)}"}
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
