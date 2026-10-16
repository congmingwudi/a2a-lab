"""Register agents for Data 360 Agentic Memory and Context, headlessly (plan/18 Phase 1).

The Connect API "Agent Configuration" tag (API v66.0+) is the documented path —
captured from the published spec `cdp-connect-api-Swagger.yaml` on 2026-10-12:

    GET    /ssot/agentic/agents                       list registered agents
    POST   /ssot/agentic/agent                        register one (409 = name taken)
    GET    /ssot/agentic/agent/{idOrApiName}          one agent (status PROCESSING→ACTIVE)
    DELETE /ssot/agentic/agent/{idOrApiName}          IRREVERSIBLY deletes its memories

The per-data-space "agentic setup" has schemas and examples in the spec but no
published path; probed read-only: `GET /ssot/agentic/setup` lists setups and
`/ssot/agentic/setups/{name}` is the item resource.

Every write here lands in the PRODUCTION org and two choices are immutable
(an agent's API name and data space), so the default is a DRY RUN that prints
the exact request. `--apply` sends it.

    uv run python scripts/setup_agentic_memory.py status
    uv run python scripts/setup_agentic_memory.py setup [--apply]
    uv run python scripts/setup_agentic_memory.py register concierge [--apply]
    uv run python scripts/setup_agentic_memory.py wait concierge
    uv run python scripts/setup_agentic_memory.py extraction concierge on|off [--apply]
    uv run python scripts/setup_agentic_memory.py probe concierge [--apply]
    uv run python scripts/setup_agentic_memory.py user [--username U] [--apply]
    uv run python scripts/setup_agentic_memory.py ungrant [--user U] [--permset PS] [--apply]
    uv run python scripts/setup_agentic_memory.py native [--org ALIAS]

`user` creates the dedicated memory integration user (D86): the Salesforce
Integration license, the API-only Minimum Access profile, and only the Data
Cloud User + Context Service permission sets plus the `A2ALab_Memory_Integration`
gate the ECA pre-authorizes. It is idempotent and refuses to adopt an existing
user on any other profile. `ungrant` removes permission sets from a user. By
default it removes the `PersonalizationUser` grant from the admin. That grant
was a 403 hypothesis (2026-10-13) that the `ctx-agent-id` header made moot.

`native` is read-only: it lists the org's standard invocable actions that touch
agent memory or context, with their inputs and outputs (D84). `--org` points it
at another org, e.g. a Data Cloud One companion org, to see whether the same
native actions exist there.

`extraction` is the UI's "Automatic Memory Extraction" toggle: off on
registration, and the part that will consume flex credits after Limited GA.
Its real home is `configuration.isMemoryGenerationEnabled` on the agent, which
`GET agent` returns at v67.0 only (V67 below; v66 omits it). That is the same
object the UI's Aura read `CdpAgentSetup.getAgentContext` returns. The UI's
Update (`CdpAgentSetup.updateAgentContext`) sends an `AgentInputRepresentation`
of `{label, configuration: {isMemoryGenerationEnabled}}`, the Connect API's own
input class, so `--apply` sends that same body as a v67 PATCH on the agent item.
The `DataAgenticContextAgent.IsMemoryGenerationEnabled` column is only a MIRROR:
a direct write moved the column but not the setting (plan/18), so it is shown
for comparison and never written. The PATCH verb is unpublished (the v66 spec
lists only GET/DELETE on the item) but proven: script on -> UI on, 2026-10-13.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field

from setup_jwt_eca import load_env, target_org

API = "/services/data/v66.0/ssot/agentic"
V67 = "/services/data/v67.0/ssot/agentic"  # first version whose agent carries `configuration`
DATA_SPACE = "default"  # the org has one data space (plan/18 "What the org already has")


@dataclass(frozen=True)
class Agent:
    name: str  # AgentApiName — immutable once registered; it is the runtime `agent_id`
    label: str
    description: str
    # Extra data graphs GetContext may read. Profile DMOs are omitted on purpose:
    # with zero identity-resolution rulesets "Data 360 handles this", and the
    # profile DMO choice is per data space and cannot be changed later (plan/18).
    data_graphs: tuple[str, ...] = field(default=())


AGENTS = {
    "concierge": Agent(
        name="A2ALab_Memory_Concierge",
        label="A2ALab Memory Concierge",
        description="A2A lab: first Agentic Memory experiment, Agentforce (plan/18 Phase 1-2).",
    ),
}


def api(method: str, path: str, body: dict | None = None, base: str = API) -> object:
    """One Connect API call through the sf CLI's auth; returns parsed JSON."""
    cmd = ["sf", "api", "request", "rest", f"{base}{path}", "-o", target_org(), "-X", method]
    if body is not None:
        cmd += ["-b", json.dumps(body)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    try:
        return json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        sys.exit(f"{method} {path} returned non-JSON:\n{proc.stdout}\n{proc.stderr}")


def error_code(resp: object) -> str | None:
    """Connect API errors come back as a list of {errorCode, message}."""
    if isinstance(resp, list) and resp and isinstance(resp[0], dict):
        return resp[0].get("errorCode")
    return None


def setups() -> list[dict]:
    resp = api("GET", f"/setup?dataSpaceName={DATA_SPACE}")
    if error_code(resp):
        sys.exit(f"listing agentic setups failed: {resp}")
    return resp.get("setupConfigs", [])


def agents() -> list[dict]:
    resp = api("GET", "/agents")
    if error_code(resp):
        sys.exit(f"listing agents failed: {resp}")
    return resp.get("agents", [])


def register_body(a: Agent) -> dict:
    # `dataspaceName`, lowercase s: the spec SCHEMA says dataSpaceName and the server
    # 400s "Unrecognized field" on it (2026-10-12). The setup endpoint is the reverse.
    body = {"dataspaceName": DATA_SPACE, "name": a.name, "label": a.label,
            "description": a.description}  # fmt: skip
    if a.data_graphs:
        body["configuration"] = {
            "contextDataSources": {"dataGraphs": [{"name": g} for g in a.data_graphs]}
        }
    return body


def send(method: str, path: str, body: dict, apply: bool) -> object | None:
    print(f"{method} {API}{path}\n{json.dumps(body, indent=2)}")
    if not apply:
        print("(dry run — re-run with --apply to send this to the production org)")
        return None
    resp = api(method, path, body)
    print(json.dumps(resp, indent=2))
    if error_code(resp):
        sys.exit(1)
    return resp


# ---------------------------------------------------------------- verbs


def cmd_status(_args) -> None:
    print(f"agentic setups in {DATA_SPACE!r}: {json.dumps(setups(), indent=2)}")
    print(f"registered agents: {json.dumps(agents(), indent=2)}")


def cmd_setup(args) -> None:
    if setups():
        print(f"an agentic setup already exists in {DATA_SPACE!r} — nothing to do")
        return
    send("POST", "/setups", {"dataSpaceName": DATA_SPACE, "enabled": True}, args.apply)


def cmd_register(args) -> None:
    a = AGENTS[args.agent]
    if any(x.get("name") == a.name for x in agents()):
        print(f"{a.name} is already registered — `wait {args.agent}` shows its status")
        return
    if not setups():
        print(f"note: no agentic setup in {DATA_SPACE!r} yet — run `setup` first if this 4xxs")
    send("POST", "/agent", register_body(a), args.apply)


def cmd_wait(args) -> None:
    """Poll until PROCESSING settles; the auto-provisioned DMOs/graphs take minutes."""
    a = AGENTS[args.agent]
    deadline = time.time() + args.timeout
    while True:
        resp = api("GET", f"/agent/{a.name}")
        if error_code(resp):
            sys.exit(f"{a.name}: {resp}")
        status = str(resp.get("status", "")).upper()
        print(f"{time.strftime('%H:%M:%S')} {a.name}: {status}")
        if status != "PROCESSING" or time.time() > deadline:
            print(json.dumps(resp, indent=2))
            sys.exit(0 if status == "ACTIVE" else 1)
        time.sleep(15)


def extraction_row(a: Agent) -> dict:
    soql = (
        "SELECT Id, IsMemoryGenerationEnabled FROM DataAgenticContextAgent "
        f"WHERE AgentApiName = '{a.name}'"
    )
    recs = sf_json("data", "query", "-q", soql).get("records") or []
    if not recs:
        sys.exit(f"{a.name} is not registered — run `register` first")
    return recs[0]


def sf_json(*args: str) -> dict:
    proc = subprocess.run(
        ["sf", *args, "-o", target_org(), "--json"], capture_output=True, text=True, check=False
    )
    out = json.loads(proc.stdout or "{}")
    if out.get("status", 1) != 0:
        # `org assign` reports per-user refusals in result.failures, not message.
        failures = (out.get("result") or {}).get("failures") or []
        why = "; ".join(f["message"] for f in failures) or out.get("message", proc.stderr)
        sys.exit(f"sf {' '.join(args[:2])} failed: {why}")
    return out["result"]


def cmd_extraction(args) -> None:
    a = AGENTS[args.agent]
    want = args.state == "on"
    resp = api("GET", f"/agent/{a.name}", base=V67)
    if error_code(resp):
        sys.exit(f"{a.name}: {resp}")
    real = (resp.get("configuration") or {}).get("isMemoryGenerationEnabled")
    mirror = extraction_row(a)["IsMemoryGenerationEnabled"]
    print(f"{a.name}: configuration.isMemoryGenerationEnabled = {real} "
          f"(sObject mirror: {mirror})")  # fmt: skip
    if real == want:
        print("already set — nothing to do")
        return
    # The UI's updateAgentContext body; it re-sends `label`, so we do too.
    body = {"label": a.label, "configuration": {"isMemoryGenerationEnabled": want}}
    print(f"PATCH {V67}/agent/{a.name}\n{json.dumps(body, indent=2)}")
    if not args.apply:
        print("(dry run — re-run with --apply to send this to the production org)")
        return
    resp = api("PATCH", f"/agent/{a.name}", body, base=V67)
    if error_code(resp):
        sys.exit(f"PATCH refused: {resp} — fall back to the Agent Config toggle (plan/18)")
    got = (resp.get("configuration") or {}).get("isMemoryGenerationEnabled")
    print(f"configuration.isMemoryGenerationEnabled = {got} "
          f"(sObject mirror: {extraction_row(a)['IsMemoryGenerationEnabled']})")  # fmt: skip
    if got != want:
        sys.exit("the PATCH returned but the setting did not change")


# ---------------------------------------------------------------- tenant probe

PROBE_OWNER = "a2alab-probe-owner"  # synthetic: no Individual/Contact behind it


def tenant_session(a: Agent):
    """An httpx client on the Data 360 tenant, as the memory ECA bound to `a` (aid)."""
    import httpx
    from setup_jwt_eca import PRESETS, CredentialError, exchange_cdp, mint_core_token

    try:
        cdp = exchange_cdp(mint_core_token(PRESETS["memory"]), a.name)
    except CredentialError as e:
        sys.exit(str(e))
    host = cdp["instance_url"]
    host = host if host.startswith("https://") else f"https://{host}"
    return httpx.Client(
        base_url=host, timeout=30, headers={"authorization": f"Bearer {cdp['access_token']}"}
    )


def show(label: str, r) -> None:
    body = r.text.strip()
    print(f"{r.status_code}  {label}\n     {body[:600] if body else '(empty body)'}")


def cmd_probe(args) -> None:
    """Write one turn the way the agentic-context SDK does, then read it back.

    Why: a read is only meaningful after a write, and GetContext 403s with an
    empty body unless `ctx-agent-id` is sent (2026-10-13; the SDK omits it). Same five calls as AsyncSessionManager, ids prefixed `a2alab-probe-`.
    """
    import uuid
    from datetime import datetime, timezone

    a = AGENTS[args.agent]
    cid = f"a2alab-probe-{uuid.uuid4().hex[:8]}"  # the SDK uses one id for both
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    owner_p, agent_p = f"{PROBE_OWNER}-participant", f"{a.name}-participant"
    turn = uuid.uuid4().hex
    writes = [
        ("create conversation", "/api/v1/agentic/conversations",
         {"conversationId": cid, "conversationOwnerId": PROBE_OWNER, "ownerType": "individual",
          "name": cid, "channel": {"type": "web", "id": "agent-sdk"}}),
        ("create session", f"/api/v1/agentic/conversations/{cid}/sessions",
         {"sessionId": cid, "conversationOwnerId": PROBE_OWNER, "ownerType": "individual",
          "participants": [{"id": owner_p, "type": "individual", "role": "owner",
                            "startTime": now, "orgId": "default"}]}),
        ("add agent participant", f"/api/v1/agentic/conversations/{cid}/sessions/{cid}/participants",
         {"participants": [{"id": agent_p, "type": "agent", "role": "participant",
                            "startTime": now}]}),
        ("add entries", f"/api/v1/agentic/conversations/{cid}/entries",
         {"sessionId": cid, "conversationOwnerId": PROBE_OWNER, "entries": [
             {"id": f"u-{turn}", "participantId": owner_p, "type": "user_message",
              "timestamp": now, "category": "Message", "interactionId": turn,
              "content": "I prefer aisle seats and I'm flying to Lisbon in November."},
             {"id": f"a-{turn}", "participantId": agent_p, "type": "agent_response",
              "timestamp": now, "category": "Message", "interactionId": turn,
              "content": "Noted: aisle seats, Lisbon in November."}]}),
    ]  # fmt: skip
    print(f"conversation/session {cid}, owner {PROBE_OWNER} (individual), agent {a.name}")
    if not args.apply:
        for label, path, body in writes:
            print(f"POST {path}  ({label})\n{json.dumps(body, indent=2)}")
        print("(dry run — re-run with --apply to write this probe turn to the production tenant)")
        return
    post_h = {"dataspace": DATA_SPACE, "owner_id": PROBE_OWNER, "agent_id": a.name}
    with tenant_session(a) as http:
        for label, path, body in writes:
            r = http.post(path, json=body, headers=post_h)
            show(label, r)
            if r.status_code >= 400 and r.status_code != 409:  # 409 = exists (SDK: success)
                sys.exit(f"{label} refused — stopping before the reads")
        read_q = {"owner_id": PROBE_OWNER, "owner_type": "individual"}
        for attempt in range(args.reads):
            time.sleep(args.wait)
            r = http.get("/api/v1/agentic/context",
                         params={**read_q, "conversation_id": cid, "agent_id": a.name},
                         headers={"ctx-agent-id": a.name,
                                  "ctx-dataspace-ds_name": DATA_SPACE})  # fmt: skip
            show(f"GetContext (+{(attempt + 1) * args.wait}s)", r)
        r = http.get(f"/api/v1/agentic/conversations/{cid}", params={**read_q, "scope": "all"},
                     headers={"x-sfdc-dataspace": DATA_SPACE, "agent_id": a.name})  # fmt: skip
        show("get conversation", r)


# ---------------------------------------------------------------- integration user

# D86: the memory ECA runs as a dedicated API-only integration user, not the org
# admin. Its license, profile and bundle live with the other seams' users in
# scripts/integration_users.py (D90); `user` below is that script's `memory`.
# GetContext's 403 was once blamed on this pair (2026-10-13); the cause was the
# ctx-agent-id header, so `ungrant` takes it back off the admin by default.
READ_GRANT = ("PersonalizationPsl", "PersonalizationUser")  # (license, permission set)


def soql(q: str) -> list[dict]:
    return sf_json("data", "query", "-q", q).get("records") or []


def operator_user() -> dict:
    username = sf_json("org", "display")["username"]
    return soql("SELECT Id, Username, Email, TimeZoneSidKey, LocaleSidKey, LanguageLocaleKey, "
                f"EmailEncodingKey FROM User WHERE Username = '{username}'")[0]  # fmt: skip


def assigned(username: str) -> tuple[set[str], set[str]]:
    """(permission set license developer names, permission set names) the user holds."""
    psls = {r["PermissionSetLicense"]["DeveloperName"] for r in soql(
        "SELECT PermissionSetLicense.DeveloperName FROM PermissionSetLicenseAssign "
        f"WHERE Assignee.Username = '{username}'")}  # fmt: skip
    pss = {r["PermissionSet"]["Name"] for r in soql(
        "SELECT PermissionSet.Name FROM PermissionSetAssignment WHERE "
        f"Assignee.Username = '{username}' AND PermissionSet.IsOwnedByProfile = false")}  # fmt: skip
    return psls, pss


def cmd_user(args) -> None:
    """Create (or reconcile) the memory integration user; integration_users.py memory."""
    from integration_users import USERS, ensure

    ensure(USERS["memory"], args.username, args.apply)


def cmd_ungrant(args) -> None:
    """Remove permission sets (and, with --license, their licenses) from a user."""
    username = args.user or operator_user()["Username"]
    names = args.permset or [READ_GRANT[1]]
    licenses = args.license if args.license is not None else (
        [READ_GRANT[0]] if not args.permset else [])  # fmt: skip
    q = "','".join(names)
    psa = soql("SELECT Id, PermissionSet.Name FROM PermissionSetAssignment WHERE "
               f"Assignee.Username = '{username}' AND PermissionSet.Name IN ('{q}')")  # fmt: skip
    ql = "','".join(licenses) or "-"
    licensed = lambda: soql(  # noqa: E731
        "SELECT Id, PermissionSetLicense.DeveloperName FROM PermissionSetLicenseAssign "
        f"WHERE Assignee.Username = '{username}' AND "
        f"PermissionSetLicense.DeveloperName IN ('{ql}')")  # fmt: skip
    psla = licensed()
    print(f"from {username}:")
    for r in psa:
        print(f"  remove permission set {r['PermissionSet']['Name']}")
    for r in psla:
        print(f"  remove permission set license {r['PermissionSetLicense']['DeveloperName']}")
    if not (psa or psla):
        print("  nothing assigned — nothing to do")
        return
    if not args.apply:
        print("(dry run — re-run with --apply to remove them in the production org)")
        return
    for r in psa:  # permission sets first: a license can't go while a set needs it
        sf_json("data", "delete", "record", "-s", "PermissionSetAssignment", "-i", r["Id"])
    # Re-read: removing the last set that needs a license can release the license too
    # (PersonalizationPsl did, 2026-10-14), and deleting it again is "invalid cross
    # reference id".
    for r in licensed():
        sf_json("data", "delete", "record", "-s", "PermissionSetLicenseAssign", "-i", r["Id"])
    print("removed; mint a fresh token before re-testing (cached tokens keep old grants)")


# ---------------------------------------------------------------- native actions

NATIVE_HINTS = ("memory", "agentcontext")  # manageUserMemory, getAgentContext, ...


def cmd_native(args) -> None:
    """The org's native memory actions (D84), read-only. Probed 2026-10-13: v67
    lists manageUserMemory, get/updateAgentMemoryPreference and getAgentContext."""
    org = args.org or target_org()
    path = "/services/data/v67.0/actions/standard"

    def get(p: str) -> object:
        proc = subprocess.run(["sf", "api", "request", "rest", p, "-o", org],
                              capture_output=True, text=True, check=False)  # fmt: skip
        try:
            return json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            sys.exit(f"GET {p} returned non-JSON:\n{proc.stdout}\n{proc.stderr}")

    listing = get(path)
    names = [a["name"] for a in listing.get("actions", [])
             if any(h in a["name"].lower() for h in NATIVE_HINTS)]  # fmt: skip
    print(f"{len(names)} native memory/context action(s) in {org}")
    for name in names:
        d = get(f"{path}/{name}")
        d = d[0] if isinstance(d, list) and d else d
        print(f"\n{name}  [{d.get('type')}]  {d.get('description', '')}")
        for kind in ("inputs", "outputs"):
            for p in d.get(kind, []):
                req = " (required)" if p.get("required") else ""
                print(f"  {kind[:-1]:6} {p['name']}: {p.get('type')}{req}")


def main() -> None:
    load_env()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    sub.add_parser("status")
    sp = sub.add_parser("setup")
    sp.add_argument("--apply", action="store_true", help="send it (default: dry run)")
    for verb in ("register", "wait"):
        sp = sub.add_parser(verb)
        sp.add_argument("agent", choices=sorted(AGENTS))
        if verb == "register":
            sp.add_argument("--apply", action="store_true", help="send it (default: dry run)")
        else:
            sp.add_argument("--timeout", type=int, default=900, help="seconds")
    sp = sub.add_parser("extraction")
    sp.add_argument("agent", choices=sorted(AGENTS))
    sp.add_argument("state", choices=("on", "off"))
    sp.add_argument("--apply", action="store_true", help="send it (default: dry run)")
    sp = sub.add_parser("probe")
    sp.add_argument("agent", choices=sorted(AGENTS))
    sp.add_argument("--apply", action="store_true", help="write the turn (default: dry run)")
    sp.add_argument("--reads", type=int, default=3, help="GetContext attempts after the write")
    sp.add_argument("--wait", type=int, default=5, help="seconds between reads")
    sp = sub.add_parser("user")
    sp.add_argument("--username", help="default: a2alab.memory@<the operator's domain>")
    sp.add_argument("--apply", action="store_true", help="create/assign (default: dry run)")
    sp = sub.add_parser("ungrant")
    sp.add_argument("--user", help="default: the sf CLI's own user (the operator admin)")
    sp.add_argument("--permset", action="append", help="default: PersonalizationUser")
    sp.add_argument("--license", action="append", help="permission set license(s) to drop")
    sp.add_argument("--apply", action="store_true", help="remove them (default: dry run)")
    sp = sub.add_parser("native")
    sp.add_argument("--org", help="sf alias to inspect (default: SF_TARGET_ORG)")
    args = ap.parse_args()
    verbs = {"status": cmd_status, "setup": cmd_setup, "register": cmd_register,
             "wait": cmd_wait, "extraction": cmd_extraction, "probe": cmd_probe,
             "user": cmd_user, "ungrant": cmd_ungrant, "native": cmd_native}  # fmt: skip
    verbs[args.verb](args)


if __name__ == "__main__":
    main()
